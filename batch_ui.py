from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, showInfo
from aqt.qt import QComboBox, QCheckBox, QPushButton, QHBoxLayout, QAction

try:
    from .i18n import _
except ImportError:
    try:
        from i18n import _
    except Exception:
        _ = lambda x: x


class BatchDialog(QDialog):
    """Dialog for Tatoeba batch processing — adds examples to all cards in a deck."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("batch_dialog_title"))
        self.resize(500, 350)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # ── Section 1: Data Management ──────────────────────────────
        data_title = QLabel(f"<b>{_('batch_data_section_title')}</b>")
        layout.addWidget(data_title)

        # Language selector
        lang_row = QHBoxLayout()
        lang_label = QLabel(_("batch_language_label") if _("batch_language_label") != "batch_language_label" else "Language:")
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "French"])
        lang_row.addWidget(lang_label)
        lang_row.addWidget(self.language_combo)
        layout.addLayout(lang_row)

        # File status
        self.file_status_label = QLabel(_("batch_file_not_downloaded"))
        layout.addWidget(self.file_status_label)

        # Download button
        self.download_button = QPushButton(_("batch_download_button"))
        self.download_button.clicked.connect(self._on_download)
        layout.addWidget(self.download_button)

        # ── Section 2: Execution ────────────────────────────────────
        exec_title = QLabel(f"<b>{_('batch_execution_section_title')}</b>")
        layout.addWidget(exec_title)

        # Deck selector
        deck_row = QHBoxLayout()
        deck_label = QLabel(_("batch_deck_label") if _("batch_deck_label") != "batch_deck_label" else "Deck:")
        self.deck_combo = QComboBox()
        self._populate_decks()
        deck_row.addWidget(deck_label)
        deck_row.addWidget(self.deck_combo)
        layout.addLayout(deck_row)

        # Skip checkbox
        self.skip_checkbox = QCheckBox(_("batch_skip_existing"))
        self.skip_checkbox.setChecked(True)
        layout.addWidget(self.skip_checkbox)

        # Run button (disabled by default)
        self.run_button = QPushButton(_("batch_run_button"))
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._on_run)
        layout.addWidget(self.run_button)

        # ── Close button ────────────────────────────────────────────
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    # ── Deck population ─────────────────────────────────────────────

    def _populate_decks(self):
        """Populate deck combo from Anki collection."""
        try:
            decks = mw.col.decks.all_names_and_ids()
            for deck in decks:
                self.deck_combo.addItem(deck.name, deck.id)
        except Exception:
            pass

    # ── Stub handlers (to be wired in Phase 1.2 / Phase 2) ─────────

    def _on_download(self):
        """Stub — download logic will be implemented in Plan 1.2."""
        showInfo("Download not yet implemented.")

    def _on_run(self):
        """Stub — batch processing engine will be implemented in Phase 2."""
        showInfo("Batch processing not yet implemented.")


def register_batch_menu():
    """Register the 'Tatoeba Batch Processing' action in Tools menu."""
    action = QAction(_("batch_menu_action"), mw)
    action.triggered.connect(lambda: BatchDialog(mw).exec())
    mw.form.menuTools.addAction(action)
