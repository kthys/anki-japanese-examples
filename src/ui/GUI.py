from aqt import gui_hooks, mw
from aqt.utils import Qt, QDialog, QVBoxLayout, QLabel, QListWidget, QDialogButtonBox, showInfo
from aqt.qt import QCheckBox, QLineEdit, QPushButton, QFormLayout, QHBoxLayout, QTimer
import os, html, logging

logger = logging.getLogger(__name__)

try:
    from ..core.japanese_examples import find_japanese_sentence
except ImportError:
    from src.core.japanese_examples import find_japanese_sentence

try:
    from ..core.audio_fetcher import fetch_audio_to_temp, register_audio_file
except ImportError:
    try:
        from src.core.audio_fetcher import fetch_audio_to_temp, register_audio_file
    except ImportError:
        fetch_audio_to_temp = None
        register_audio_file = None

# Try to import QueryOp for background operations (Anki 2.1.50+)
try:
    from aqt.operations import QueryOp
except ImportError:
    QueryOp = None

# Global set to keep references to active operations to prevent premature garbage collection
_active_ops = set()


def get_plugin_dir_path():
    """
    Determine and return the path of the plugin directory.

    Returns:
    - The absolute string path to the plugin directory.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from ..utils.i18n import _
except ImportError:
    from src.utils.i18n import _

def create_custom_dialog(message, choices, start_row=0, parent=None, with_checkbox=False, checkbox_text=""):
    """
    This function creates a custom dialog with a selection list
    and OK/Cancel buttons. It is based on code from Anki
    open-source project.

    Args:
    - message (str): The label message to display at the top of the dialog.
    - choices (list): A list of strings representing the options for the selection list.
    - start_row (int): The index of the initially selected row. Default is 0.
    - parent (QWidget): The parent window for the dialog. Default is None.
    - with_checkbox (bool): Whether to include a checkbox in the dialog. Default is False.
    - checkbox_text (str): The text description for the checkbox, if included. Default is "".

    Returns:
    - If with_checkbox is False: Returns the integer index of the selected row, or None if the dialog is cancelled.
    - If with_checkbox is True: Returns a tuple containing the integer index of the selected row and a boolean indicating if the checkbox is checked, or None if the dialog is cancelled.
    """

    # get the active window of the application if no parent is provided
    if parent is None:
        parent_window = mw.app.activeWindow()
    else:
        parent_window = parent

    # initialize a new dialog
    dialog = QDialog(parent_window)

    # set window modality to WindowModal
    dialog.setWindowModality(Qt.WindowModality.WindowModal)


    # create and set a layout for the dialog
    layout = QVBoxLayout()
    dialog.setLayout(layout)

    # create a label with the provided message
    text = QLabel(message)
    layout.addWidget(text)

    # create a list widget and add the provided choices
    selection_list = QListWidget()
    selection_list.addItems(choices)
    selection_list.setCurrentRow(start_row)
    layout.addWidget(selection_list)

    checkbox = None
    if with_checkbox:
        h_layout = QHBoxLayout()
        checkbox = QCheckBox(checkbox_text)
        h_layout.addWidget(checkbox)

        info_label = QLabel("ⓘ")
        info_label.setToolTip(_("deck_preference_info_tooltip"))
        h_layout.addWidget(info_label)

        h_layout.addStretch()
        layout.addLayout(h_layout)

    # set the standard buttons
    standard_buttons = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel

    # create a button box with the standard buttons
    button_box = QDialogButtonBox(standard_buttons)
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)

    # execute the dialog and get the result
    result = dialog.exec()  # 1 if Ok, 0 if Cancel or window closed

    # return None if the result is 0 (Cancel or window closed)
    if result == 0:
        return None

    # return the current row of the selection list
    if with_checkbox:
        return (selection_list.currentRow(), checkbox.isChecked())
    else:
        return selection_list.currentRow()


def create_multi_selection_dialog(message, choices, parent=None, with_checkbox=False, checkbox_text="", max_selections=None):
    """
    Creates a custom dialog with a multi-selection list
    and OK/Cancel buttons.

    Returns:
    - If with_checkbox is False: Returns a list of integer indices of the selected rows, or None if cancelled.
    - If with_checkbox is True: Returns a tuple (list of indices, bool checkbox_checked), or None if cancelled.
    """
    if parent is None:
        parent_window = mw.app.activeWindow()
    else:
        parent_window = parent

    dialog = QDialog(parent_window)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)

    layout = QVBoxLayout()
    dialog.setLayout(layout)

    text = QLabel(message)
    layout.addWidget(text)

    selection_list = QListWidget()
    selection_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
    selection_list.addItems(choices)
    layout.addWidget(selection_list)

    checkbox = None
    if with_checkbox:
        h_layout = QHBoxLayout()
        checkbox = QCheckBox(checkbox_text)
        h_layout.addWidget(checkbox)

        info_label = QLabel("ⓘ")
        info_label.setToolTip(_("deck_preference_info_tooltip"))
        h_layout.addWidget(info_label)

        h_layout.addStretch()
        layout.addLayout(h_layout)

    standard_buttons = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    button_box = QDialogButtonBox(standard_buttons)
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)

    while True:
        result = dialog.exec()
        if result == 0:
            return None
        
        selected_indices = [item.row() for item in selection_list.selectedIndexes()]
        if max_selections is not None and len(selected_indices) > max_selections:
            # Let the user know they selected too many
            showInfo(f"Please select up to {max_selections} sentences. Your note schema only supports inserting {max_selections} examples.")
            continue
        
        break

    if with_checkbox:
        return (selected_indices, checkbox.isChecked())
    else:
        return selected_indices



def get_current_deck_id(editor):
    """
    Get the deck ID of the current note or selected deck.

    Args:
    - editor (Editor): The Anki editor instance currently in use.

    Returns:
    - The integer deck ID if found, otherwise None.
    """
    # Check if we are in Add Cards dialog
    if hasattr(editor.parentWindow, 'deckChooser'):
        return editor.parentWindow.deckChooser.selectedId()

    # Check if we are in Browser
    if editor.note:
        cards = editor.note.cards()
        if cards:
            return cards[0].did

    return None

def add_example_manually_dialog(editor):
    """
    Dialog for adding an example of sentence based on japanese word present in the selected field.
    The target fields are defined in the config file.

    Args:
    - editor (Editor): The Anki editor instance triggered the dialog.

    Returns:
    - None
    """

    if editor.web.editor.currentField is None or editor.web.editor.currentField == '':
        showInfo(_('select_field_to_use'))
        return

    japanese_word = editor.note.fields[editor.web.editor.currentField]

    if not japanese_word or not japanese_word.strip():
        showInfo(_("no_japanese_sentence_found").format(word=japanese_word))
        return

    # Check for deck preferences
    deck_id = get_current_deck_id(editor)
    addon_name = __name__.split('.')[0]
    config = mw.addonManager.getConfig(addon_name) or {}
    deck_prefs = config.get('deck_preferences', {})

    target_lang = None
    if deck_id and str(deck_id) in deck_prefs:
        target_lang = deck_prefs[str(deck_id)]

    if not target_lang:
        # User chooses where to get the examples from
        result = create_custom_dialog(
            _("select_translation_language_dialog"),
            ['English', 'French'],
            with_checkbox=(deck_id is not None),
            checkbox_text=_('use_as_default_for_deck')
        )

        if result is None:
            return None

        if deck_id is not None:
             source_index, save_default = result
        else:
             source_index = result
             save_default = False

        # Determine target language code
        if source_index == 0:
            target_lang = 'eng'
        elif source_index == 1:
            target_lang = 'fra'
        else:
            # Should not happen given the dialog choices
            return

        if save_default and deck_id:
            deck_prefs[str(deck_id)] = target_lang
            config['deck_preferences'] = deck_prefs
            mw.addonManager.writeConfig(addon_name, config)

    # Define op variable to be accessible in on_success
    op = None

    def on_success(examples_sentences):
        # Cleanup op reference to avoid memory leak
        if op:
            _active_ops.discard(op)

        # Function to safely execute a callback only after the progress dialog has closed
        def safe_execute(callback):
            try:
                if mw.progress.busy():
                    # If busy, try again in 100ms
                    QTimer.singleShot(100, lambda: safe_execute(callback))
                    return
            except AttributeError:
                # In case mw.progress is not available (very old versions)
                pass

            # Execute the actual logic
            callback()

        # Define the logic for different outcomes
        def handle_result():
            if examples_sentences is None:
                showInfo(_('example_not_found'))
                return

            elif isinstance(examples_sentences, str):
                showInfo(examples_sentences)
                return

            else:
                try:
                    examples = [
                        ("🔊 " if example.get('has_audio') else "") + f"{example['jp_sentence']}\n{example['tr_sentence']}"
                        for example in examples_sentences
                    ]
                except TypeError:
                    showInfo(_('example_not_found_check_encoding'))
                    return

                def show_result_dialog():
                    # Get the current note opened in the editor
                    note = editor.note
                    
                    # Get the field names
                    note_type = note.note_type()
                    fields = note_type['flds']
                    field_names = [field['name'] for field in fields]
                    
                    # Use dynamic config for field names
                    current_config = mw.addonManager.getConfig(addon_name) or {}
                    
                    jp_f = current_config.get("japaneseDstField", "ExampleJapanese")
                    tr_f = current_config.get("translationDstField", "ExampleTranslated")

                    valid_field_pairs = []
                    if jp_f in field_names and tr_f in field_names:
                        valid_field_pairs.append((field_names.index(jp_f), field_names.index(tr_f)))
                    
                    if not valid_field_pairs:
                        missing = []
                        if jp_f not in field_names:
                            missing.append(f"'{jp_f}' (Japanese)")
                        if tr_f not in field_names:
                            missing.append(f"'{tr_f}' (Translation)")
                        available = ", ".join(field_names)
                        showInfo(
                            _("no_valid_dst_fields").format(
                                missing=", ".join(missing),
                                available=available
                            )
                        )
                        return

                    # User chooses which example to add
                    selected_index = create_custom_dialog(
                        _('select_sentence_dialog'),
                        examples,
                        parent=editor.parentWindow
                    )

                    if selected_index is None:
                        showInfo(_('no_example_selected'))
                        return

                    chosen_example = examples_sentences[selected_index]
                    jp_sentence = chosen_example['jp_sentence']
                    tr_sentence = chosen_example['tr_sentence']

                    jp_field_index, en_field_index = valid_field_pairs[0]

                    # Set the value of the field
                    note.fields[jp_field_index] = html.escape(jp_sentence)
                    note.fields[en_field_index] = html.escape(tr_sentence)

                    # Save the changes to the note if the note already exists
                    if note.id != 0:
                        mw.col.update_note(note)

                    # Update the editor to show the changes
                    editor.loadNote()

                    # Audio write path — the slow network fetch runs in the
                    # background op; only col.media.add_file() and the note
                    # update happen on the main thread in the success callback.
                    audio_f = current_config.get("audioDstField", "ExampleAudio")
                    if audio_f and audio_f in field_names and fetch_audio_to_temp is not None:
                        audio_field_index = field_names.index(audio_f)
                        chosen_jpn_id = chosen_example.get('jpn_id')
                        if chosen_jpn_id is not None:
                            audio_op = None

                            def audio_background(col):
                                expected_fname = f"{chosen_jpn_id}.mp3"
                                if col.media.have(expected_fname):
                                    return ("exists", expected_fname)
                                tmp_path = fetch_audio_to_temp(chosen_jpn_id)
                                if tmp_path is None:
                                    return ("no_audio", None)
                                return ("fetched", tmp_path)

                            def on_audio_success(outcome):
                                if audio_op:
                                    _active_ops.discard(audio_op)
                                status, payload = outcome
                                if status == "no_audio":
                                    return
                                try:
                                    if status == "exists":
                                        fname = payload
                                    else:
                                        fname = register_audio_file(payload, mw.col)
                                    note.fields[audio_field_index] = f"[sound:{fname}]"
                                    if note.id != 0:
                                        mw.col.update_note(note)
                                    editor.loadNote()
                                except Exception:
                                    # No error dialog — audio is best-effort — but keep a trace
                                    logger.exception(
                                        "Failed to register audio for sentence %s", chosen_jpn_id)

                            def on_audio_failure(exc):
                                if audio_op:
                                    _active_ops.discard(audio_op)
                                # No error dialog — audio is best-effort — but keep a trace
                                logger.warning(
                                    "Audio download failed for sentence %s: %s", chosen_jpn_id, exc)

                            if QueryOp:
                                audio_op = QueryOp(
                                    parent=editor.parentWindow,
                                    op=audio_background,
                                    success=on_audio_success
                                ).failure(on_audio_failure)
                                _active_ops.add(audio_op)
                                audio_op.run_in_background()
                            else:
                                # Fallback for older Anki: blocking call, as before
                                try:
                                    on_audio_success(audio_background(mw.col))
                                except Exception:
                                    logger.exception(
                                        "Audio fetch failed for sentence %s", chosen_jpn_id)

                show_result_dialog()

        # Schedule the execution with initial delay
        QTimer.singleShot(200, lambda: safe_execute(handle_result))

    # Use QueryOp if available (Anki 2.1.50+), otherwise fall back to blocking call
    if QueryOp:
        # Pass editor.parentWindow as parent so the progress dialog attaches to the correct window
        # (Browser/Add window) instead of the main window. This ensures focus returns correctly when closing.
        op = QueryOp(
            parent=editor.parentWindow,
            op=lambda col: find_japanese_sentence(japanese_word, target_lang),
            success=on_success
        )
        _active_ops.add(op)
        op.with_progress(_("searching")).run_in_background()
    else:
        # Fallback for older versions: blocking call
        examples_sentences = find_japanese_sentence(japanese_word, target_lang)
        on_success(examples_sentences)

def add_examples_buttons(buttons, editor):
    """
    Add buttons to editor menu.

    Args:
    - buttons (list): The list of existing buttons in the editor.
    - editor (Editor): The Anki editor instance to which the buttons are added.

    Returns:
    - None
    """

    # manual mode
    icon_path_manual = os.path.join(get_plugin_dir_path(), 'editor_icon_manual.png')
    manual_button = editor.addButton(
        icon_path_manual,
        'manualexample',
        add_example_manually_dialog,
        tip=_('add_example_manually_tip')
    )

    buttons.append(manual_button)

# Link buttons to Anki
gui_hooks.editor_did_init_buttons.append(add_examples_buttons)

