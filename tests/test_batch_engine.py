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
from src.core.batch_engine import BatchResult


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
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("猫が好きです。", "I like cats.")]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫[ねこ]", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")],
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
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "already", "ExampleTranslated": "here"},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")],
            skip_existing=True
        )

        self.assertEqual(result.skipped_existing, 1)
        self.assertEqual(result.updated, 0)
        col.update_note.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_no_skip_when_disabled(self, mock_td):
        """run_batch should overwrite existing examples when skip_existing=False."""
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = [("新しい例。", "New example.")]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "猫", "ExampleJapanese": "old", "ExampleTranslated": "old"},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")],
            skip_existing=False
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_existing, 0)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_no_match(self, mock_td):
        """run_batch should count notes with no match as skipped_no_match."""
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path
        mock_td.search_word.return_value = []

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "罕見", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")]
        )

        self.assertEqual(result.skipped_no_match, 1)
        self.assertEqual(result.updated, 0)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_missing_source_field(self, mock_td):
        """run_batch should skip notes missing the source field."""
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path

        field_order = ["OtherField", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"OtherField": "test", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")]
        )

        self.assertEqual(result.skipped_missing_fields, 1)

    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_unknown_language(self, mock_td):
        """run_batch should return empty result for unknown language."""
        mock_td.LANG_MAP = {"English": "eng"}

        col = MagicMock()
        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="Klingon",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")]
        )

        self.assertEqual(result.total_processed, 0)
        col.find_notes.assert_not_called()

    @patch('src.core.batch_engine.tatoeba_data')
    @patch('src.core.batch_engine.random.sample')
    def test_run_batch_selects_random_sample(self, mock_sample, mock_td):
        """run_batch should use random.sample when multiple matches exist."""
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path
        matches = [("例文A。", "Example A."), ("例文B。", "Example B.")]
        mock_td.search_word.return_value = matches
        mock_sample.return_value = [("例文B。", "Example B.")]

        field_order = ["Word", "ExampleJapanese", "ExampleTranslated"]
        note = self._make_mock_note(
            {"Word": "例文", "ExampleJapanese": "", "ExampleTranslated": ""},
            field_order
        )
        col = self._make_mock_col([1], {1: note})

        result = batch_engine.run_batch(
            col=col, deck_id=1, lang_label="English",
            source_field="Word", dest_field_pairs=[("ExampleJapanese", "ExampleTranslated")]
        )

        mock_sample.assert_called_once_with(matches, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(note.fields[1], "例文B。")


    @patch('src.core.batch_engine.tatoeba_data')
    def test_run_batch_multiple_pairs(self, mock_td):
        """run_batch should populate multiple fields when dest_field_pairs > 1."""
        mock_td.LANG_MAP = {"English": "eng"}
        mock_td.get_db_path.return_value = self.temp_db_path
        
        matches = [
            ("例文1。", "Example 1."),
            ("例文2。", "Example 2."),
            ("例文3。", "Example 3.")
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
            col=col, deck_id=1, lang_label="English",
            source_field="Word",
            dest_field_pairs=[
                ("Jpn1", "Trans1"),
                ("Jpn2", "Trans2")
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

if __name__ == '__main__':
    unittest.main()
