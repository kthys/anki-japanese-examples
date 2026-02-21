import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigUI(unittest.TestCase):

    def setUp(self):
        # Create mocks for aqt modules
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_utils = MagicMock()
        self.mock_qt = MagicMock()

        # Patch sys.modules to simulate aqt existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.utils': self.mock_utils,
            'aqt.qt': self.mock_qt,
        })
        self.modules_patcher.start()

        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Default config
        self.mock_config = {
            "japaneseDstField": "ExampleJapanese",
            "translationDstField": "ExampleTranslated",
            "deck_preferences": {"1": "eng"}
        }
        self.mock_mw.addonManager.getConfig.return_value = self.mock_config.copy()

        # Make QDialog a proper base for ConfigDialog
        # We need QDialog to be a real class so ConfigDialog can subclass it
        real_qdialog = type('QDialog', (), {
            '__init__': lambda self, parent=None: None,
            'setWindowTitle': MagicMock(),
            'resize': MagicMock(),
            'setLayout': MagicMock(),
            'accept': MagicMock(),
            'reject': MagicMock(),
            'exec': MagicMock(),
        })
        self.mock_utils.QDialog = real_qdialog
        self.mock_utils.QVBoxLayout = MagicMock
        self.mock_utils.QPushButton = MagicMock()
        self.mock_utils.QFormLayout = MagicMock
        self.mock_utils.QDialogButtonBox = MagicMock()
        self.mock_utils.showInfo = MagicMock()
        self.mock_qt.QLineEdit = MagicMock()

        # Import the module under test
        if 'config_ui' in sys.modules:
            del sys.modules['config_ui']
        import config_ui
        self.config_ui = config_ui

    def tearDown(self):
        self.modules_patcher.stop()
        if 'config_ui' in sys.modules:
            del sys.modules['config_ui']

    # ── save_config ─────────────────────────────────────────────────

    def test_save_config_writes_field_names(self):
        """Should write the field name values from the QLineEdit inputs to config."""
        dialog = self.config_ui.ConfigDialog()

        # Simulate user input
        dialog.jp_field = MagicMock()
        dialog.jp_field.text.return_value = "MyJapField"
        dialog.tr_field = MagicMock()
        dialog.tr_field.text.return_value = "MyTransField"
        dialog.config = self.mock_config.copy()

        dialog.save_config()

        self.assertEqual(dialog.config["japaneseDstField"], "MyJapField")
        self.assertEqual(dialog.config["translationDstField"], "MyTransField")
        self.mock_mw.addonManager.writeConfig.assert_called_once()

    def test_save_config_creates_config_if_none(self):
        """Should create a new config dict if existing config is None/empty."""
        dialog = self.config_ui.ConfigDialog()
        dialog.config = None
        dialog.jp_field = MagicMock()
        dialog.jp_field.text.return_value = "Field1"
        dialog.tr_field = MagicMock()
        dialog.tr_field.text.return_value = "Field2"

        dialog.save_config()

        self.assertIsNotNone(dialog.config)
        self.assertEqual(dialog.config["japaneseDstField"], "Field1")

    # ── reset_preferences ───────────────────────────────────────────

    def test_reset_preferences_clears_deck_prefs(self):
        """Should clear deck_preferences and write config."""
        dialog = self.config_ui.ConfigDialog()
        dialog.config = {"deck_preferences": {"1": "eng", "2": "fra"}}

        dialog.reset_preferences()

        self.assertEqual(dialog.config["deck_preferences"], {})
        self.mock_mw.addonManager.writeConfig.assert_called_once()

    def test_reset_preferences_creates_config_if_none(self):
        """Should create config dict if it was None when resetting."""
        dialog = self.config_ui.ConfigDialog()
        dialog.config = None

        dialog.reset_preferences()

        self.assertIsNotNone(dialog.config)
        self.assertEqual(dialog.config["deck_preferences"], {})


if __name__ == '__main__':
    unittest.main()
