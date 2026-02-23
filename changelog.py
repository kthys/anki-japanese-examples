from aqt import mw, gui_hooks
from aqt.qt import QDialog, QVBoxLayout, QTextBrowser, QPushButton, Qt
import json
import os

def get_plugin_version():
    """
    Get the plugin version from the manifest.json file.

    Returns:
    - A string containing the plugin version. Default is '1.1.0'.
    """
    addon_dir = os.path.dirname(__file__)
    manifest_path = os.path.join(addon_dir, "manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            return manifest.get("version", "1.1.0")
    except Exception:
        return "1.1.0"

def get_changelog_text():
    """
    Get the changelog text from the changelog.md file.

    Returns:
    - A string containing the changelog text. Returns an empty string if an error occurs.
    """
    addon_dir = os.path.dirname(__file__)
    changelog_path = os.path.join(addon_dir, "changelog.md")
    try:
        with open(changelog_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def show_changelog_dialog(version, md_text):
    """
    Display a popup dialog with the updated changelog information.

    Args:
    - version (str): The current plugin version to display in the title.
    - md_text (str): The changelog content formatted as Markdown or plain text.
    """
    dialog = QDialog(mw)
    dialog.setWindowTitle(f"Japanese Examples Update (v{version})")
    dialog.resize(500, 450)
    layout = QVBoxLayout()
    
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    try:
        browser.setMarkdown(md_text) # PyQT5/6 support for rendering markdown
    except AttributeError:
        browser.setPlainText(md_text) # Fallback if setMarkdown is missing
        
    layout.addWidget(browser)
    
    btn = QPushButton("OK")
    btn.clicked.connect(dialog.accept)
    layout.addWidget(btn)
    
    dialog.setLayout(layout)
    dialog.exec()

def check_and_show_changelog(addon_name):
    """
    Check if the add-on has updated and display the changelog dialog if it has.

    Args:
    - addon_name (str): The name of the add-on to retrieve and update its configuration.
    """
    config = mw.addonManager.getConfig(addon_name)
    if not config:
        return
        
    current_version = get_plugin_version()
    last_seen = config.get("last_seen_version", "")
    
    if last_seen != current_version:
        # Show the changelog
        md_text = get_changelog_text()
        if md_text:
            show_changelog_dialog(current_version, md_text)
        
        # Update config
        config["last_seen_version"] = current_version
        mw.addonManager.writeConfig(addon_name, config)
