from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, showInfo, showWarning
from aqt.qt import QComboBox, QCheckBox, QPushButton, QHBoxLayout, QAction, QTextEdit, QProgressBar
from aqt.operations import QueryOp

try:
    from PyQt6.QtCore import QTimer
except ImportError:
    from PyQt5.QtCore import QTimer

_active_ops = set()

try:
    from ..utils.i18n import _
except ImportError:
    try:
        from src.utils.i18n import _
    except Exception:
        _ = lambda x: x

try:
    from ..core import tatoeba_data
except ImportError:
    from src.core import tatoeba_data

try:
    from ..core import batch_engine
except ImportError:
    from src.core import batch_engine


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
        lang_label = QLabel(_("batch_language_label"))
        self.language_combo = QComboBox()
        self.language_combo.addItem(_("batch_language_english"), "English")
        self.language_combo.addItem(_("batch_language_french"), "French")
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
        deck_label = QLabel(_("batch_deck_label"))
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
        source_label = QLabel(_("batch_source_field_label"))
        self.source_field_combo = QComboBox()
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_field_combo)
        layout.addLayout(source_row)

        self.jpn_field_combos = []
        self.trans_field_combos = []
        self.audio_field_combos = []
        self.field_pair_widgets = []

        # Wrap everything in an HBox
        mapping_layout = QHBoxLayout()
        layout.addLayout(mapping_layout)

        # Left side: +/- buttons
        btn_layout = QVBoxLayout()
        
        self.plus_btn = QPushButton("+")
        self.plus_btn.setFixedWidth(30)
        self.plus_btn.setStyleSheet("color: #1a7f37; font-weight: bold; font-size: 16px;")
        self.plus_btn.clicked.connect(self._add_field_pair)
        btn_layout.addWidget(self.plus_btn)
        
        self.minus_btn = QPushButton("-")
        self.minus_btn.setFixedWidth(30)
        self.minus_btn.setStyleSheet("color: #d1242f; font-weight: bold; font-size: 16px;")
        self.minus_btn.clicked.connect(self._remove_last_field_pair)
        btn_layout.addWidget(self.minus_btn)
        
        btn_layout.addStretch()
        mapping_layout.addLayout(btn_layout)

        # Right side: container for fields
        self.fields_container = QVBoxLayout()
        mapping_layout.addLayout(self.fields_container)

        # Added dynamic row logic
        self._add_field_pair()

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

    def _add_field_pair(self):
        if hasattr(self, 'field_pair_widgets') and len(self.field_pair_widgets) >= 3:
            return
            
        from aqt.qt import QWidget, QFrame
        row_widget = QWidget()
        row_layout = QVBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 10)
        row_widget.setLayout(row_layout)
        
        idx = len(self.field_pair_widgets) + 1
        
        # Japanese row
        jpn_layout = QHBoxLayout()
        jpn_label = QLabel(_(f"batch_jpn_field_label_{idx}"))
        jpn_combo = QComboBox()
        jpn_combo.currentIndexChanged.connect(self._validate_run_button)
        jpn_layout.addWidget(jpn_label)
        jpn_layout.addWidget(jpn_combo)
        row_layout.addLayout(jpn_layout)
        
        # Translation row
        trans_layout = QHBoxLayout()
        trans_label = QLabel(_(f"batch_trans_field_label_{idx}"))
        trans_combo = QComboBox()
        trans_combo.currentIndexChanged.connect(self._validate_run_button)
        trans_layout.addWidget(trans_label)
        trans_layout.addWidget(trans_combo)
        row_layout.addLayout(trans_layout)
        
        # Separator line if not the first
        if idx > 1:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)
            # Insert at the top of this row widget to separate from previous
            row_layout.insertWidget(0, line)
            
        self.fields_container.addWidget(row_widget)
        
        # Audio row — follows the exact same QHBoxLayout pattern as jpn and trans rows
        # NOTE: audio combo does NOT connect to _validate_run_button — audio is optional
        audio_layout = QHBoxLayout()
        audio_label = QLabel(_(f"batch_audio_field_label_{idx}"))
        audio_combo = QComboBox()
        audio_layout.addWidget(audio_label)
        audio_layout.addWidget(audio_combo)
        row_layout.addLayout(audio_layout)

        self.field_pair_widgets.append(row_widget)
        self.jpn_field_combos.append(jpn_combo)
        self.trans_field_combos.append(trans_combo)
        self.audio_field_combos.append(audio_combo)

        # Populate combinations if we already have field names
        jpn_combo.addItem(_("batch_field_none"))
        trans_combo.addItem(_("batch_field_none"))
        audio_combo.addItem(_("batch_field_none"))
        if hasattr(self, 'current_field_names') and self.current_field_names:
            jpn_combo.addItems(self.current_field_names)
            trans_combo.addItems(self.current_field_names)
            audio_combo.addItems(self.current_field_names)

        self._update_buttons()

    def _remove_last_field_pair(self):
        if len(self.field_pair_widgets) <= 1:
            return
            
        row_widget = self.field_pair_widgets.pop()
        self.fields_container.removeWidget(row_widget)
        row_widget.deleteLater()
        
        self.jpn_field_combos.pop()
        self.trans_field_combos.pop()
        self.audio_field_combos.pop()

        self._update_buttons()

        # Ask the layout to reconsider its size, then shrink window
        try:
            self.layout().activate()
            self.adjustSize()
        except Exception:
            pass

    def _update_buttons(self):
        self.minus_btn.setEnabled(len(self.field_pair_widgets) > 1)
        self.plus_btn.setEnabled(len(self.field_pair_widgets) < 3)
        self._validate_run_button()

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
        for jpn_combo in getattr(self, "jpn_field_combos", []):
            jpn_combo.clear()
        for trans_combo in getattr(self, "trans_field_combos", []):
            trans_combo.clear()
        for audio_combo in getattr(self, "audio_field_combos", []):
            audio_combo.clear()
        self.deck_status_label.hide()

        deck_id = self.deck_combo.currentData()
        if deck_id is None:
            return

        try:
            note_ids = mw.col.find_notes(f"did:{deck_id}")
            if not note_ids:
                self.deck_status_label.setText(_("batch_no_fields"))
                self.deck_status_label.show()
                return

            note = mw.col.get_note(note_ids[0])
            self.current_field_names = [fld["name"] for fld in note.note_type()["flds"]]

            self.source_field_combo.addItems(self.current_field_names)
            
            for jpn_combo in self.jpn_field_combos:
                jpn_combo.addItem(_("batch_field_none"))
                jpn_combo.addItems(self.current_field_names)

            for trans_combo in self.trans_field_combos:
                trans_combo.addItem(_("batch_field_none"))
                trans_combo.addItems(self.current_field_names)

            for audio_combo in self.audio_field_combos:
                audio_combo.addItem(_("batch_field_none"))
                audio_combo.addItems(self.current_field_names)
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
            self._validate_run_button()
        else:
            self.file_status_label.setText(_("batch_file_not_downloaded"))
            self.run_button.setEnabled(False)

    def _validate_run_button(self):
        """Enable run button only if valid pairs are selected and file exists."""
        if not hasattr(self, 'run_button'):
            return

        lang = self.language_combo.currentText()
        if not tatoeba_data.is_data_available(lang):
            self.run_button.setEnabled(False)
            return

        # Check if all pairs have exactly "none" in both, or something valid in both
        has_at_least_one_valid_pair = False
        
        for jpn_combo, trans_combo in zip(self.jpn_field_combos, self.trans_field_combos):
            jpn_dest = jpn_combo.currentText()
            trans_dest = trans_combo.currentText()
            
            jpn_set = jpn_dest and jpn_dest != _("batch_field_none")
            trans_set = trans_dest and trans_dest != _("batch_field_none")
            
            if jpn_set and trans_set:
                has_at_least_one_valid_pair = True
            elif (jpn_set and not trans_set) or (not jpn_set and trans_set):
                self.run_button.setEnabled(False)
                return
                
        self.run_button.setEnabled(has_at_least_one_valid_pair)

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
            _active_ops.discard(op)

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

        def on_failure(exc):
            _active_ops.discard(op)
            showWarning(f"Operation failed: {exc}")

        op = QueryOp(parent=self, op=background_func, success=on_success).failure(on_failure)
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
        skip_existing = self.skip_checkbox.isChecked()

        dest_field_pairs = []
        for jpn_combo, trans_combo, audio_combo in zip(
                self.jpn_field_combos, self.trans_field_combos, self.audio_field_combos):
            jpn_dest = jpn_combo.currentText()
            trans_dest = trans_combo.currentText()
            audio_dest = audio_combo.currentText()

            jpn_set = jpn_dest and jpn_dest != _("batch_field_none")
            trans_set = trans_dest and trans_dest != _("batch_field_none")
            audio_set = audio_dest and audio_dest != _("batch_field_none")

            if jpn_set and trans_set:
                dest_field_pairs.append((jpn_dest, trans_dest, audio_dest if audio_set else None))

        if not source_field or not dest_field_pairs:
            translated = _("batch_no_fields")
            if translated != "batch_no_fields":
                showInfo(translated)
            else:
                showInfo("No fields available or no valid destination pairs selected.")
            return

        self.run_button.setEnabled(False)

        def background_func(col):
            return batch_engine.run_batch(
                col=col,
                deck_id=deck_id,
                lang_label=lang,
                source_field=source_field,
                dest_field_pairs=dest_field_pairs,
                skip_existing=skip_existing,
            )

        def on_success(result):
            _active_ops.discard(op)

            def safe_execute(callback):
                def check_and_run():
                    if getattr(mw, "progress", None) and getattr(mw.progress, "busy", lambda: False)():
                        QTimer.singleShot(100, check_and_run)
                    else:
                        callback()
                check_and_run()

            batch_engine.process_pending_audio(result, mw.col)

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
                        audio_added=result.audio_added,
                        audio_skipped=result.audio_skipped,
                    )
                else:
                    report = (
                        f"Batch processing complete.\n\n"
                        f"Updated: {result.updated}\n"
                        f"Skipped (already have examples): {result.skipped_existing}\n"
                        f"Skipped (no match found): {result.skipped_no_match}\n"
                        f"Skipped (missing fields): {result.skipped_missing_fields}\n"
                        f"Errors: {result.errors}\n"
                        f"Audio added: {result.audio_added}\n"
                        f"Audio skipped (no recording): {result.audio_skipped}"
                    )

                showInfo(report)
                self.run_button.setEnabled(True)

            safe_execute(show_report)

        def on_failure(exc):
            _active_ops.discard(op)
            showWarning(f"Operation failed: {exc}")

        op = QueryOp(parent=self, op=background_func, success=on_success).failure(on_failure)
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

