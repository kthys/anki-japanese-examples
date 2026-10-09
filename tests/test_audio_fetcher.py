import sys
import os
import html
import unittest
import responses as responses_lib
from requests.exceptions import ConnectionError as RequestsConnectionError

# Mock aqt BEFORE importing the module under test (established project pattern)
from unittest.mock import MagicMock
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
            "99999", [("alt", "文。", "Ex.")], FakeCol())
        self.assertIsNone(resolved)
        # Only the primary was requested — a 404 is not a re-selection case.
        self.assertEqual(len(responses_lib.calls), 1)

    @responses_lib.activate
    def test_403_reselects_a_candidate(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/primary.mp3",
            status=403,
        )
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/alt1.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        resolved = audio_fetcher.resolve_audio(
            "primary", [("alt1", "猫B。", "Cat B.")], FakeCol())
        self.assertEqual(resolved.jpn_id, "alt1")
        self.assertEqual(resolved.jpn_text, "猫B。")
        self.assertEqual(resolved.trans_text, "Cat B.")
        self.assertEqual(resolved.source, "temp")
        audio_fetcher.cleanup_temp_audio(resolved.payload)

    @responses_lib.activate
    def test_403_all_candidates_restricted_returns_none(self):
        for jpn_id in ("primary", "alt1", "alt2"):
            responses_lib.get(
                f"https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3",
                status=403,
            )
        resolved = audio_fetcher.resolve_audio(
            "primary",
            [("alt1", "a", "A"), ("alt2", "b", "B")],
            FakeCol())
        self.assertIsNone(resolved)

    @responses_lib.activate
    def test_candidate_media_hit_avoids_download(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/primary.mp3",
            status=403,
        )
        col = FakeCol()
        col.media._files["alt1.mp3"] = "/fake/alt1.mp3"
        resolved = audio_fetcher.resolve_audio(
            "primary", [("alt1", "猫B。", "Cat B.")], col)
        self.assertEqual(resolved.jpn_id, "alt1")
        self.assertEqual(resolved.source, "media")
        self.assertEqual(resolved.payload, "alt1.mp3")

    @responses_lib.activate
    def test_candidate_attempts_are_capped(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/primary.mp3",
            status=403,
        )
        # Register only as many candidates as the cap allows; a 4th attempt
        # would hit an unstubbed URL and raise, so this proves the cap held.
        alts = [(f"alt{i}", f"文{i}。", f"Ex {i}.") for i in range(10)]
        for jpn_id, _, _ in alts[:audio_fetcher.MAX_RESELECT_ATTEMPTS]:
            responses_lib.get(
                f"https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3",
                status=403,
            )
        resolved = audio_fetcher.resolve_audio("primary", alts, FakeCol())
        self.assertIsNone(resolved)
        # primary + MAX_RESELECT_ATTEMPTS candidates were attempted.
        self.assertEqual(
            len(responses_lib.calls),
            1 + audio_fetcher.MAX_RESELECT_ATTEMPTS)

    @responses_lib.activate
    def test_used_ids_prevent_reusing_a_sentence(self):
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/primary.mp3",
            status=403,
        )
        responses_lib.get(
            "https://audio.tatoeba.org/sentences/jpn/alt2.mp3",
            body=b"\xff\xfb",
            status=200,
        )
        used = {"alt1"}
        resolved = audio_fetcher.resolve_audio(
            "primary",
            [("alt1", "a", "A"), ("alt2", "b", "B")],
            FakeCol(),
            used_ids=used,
        )
        self.assertEqual(resolved.jpn_id, "alt2")
        self.assertIn("alt2", used)
        audio_fetcher.cleanup_temp_audio(resolved.payload)


if __name__ == "__main__":
    unittest.main()
