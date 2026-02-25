from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, showInfo
from aqt.qt import QComboBox, QCheckBox, QPushButton, QHBoxLayout, QAction, QTextEdit, QProgressBar
from aqt.operations import QueryOp

try:
    from PyQt6.QtCore import QTimer
except ImportError:
    from PyQt5.QtCore import QTimer

_active_ops = set()

try:
    from .i18n import _
except ImportError:
    try:
        from i18n import _
    except Exception:
        _ = lambda x: x

try:
    from . import tatoeba_data
except ImportError:
    import tatoeba_data

try:
    from . import batch_engine
except ImportError:
    import batch_engine


class DownloadProgressDialog(QDialog):
    """Modal-less dialog that displays an indeterminate progress bar and a read-only text log.

    Shown during the Tatoeba data download so the user can see each step
    as it happens instead of a generic spinner.
    """

    def __init__(self, parent=None):
        """
        Initialize the DownloadProgressDialog.

        Args:
        - parent (QWidget): The parent widget. Default is None.

        Returns:
        - None
        """
        super().__init__(parent)
        self.setWindowTitle(_("batch_progress_title"))
        self.resize(400, 250)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def append_log(self, message: str):
        """
        Append a message line to the read-only text log.

        Args:
        - message (str): The status message to append.

        Returns:
        - None
        """
        self.log_text.append(message)


class BatchDialog(QDialog):
    """Dialog for Tatoeba batch processing — adds examples to all cards in a deck."""

    def __init__(self, parent=None):
        """
        Initialize the BatchDialog.

        Args:
        - parent (QWidget): The parent widget, typically the Anki main window. Default is None.

        Returns:
        - None
        """
        super().__init__(parent)
        self.setWindowTitle(_("batch_dialog_title"))
        self.resize(500, 400)
        self.setup_ui()

    def setup_ui(self):
        """
        Set up the user interface for the dialog.

        Creates and lays out the widgets for the data management and execution sections.

        Args:
        - None

        Returns:
        - None
        """
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
        self.language_combo.currentIndexChanged.connect(self._update_file_status)
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
        self.deck_combo.currentIndexChanged.connect(self._populate_fields)
        deck_row.addWidget(deck_label)
        deck_row.addWidget(self.deck_combo)
        layout.addLayout(deck_row)

        self.deck_status_label = QLabel("")
        self.deck_status_label.setStyleSheet("color: red;")
        self.deck_status_label.hide()
        layout.addWidget(self.deck_status_label)

        # ── Field selectors ─────────────────────────────────────────
        # Source field (word to look up)
        source_row = QHBoxLayout()
        source_label = QLabel(_("batch_source_field_label") if _("batch_source_field_label") != "batch_source_field_label" else "Source field (word):")
        self.source_field_combo = QComboBox()
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_field_combo)
        layout.addLayout(source_row)

        # Japanese destination field
        jpn_row = QHBoxLayout()
        jpn_label = QLabel(_("batch_jpn_field_label") if _("batch_jpn_field_label") != "batch_jpn_field_label" else "Japanese example field:")
        self.jpn_field_combo = QComboBox()
        jpn_row.addWidget(jpn_label)
        jpn_row.addWidget(self.jpn_field_combo)
        layout.addLayout(jpn_row)

        # Translation destination field
        trans_row = QHBoxLayout()
        trans_label = QLabel(_("batch_trans_field_label") if _("batch_trans_field_label") != "batch_trans_field_label" else "Translation example field:")
        self.trans_field_combo = QComboBox()
        trans_row.addWidget(trans_label)
        trans_row.addWidget(self.trans_field_combo)
        layout.addLayout(trans_row)

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

        # Set initial file status and populate fields
        self._update_file_status()
        self._populate_fields()

    # ── Deck population ─────────────────────────────────────────────

    def _populate_decks(self):
        """
        Populate deck combo from Anki collection.

        Reads all deck names from the current Anki collection and adds them to the deck selector.

        Args:
        - None

        Returns:
        - None
        """
        try:
            decks = mw.col.decks.all_names_and_ids()
            for deck in decks:
                self.deck_combo.addItem(deck.name, deck.id)
        except Exception:
            pass

    # ── Field population ────────────────────────────────────────────

    def _populate_fields(self):
        """
        Populate the 3 field selector combos from the selected deck's note type.

        Reads the first note in the selected deck to discover available field
        names. If no notes are found, the combos are cleared and a warning is
        shown in the file status label.

        Args:
        - None (reads from self.deck_combo).

        Returns:
        - None
        """
        self.source_field_combo.clear()
        self.jpn_field_combo.clear()
        self.trans_field_combo.clear()
        self.deck_status_label.hide()

        deck_id = self.deck_combo.currentData()
        if deck_id is None:
            return

        try:
            note_ids = mw.col.find_notes(f"did:{deck_id}")
            if not note_ids:
                translated = _("batch_no_fields")
                if translated == "batch_no_fields":
                    translated = "No fields available. Please select a deck with notes."
                self.deck_status_label.setText(translated)
                self.deck_status_label.show()
                return

            note = mw.col.get_note(note_ids[0])
            field_names = [fld["name"] for fld in note.note_type()["flds"]]

            self.source_field_combo.addItems(field_names)
            self.jpn_field_combo.addItems(field_names)
            self.trans_field_combo.addItems(field_names)
        except Exception:
            pass

    # ── Handlers ────────────────────────────────────────────────────

    def _update_file_status(self):
        """
        Updates file status UI based on currently selected language.

        Reads the downloaded file status and metadata count, and shows it in the status label.

        Args:
        - None (reads from UI widgets).

        Returns:
        - None
        """
        lang = self.language_combo.currentText()
        date_str = tatoeba_data.get_file_status(lang)

        if date_str:
            count = 0
            # Attempt to read count from metadata
            try:
                import json
                if tatoeba_data.os.path.exists(tatoeba_data.METADATA_FILE):
                    with open(tatoeba_data.METADATA_FILE, "r", encoding="utf-8") as f:
                        md = json.load(f)
                        count = md.get(tatoeba_data.LANG_MAP.get(lang), {}).get("count", 0)
            except Exception:
                pass

            translated = _("batch_file_available")
            if translated != "batch_file_available":
                msg = translated.format(date=date_str, count=count)
            else:
                msg = f"Status: Downloaded on {date_str} ({count} pairs)"

            self.file_status_label.setText(msg)
            self.run_button.setEnabled(True)
        else:
            self.file_status_label.setText(_("batch_file_not_downloaded"))
            self.run_button.setEnabled(False)

    def _on_download(self):
        """
        Downloads Tatoeba data for the selected language in the background.

        Opens a DownloadProgressDialog with an indeterminate progress bar and
        a read-only text log, then runs the download via QueryOp.  Each step
        in download_tatoeba_data calls back to append a log line.

        Args:
        - None (reads from UI widgets).

        Returns:
        - None
        """
        self.download_button.setEnabled(False)
        self.file_status_label.setText(_("batch_downloading") if _("batch_downloading") != "batch_downloading" else "Downloading Tatoeba data...")

        lang = self.language_combo.currentText()

        # Open custom progress dialog
        self._download_dlg = DownloadProgressDialog(self)
        self._download_dlg.show()

        def progress_callback(msg):
            mw.taskman.run_on_main(lambda: self._download_dlg.append_log(msg))

        def background_func(col):
            return tatoeba_data.download_tatoeba_data(lang, progress_callback=progress_callback)

        def on_success(result):
            _active_ops.remove(op)

            def safe_execute(callback):
                def check_and_run():
                    if getattr(mw, "progress", None) and getattr(mw.progress, "busy", lambda: False)():
                        QTimer.singleShot(100, check_and_run)
                    else:
                        callback()
                check_and_run()

            def finish_download():
                self._download_dlg.close()
                self.download_button.setEnabled(True)
                success, message = result
                if success:
                    self._update_file_status()
                    showInfo(message)
                else:
                    self.file_status_label.setText(_("batch_file_not_downloaded"))
                    showInfo(message)

            safe_execute(finish_download)

        op = QueryOp(parent=self, op=background_func, success=on_success)
        _active_ops.add(op)
        op.run_in_background()

    def _on_run(self):
        """
        Run batch processing on the selected deck using batch_engine.run_batch.

        Reads the selected field names from the 3 combo boxes, validates that
        data is available, calls run_batch, and displays a summary report via
        showInfo.

        Args:
        - None (reads from UI widgets).

        Returns:
        - None
        """
        lang = self.language_combo.currentText()

        # Check data availability
        if not tatoeba_data.is_data_available(lang):
            translated = _("batch_no_data")
            if translated != "batch_no_data":
                showInfo(translated)
            else:
                showInfo("No Tatoeba data available. Please download data first.")
            return

        deck_id = self.deck_combo.currentData()
        source_field = self.source_field_combo.currentText()
        jpn_dest_field = self.jpn_field_combo.currentText()
        trans_dest_field = self.trans_field_combo.currentText()
        skip_existing = self.skip_checkbox.isChecked()

        if not source_field or not jpn_dest_field or not trans_dest_field:
            translated = _("batch_no_fields")
            if translated != "batch_no_fields":
                showInfo(translated)
            else:
                showInfo("No fields available. Please select a deck with notes.")
            return

        self.run_button.setEnabled(False)

        def background_func(col):
            return batch_engine.run_batch(
                col=col,
                deck_id=deck_id,
                lang_label=lang,
                source_field=source_field,
                jpn_dest_field=jpn_dest_field,
                trans_dest_field=trans_dest_field,
                skip_existing=skip_existing,
            )

        def on_success(result):
            _active_ops.remove(op)

            def safe_execute(callback):
                def check_and_run():
                    if getattr(mw, "progress", None) and getattr(mw.progress, "busy", lambda: False)():
                        QTimer.singleShot(100, check_and_run)
                    else:
                        callback()
                check_and_run()

            def show_report():
                # Build report
                translated_title = _("batch_report_title")
                translated_body = _("batch_report_body")

                if translated_body != "batch_report_body":
                    report = translated_body.format(
                        updated=result.updated,
                        skipped_existing=result.skipped_existing,
                        skipped_no_match=result.skipped_no_match,
                        skipped_missing=result.skipped_missing_fields,
                        errors=result.errors,
                    )
                else:
                    report = (
                        f"Batch processing complete.\n\n"
                        f"Updated: {result.updated}\n"
                        f"Skipped (already have examples): {result.skipped_existing}\n"
                        f"Skipped (no match found): {result.skipped_no_match}\n"
                        f"Skipped (missing fields): {result.skipped_missing_fields}\n"
                        f"Errors: {result.errors}"
                    )

                showInfo(report)
                self.run_button.setEnabled(True)

            safe_execute(show_report)

        op = QueryOp(parent=self, op=background_func, success=on_success)
        _active_ops.add(op)
        
        progress_msg = _("batch_running")
        if progress_msg == "batch_running":
            progress_msg = "Running batch process..."
        
        op.with_progress(progress_msg).run_in_background()


def register_batch_menu():
    """
    Register the 'Tatoeba Batch Processing' action in Tools menu.

    Adds a menu item to the Anki tools menu that opens the BatchDialog.

    Args:
    - None

    Returns:
    - None
    """
    action = QAction(_("batch_menu_action"), mw)
    action.triggered.connect(lambda: BatchDialog(mw).exec())
    mw.form.menuTools.addAction(action)

