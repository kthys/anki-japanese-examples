from aqt import gui_hooks, mw
from aqt.utils import Qt, QDialog, QVBoxLayout, QLabel, QListWidget, QDialogButtonBox, showInfo
from aqt.qt import QCheckBox, QHBoxLayout, QTimer
import os, html, logging

logger = logging.getLogger(__name__)

try:
    from ..core.japanese_examples import find_japanese_sentence
except ImportError:
    from src.core.japanese_examples import find_japanese_sentence

try:
    from ..core.languages import get_codes, get_localized_name
except ImportError:
    try:
        from src.core.languages import get_codes, get_localized_name
    except ImportError:
        # Fallback: keep the pre-registry two-language behavior
        get_codes = lambda: ["eng", "fra"]
        get_localized_name = lambda code: code

try:
    from ..core.audio_fetcher import (
        resolve_audio,
        register_audio_file,
        cleanup_temp_audio,
    )
except ImportError:
    try:
        from src.core.audio_fetcher import (
            resolve_audio,
            register_audio_file,
            cleanup_temp_audio,
        )
    except ImportError:
        resolve_audio = None
        register_audio_file = None
        cleanup_temp_audio = None

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


def get_note_field_names(note):
    """
    Return the current note type's field names, or an empty list when the note
    type metadata is unavailable.

    Args:
    - note (Note): The note whose note type field names should be read.

    Returns:
    - A list of field name strings (possibly empty).
    """
    try:
        return [f['name'] for f in note.note_type()['flds']]
    except (TypeError, KeyError, AttributeError):
        return []


def find_dst_field_indices(field_names, jp_field, tr_field):
    """
    Resolve the configured Japanese/Translation destination fields to indices.

    Args:
    - field_names (list): Field names of the current note type.
    - jp_field (str): Configured Japanese example destination field name.
    - tr_field (str): Configured translated example destination field name.

    Returns:
    - A (japanese_index, translation_index) tuple when both fields are present,
      otherwise None.
    """
    if jp_field in field_names and tr_field in field_names:
        return field_names.index(jp_field), field_names.index(tr_field)
    return None


def missing_dst_fields_message(field_names, jp_field, tr_field):
    """
    Build the localized "no_valid_dst_fields" message for whichever required
    destination field is missing from the note type.

    Reuses the exact placeholder contract ({missing} / {available}) already
    present in every locale file, so no translations need updating.
    """
    missing = []
    if jp_field not in field_names:
        missing.append(f"'{jp_field}' (Japanese)")
    if tr_field not in field_names:
        missing.append(f"'{tr_field}' (Translation)")
    return _("no_valid_dst_fields").format(
        missing=", ".join(missing),
        available=", ".join(field_names),
    )


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

    addon_name = __name__.split('.')[0]
    config = mw.addonManager.getConfig(addon_name) or {}

    # Fail fast when the configured destination fields are absent from this
    # note type: no point asking for a language or contacting Tatoeba.
    field_names = get_note_field_names(editor.note)
    jp_f = config.get("japaneseDstField", "ExampleJapanese")
    tr_f = config.get("translationDstField", "ExampleTranslated")
    if find_dst_field_indices(field_names, jp_f, tr_f) is None:
        showInfo(missing_dst_fields_message(field_names, jp_f, tr_f))
        return

    # Check for deck preferences
    deck_id = get_current_deck_id(editor)
    deck_prefs = config.get('deck_preferences', {})

    target_lang = None
    if deck_id and str(deck_id) in deck_prefs:
        target_lang = deck_prefs[str(deck_id)]

    if not target_lang:
        # User chooses where to get the examples from
        result = create_custom_dialog(
            _("select_translation_language_dialog"),
            [get_localized_name(code) for code in get_codes()],
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

        # Determine target language code from the registry
        codes = get_codes()
        if 0 <= source_index < len(codes):
            target_lang = codes[source_index]
        else:
            # Should not happen given the dialog choices
            return

        if save_default and deck_id:
            deck_prefs[str(deck_id)] = target_lang
            config['deck_preferences'] = deck_prefs
            mw.addonManager.writeConfig(addon_name, config)

    # How many sentence options to fetch and show in the selection dialog
    try:
        max_options = int(config.get("maxSentenceOptions", 30))
    except (TypeError, ValueError):
        max_options = 30
    if max_options < 1:
        max_options = 30

    # Audio pre-check is only worthwhile when audio will actually be written:
    # the audio destination field must be configured and present on the note.
    audio_dst = config.get("audioDstField", "ExampleAudio")
    audio_active = (
        bool(audio_dst) and audio_dst in field_names and resolve_audio is not None
    )

    def audio_cache_for(col, sentences):
        """Probe audio-bearing sentences; return {jpn_id: ResolvedAudio}.

        BACKGROUND-SAFE. A sentence whose audio 403s (author restricts reuse) or
        404s gets no entry, so the selection dialog shows it without the speaker
        icon and treats it as an example without audio. Files fetched here are
        cached so the chosen sentence registers without a second download;
        callers must clean up the unused ones with discard_cached_audio().
        """
        cache = {}
        if not audio_active or not isinstance(sentences, list):
            return cache
        for example in sentences:
            if not example.get('has_audio'):
                continue
            jpn_id = example.get('jpn_id')
            if not jpn_id or jpn_id in cache:
                continue
            try:
                resolved = resolve_audio(jpn_id, [], col)
            except Exception:
                logger.warning("Audio pre-check failed for sentence %s", jpn_id, exc_info=True)
                resolved = None
            if resolved is not None:
                cache[jpn_id] = resolved
        return cache

    def discard_cached_audio(cache, keep_jpn_id=None):
        """Delete cached temp files the selection did not keep."""
        if cleanup_temp_audio is None:
            return
        for jpn_id, resolved in cache.items():
            if jpn_id == keep_jpn_id or resolved.source != "temp":
                continue
            try:
                cleanup_temp_audio(resolved.payload)
            except Exception:
                logger.warning("Could not clean up temp audio for %s", jpn_id, exc_info=True)

    # Define op variable to be accessible in on_success
    op = None

    def on_success(outcome):
        # Cleanup op reference to avoid memory leak
        if op:
            _active_ops.discard(op)

        # The op returns (examples, audio_cache); tolerate a bare examples value
        # (older callers / tests) by treating it as having no cached audio.
        if (isinstance(outcome, tuple) and len(outcome) == 2
                and isinstance(outcome[1], dict)):
            examples_sentences, audio_cache = outcome
        else:
            examples_sentences, audio_cache = outcome, {}

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
                def has_audio_icon(example):
                    # With audio probing on, the icon means "audio is really
                    # available right now"; otherwise keep the API's hint.
                    if audio_active:
                        return example.get('jpn_id') in audio_cache
                    return bool(example.get('has_audio'))

                try:
                    examples = [
                        ("🔊 " if has_audio_icon(example) else "")
                        + f"{example['jp_sentence']}\n{example['tr_sentence']}"
                        for example in examples_sentences
                    ]
                except TypeError:
                    showInfo(_('example_not_found_check_encoding'))
                    return

                def show_result_dialog():
                    # Get the current note opened in the editor
                    note = editor.note

                    # Get the field names
                    field_names = get_note_field_names(note)

                    # Use dynamic config for field names
                    current_config = mw.addonManager.getConfig(addon_name) or {}

                    jp_f = current_config.get("japaneseDstField", "ExampleJapanese")
                    tr_f = current_config.get("translationDstField", "ExampleTranslated")

                    # Safety net: the early guard already checked this at click
                    # time, but re-check in case the note type changed meanwhile.
                    indices = find_dst_field_indices(field_names, jp_f, tr_f)
                    if indices is None:
                        discard_cached_audio(audio_cache)
                        showInfo(missing_dst_fields_message(field_names, jp_f, tr_f))
                        return

                    # User chooses which example to add
                    selected_index = create_custom_dialog(
                        _('select_sentence_dialog'),
                        examples,
                        parent=editor.parentWindow
                    )

                    if selected_index is None:
                        discard_cached_audio(audio_cache)
                        showInfo(_('no_example_selected'))
                        return

                    chosen_example = examples_sentences[selected_index]
                    jp_sentence = chosen_example['jp_sentence']
                    tr_sentence = chosen_example['tr_sentence']

                    jp_field_index, en_field_index = indices

                    # Audio write path: the pre-check already fetched the chosen
                    # sentence's audio (or found it in col.media), so registering
                    # it is a local copy — safe on the main thread. Unused cached
                    # files are cleaned up now. A 403/404 pick simply has no audio.
                    audio_f = current_config.get("audioDstField", "ExampleAudio")
                    chosen_jpn_id = chosen_example.get('jpn_id')
                    chosen_audio = audio_cache.get(chosen_jpn_id) if chosen_jpn_id else None
                    if not (audio_f and audio_f in field_names and chosen_audio is not None):
                        chosen_audio = None
                    discard_cached_audio(
                        audio_cache,
                        keep_jpn_id=chosen_jpn_id if chosen_audio else None)

                    # Set the value of the field
                    note.fields[jp_field_index] = html.escape(jp_sentence)
                    note.fields[en_field_index] = html.escape(tr_sentence)

                    if chosen_audio is not None:
                        try:
                            if chosen_audio.source == "media":
                                fname = chosen_audio.payload
                            else:
                                fname = register_audio_file(chosen_audio.payload, mw.col)
                            note.fields[field_names.index(audio_f)] = f"[sound:{fname}]"
                        except Exception:
                            # No error dialog — audio is best-effort — but keep a trace
                            logger.exception(
                                "Failed to register audio for sentence %s", chosen_jpn_id)

                    # Save the changes to the note if the note already exists
                    if note.id != 0:
                        mw.col.update_note(note)

                    # Update the editor to show the changes
                    editor.loadNote()

                show_result_dialog()

        # Schedule the execution with initial delay
        QTimer.singleShot(200, lambda: safe_execute(handle_result))

    # Search runs in the background; the audio pre-check rides along so the
    # selection dialog can show accurate speaker icons without a second op.
    def search_and_probe(col):
        sentences = find_japanese_sentence(japanese_word, target_lang, max_results=max_options)
        return sentences, audio_cache_for(col, sentences)

    # Use QueryOp if available (Anki 2.1.50+), otherwise fall back to blocking call
    if QueryOp:
        # Pass editor.parentWindow as parent so the progress dialog attaches to the correct window
        # (Browser/Add window) instead of the main window. This ensures focus returns correctly when closing.
        op = QueryOp(
            parent=editor.parentWindow,
            op=search_and_probe,
            success=on_success
        )
        _active_ops.add(op)
        op.with_progress(_("searching")).run_in_background()
    else:
        # Fallback for older versions: blocking call
        on_success(search_and_probe(mw.col))

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

