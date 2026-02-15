import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import html

# Add parent directory to path to import GUI
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestSecurity(unittest.TestCase):

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
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore'

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

    def test_xss_prevention(self):
        """Test that HTML tags in fetched examples are escaped before insertion into note fields."""

        # Setup mocks
        editor = MagicMock()
        editor.web.editor.currentField = 0

        # Note fields mock
        # Initially empty fields
        # 0: Expression, 1: Meaning
        note_fields = ['japanese_word', '', '']
        editor.note.fields = note_fields

        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }

        # Malicious payload
        malicious_jp = "<script>alert('XSS_JP')</script>"
        malicious_tr = "<script>alert('XSS_TR')</script>"

        # Expected escaped output
        expected_jp = html.escape(malicious_jp)
        expected_tr = html.escape(malicious_tr)

        mock_results = [{'jp_sentence': malicious_jp, 'tr_sentence': malicious_tr}]

        # Mock find_japanese_sentence to return our results
        # We also need to capture the success callback of QueryOp if used

        with patch('GUI.create_custom_dialog') as mock_dialog:
            # 1st call: Select Language (return 0 for English)
            # 2nd call: Select Example (return 0 for the first example)
            mock_dialog.side_effect = [0, 0]

            # Since GUI.py uses QueryOp(..., success=on_success), we need to intercept that
            # Or simpler: if we rely on QueryOp logic which calls on_success

            # Since we mocked aqt.operations.QueryOp, we can grab the callback from the call args

            self.GUI.add_example_manually_dialog(editor)

            if self.GUI.QueryOp:
                args, kwargs = self.mock_operations.QueryOp.call_args
                on_success = kwargs['success']
                # Simulate success with malicious payload
                on_success(mock_results)
            else:
                # If QueryOp is not available, it calls find_japanese_sentence synchronously
                # We would need to mock find_japanese_sentence to return mock_results
                pass

        # Check the fields in the note
        jp_field_value = editor.note.fields[0]
        tr_field_value = editor.note.fields[1]

        # Assertions
        self.assertEqual(jp_field_value, expected_jp, "JP field content was not properly escaped")
        self.assertEqual(tr_field_value, expected_tr, "TR field content was not properly escaped")

        # Double check that script tags are gone (redundant but explicit)
        self.assertNotIn("<script>", jp_field_value)
        self.assertNotIn("<script>", tr_field_value)

if __name__ == '__main__':
    unittest.main()
