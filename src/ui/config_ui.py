from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QPushButton, QFormLayout, QDialogButtonBox, showInfo
from aqt.qt import QLineEdit

try:
    from ..utils.i18n import _
except ImportError:
    from src.utils.i18n import _

class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("settings_dialog_title"))
        self.resize(400, 200)
        # Assuming package structure is "japanese_examples.gui" or similar,
        # but mw.addonManager needs the top-level addon folder name.
        # Since this file is in the root of the addon folder (presumably),
        # __name__.split('.')[0] works if imported as a module.
        # However, to be safe, we can inspect mw.addonManager.
        self.addon_name = __name__.split('.')[0]
        self.config = mw.addonManager.getConfig(self.addon_name) or {}
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        form_layout = QFormLayout()

        # Japanese Field
        self.jp_field = QLineEdit(self.config.get("japaneseDstField", "ExampleJapanese"))
        form_layout.addRow(_("japanese_field_label"), self.jp_field)

        # Translation Field
        self.tr_field = QLineEdit(self.config.get("translationDstField", "ExampleTranslated"))
        form_layout.addRow(_("translation_field_label"), self.tr_field)

        # Audio Field
        self.audio_field = QLineEdit(self.config.get("audioDstField", "ExampleAudio"))
        form_layout.addRow(_("audio_field_label"), self.audio_field)

        layout.addLayout(form_layout)

        # Reset Deck Preferences Button
        reset_btn = QPushButton(_("reset_deck_preferences"))
        reset_btn.clicked.connect(self.reset_preferences)
        layout.addWidget(reset_btn)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reset_preferences(self):
        if not self.config:
            self.config = {}
        self.config['deck_preferences'] = {}
        mw.addonManager.writeConfig(self.addon_name, self.config)
        showInfo(_("deck_preferences_reset"))

    def save_config(self):
        if not self.config:
            self.config = {}
        self.config["japaneseDstField"] = self.jp_field.text()
        self.config["translationDstField"] = self.tr_field.text()
        self.config["audioDstField"] = self.audio_field.text()

        # Clean up legacy multi-fields if present
        for key in ["japaneseDstField2", "japaneseDstField3", "translationDstField2", "translationDstField3"]:
            if key in self.config:
                del self.config[key]
        
        mw.addonManager.writeConfig(self.addon_name, self.config)
        self.accept()

def on_config():
    dialog = ConfigDialog(mw.app.activeWindow())
    dialog.exec()
