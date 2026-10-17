import sys
import os
import html
import tempfile
import unittest
import responses as responses_lib
from requests.exceptions import ConnectionError as RequestsConnectionError

# Mock aqt BEFORE importing the module under test (established project pattern)
from unittest.mock import MagicMock, patch
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.mw'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['aqt.qt'] = MagicMock()

# Module under test — will raise ImportError until Plan 02 creates it
import src.core.audio_fetcher as audio_fetcher
from src.core.audio_fetcher import AudioDownloadError


class FakeMedia:
    """Minimal fake of col.media — backed by a dict. No Anki import needed."""

    def __init__(self):
        self._files: dict = {}

    def have(self, fname: str) -> bool:
        return fname in self._files

    def add_file(self, path: str) -> str:
        """Copy semantics: store by basename, return that basename."""
        fname = os.path.basename(path)
        self._files[fname] = path
        return fname


class FakeCol:
    def __init__(self):
        self.media = FakeMedia()


class FakeLegacyMedia:
    """Fake of col.media without trash_files() — models older Anki, where
    unregister_audio_files() must fall back to deleting from media.dir()."""

    def __init__(self, media_dir: str):
        self._dir = media_dir

    def dir(self) -> str:
        return self._dir


class FakeLegacyCol:
    def __init__(self, media_dir: str):
        self.media = FakeLegacyMedia(media_dir)


class TestDownloadAudio(unittest.TestCase):

    @responses_lib.activate
    def test_successful_download_returns_filename(self):
        """successful download returns filename from add_file."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/12345.mp3",
            body=b"\xff\xfb",  # minimal MP3 magic bytes
            status=200,
        )
        col = FakeCol()
        result = audio_fetcher.download_audio("12345", col)
        self.assertEqual(result, "12345.mp3")
        self.assertTrue(col.media.have("12345.mp3"))

    @responses_lib.activate
    def test_404_returns_none(self):
        """404 means no recording — return None, do not raise."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/99999.mp3",
            status=404,
        )
        col = FakeCol()
        result = audio_fetcher.download_audio("99999", col)
        self.assertIsNone(result)

    @responses_lib.activate
    def test_403_restricted_recording_returns_none(self):
        """403 (author disallows reuse outside Tatoeba) is skipped, not an error.

        The restriction is permanent, so no alternative endpoint is tried.
        """
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/10933373.mp3",
            status=403,
        )
        col = FakeCol()
        result = audio_fetcher.download_audio("10933373", col)
        self.assertIsNone(result)
        # Only the CDN request was made — nothing else was attempted.
        self.assertEqual(len(responses_lib.calls), 1)

    @responses_lib.activate
    def test_network_error_raises_audio_download_error(self):
        """non-404 failures raise AudioDownloadError."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/11111.mp3",
            body=RequestsConnectionError("simulated timeout"),
        )
        col = FakeCol()
        with self.assertRaises(AudioDownloadError):
            audio_fetcher.download_audio("11111", col)

    @responses_lib.activate
    def test_cached_file_not_re_downloaded(self):
        """cached file returns immediately — no HTTP call made."""
        col = FakeCol()
        # Pre-populate cache as if a previous run already registered the file
        col.media._files["12345.mp3"] = "/fake/media/12345.mp3"
        result = audio_fetcher.download_audio("12345", col)
        self.assertEqual(result, "12345.mp3")
        # responses_lib.calls would contain entries if any HTTP request was made
        self.assertEqual(len(responses_lib.calls), 0)

    @responses_lib.activate
    def test_sound_tag_not_escaped(self):
        """returned filename when wrapped in [sound:] is not mutated by html.escape."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/12345.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        col = FakeCol()
        fname = audio_fetcher.download_audio("12345", col)
        sound_tag = f"[sound:{fname}]"
        # html.escape must not alter the tag — [ ] are not HTML-special characters
        self.assertEqual(html.escape(sound_tag), sound_tag)
        self.assertEqual(sound_tag, "[sound:12345.mp3]")


class TestFetchAudioToTemp(unittest.TestCase):
    """Status-returning fetch used by resolve_audio()."""

    @responses_lib.activate
    def test_ok_returns_path(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/12345.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        status, path = audio_fetcher.fetch_audio_to_temp("12345")
        self.assertEqual(status, audio_fetcher.FETCH_OK)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        audio_fetcher.cleanup_temp_audio(path)

    @responses_lib.activate
    def test_404_reports_no_recording(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/99999.mp3",
            status=404,
        )
        status, path = audio_fetcher.fetch_audio_to_temp("99999")
        self.assertEqual(status, audio_fetcher.FETCH_NO_RECORDING)
        self.assertIsNone(path)

    @responses_lib.activate
    def test_403_reports_restricted(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/10933373.mp3",
            status=403,
        )
        status, path = audio_fetcher.fetch_audio_to_temp("10933373")
        self.assertEqual(status, audio_fetcher.FETCH_RESTRICTED)
        self.assertIsNone(path)

    @responses_lib.activate
    def test_network_error_still_raises(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/11111.mp3",
            body=RequestsConnectionError("simulated timeout"),
        )
        with self.assertRaises(AudioDownloadError):
            audio_fetcher.fetch_audio_to_temp("11111")


class TestResolveAudio(unittest.TestCase):
    """Shared 403 re-selection logic used by batch and manual modes."""

    @responses_lib.activate
    def test_media_hit_returns_media_source_without_fetching(self):
        col = FakeCol()
        col.media._files["12345.mp3"] = "/fake/12345.mp3"
        resolved = audio_fetcher.resolve_audio("12345", [], col)
        self.assertEqual(resolved.jpn_id, "12345")
        self.assertEqual(resolved.source, "media")
        self.assertEqual(resolved.payload, "12345.mp3")
        self.assertIsNone(resolved.jpn_text)
        self.assertEqual(len(responses_lib.calls), 0)

    @responses_lib.activate
    def test_primary_ok_returns_temp_source(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/12345.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        resolved = audio_fetcher.resolve_audio("12345", [], FakeCol())
        self.assertEqual(resolved.jpn_id, "12345")
        self.assertEqual(resolved.source, "temp")
        self.assertTrue(os.path.exists(resolved.payload))
        self.assertIsNone(resolved.jpn_text)
        audio_fetcher.cleanup_temp_audio(resolved.payload)

    @responses_lib.activate
    def test_404_does_not_try_candidates(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/99999.mp3",
            status=404,
        )
        resolved = audio_fetcher.resolve_audio(
            "99999", [("88001", "文。", "Ex.")], FakeCol())
        self.assertIsNone(resolved)
        # Only the primary was requested — a 404 is not a re-selection case.
        self.assertEqual(len(responses_lib.calls), 1)

    @responses_lib.activate
    def test_403_reselects_a_candidate(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/77001.mp3",
            status=403,
        )
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/88001.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        resolved = audio_fetcher.resolve_audio(
            "77001", [("88001", "猫B。", "Cat B.")], FakeCol())
        self.assertEqual(resolved.jpn_id, "88001")
        self.assertEqual(resolved.jpn_text, "猫B。")
        self.assertEqual(resolved.trans_text, "Cat B.")
        self.assertEqual(resolved.source, "temp")
        audio_fetcher.cleanup_temp_audio(resolved.payload)

    @responses_lib.activate
    def test_403_all_candidates_restricted_returns_none(self):
        for jpn_id in ("77001", "88001", "88002"):
            responses_lib.get(
                f"https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3",
                status=403,
            )
        resolved = audio_fetcher.resolve_audio(
            "77001",
            [("88001", "a", "A"), ("88002", "b", "B")],
            FakeCol())
        self.assertIsNone(resolved)

    @responses_lib.activate
    def test_candidate_media_hit_avoids_download(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/77001.mp3",
            status=403,
        )
        col = FakeCol()
        col.media._files["88001.mp3"] = "/fake/88001.mp3"
        resolved = audio_fetcher.resolve_audio(
            "77001", [("88001", "猫B。", "Cat B.")], col)
        self.assertEqual(resolved.jpn_id, "88001")
        self.assertEqual(resolved.source, "media")
        self.assertEqual(resolved.payload, "88001.mp3")

    @responses_lib.activate
    def test_candidate_attempts_are_capped(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/77001.mp3",
            status=403,
        )
        # Register only as many candidates as the cap allows; a 4th attempt
        # would hit an unstubbed URL and raise, so this proves the cap held.
        alts = [(f"88{i:03d}", f"文{i}。", f"Ex {i}.") for i in range(10)]
        for jpn_id, _, _ in alts[:audio_fetcher.MAX_RESELECT_ATTEMPTS]:
            responses_lib.get(
                f"https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3",
                status=403,
            )
        resolved = audio_fetcher.resolve_audio("77001", alts, FakeCol())
        self.assertIsNone(resolved)
        # primary + MAX_RESELECT_ATTEMPTS candidates were attempted.
        self.assertEqual(
            len(responses_lib.calls),
            1 + audio_fetcher.MAX_RESELECT_ATTEMPTS)

    @responses_lib.activate
    def test_used_ids_prevent_reusing_a_sentence(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/77001.mp3",
            status=403,
        )
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/88002.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        used = {"88001"}
        resolved = audio_fetcher.resolve_audio(
            "77001",
            [("88001", "a", "A"), ("88002", "b", "B")],
            FakeCol(),
            used_ids=used,
        )
        self.assertEqual(resolved.jpn_id, "88002")
        self.assertIn("88002", used)
        audio_fetcher.cleanup_temp_audio(resolved.payload)


class TestUntrustedSentenceId(unittest.TestCase):
    """Malicious/odd sentence ids must never reach the audio URL, a col.media
    lookup, or a filesystem path (CWE-22)."""

    # Traversal sequences, separators, absolute paths, Windows drive/UNC
    # paths, URL metacharacters, encoded traversal, fullwidth digits ('１２３'
    # — accepted by str.isdigit(), rejected by the regex), control
    # characters, and a 17-digit id over the length cap.
    BAD_IDS = [
        '',
        '.',
        '..',
        '../../..',
        '../../../etc/cron.d/evil',
        '/etc/passwd',
        '123/../../evil',
        r'12345\..\win',
        r'..\..\win',
        'C:\\evil',
        '\\\\server\\share',
        '123?x=1',
        '123#frag',
        '123%2e%2e',
        '１２３',
        '12\t3',
        '123\n456',
        'id; rm -rf',
        '.mp3',
        '12345678901234567',
    ]
    NON_STR_IDS = [None, 123, 1.5, b'12345', ['12345']]

    def test_valid_sentence_id_accepts_plain_digits(self):
        """_valid_sentence_id accepts plain ASCII digit strings."""
        for good in ('1', '0', '12345', '10933373', '1234567890123456'):
            with self.subTest(good=good):
                self.assertTrue(audio_fetcher._valid_sentence_id(good))

    def test_valid_sentence_id_rejects_malformed(self):
        """Traversal payloads, URL metacharacters, fullwidth digits, over-cap
        ids, and non-strings are all rejected."""
        for bad in self.BAD_IDS + self.NON_STR_IDS:
            with self.subTest(bad=repr(bad)):
                self.assertFalse(audio_fetcher._valid_sentence_id(bad))

    @responses_lib.activate
    def test_fetch_rejects_malformed_ids_before_request_or_tempdir(self):
        """fetch_audio_to_temp raises AudioDownloadError for malformed ids
        without making an HTTP request or creating a temp directory."""
        with patch('tempfile.mkdtemp') as mkdtemp:
            for bad in self.BAD_IDS + self.NON_STR_IDS:
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(AudioDownloadError):
                        audio_fetcher.fetch_audio_to_temp(bad)
            mkdtemp.assert_not_called()
        self.assertEqual(len(responses_lib.calls), 0)

    @responses_lib.activate
    def test_resolve_rejects_malformed_primary_before_media_lookup(self):
        """resolve_audio raises for a malformed primary id before col.media
        is consulted or any HTTP request is made."""
        col = MagicMock()
        for bad in self.BAD_IDS:
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(AudioDownloadError):
                    audio_fetcher.resolve_audio(bad, [], col)
        col.media.have.assert_not_called()
        self.assertEqual(len(responses_lib.calls), 0)

    @responses_lib.activate
    def test_resolve_rejects_malformed_candidate_on_reselect(self):
        """A malformed fallback id raises AudioDownloadError when probed
        after a 403, without ever reaching the network."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/77001.mp3",
            status=403,
        )
        with self.assertRaises(AudioDownloadError):
            audio_fetcher.resolve_audio(
                "77001", [("../../evil", "文。", "Ex.")], FakeCol())
        # Only the valid primary was requested — the malformed candidate
        # never reached the network.
        self.assertEqual(len(responses_lib.calls), 1)

    @responses_lib.activate
    def test_download_rejects_malformed_id(self):
        """download_audio raises AudioDownloadError for malformed ids."""
        for bad in ('../../..', '123/../../evil', None, 123):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(AudioDownloadError):
                    audio_fetcher.download_audio(bad, FakeCol())
        self.assertEqual(len(responses_lib.calls), 0)

    @responses_lib.activate
    def test_valid_id_writes_inside_the_system_temp_root(self):
        """A valid id still fetches, and the file provably lands inside the
        system temp root under its own fresh directory."""
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/12345.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        status, path = audio_fetcher.fetch_audio_to_temp("12345")
        self.assertEqual(status, audio_fetcher.FETCH_OK)
        self.assertEqual(os.path.basename(path), "12345.mp3")
        parent_real = os.path.realpath(os.path.dirname(path))
        temp_root_real = os.path.realpath(tempfile.gettempdir())
        self.assertEqual(
            os.path.commonpath([parent_real, temp_root_real]), temp_root_real)
        audio_fetcher.cleanup_temp_audio(path)


class TestUnregisterAudioFiles(unittest.TestCase):
    """Tests for unregister_audio_files() — the compensating-cleanup primitive."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, fname: str) -> str:
        path = os.path.join(self.tmp_dir, fname)
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfb")
        return path

    def test_empty_input_is_noop(self):
        """No filenames means no media interaction at all."""
        col = MagicMock()
        self.assertEqual(audio_fetcher.unregister_audio_files([], col), [])
        col.media.trash_files.assert_not_called()

    def test_trash_files_is_preferred(self):
        """When col.media.trash_files() exists it is used (recoverable), and
        the input is echoed as removed."""
        col = MagicMock()
        removed = audio_fetcher.unregister_audio_files(
            ["11111.mp3", "22222.mp3"], col)

        self.assertEqual(removed, ["11111.mp3", "22222.mp3"])
        col.media.trash_files.assert_called_once_with(["11111.mp3", "22222.mp3"])

    def test_direct_delete_fallback_without_trash_files(self):
        """Older Anki lacks trash_files() — files are deleted from
        media.dir(); a missing file is skipped without raising."""
        self._write("11111.mp3")
        self._write("22222.mp3")
        col = FakeLegacyCol(self.tmp_dir)

        removed = audio_fetcher.unregister_audio_files(
            ["11111.mp3", "22222.mp3", "missing.mp3"], col)

        self.assertEqual(removed, ["11111.mp3", "22222.mp3"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "11111.mp3")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "22222.mp3")))

    def test_trash_failure_falls_back_to_direct_delete(self):
        """A trash_files() exception must not abort cleanup — direct
        deletion follows for whatever is still on disk."""
        self._write("11111.mp3")
        col = MagicMock()
        col.media.dir.return_value = self.tmp_dir
        col.media.trash_files.side_effect = RuntimeError("trash backend down")

        removed = audio_fetcher.unregister_audio_files(["11111.mp3"], col)

        self.assertEqual(removed, ["11111.mp3"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "11111.mp3")))

    def test_removal_failures_never_raise(self):
        """A file that cannot be deleted is logged and skipped — the function
        must never raise, so failure paths can always call it."""
        col = FakeLegacyCol(self.tmp_dir)  # directory exists, file does not
        removed = audio_fetcher.unregister_audio_files(["missing.mp3"], col)
        self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
