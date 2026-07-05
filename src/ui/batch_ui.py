import logging

from aqt import mw
from aqt.utils import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, showInfo, showWarning, askUser
from aqt.qt import QComboBox, QCheckBox, QPushButton, QHBoxLayout, QAction, QTextEdit, QProgressBar
from aqt.operations import QueryOp

try:
    from PyQt6.QtCore import QTimer
except ImportError:
    from PyQt5.QtCore import QTimer

_active_ops = set()

logger = logging.getLogger(__name__)

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

        # Deck selector — top-level decks only; subdecks are picked below
        deck_row = QHBoxLayout()
        deck_label = QLabel(_("batch_deck_label"))
        self.deck_combo = QComboBox()
        self._populate_decks()
        self.deck_combo.currentIndexChanged.connect(self._on_deck_changed)
        deck_row.addWidget(deck_label)
        deck_row.addWidget(self.deck_combo)
        layout.addLayout(deck_row)

        # Subdeck selector — narrows the batch to one subtree of the chosen
        # deck; the default first entry processes the entire deck. Whatever
        # is selected is processed including its own subdecks.
        subdeck_row = QHBoxLayout()
        subdeck_label = QLabel(_("batch_subdeck_label"))
        self.subdeck_combo = QComboBox()
        self.subdeck_combo.currentIndexChanged.connect(self._populate_fields)
        subdeck_row.addWidget(subdeck_label)
        subdeck_row.addWidget(self.subdeck_combo)
        layout.addLayout(subdeck_row)

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

        # Set initial subdeck list, file status, and fields
        self._populate_subdecks()
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
        Populate the deck combo with top-level decks only.

        Reads all deck names from the current Anki collection, keeps the full
        list in self._all_decks (used by _populate_subdecks), and adds only
        decks without a "::" in their name to the deck selector.

        Args:
        - None

        Returns:
        - None
        """
        self._all_decks = []
        try:
            decks = mw.col.decks.all_names_and_ids(
                skip_empty_default=True, include_filtered=False)
            self._all_decks = [(deck.name, deck.id) for deck in decks]
            for name, deck_id in self._all_decks:
                if "::" not in name:
                    self.deck_combo.addItem(name, deck_id)
        except Exception:
            logger.exception("Failed to populate deck list")

    def _on_deck_changed(self):
        """Refresh the subdeck list and field combos after a deck change."""
        self._populate_subdecks()
        self._populate_fields()

    def _populate_subdecks(self):
        """
        Populate the subdeck combo for the currently selected top-level deck.

        The first entry ("Entire deck") carries None as data and means the
        whole deck including all subdecks. Every descendant (any depth) is
        then listed with its path relative to the root. Sets
        self._subdeck_count to the number of descendants found.

        Args:
        - None (reads from self.deck_combo and self._all_decks).

        Returns:
        - None
        """
        self._subdeck_count = 0
        # Block signals during repopulation so _populate_fields fires once
        # (from the caller), not on every addItem.
        self.subdeck_combo.blockSignals(True)
        try:
            self.subdeck_combo.clear()
            self.subdeck_combo.addItem(_("batch_subdeck_all"), None)

            root_id = self.deck_combo.currentData()
            root_name = next(
                (name for name, deck_id in self._all_decks if deck_id == root_id),
                None,
            )
            if root_name:
                prefix = root_name + "::"
                for name, deck_id in self._all_decks:
                    if name.startswith(prefix):
                        self.subdeck_combo.addItem(name[len(prefix):], deck_id)
                        self._subdeck_count += 1
            self.subdeck_combo.setEnabled(self._subdeck_count > 0)
        except Exception:
            logger.exception("Failed to populate subdeck list")
        finally:
            self.subdeck_combo.blockSignals(False)

    def _selected_deck_id(self):
        """Return the effective deck id: the chosen subdeck, or the root deck
        when 'Entire deck' is selected."""
        subdeck_id = self.subdeck_combo.currentData()
        if subdeck_id is not None:
            return subdeck_id
        return self.deck_combo.currentData()

    # ── Field population ────────────────────────────────────────────

    def _populate_fields(self):
        """
        Populate the field selector combos from the selected deck subtree.

        Collects every note type used by notes in the effective deck (chosen
        subdeck or entire deck, subdecks included) and offers the union of
        their field names, order preserved. run_batch() skips notes that lack
        the chosen fields, so offering the union is safe for mixed decks.
        If no notes are found, the combos are cleared and a warning is shown.

        Args:
        - None (reads from self.deck_combo / self.subdeck_combo).

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

        deck_id = self._selected_deck_id()
        if deck_id is None:
            return

        try:
            # Distinct note types across the whole subtree, without loading
            # every note. odid covers cards temporarily in a filtered deck.
            dids = list(mw.col.decks.deck_and_child_ids(deck_id))
            placeholders = ",".join("?" * len(dids))
            mids = mw.col.db.list(
                "select distinct mid from notes where id in "
                f"(select nid from cards where did in ({placeholders}) "
                f"or odid in ({placeholders}))",
                *dids, *dids,
            )
            if not mids:
                self.deck_status_label.setText(_("batch_no_fields"))
                self.deck_status_label.show()
                return

            field_names: list = []
            for mid in mids:
                note_type = mw.col.models.get(mid)
                if not note_type:
                    continue
                for fld in note_type["flds"]:
                    if fld["name"] not in field_names:
                        field_names.append(fld["name"])
            self.current_field_names = field_names

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
            logger.exception("Failed to populate field selectors")

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
        lang = self.language_combo.currentData()
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
                logger.warning("Could not read pair count from metadata", exc_info=True)

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

        lang = self.language_combo.currentData()
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

        lang = self.language_combo.currentData()

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
        lang = self.language_combo.currentData()

        # Check data availability
        if not tatoeba_data.is_data_available(lang):
            translated = _("batch_no_data")
            if translated != "batch_no_data":
                showInfo(translated)
            else:
                showInfo("No Tatoeba data available. Please download data first.")
            return

        deck_id = self._selected_deck_id()
        source_field = self.source_field_combo.currentText()
        skip_existing = self.skip_checkbox.isChecked()

        # Root deck chosen with "Entire deck" while subdecks exist: make the
        # scope explicit before writing to every note in the tree.
        if self.subdeck_combo.currentData() is None and getattr(self, "_subdeck_count", 0) > 0:
            try:
                note_count = len(mw.col.find_notes(
                    batch_engine.build_deck_search(mw.col, deck_id)))
            except Exception:
                note_count = 0
                logger.warning("Could not count notes for confirmation dialog", exc_info=True)
            confirm_msg = _("batch_confirm_root_deck")
            if confirm_msg == "batch_confirm_root_deck":
                confirm_msg = (
                    "You selected the deck '{deck}' without choosing a subdeck.\n"
                    "All {subdecks} subdeck(s) ({notes} notes) will be processed.\n\n"
                    "Continue?"
                )
            confirm_msg = confirm_msg.format(
                deck=self.deck_combo.currentText(),
                subdecks=self._subdeck_count,
                notes=note_count,
            )
            if not askUser(confirm_msg, parent=self):
                return

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
                        audio_errors=result.audio_errors,
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
                        f"Audio skipped (no recording): {result.audio_skipped}\n"
                        f"Audio errors: {result.audio_errors}"
                    )

                showInfo(report)
                self.run_button.setEnabled(True)

            if not result.pending_audio:
                safe_execute(show_report)
                return

            # Audio downloads run in a second background op so the network
            # I/O never blocks the UI; only the fast register step (media
            # add_file + note updates) runs on the main thread afterwards.
            total_audio = len(result.pending_audio)
            audio_msg = _("batch_audio_progress")
            if audio_msg == "batch_audio_progress":
                audio_msg = "Downloading audio {current}/{total}..."

            def audio_progress(current, total):
                label = audio_msg.format(current=current, total=total)
                mw.taskman.run_on_main(lambda: mw.progress.update(label=label))

            def audio_background(col):
                return batch_engine.download_pending_audio(
                    result, col, progress_cb=audio_progress
                )

            def on_audio_success(items):
                _active_ops.discard(audio_op)
                batch_engine.register_pending_audio(items, result, mw.col)
                safe_execute(show_report)

            def on_audio_failure(exc):
                _active_ops.discard(audio_op)
                # download_pending_audio contains per-item errors, so this only
                # fires on catastrophic failure — count the whole batch as
                # errored and still show the report.
                result.audio_errors += total_audio
                result.pending_audio.clear()
                safe_execute(show_report)

            audio_op = QueryOp(
                parent=self, op=audio_background, success=on_audio_success
            ).failure(on_audio_failure)
            _active_ops.add(audio_op)
            audio_op.with_progress(
                audio_msg.format(current=1, total=total_audio)
            ).run_in_background()

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

