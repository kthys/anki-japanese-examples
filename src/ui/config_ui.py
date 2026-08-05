from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QPushButton, QFormLayout, QDialogButtonBox, showInfo, showWarning
from aqt.qt import QLineEdit, QSpinBox

try:
    from ..utils.i18n import _
except ImportError:
    from src.utils.i18n import _

try:
    from ..core.japanese_examples import test_tatoeba_connection
except ImportError:
    try:
        from src.core.japanese_examples import test_tatoeba_connection
    except ImportError:
        test_tatoeba_connection = None

# Try to import QueryOp for background operations (Anki 2.1.50+)
try:
    from aqt.operations import QueryOp
except ImportError:
    QueryOp = None

# Global set to keep references to active operations to prevent premature
# garbage collection (same pattern as GUI.py / batch_ui.py)
_active_ops = set()

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

        # Number of sentence options shown when adding an example
        self.max_options = QSpinBox()
        self.max_options.setRange(1, 100)
        try:
            self.max_options.setValue(int(self.config.get("maxSentenceOptions", 30)))
        except (TypeError, ValueError):
            self.max_options.setValue(30)
        form_layout.addRow(_("max_sentence_options_label"), self.max_options)

        layout.addLayout(form_layout)

        # Test connection button — pings the Tatoeba API and reports the result
        test_btn = QPushButton(_("test_connection_button"))
        test_btn.clicked.connect(self.test_connection)
        layout.addWidget(test_btn)

        # Reset Deck Preferences Button
        reset_btn = QPushButton(_("reset_deck_preferences"))
        reset_btn.clicked.connect(self.reset_preferences)
        layout.addWidget(reset_btn)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def test_connection(self):
        """
        Ping the Tatoeba API in the background and show the result.

        Runs the network request in a QueryOp (Anki 2.1.50+) so the dialog
        never blocks; falls back to a blocking call on older Anki versions.
        Success and failure are both reported with a visible dialog.
        """
        if test_tatoeba_connection is None:
            showWarning(_("test_connection_failed").format(error="module unavailable"))
            return

        op = None

        def on_success(result):
            if op:
                _active_ops.discard(op)
            success, message = result
            if success:
                showInfo(message)
            else:
                showWarning(message)

        def on_failure(exc):
            if op:
                _active_ops.discard(op)
            showWarning(_("test_connection_failed").format(error=str(exc)))

        if QueryOp:
            op = QueryOp(
                parent=self,
                op=lambda col: test_tatoeba_connection(),
                success=on_success,
            ).failure(on_failure)
            _active_ops.add(op)
            op.run_in_background()
        else:
            # Fallback for older Anki: blocking call, as before
            try:
                on_success(test_tatoeba_connection())
            except Exception as exc:
                on_failure(exc)

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
        self.config["maxSentenceOptions"] = self.max_options.value()

        # Clean up legacy multi-fields if present
        for key in ["japaneseDstField2", "japaneseDstField3", "translationDstField2", "translationDstField3"]:
            if key in self.config:
                del self.config[key]
        
        mw.addonManager.writeConfig(self.addon_name, self.config)
        self.accept()

def on_config():
    dialog = ConfigDialog(mw.app.activeWindow())
    dialog.exec()
