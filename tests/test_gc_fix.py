import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestGCFix(unittest.TestCase):

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
        self.mock_aqt.mw = self.mock_mw

        # Mock Qt constants
        self.mock_utils.Qt = MagicMock()
        self.mock_utils.Qt.WindowModality.WindowModal = 1
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore'

        # Mock japanese_examples
        self.mock_japanese_examples = MagicMock()
        self.mock_japanese_examples.DST_FIELD_JAP = 'Expression'
        self.mock_japanese_examples.DST_FIELD_TRANSLATION = 'Meaning'
        sys.modules['japanese_examples'] = self.mock_japanese_examples

        # Mock QueryOp
        # IMPORTANT: We need QueryOp to be a real class or at least return a unique object
        # so we can track it in the set.
        self.mock_op_instance = MagicMock()
        self.mock_op_instance.with_progress.return_value.run_in_background = MagicMock()

        # When QueryOp(..., success=...) is called, capture the success callback
        def query_op_side_effect(*args, **kwargs):
            self.success_callback = kwargs.get('success')
            return self.mock_op_instance

        self.mock_operations.QueryOp.side_effect = query_op_side_effect

        # Import the module under test (reloading to get fresh state)
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
    def test_queryop_gc_prevention(self, mock_showInfo, mock_create_custom_dialog, mock_find_japanese_sentence):
        # Setup editor mock
        editor = MagicMock()
        editor.web.editor.currentField = 0
        editor.note.fields = ['test_word', '', '']
        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }

        # Mock dialog to pick English
        mock_create_custom_dialog.return_value = 0

        # Verify _active_ops is empty initially
        self.assertEqual(len(self.GUI._active_ops), 0)

        # Call the function
        self.GUI.add_example_manually_dialog(editor)

        # Verify QueryOp was created
        self.assertTrue(self.mock_operations.QueryOp.called)

        # Verify op is now in _active_ops
        self.assertEqual(len(self.GUI._active_ops), 1)
        self.assertIn(self.mock_op_instance, self.GUI._active_ops)

        # Now simulate success
        # The success callback was captured in setUp side_effect
        mock_results = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]

        # Mock dialog for picking result
        mock_create_custom_dialog.return_value = 0

        self.success_callback(mock_results)

        # Verify op is removed from _active_ops
        self.assertEqual(len(self.GUI._active_ops), 0)
        self.assertNotIn(self.mock_op_instance, self.GUI._active_ops)

if __name__ == '__main__':
    unittest.main()
