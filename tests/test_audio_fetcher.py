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


if __name__ == "__main__":
    unittest.main()
