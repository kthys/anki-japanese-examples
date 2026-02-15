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

        # Mock PyQt5
        self.mock_pyqt5 = MagicMock()
        self.mock_pyqt5_qtcore = MagicMock()
        self.mock_pyqt5.QtCore = self.mock_pyqt5_qtcore
        self.mock_pyqt5_qtcore.QTimer = MagicMock()

        # Patch sys.modules to simulate aqt existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.operations': self.mock_operations,
            'aqt.utils': self.mock_utils,
            'aqt.gui_hooks': self.mock_gui_hooks,
            'aqt.qt': MagicMock(),
            'PyQt5': self.mock_pyqt5,
            'PyQt5.QtCore': self.mock_pyqt5_qtcore
        })
        self.modules_patcher.start()

        # Configure mw
        self.mock_mw.pm.meta.get.return_value = 'en'
        self.mock_mw.col.path = "/path/to/collection.anki2"
        self.mock_mw.progress.busy.return_value = False
        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Add config mock
        self.mock_config = {
            "japaneseDstField": "Expression",
            "translationDstField": "Meaning",
            "deck_preferences": {}
        }
        self.mock_mw.addonManager.getConfig.return_value = self.mock_config

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

        # Import the module under test
        if 'GUI' in sys.modules:
            del sys.modules['GUI']
        import GUI
        self.GUI = GUI

    def tearDown(self):
        self.modules_patcher.stop()
        if 'GUI' in sys.modules:
            del sys.modules['GUI']

    @patch('GUI.find_japanese_sentence')
    @patch('GUI.create_custom_dialog')
    @patch('GUI.showInfo')
    def test_add_example_manually_dialog_flow(self, mock_showInfo, mock_create_custom_dialog, mock_find_japanese_sentence):
        # Setup mocks
        editor = MagicMock()
        editor.web.editor.currentField = 0 # Assuming index

        # Mock fields list
        editor.note.fields = ['test_word', '', '']

        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }

        # Mock create_custom_dialog returns
        # 1. Language selection: returns (0, False) because deck_id is detected (via mocks) so checkbox is shown
        # 2. Example selection: returns 0 (index)
        mock_create_custom_dialog.side_effect = [(0, False), 0]

        # Mock find_japanese_sentence result
        mock_results = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]
        mock_find_japanese_sentence.return_value = mock_results

        # Call the function
        self.GUI.add_example_manually_dialog(editor)

        # Verify initial dialog (Language selection)
        _ = self.GUI._

        # Verify call arguments
        args, kwargs = mock_create_custom_dialog.call_args_list[0]
        self.assertEqual(args[0], _("select_translation_language_dialog"))
        self.assertTrue(kwargs.get('with_checkbox')) # Checkbox should be True as deck_id is present

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

        # Execute the scheduled function by QTimer
        # Verify singleShot called
        self.mock_pyqt5_qtcore.QTimer.singleShot.assert_called()
        timer_args = self.mock_pyqt5_qtcore.QTimer.singleShot.call_args[0]
        # singleShot(delay, func)
        scheduled_func = timer_args[1]
        scheduled_func()

        # Verify second dialog (Example selection)
        # Check second call to create_custom_dialog
        args2, kwargs2 = mock_create_custom_dialog.call_args_list[1]
        self.assertEqual(args2[0], _('select_sentence_dialog'))
        # Should NOT have with_checkbox=True (default is False)
        self.assertFalse(kwargs2.get('with_checkbox', False))

        # Verify note update
        # DST_FIELD_JAP is 'Expression' (index 0)
        # DST_FIELD_TRANSLATION is 'Meaning' (index 1)
        self.assertEqual(editor.note.fields[0], 'JP1')
        self.assertEqual(editor.note.fields[1], 'TR1')

        # Verify flush and loadNote
        self.mock_mw.col.update_note.assert_called_with(editor.note)
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

                mock_dialog.return_value = (0, False) # English, no save
                mock_find.return_value = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]

                # Mock create_custom_dialog again for result picker
                # 1. Language: (0, False)
                # 2. Result: 0
                mock_dialog.side_effect = [(0, False), 0]

                # Call
                self.GUI.add_example_manually_dialog(editor)

                # QTimer should have been called in on_success
                self.mock_pyqt5_qtcore.QTimer.singleShot.assert_called()
                timer_args = self.mock_pyqt5_qtcore.QTimer.singleShot.call_args[0]
                scheduled_func = timer_args[1]
                scheduled_func()

                # Verify find_japanese_sentence called directly (synchronously)
                mock_find.assert_called_with('test_word', 'eng')

                # Verify logic ran (check note update)
                self.assertEqual(editor.note.fields[0], 'JP1')

        finally:
            self.GUI.QueryOp = original_query_op

if __name__ == '__main__':
    unittest.main()
