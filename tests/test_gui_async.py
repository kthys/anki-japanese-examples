import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestGUIAsync(unittest.TestCase):

    def setUp(self):
        # Create mocks for aqt modules
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_operations = MagicMock()
        self.mock_utils = MagicMock()
        self.mock_gui_hooks = MagicMock()

        # Patch sys.modules to simulate aqt existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.operations': self.mock_operations,
            'aqt.utils': self.mock_utils,
            'aqt.gui_hooks': self.mock_gui_hooks
        })
        self.modules_patcher.start()

        # Configure mw
        self.mock_mw.pm.meta.get.return_value = 'en'
        self.mock_mw.col.path = "/path/to/collection.anki2"
        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Mock Qt constants
        self.mock_utils.Qt = MagicMock()
        self.mock_utils.Qt.WindowModality.WindowModal = 1
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore' # Simulate Qt5

        # Also need to mock japanese_examples because GUI imports from it
        self.mock_japanese_examples = MagicMock()
        # Set constants to match the field names we will use in the test
        self.mock_japanese_examples.DST_FIELD_JAP = 'Expression'
        self.mock_japanese_examples.DST_FIELD_TRANSLATION = 'Meaning'
        sys.modules['japanese_examples'] = self.mock_japanese_examples

        # Mock setup_i18n related functions to prevent file system changes
        self.os_walk_patcher = patch('os.walk')
        self.mock_os_walk = self.os_walk_patcher.start()
        self.mock_os_walk.return_value = [] # Yield nothing

        self.shutil_move_patcher = patch('shutil.move')
        self.mock_shutil_move = self.shutil_move_patcher.start()

        self.gettext_patcher = patch('gettext.translation')
        self.mock_gettext_translation = self.gettext_patcher.start()

        # Mock translation object
        self.mock_trans = MagicMock()
        self.mock_trans.gettext.side_effect = lambda x: x # Identity translation
        self.mock_gettext_translation.return_value = self.mock_trans

        # Import the module under test
        if 'GUI' in sys.modules:
            del sys.modules['GUI']
        import GUI
        self.GUI = GUI

    def tearDown(self):
        self.os_walk_patcher.stop()
        self.shutil_move_patcher.stop()
        self.gettext_patcher.stop()
        self.modules_patcher.stop()
        if 'GUI' in sys.modules:
            del sys.modules['GUI']

    @patch('GUI.find_japanese_sentence')
    @patch('GUI.create_custom_dialog')
    @patch('GUI.showInfo')
    def test_add_example_manually_dialog_flow(self, mock_showInfo, mock_create_custom_dialog, mock_find_japanese_sentence):
        # Setup mocks
        editor = MagicMock()
        editor.web.editor.currentField = 'Expression'
        # Note fields are usually a list of values, but here it seems accessed by index or something.
        # Wait, the code: editor.note.fields[jp_field_index] = jp_sentence
        # And: japanese_word = editor.note.fields[editor.web.editor.currentField]
        # This implies fields is dict-like or Anki's Note object supports access by name?
        # Standard Anki Note.fields is a list of strings. Access by name uses note['Name'].
        # But `editor.note.fields` is usually the list.
        # Let's check the code: `japanese_word = editor.note.fields[editor.web.editor.currentField]`
        # If `currentField` is an index (int), this works.
        # If `currentField` is a name (str), `note.fields` (list) would raise TypeError.
        # But `editor.web.editor.currentField` usually returns the field index in older Anki, or name?

        # In `GUI.py`: `japanese_word = editor.note.fields[editor.web.editor.currentField]`
        # If `currentField` is int, fine.

        # But later: `note.fields[jp_field_index] = jp_sentence`
        # `jp_field_index` is found via `field_names.index(DST_FIELD_JAP)`.

        # So `fields` behaves like a list.
        # If `editor.web.editor.currentField` is an int (index), then `editor.note.fields` is list.

        # Let's assume currentField is an int index for the mocking purpose.
        editor.web.editor.currentField = 0

        # Mock fields list
        editor.note.fields = ['test_word', '', '']

        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }

        # Mock create_custom_dialog to return 0 (English) first, then 0 (First example)
        mock_create_custom_dialog.side_effect = [0, 0]

        # Mock find_japanese_sentence result
        mock_results = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]
        mock_find_japanese_sentence.return_value = mock_results

        # Call the function
        self.GUI.add_example_manually_dialog(editor)

        # Verify initial dialog (Language selection)
        _ = self.GUI._

        mock_create_custom_dialog.assert_any_call(
            _("select_translation_language_dialog"),
            ['English', 'French']
        )

        # Verify QueryOp usage
        self.assertTrue(self.mock_operations.QueryOp.called, "QueryOp should be instantiated")

        # Get the instance
        op_instance = self.mock_operations.QueryOp.return_value

        # Check run_in_background was called
        op_instance.with_progress.return_value.run_in_background.assert_called_once()

        # Now we need to simulate the success callback
        call_args = self.mock_operations.QueryOp.call_args
        args, kwargs = call_args
        success_callback = kwargs['success']

        # Simulate success callback with results
        success_callback(mock_results)

        # Verify second dialog (Example selection)
        mock_create_custom_dialog.assert_called_with(
            _('select_sentence_dialog'),
            ['JP1\nTR1'],
            parent=editor.parentWindow
        )

        # Verify note update
        # DST_FIELD_JAP is 'Expression' (index 0)
        # DST_FIELD_TRANSLATION is 'Meaning' (index 1)
        self.assertEqual(editor.note.fields[0], 'JP1')
        self.assertEqual(editor.note.fields[1], 'TR1')

        # Verify flush and loadNote
        editor.note.flush.assert_called_once()
        editor.loadNote.assert_called_once()

    def test_fallback_when_queryop_missing(self):
        # Unpatch operations to simulate older Anki
        # This requires reloading GUI without operations

        # Manually set QueryOp to None in GUI module for this test
        original_query_op = self.GUI.QueryOp
        self.GUI.QueryOp = None

        try:
             # Setup mocks
            editor = MagicMock()
            editor.web.editor.currentField = 0
            editor.note.fields = ['test_word', '', '']
            editor.note.note_type.return_value = {
                'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
            }

            with patch('GUI.create_custom_dialog') as mock_dialog, \
                 patch('GUI.find_japanese_sentence') as mock_find:

                mock_dialog.return_value = 0 # English
                mock_find.return_value = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]

                # Mock create_custom_dialog again for result picker
                mock_dialog.side_effect = [0, 0]

                # Call
                self.GUI.add_example_manually_dialog(editor)

                # Verify find_japanese_sentence called directly (synchronously)
                mock_find.assert_called_with('test_word', 'eng')

                # Verify logic ran (check note update)
                self.assertEqual(editor.note.fields[0], 'JP1')

        finally:
            self.GUI.QueryOp = original_query_op

if __name__ == '__main__':
    unittest.main()
