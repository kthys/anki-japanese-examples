import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestGUIUtilities(unittest.TestCase):

    def setUp(self):
        # Create mocks for aqt modules
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_operations = MagicMock()
        self.mock_utils = MagicMock()
        self.mock_gui_hooks = MagicMock()
        self.mock_qt = MagicMock()

        # Mock PyQt5
        self.mock_pyqt5 = MagicMock()
        self.mock_pyqt5_qtcore = MagicMock()
        self.mock_pyqt5.QtCore = self.mock_pyqt5_qtcore

        # Configure QTimer
        self.mock_qtimer = MagicMock()
        self.mock_pyqt5_qtcore.QTimer = self.mock_qtimer

        # Patch sys.modules to simulate aqt and PyQt5 existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.operations': self.mock_operations,
            'aqt.utils': self.mock_utils,
            'aqt.gui_hooks': self.mock_gui_hooks,
            'aqt.qt': self.mock_qt,
            'PyQt5': self.mock_pyqt5,
            'PyQt5.QtCore': self.mock_pyqt5_qtcore
        })
        self.modules_patcher.start()

        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Mock Qt constants
        self.mock_utils.Qt = MagicMock()
        # Default to Qt5
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore'

        # Mock japanese_examples because GUI imports from it
        self.mock_japanese_examples = MagicMock()
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
        if 'japanese_examples' in sys.modules:
            del sys.modules['japanese_examples']


    def test_get_plugin_dir_path(self):
        """Test get_plugin_dir_path returns correct path based on __file__."""
        expected_path = os.path.dirname(os.path.abspath(self.GUI.__file__))
        result = self.GUI.get_plugin_dir_path()
        self.assertEqual(result, expected_path)

    def test_create_custom_dialog_with_checkbox_adds_tooltip(self):
        """Test create_custom_dialog adds a tooltip and icon when checkbox is enabled."""
        # Setup mocks
        mock_dialog = self.mock_utils.QDialog.return_value
        mock_dialog.exec.return_value = 1  # OK

        mock_selection_list = self.mock_utils.QListWidget.return_value
        mock_selection_list.currentRow.return_value = 0

        mock_checkbox = self.mock_qt.QCheckBox.return_value
        mock_checkbox.isChecked.return_value = True

        # Call function
        result = self.GUI.create_custom_dialog(
            "Test Message",
            ["Choice 1", "Choice 2"],
            with_checkbox=True,
            checkbox_text="Save default"
        )

        # Verify layout structure
        # Should create QHBoxLayout
        self.assertTrue(self.mock_qt.QHBoxLayout.called, "QHBoxLayout should be instantiated")
        mock_h_layout = self.mock_qt.QHBoxLayout.return_value

        # Should create QLabel for info icon
        # Check if QLabel was called with "ⓘ"
        # Since QLabel is called multiple times (for message and info icon), check call_args_list
        calls = self.mock_utils.QLabel.call_args_list
        info_icon_created = any(call[0][0] == "ⓘ" for call in calls)
        self.assertTrue(info_icon_created, "Info icon QLabel('ⓘ') should be created")

        # Find the mock for the info label to verify setToolTip
        # It's tricky because return_value is the same mock object for all calls by default unless side_effect is used.
        # But we can check if setToolTip was called on the return value of QLabel.
        mock_label = self.mock_utils.QLabel.return_value
        mock_label.setToolTip.assert_called()

        # Verify widgets added to HBox
        # mock_h_layout.addWidget called with checkbox and label
        self.assertTrue(mock_h_layout.addWidget.called)

        # Verify HBox added to main layout (VBox)
        # Main layout is created via QVBoxLayout()
        mock_v_layout = self.mock_utils.QVBoxLayout.return_value
        mock_v_layout.addLayout.assert_called_with(mock_h_layout)

    def test_create_custom_dialog_returns_none_on_cancel(self):
        """Test create_custom_dialog returns None when user cancels."""
        mock_dialog = self.mock_utils.QDialog.return_value
        mock_dialog.exec.return_value = 0  # Cancel

        result = self.GUI.create_custom_dialog(
            "Test Message",
            ["Choice 1", "Choice 2"]
        )

        self.assertIsNone(result)

    def test_create_custom_dialog_without_checkbox_returns_index(self):
        """Test create_custom_dialog without checkbox returns a plain integer index."""
        mock_dialog = self.mock_utils.QDialog.return_value
        mock_dialog.exec.return_value = 1  # OK

        mock_selection_list = self.mock_utils.QListWidget.return_value
        mock_selection_list.currentRow.return_value = 1

        result = self.GUI.create_custom_dialog(
            "Test Message",
            ["Choice 1", "Choice 2"]
        )

        self.assertEqual(result, 1)
        # QCheckBox should NOT have been instantiated
        self.mock_qt.QCheckBox.assert_not_called()


if __name__ == '__main__':
    unittest.main()
