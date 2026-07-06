import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestChangelog(unittest.TestCase):

    def setUp(self):
        # Create mocks for aqt modules
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_gui_hooks = MagicMock()
        self.mock_qt = MagicMock()

        # Patch sys.modules to simulate aqt existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.gui_hooks': self.mock_gui_hooks,
            'aqt.qt': self.mock_qt,
        })
        self.modules_patcher.start()

        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Import the module under test
        if 'src.utils.changelog' in sys.modules:
            del sys.modules['src.utils.changelog']
        import src.utils.changelog as changelog
        self.changelog = changelog

    def tearDown(self):
        self.modules_patcher.stop()
        if 'changelog' in sys.modules:
            del sys.modules['changelog']

    # ── get_plugin_version ──────────────────────────────────────────

    def test_get_plugin_version_returns_version_from_manifest(self):
        """Should read the version string from manifest.json."""
        manifest_data = json.dumps({"version": "2.0.0"})
        with patch("builtins.open", mock_open(read_data=manifest_data)):
            result = self.changelog.get_plugin_version()
        self.assertEqual(result, "2.0.0")

    def test_get_plugin_version_returns_default_when_key_missing(self):
        """Should return '1.1.0' when manifest.json has no 'version' key."""
        manifest_data = json.dumps({"name": "test"})
        with patch("builtins.open", mock_open(read_data=manifest_data)):
            result = self.changelog.get_plugin_version()
        self.assertEqual(result, "1.1.0")

    def test_get_plugin_version_returns_default_on_file_error(self):
        """Should return '1.1.0' when manifest.json cannot be read."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = self.changelog.get_plugin_version()
        self.assertEqual(result, "1.1.0")

    def test_get_plugin_version_returns_default_on_invalid_json(self):
        """Should return '1.1.0' when manifest.json contains invalid JSON."""
        with patch("builtins.open", mock_open(read_data="not json")):
            result = self.changelog.get_plugin_version()
        self.assertEqual(result, "1.1.0")

    # ── get_changelog_text ──────────────────────────────────────────

    def test_get_changelog_text_returns_file_contents(self):
        """Should return the full text of changelog.md."""
        md_content = "# Changelog\n\n## v2.0.0\n- New feature"
        with patch("builtins.open", mock_open(read_data=md_content)):
            result = self.changelog.get_changelog_text()
        self.assertEqual(result, md_content)

    def test_get_changelog_text_returns_empty_on_file_error(self):
        """Should return '' when changelog.md cannot be read."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = self.changelog.get_changelog_text()
        self.assertEqual(result, "")

    def test_get_changelog_text_returns_empty_on_permission_error(self):
        """Should return '' on PermissionError."""
        with patch("builtins.open", side_effect=PermissionError):
            result = self.changelog.get_changelog_text()
        self.assertEqual(result, "")

    # ── show_changelog_dialog ───────────────────────────────────────

    def test_show_changelog_dialog_creates_dialog_with_correct_title(self):
        """Should create a QDialog with the version in the title."""
        mock_dialog = self.mock_qt.QDialog.return_value
        mock_browser = self.mock_qt.QTextBrowser.return_value

        self.changelog.show_changelog_dialog("3.0.0", "# Changes")

        self.mock_qt.QDialog.assert_called_once_with(self.mock_mw)
        mock_dialog.setWindowTitle.assert_called_once_with(
            "Japanese Examples Update (v3.0.0)"
        )

    def test_show_changelog_dialog_sets_size(self):
        """Should resize the dialog to 500×450."""
        mock_dialog = self.mock_qt.QDialog.return_value
        self.changelog.show_changelog_dialog("1.0.0", "text")
        mock_dialog.resize.assert_called_once_with(500, 450)

    def test_show_changelog_dialog_uses_setMarkdown(self):
        """Should render content with setMarkdown when available."""
        mock_browser = self.mock_qt.QTextBrowser.return_value
        self.changelog.show_changelog_dialog("1.0.0", "# Hello")
        mock_browser.setMarkdown.assert_called_once_with("# Hello")

    def test_show_changelog_dialog_falls_back_to_plaintext(self):
        """Should fall back to setPlainText if setMarkdown raises AttributeError."""
        mock_browser = self.mock_qt.QTextBrowser.return_value
        mock_browser.setMarkdown.side_effect = AttributeError

        self.changelog.show_changelog_dialog("1.0.0", "# Hello")

        mock_browser.setPlainText.assert_called_once_with("# Hello")

    def test_show_changelog_dialog_enables_external_links(self):
        """Should enable opening external links in the text browser."""
        mock_browser = self.mock_qt.QTextBrowser.return_value
        self.changelog.show_changelog_dialog("1.0.0", "text")
        mock_browser.setOpenExternalLinks.assert_called_once_with(True)

    def test_show_changelog_dialog_adds_ok_button(self):
        """Should add an OK button that closes the dialog."""
        mock_dialog = self.mock_qt.QDialog.return_value
        mock_btn = self.mock_qt.QPushButton.return_value

        self.changelog.show_changelog_dialog("1.0.0", "text")

        self.mock_qt.QPushButton.assert_called_once_with("OK")
        mock_btn.clicked.connect.assert_called_once_with(mock_dialog.accept)

    def test_show_changelog_dialog_calls_exec(self):
        """Should call exec() to display the dialog modally."""
        mock_dialog = self.mock_qt.QDialog.return_value
        self.changelog.show_changelog_dialog("1.0.0", "text")
        mock_dialog.exec.assert_called_once()

    # ── check_and_show_changelog ────────────────────────────────────

    def test_check_and_show_changelog_returns_early_if_no_config(self):
        """Should return immediately when getConfig returns None."""
        self.mock_mw.addonManager.getConfig.return_value = None

        with patch.object(self.changelog, 'get_plugin_version') as mock_ver:
            self.changelog.check_and_show_changelog("my_addon")
            mock_ver.assert_not_called()

    def test_check_and_show_changelog_shows_dialog_on_version_change(self):
        """Should show the changelog dialog when the version has changed."""
        self.mock_mw.addonManager.getConfig.return_value = {
            "last_seen_version": "1.0.0"
        }

        with patch.object(self.changelog, 'get_plugin_version', return_value="2.0.0"), \
             patch.object(self.changelog, 'get_changelog_text', return_value="# New") as mock_text, \
             patch.object(self.changelog, 'show_changelog_dialog') as mock_dialog:
            self.changelog.check_and_show_changelog("my_addon")

            mock_text.assert_called_once()
            mock_dialog.assert_called_once_with("2.0.0", "# New")

    def test_check_and_show_changelog_updates_config_after_showing(self):
        """Should write the new version to config after showing the dialog."""
        config = {"last_seen_version": "1.0.0"}
        self.mock_mw.addonManager.getConfig.return_value = config

        with patch.object(self.changelog, 'get_plugin_version', return_value="2.0.0"), \
             patch.object(self.changelog, 'get_changelog_text', return_value="# New"), \
             patch.object(self.changelog, 'show_changelog_dialog'):
            self.changelog.check_and_show_changelog("my_addon")

        self.assertEqual(config["last_seen_version"], "2.0.0")
        self.mock_mw.addonManager.writeConfig.assert_called_once_with(
            "my_addon", config
        )

    def test_check_and_show_changelog_skips_when_version_matches(self):
        """Should not show the dialog when last_seen_version equals current."""
        self.mock_mw.addonManager.getConfig.return_value = {
            "last_seen_version": "2.0.0"
        }

        with patch.object(self.changelog, 'get_plugin_version', return_value="2.0.0"), \
             patch.object(self.changelog, 'show_changelog_dialog') as mock_dialog:
            self.changelog.check_and_show_changelog("my_addon")
            mock_dialog.assert_not_called()

    def test_check_and_show_changelog_skips_dialog_when_text_empty(self):
        """Should not show the dialog if changelog text is empty, but still update config."""
        config = {"last_seen_version": "1.0.0"}
        self.mock_mw.addonManager.getConfig.return_value = config

        with patch.object(self.changelog, 'get_plugin_version', return_value="2.0.0"), \
             patch.object(self.changelog, 'get_changelog_text', return_value=""), \
             patch.object(self.changelog, 'show_changelog_dialog') as mock_dialog:
            self.changelog.check_and_show_changelog("my_addon")

            mock_dialog.assert_not_called()
            # Config should still be updated
            self.assertEqual(config["last_seen_version"], "2.0.0")
            self.mock_mw.addonManager.writeConfig.assert_called_once()

    def test_check_and_show_changelog_first_install_no_last_seen(self):
        """Should show changelog on first install when last_seen_version is missing."""
        config = {"some_other_setting": True}
        self.mock_mw.addonManager.getConfig.return_value = config

        with patch.object(self.changelog, 'get_plugin_version', return_value="1.2.0"), \
             patch.object(self.changelog, 'get_changelog_text', return_value="# v1.2.0"), \
             patch.object(self.changelog, 'show_changelog_dialog') as mock_dialog:
            self.changelog.check_and_show_changelog("my_addon")

            mock_dialog.assert_called_once_with("1.2.0", "# v1.2.0")
            self.assertEqual(config["last_seen_version"], "1.2.0")


if __name__ == '__main__':
    unittest.main()
