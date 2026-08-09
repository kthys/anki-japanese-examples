import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import tempfile

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock aqt before importing our modules
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.mw'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['aqt.qt'] = MagicMock()

# Import freshly
import src.core.batch_engine as batch_engine
from src.core.batch_engine import BatchResult, process_pending_audio
from src.core.audio_fetcher import AudioDownloadError


class TestBatchResult(unittest.TestCase):
    """Tests for the BatchResult dataclass."""

    def test_default_values(self):
        """All counters should default to zero."""
        result = BatchResult()
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped_existing, 0)
        self.assertEqual(result.skipped_no_match, 0)
        self.assertEqual(result.skipped_missing_fields, 0)
        self.assertEqual(result.errors, 0)

    def test_total_processed(self):
        """total_processed should return the sum of all counters."""
        result = BatchResult(updated=3, skipped_existing=2, skipped_no_match=1,
                             skipped_missing_fields=4, errors=1)
        self.assertEqual(result.total_processed, 11)


class TestCleanWord(unittest.TestCase):
    """Tests for the clean_word utility function."""

    def test_clean_word_strips_html_and_brackets(self):
        self.assertEqual(batch_engine.clean_word("週末[しゅうまつ]"), "週末")
        self.assertEqual(batch_engine.clean_word("食べる(たべる)"), "食べる")
        self.assertEqual(batch_engine.clean_word("学校（がっこう）"), "学校")
        self.assertEqual(batch_engine.clean_word("<b>太字</b>"), "太字")
        self.assertEqual(batch_engine.clean_word("  綺麗  "), "綺麗")
        self.assertEqual(batch_engine.clean_word("漢字[かんじ](kanji)"), "漢字")

class TestRunBatch(unittest.TestCase):
    """Tests for the run_batch function."""

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        
    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def _make_mock_note(self, fields_dict, field_order=None):
        """Helper to create a mock Anki note.

        Args:
        - fields_dict (dict): Mapping field name -> value.
        - field_order (list): Optional ordered list of field names.

        Returns:
        - A MagicMock note object with properly set up fields.
        """
        if field_order is None:
            field_order = list(fields_dict.keys())
        note = MagicMock()
        note.fields = [fields_dict.get(name, "") for name in field_order]
        note.note_type.return_value = {
            "flds": [{"name": name} for name in field_order]
        }
        return note

    def _make_mock_col(self, note_ids, notes_dict):
        """Helper to create a mock Anki collection.

        Args:
        - note_ids (list): List of note IDs to return from find_notes.
        - notes_dict (dict): Mapping note_id -> mock note.

        Returns:
        - A MagicMock collection object.
        """
        col = MagicMock()
        col.find_notes.return_value = note_ids
        col.get_note.side_effect = lambda nid: notes_dict[nid]
        return col

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_updates_notes(self, mock_td):
        """run_batch should update notes when matches are found."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("1", "猫が好きです。", "I like cats.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫[ねこ]", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)],
            skip_existing=True
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_no_match, 0)
        col.update_note.assert_called_once_with(note)
        # Check fields were set (HTML escaped)
        self.assertEqual(note.fields[1], "猫が好きです。")
        self.assertEqual(note.fields[2], "I like cats.")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_skips_existing(self, mock_td):
        """run_batch should skip notes that already have examples when skip_existing=True."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("1", "猫が好きです。", "I like cats.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "already", "ExampleTranslated": "here"},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)],
            skip_existing=True
        )

        self.assertEqual(result.skipped_existing, 1)
        self.assertEqual(result.updated, 0)
        col.update_note.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_no_skip_when_disabled(self, mock_td):
        """run_batch should overwrite existing examples when skip_existing=False."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("2", "新しい例。", "New example.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "old", "ExampleTranslated": "old"},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)],
            skip_existing=False
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_no_match(self, mock_td):
        """run_batch should count notes with no match as skipped_no_match."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = []

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "罕見", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)]
        )

        self.assertEqual(result.skipped_no_match, 1)
        self.assertEqual(result.updated, 0)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_missing_source_field(self, mock_td):
        """run_batch should skip notes missing the source field."""
        mock_td.get_db_path.return_value = self.temp_db_path

        field_order = ["OtherField", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"OtherField": "test", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)]
        )

        self.assertEqual(result.skipped_missing_fields, 1)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_unknown_language(self, mock_td):
        """run_batch should return empty result for unknown language."""

        col = MagicMock()
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="Klingon",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)]
        )

        self.assertEqual(result.total_processed, 0)
        col.find_notes.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    @patch('src.core.batch_engine.random.sample')
    def test_run_batch_selects_random_sample(self, mock_sample, mock_td):
        """run_batch should use random.sample when multiple matches exist."""
        mock_td.get_db_path.return_value = self.temp_db_path
        matches = [("3", "例文A。", "Example A.", 0), ("4", "例文B。", "Example B.", 0)]
        mock_td.search_word.return_value = matches
        mock_sample.return_value = [("4", "例文B。", "Example B.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "例文", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)]
        )

        # Audio-first algorithm: random.sample called on audio pool (empty here, has_audio=0)
        # then on non-audio pool to fill remaining slots
        self.assertGreaterEqual(mock_sample.call_count, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(note.fields[1], "例文B。")


    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_multiple_pairs(self, mock_td):
        """run_batch should populate multiple fields when dest_field_pairs > 1."""
        mock_td.get_db_path.return_value = self.temp_db_path
        
        matches = [
            ("5", "例文1。", "Example 1.", 0),
            ("6", "例文2。", "Example 2.", 0),
            ("7", "例文3。", "Example 3.", 0)
        ]
        mock_td.search_word.return_value = matches

        field_order = [
            "Word",
            "Jpn1", "Trans1",
            "Jpn2", "Trans2"
        ]
        
        note = self._make_mock_note(
            {"Word": "例文", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[
                ("Jpn1", "Trans1", None),
                ("Jpn2", "Trans2", None)
            ]
        )

        self.assertEqual(result.updated, 1)
        col.update_note.assert_called_once_with(note)
        
        # We expect 2 matches to be chosen
        self.assertNotEqual(note.fields[1], "")
        self.assertNotEqual(note.fields[2], "")
        self.assertNotEqual(note.fields[3], "")
        self.assertNotEqual(note.fields[4], "")
        
        # Make sure they are distinct
        self.assertNotEqual(note.fields[1], note.fields[3])

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_audio_field_populates_pending_audio(self, mock_td):
        """audio_field not None causes pending_audio to accumulate entries."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("555", "猫が好きです。", "I like cats.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated", "ExampleAudio"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "", "ExampleTranslated": "", "ExampleAudio": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", "ExampleAudio")],
            skip_existing=True
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(len(result.pending_audio), 1)
        jpn_id, note_id, audio_field = result.pending_audio[0]
        self.assertEqual(jpn_id, "555")
        self.assertEqual(note_id, 1)
        self.assertEqual(audio_field, "ExampleAudio")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_no_audio_field_leaves_pending_audio_empty(self, mock_td):
        """audio_field=None means no pending_audio entries."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("556", "猫が好きです。", "I like cats.", 0)]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("ExampleJapanese", "ExampleTranslated", None)],
            skip_existing=True
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.pending_audio, [])


class TestBatchResultAudioFields(unittest.TestCase):
    def test_audio_counter_defaults(self):
        result = BatchResult()
        self.assertEqual(result.audio_added, 0)
        self.assertEqual(result.audio_skipped, 0)
        self.assertEqual(result.audio_errors, 0)

    def test_pending_audio_defaults_empty(self):
        result = BatchResult()
        self.assertEqual(result.pending_audio, [])

    def test_pending_audio_not_shared(self):
        r1 = BatchResult()
        r2 = BatchResult()
        r1.pending_audio.append(("123", 1, "Audio"))
        self.assertEqual(len(r2.pending_audio), 0)

    def test_total_processed_excludes_audio_counters(self):
        result = BatchResult(
            updated=2, skipped_existing=1, skipped_no_match=1,
            skipped_missing_fields=0, errors=0,
            audio_added=5, audio_skipped=3, audio_errors=1
        )
        self.assertEqual(result.total_processed, 4)


class TestProcessPendingAudio(unittest.TestCase):

    def _make_mock_note(self, fields_dict, field_order=None):
        if field_order is None:
            field_order = list(fields_dict.keys())
        note = MagicMock()
        note.fields = [fields_dict.get(name, "") for name in field_order]
        note.note_type.return_value = {"flds": [{"name": name} for name in field_order]}
        return note

    @patch('src.core.batch_engine.audio_fetcher')
    def test_successful_download_writes_sound_tag(self, mock_af):
        """Successful download writes [sound:fname] verbatim and increments audio_added."""
        mock_af.fetch_audio_to_temp.return_value = "/tmp/x/12345.mp3"
        mock_af.register_audio_file.return_value = "12345.mp3"
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note(
            {"Word": "猫", "Audio": ""},
            field_order=["Word", "Audio"]
        )
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("12345", 1, "Audio"))

        process_pending_audio(result, col)

        self.assertEqual(result.audio_added, 1)
        self.assertEqual(result.audio_skipped, 0)
        self.assertEqual(result.audio_errors, 0)
        self.assertEqual(note.fields[1], "[sound:12345.mp3]")
        col.update_note.assert_called_once_with(note)
        self.assertEqual(result.pending_audio, [])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_already_registered_file_skips_download(self, mock_af):
        """File already in col.media: tag written without fetching, audio_added incremented."""
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = True

        result = BatchResult()
        result.pending_audio.append(("12345", 1, "Audio"))
        process_pending_audio(result, col)

        self.assertEqual(result.audio_added, 1)
        self.assertEqual(note.fields[0], "[sound:12345.mp3]")
        mock_af.fetch_audio_to_temp.assert_not_called()
        mock_af.register_audio_file.assert_not_called()

    @patch('src.core.batch_engine.audio_fetcher')
    def test_sound_tag_not_html_escaped(self, mock_af):
        """[sound:] tag must be written verbatim — not passed through html.escape."""
        import html
        mock_af.fetch_audio_to_temp.return_value = "/tmp/x/12345.mp3"
        mock_af.register_audio_file.return_value = "12345.mp3"
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("12345", 1, "Audio"))
        process_pending_audio(result, col)

        self.assertEqual(note.fields[0], "[sound:12345.mp3]")
        # Verify the tag was not altered by html.escape ([ ] are not HTML-special, but confirm)
        self.assertEqual(html.escape(note.fields[0]), note.fields[0])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_404_increments_audio_skipped(self, mock_af):
        """fetch returning None (404) increments audio_skipped; no update_note call."""
        mock_af.fetch_audio_to_temp.return_value = None
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("99999", 1, "Audio"))
        process_pending_audio(result, col)

        self.assertEqual(result.audio_skipped, 1)
        self.assertEqual(result.audio_added, 0)
        self.assertEqual(note.fields[0], "")
        col.update_note.assert_not_called()
        self.assertEqual(result.pending_audio, [])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_audio_download_error_increments_audio_errors(self, mock_af):
        """AudioDownloadError increments audio_errors; audio field left empty."""
        mock_af.fetch_audio_to_temp.side_effect = AudioDownloadError("Connection refused")
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("11111", 1, "Audio"))
        process_pending_audio(result, col)

        self.assertEqual(result.audio_errors, 1)
        self.assertEqual(result.audio_added, 0)
        self.assertEqual(note.fields[0], "")
        col.update_note.assert_not_called()
        self.assertEqual(result.pending_audio, [])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_missing_audio_field_increments_audio_errors(self, mock_af):
        """Audio field not on note type increments audio_errors without fetching."""
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Word": "猫"}, field_order=["Word"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("12345", 1, "NonexistentAudioField"))
        process_pending_audio(result, col)

        self.assertEqual(result.audio_errors, 1)
        mock_af.fetch_audio_to_temp.assert_not_called()
        self.assertEqual(result.pending_audio, [])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_unexpected_error_does_not_abort_remaining_items(self, mock_af):
        """A non-AudioDownloadError failure on one item counts as audio_errors
        and processing continues with the remaining items."""
        mock_af.fetch_audio_to_temp.return_value = "/tmp/x/22222.mp3"
        mock_af.register_audio_file.return_value = "22222.mp3"
        mock_af.AudioDownloadError = AudioDownloadError

        good_note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.media.have.return_value = False

        # First note is gone (e.g. deleted) — get_note raises; second succeeds.
        def get_note(nid):
            if nid == 1:
                raise KeyError("note not found")
            return good_note
        col.get_note.side_effect = get_note

        result = BatchResult()
        result.pending_audio.append(("11111", 1, "Audio"))
        result.pending_audio.append(("22222", 2, "Audio"))
        process_pending_audio(result, col)

        self.assertEqual(result.audio_errors, 1)
        self.assertEqual(result.audio_added, 1)
        self.assertEqual(good_note.fields[0], "[sound:22222.mp3]")
        self.assertEqual(result.pending_audio, [])

    @patch('src.core.batch_engine.audio_fetcher')
    def test_download_phase_reports_progress(self, mock_af):
        """download_pending_audio calls progress_cb(current, total) per item."""
        from src.core.batch_engine import download_pending_audio
        mock_af.fetch_audio_to_temp.return_value = None
        mock_af.AudioDownloadError = AudioDownloadError

        note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()
        col.get_note.return_value = note
        col.media.have.return_value = False

        result = BatchResult()
        result.pending_audio.append(("1", 1, "Audio"))
        result.pending_audio.append(("2", 2, "Audio"))

        calls = []
        download_pending_audio(result, col, progress_cb=lambda c, t: calls.append((c, t)))

        self.assertEqual(calls, [(1, 2), (2, 2)])
        # Download phase must not clear pending_audio — that's the register phase's job
        self.assertEqual(len(result.pending_audio), 2)

    @patch('src.core.batch_engine.audio_fetcher')
    def test_media_phase_registers_temp_and_apply_errors_are_contained(self, mock_af):
        """register_audio_media hands the temp to register_audio_file (which
        owns cleanup); an apply-phase failure counts as audio_errors without
        aborting other items."""
        from src.core.batch_engine import register_pending_audio, AUDIO_FETCHED
        mock_af.AudioDownloadError = AudioDownloadError
        mock_af.register_audio_file.side_effect = ["11111.mp3", "22222.mp3"]

        good_note = self._make_mock_note({"Audio": ""}, field_order=["Audio"])
        col = MagicMock()

        def get_note(nid):
            if nid == 1:
                raise KeyError("note not found")
            return good_note
        col.get_note.side_effect = get_note

        result = BatchResult()
        items = [
            (1, "Audio", "11111", AUDIO_FETCHED, "/tmp/x/11111.mp3"),
            (2, "Audio", "22222", AUDIO_FETCHED, "/tmp/x/22222.mp3"),
        ]
        register_pending_audio(items, result, col)

        # Both temp files were handed to register_audio_file (temp cleanup
        # is its responsibility, covered by test_audio_fetcher)
        self.assertEqual(mock_af.register_audio_file.call_count, 2)
        self.assertEqual(result.audio_errors, 1)
        self.assertEqual(result.audio_added, 1)
        self.assertEqual(good_note.fields[0], "[sound:22222.mp3]")


class TestUndoBracketing(unittest.TestCase):
    """Tests for the named-undo-entry bracketing in run_batch and apply_audio_fields."""

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def _make_col_with_one_note(self):
        note = MagicMock()
        note.fields = ["猫", "", ""]
        note.note_type.return_value = {
            "flds": [{"name": n} for n in ["Word", "Jpn", "Trans"]]
        }
        col = MagicMock()
        col.find_notes.return_value = [1]
        col.get_note.return_value = note
        return col

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_with_undo_name_brackets_and_stores_changes(self, mock_td):
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        col = self._make_col_with_one_note()
        col.add_custom_undo_entry.return_value = 42
        col.merge_undo_entries.return_value = "OPCHANGES"

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn", "Trans", None)],
            undo_name="Add Japanese examples",
        )

        col.add_custom_undo_entry.assert_called_once_with("Add Japanese examples")
        col.merge_undo_entries.assert_called_with(42)
        self.assertEqual(result.changes, "OPCHANGES")
        self.assertEqual(result.updated, 1)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_without_undo_name_skips_bracketing(self, mock_td):
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        col = self._make_col_with_one_note()

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn", "Trans", None)],
        )

        col.add_custom_undo_entry.assert_not_called()
        col.merge_undo_entries.assert_not_called()
        self.assertIsNone(result.changes)
        self.assertEqual(result.updated, 1)

    def test_apply_audio_fields_brackets_and_returns_changes(self):
        from src.core.batch_engine import apply_audio_fields

        note = MagicMock()
        note.fields = [""]
        note.note_type.return_value = {"flds": [{"name": "Audio"}]}
        col = MagicMock()
        col.get_note.return_value = note
        col.add_custom_undo_entry.return_value = 7
        col.merge_undo_entries.return_value = "AUDIOCHANGES"

        result = BatchResult()
        result.pending_audio.append(("111", 1, "Audio"))
        changes = apply_audio_fields(
            [(1, "Audio", "111", "111.mp3")], result, col,
            undo_name="Add example audio")

        col.add_custom_undo_entry.assert_called_once_with("Add example audio")
        col.merge_undo_entries.assert_called_with(7)
        self.assertEqual(changes, "AUDIOCHANGES")
        self.assertEqual(result.audio_added, 1)
        self.assertEqual(note.fields[0], "[sound:111.mp3]")
        self.assertEqual(result.pending_audio, [])


class TestPerPairSkipLogic(unittest.TestCase):
    """Tests for per-field-pair skip logic in run_batch()."""

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def _make_mock_note(self, fields_dict, field_order=None):
        if field_order is None:
            field_order = list(fields_dict.keys())
        note = MagicMock()
        note.fields = [fields_dict.get(name, "") for name in field_order]
        note.note_type.return_value = {
            "flds": [{"name": name} for name in field_order]
        }
        return note

    def _make_mock_col(self, note_ids, notes_dict):
        col = MagicMock()
        col.find_notes.return_value = note_ids
        col.get_note.side_effect = lambda nid: notes_dict[nid]
        return col

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_per_pair_partial(self, mock_td):
        """Card with pair 1 filled and pair 2 empty: pair 1 untouched, pair 2 populated."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫A。", "Cat A.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Audio1", "Jpn2", "Trans2", "Audio2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                "Jpn1": "existing jpn", "Trans1": "existing trans", "Audio1": "",
                "Jpn2": "", "Trans2": "", "Audio2": "",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=True,
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)
        # Pair 1 must remain untouched
        self.assertEqual(note.fields[1], "existing jpn")
        self.assertEqual(note.fields[2], "existing trans")
        # Pair 2 must be populated
        self.assertNotEqual(note.fields[4], "")
        col.update_note.assert_called_once()

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_per_pair_all_filled(self, mock_td):
        """Card with all pairs filled: counted as skipped_existing, update_note not called."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫A。", "Cat A.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                "Jpn1": "existing1", "Trans1": "existing1",
                "Jpn2": "existing2", "Trans2": "existing2",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=True,
        )

        self.assertEqual(result.skipped_existing, 1)
        self.assertEqual(result.updated, 0)
        col.update_note.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_per_pair_none_filled(self, mock_td):
        """Card with all pairs empty: both pairs populated, counted as updated."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫A。", "Cat A.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {"Word": "猫", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=True,
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)
        self.assertNotEqual(note.fields[1], "")
        self.assertNotEqual(note.fields[2], "")
        self.assertNotEqual(note.fields[3], "")
        self.assertNotEqual(note.fields[4], "")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_per_pair_audio_not_queued_for_skipped(self, mock_td):
        """Audio queued only for pairs actually written, not for skipped pairs."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫A。", "Cat A.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Audio1", "Jpn2", "Trans2", "Audio2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                "Jpn1": "existing", "Trans1": "existing", "Audio1": "",
                "Jpn2": "", "Trans2": "", "Audio2": "",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", "Audio1"), ("Jpn2", "Trans2", "Audio2")],
            skip_existing=True,
        )

        self.assertEqual(len(result.pending_audio), 1)
        self.assertEqual(result.pending_audio[0][2], "Audio2")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_per_pair_no_update_note_when_all_skipped(self, mock_td):
        """Single pair already filled: update_note not called, skipped_existing==1."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        field_order = ["Word", "Jpn1", "Trans1"]
        note = self._make_mock_note(
            {"Word": "猫", "Jpn1": "existing", "Trans1": "existing"},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None)],
            skip_existing=True,
        )

        col.update_note.assert_not_called()
        self.assertEqual(result.skipped_existing, 1)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_only_one_field_filled_not_considered_existing(self, mock_td):
        """Pair with only one field filled is NOT considered existing; pair gets written."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        field_order = ["Word", "Jpn1", "Trans1"]
        note = self._make_mock_note(
            {"Word": "猫", "Jpn1": "existing", "Trans1": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None)],
            skip_existing=True,
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_filled_pair_does_not_starve_empty_pair_of_scarce_match(self, mock_td):
        """Pair 1 filled, pair 2 empty, only ONE match: the match must go to
        pair 2 instead of being consumed by the skipped pair 1."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                "Jpn1": "existing jpn", "Trans1": "existing trans",
                "Jpn2": "", "Trans2": "",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=True,
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)
        self.assertEqual(note.fields[1], "existing jpn")
        self.assertEqual(note.fields[3], "猫A。")
        self.assertEqual(note.fields[4], "Cat A.")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_fully_filled_note_skips_database_search(self, mock_td):
        """All pairs filled: search_word must not be called at all."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("10", "猫A。", "Cat A.", 0)]

        field_order = ["Word", "Jpn1", "Trans1"]
        note = self._make_mock_note(
            {"Word": "猫", "Jpn1": "existing", "Trans1": "existing"},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None)],
            skip_existing=True,
        )

        self.assertEqual(result.skipped_existing, 1)
        mock_td.search_word.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    def test_sentence_in_filled_pair_not_reselected_for_empty_pair(self, mock_td):
        """A sentence already present in a filled pair must not be picked again
        for another pair on a re-run (comparison is against the HTML-escaped
        text stored in the field)."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫&犬。", "Cat & dog.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                # Contents as written by a previous run: html.escape'd
                "Jpn1": "猫&amp;犬。", "Trans1": "Cat &amp; dog.",
                "Jpn2": "", "Trans2": "",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=True,
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(note.fields[3], "猫B。")
        self.assertEqual(note.fields[4], "Cat B.")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_skip_existing_false_overwrites_filled_pairs(self, mock_td):
        """skip_existing=False: filled pairs are overwritten, nothing filtered."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("10", "猫A。", "Cat A.", 0),
            ("11", "猫B。", "Cat B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {
                "Word": "猫",
                "Jpn1": "existing", "Trans1": "existing",
                "Jpn2": "", "Trans2": "",
            },
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        self.assertNotEqual(note.fields[1], "existing")
        self.assertIn(note.fields[1], ["猫A。", "猫B。"])
        self.assertNotEqual(note.fields[3], "")


class TestAudioPrioritizationSelection(unittest.TestCase):
    """Tests for audio-first random selection in run_batch()."""

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def _make_mock_note(self, fields_dict, field_order=None):
        if field_order is None:
            field_order = list(fields_dict.keys())
        note = MagicMock()
        note.fields = [fields_dict.get(name, "") for name in field_order]
        note.note_type.return_value = {
            "flds": [{"name": name} for name in field_order]
        }
        return note

    def _make_mock_col(self, note_ids, notes_dict):
        col = MagicMock()
        col.find_notes.return_value = note_ids
        col.get_note.side_effect = lambda nid: notes_dict[nid]
        return col

    @patch('src.core.batch_engine.tatoeba_data')
    def test_audio_first_prefers_audio_matches(self, mock_td):
        """Audio-first selection: when 2 pairs requested and 2 audio + 1 non-audio exist, both pairs come from audio matches."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("A1", "音声A。", "Audio A.", 1),
            ("A2", "音声B。", "Audio B.", 1),
            ("N1", "普通。", "Normal.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {"Word": "例", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        import random as _random
        _random.seed(42)
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        # Both written texts must come from audio matches (A1 or A2)
        written_jpn = {note.fields[1], note.fields[3]}
        self.assertNotIn("普通。", written_jpn)
        self.assertTrue(written_jpn.issubset({"音声A。", "音声B。"}))

    @patch('src.core.batch_engine.tatoeba_data')
    def test_all_audio_pool_no_non_audio_selected(self, mock_td):
        """All matches have audio: both pairs get filled, result.updated == 1."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("A1", "音声A。", "Audio A.", 1),
            ("A2", "音声B。", "Audio B.", 1),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {"Word": "例", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        import random as _random
        _random.seed(42)
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        self.assertNotEqual(note.fields[1], "")
        self.assertNotEqual(note.fields[3], "")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_empty_audio_pool_falls_back_to_non_audio(self, mock_td):
        """No audio matches: falls back gracefully to non-audio selection. result.updated == 1."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("N1", "普通A。", "Normal A.", 0),
            ("N2", "普通B。", "Normal B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1"]
        note = self._make_mock_note(
            {"Word": "例", "Jpn1": "", "Trans1": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        import random as _random
        _random.seed(42)
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None)],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        self.assertNotEqual(note.fields[1], "")

    @patch('src.core.batch_engine.tatoeba_data')
    def test_mixed_pool_audio_fills_first_then_non_audio(self, mock_td):
        """1 audio + 2 non-audio, 2 pairs: first pair gets audio match, second gets non-audio."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("A1", "音声A。", "Audio A.", 1),
            ("N1", "普通A。", "Normal A.", 0),
            ("N2", "普通B。", "Normal B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2"]
        note = self._make_mock_note(
            {"Word": "例", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        import random as _random
        _random.seed(42)
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[("Jpn1", "Trans1", None), ("Jpn2", "Trans2", None)],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        # The audio match must be in one of the written pairs
        written_jpn = {note.fields[1], note.fields[3]}
        self.assertIn("音声A。", written_jpn)
        # The other pair must be a non-audio match
        non_audio_written = written_jpn - {"音声A。"}
        self.assertTrue(non_audio_written.issubset({"普通A。", "普通B。"}))

    @patch('src.core.batch_engine.tatoeba_data')
    def test_audio_sentence_assigned_to_audio_configured_pair(self, mock_td):
        """Regression: audio sentence must be assigned to the pair that has an audio field,
        not to a pair without one. When pair 0 has no audio field and pair 1 does, the
        audio sentence must land in pair 1's text AND be queued for pair 1's audio field."""
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [
            ("A1", "音声A。", "Audio A.", 1),
            ("N1", "普通A。", "Normal A.", 0),
            ("N2", "普通B。", "Normal B.", 0),
        ]

        field_order = ["Word", "Jpn1", "Trans1", "Jpn2", "Trans2", "Audio2"]
        note = self._make_mock_note(
            {"Word": "例", "Jpn1": "", "Trans1": "", "Jpn2": "", "Trans2": "", "Audio2": ""},
            field_order,
        )
        col = self._make_mock_col([1], {1: note})

        import random as _random
        _random.seed(0)
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_code="eng",
            source_field="Word",
            dest_field_pairs=[
                ("Jpn1", "Trans1", None),       # pair 0: no audio field
                ("Jpn2", "Trans2", "Audio2"),   # pair 1: has audio field
            ],
            skip_existing=False,
        )

        self.assertEqual(result.updated, 1)
        # The audio sentence must be written to the pair that has an audio field (pair 1)
        self.assertEqual(note.fields[3], "音声A。",
            "Audio sentence text must be placed in the audio-configured pair (Jpn2)")
        # Audio2 must be queued with the same sentence ID as the text in Jpn2
        self.assertEqual(len(result.pending_audio), 1)
        queued_jpn_id, _note_id, queued_audio_field = result.pending_audio[0]
        self.assertEqual(queued_jpn_id, "A1",
            "Audio field must be queued with the audio sentence's jpn_id")
        self.assertEqual(queued_audio_field, "Audio2")
        # Jpn1 (no audio pair) must have a non-audio sentence
        self.assertIn(note.fields[1], {"普通A。", "普通B。"},
            "Pair without audio field should receive a non-audio sentence")


class TestBuildDeckSearch(unittest.TestCase):
    """Tests for build_deck_search() — deck+subdeck search string construction."""

    def test_searchnode_path_used_when_available(self):
        fake_search_node = MagicMock()
        with patch('src.core.batch_engine.SearchNode', fake_search_node):
            col = MagicMock()
            col.decks.name.return_value = "Japanese"
            col.build_search_string.return_value = 'deck:Japanese'
            search = batch_engine.build_deck_search(col, 1)

        fake_search_node.assert_called_once_with(deck="Japanese")
        self.assertEqual(search, 'deck:Japanese')

    def test_fallback_escapes_special_characters(self):
        """Without SearchNode, deck names with \\ " * _ must be escaped."""
        with patch('src.core.batch_engine.SearchNode', None):
            col = MagicMock()
            col.decks.name.return_value = 'My "Deck" with \\ and * and _'
            search = batch_engine.build_deck_search(col, 1)

        self.assertEqual(search, 'deck:"My \\"Deck\\" with \\\\ and \\* and \\_"')

    def test_fallback_used_when_searchnode_rejects_input(self):
        """SearchNode raising (e.g. non-string name) falls back to manual escaping."""
        fake_search_node = MagicMock(side_effect=TypeError("bad field"))
        with patch('src.core.batch_engine.SearchNode', fake_search_node):
            col = MagicMock()
            col.decks.name.return_value = "Japanese"
            search = batch_engine.build_deck_search(col, 1)

        self.assertEqual(search, 'deck:"Japanese"')

    def test_run_batch_uses_deck_search_not_did(self):
        """run_batch must search by deck name (incl. subdecks), never did:."""
        temp_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)
        try:
            with patch('src.core.batch_engine.SearchNode', None), \
                 patch('src.core.batch_engine.tatoeba_data') as mock_td:
                mock_td.get_db_path.return_value = temp_db_path

                col = MagicMock()
                col.decks.name.return_value = "Japanese"
                col.find_notes.return_value = []

                batch_engine.run_batch(
                    col=col, deck_id=7, lang_code="eng",
                    source_field="Word",
                    dest_field_pairs=[("Jpn", "Trans", None)],
                )

            col.decks.name.assert_called_once_with(7)
            col.find_notes.assert_called_once_with('deck:"Japanese"')
        finally:
            os.remove(temp_db_path)


if __name__ == '__main__':
    unittest.main()
