from aqt import gui_hooks, mw
from aqt.utils import Qt, QDialog, QVBoxLayout, QLabel, QListWidget, QDialogButtonBox
from aqt.utils import showInfo
import os, json

try:
    from PyQt5.QtCore import QTimer
except ImportError:
    from PyQt6.QtCore import QTimer

try:
    from .japanese_examples import find_japanese_sentence, DST_FIELD_TRANSLATION, DST_FIELD_JAP
except ImportError:
    from japanese_examples import find_japanese_sentence, DST_FIELD_TRANSLATION, DST_FIELD_JAP

# Try to import QueryOp for background operations (Anki 2.1.50+)
try:
    from aqt.operations import QueryOp
except ImportError:
    QueryOp = None

# Global set to keep references to active operations to prevent premature garbage collection
_active_ops = set()

def get_qt_version():
    """ Return the version of Qt used by Anki.
    """

    qt_ver = 5  # assume 5 for now

    if Qt.__module__ == 'PyQt5.QtCore':
        # PyQt5
        # tested on aqt[qt5]
        qt_ver = 5
    elif Qt.__module__ == 'PyQt6.QtCore':
        # PyQt6
        # tested on aqt[qt6]
        qt_ver = 6

    # NOTE
    # when Anki runs with the temporary Qt5 compatibility
    # shims, Qt.__module__ is 'PyQt6.sip.wrappertype', but
    # then it should also be no problem to defer to 5

    return qt_ver


def get_plugin_dir_path():
    """ Determine and return the path of the plugin directory.
    """

    collection_path = mw.col.path
    plugin_dir_name = __name__.split('.')[0]  # remove ".gui"

    user_dir_path = os.path.split(collection_path)[0]
    anki_dir_path = os.path.split(user_dir_path)[0]
    plugin_dir_path = os.path.join(anki_dir_path, 'addons21', plugin_dir_name)

    return plugin_dir_path

try:
    from .i18n import _
except ImportError:
    from i18n import _

def create_custom_dialog(message, choices, start_row=0, parent=None):
    """ This function creates a custom dialog with a selection list
        and OK/Cancel buttons. It is based on code from Anki
        open-source project.
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
    return selection_list.currentRow()



def add_example_manually_dialog(editor):
    """ Dialog for adding an example of sentence based on japanese word present in the selected field.
        The target fields are defined in the config file.
    """

    if editor.web.editor.currentField is None or editor.web.editor.currentField == '':
        showInfo(_('select_field_to_use'))
        return

    japanese_word = editor.note.fields[editor.web.editor.currentField]

    if not japanese_word or not japanese_word.strip():
        showInfo(_("no_japanese_sentence_found").format(word=japanese_word))
        return

    # User choses where to get the examples from
    source_index = create_custom_dialog(
    _("select_translation_language_dialog"), 
    ['English', 'French']
    )


    if source_index is None:
        return None
    
    # Determine target language code
    if source_index == 0:
        target_lang = 'eng'
    elif source_index == 1:
        target_lang = 'fra'
    else:
        # Should not happen given the dialog choices
        return

    # Define op variable to be accessible in on_success
    op = None

    def on_success(examples_sentences):
        # Cleanup op reference to avoid memory leak
        if op:
            _active_ops.discard(op)

        if examples_sentences is None:
            showInfo(_('example_not_found'))
            return
        
        elif isinstance(examples_sentences, str):
            showInfo(examples_sentences)
            return
        
        else:
            try:
                examples = [f"{example['jp_sentence']}\n{example['tr_sentence']}" for example in examples_sentences]
            except TypeError:
                showInfo(_('example_not_found_check_encoding'))
                return

        def show_result_dialog():
            # User choses which example to add
            # We pass the parent window explicitly to avoid attaching to the progress dialog
            # Use editor.parentWindow (Browser/Add window) as parent, ensuring it's not obscured by closing progress dialog
            example_picker_index = create_custom_dialog(
            _('select_sentence_dialog'),
            examples,
            parent=editor.parentWindow
            )

            if example_picker_index is None:
                showInfo(_('no_example_selected'))
                return

            else:
                chosen_example = examples_sentences[example_picker_index]
                jp_sentence = chosen_example['jp_sentence']
                tr_sentence = chosen_example['tr_sentence']

                # Get the current note opened in the editor
                note = editor.note

                # Get the field names
                note_type = note.note_type()
                fields = note_type['flds']
                field_names = [field['name'] for field in fields]

                # Find the index of the target fields, according to the ones defined in the config file
                try:
                    jp_field_index = field_names.index(DST_FIELD_JAP)
                except ValueError:
                    showInfo(_("{DST_FIELD_JAP}_field_not_found").format(DST_FIELD_JAP=DST_FIELD_JAP))
                    return

                try:
                    en_field_index = field_names.index(DST_FIELD_TRANSLATION)
                except ValueError:
                    showInfo(_("{DST_FIELD_TRANSLATION}_field_not_found").format(DST_FIELD_TRANSLATION=DST_FIELD_TRANSLATION))
                    return

                # Set the value of the field
                note.fields[jp_field_index] = jp_sentence
                note.fields[en_field_index]= tr_sentence

                # Save the changes to the note if the note already exists
                if note.id != 0 :
                    note.flush()

                # Update the editor to show the changes
                editor.loadNote()

        # Schedule the dialog to open on the next event loop iteration
        # allowing the progress dialog to close cleanly first.
        QTimer.singleShot(100, show_result_dialog)

    # Use QueryOp if available (Anki 2.1.50+), otherwise fall back to blocking call
    if QueryOp:
        op = QueryOp(
            parent=mw,
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
    """ Add buttons to editor menu.
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

