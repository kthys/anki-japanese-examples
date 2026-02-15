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
            'aqt.qt': MagicMock(),
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

    def test_get_qt_version_qt5(self):
        """Test get_qt_version returns 5 when Qt module is PyQt5."""
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore'
        self.assertEqual(self.GUI.get_qt_version(), 5)

    def test_get_qt_version_qt6(self):
        """Test get_qt_version returns 6 when Qt module is PyQt6."""
        self.mock_utils.Qt.__module__ = 'PyQt6.QtCore'
        self.assertEqual(self.GUI.get_qt_version(), 6)

    def test_get_plugin_dir_path(self):
        """Test get_plugin_dir_path returns correct path based on mw.col.path."""
        # Setup mock collection path
        # Assume standard structure: .../User 1/collection.anki2
        # And plugin directory: .../addons21/plugin_name

        collection_path = "/home/user/Anki/User 1/collection.anki2"
        self.mock_mw.col.path = collection_path

        # Expected path derivation
        # user_dir = /home/user/Anki/User 1
        # anki_dir = /home/user/Anki
        # plugin_dir = /home/user/Anki/addons21/GUI (since __name__ is GUI)

        expected_path = os.path.join("/home/user/Anki", "addons21", "GUI")

        result = self.GUI.get_plugin_dir_path()
        self.assertEqual(result, expected_path)

if __name__ == '__main__':
    unittest.main()
