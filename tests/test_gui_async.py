import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestGUIAsync(unittest.TestCase):

    def setUp(self):
        # Create mocks for aqt modules
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_operations = MagicMock()
        self.mock_utils = MagicMock()
        self.mock_gui_hooks = MagicMock()

        # Mock aqt.qt — GUI imports QTimer (and other Qt classes) from here
        self.mock_qt = MagicMock()

        # Patch sys.modules to simulate aqt existence
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.operations': self.mock_operations,
            'aqt.utils': self.mock_utils,
            'aqt.gui_hooks': self.mock_gui_hooks,
            'aqt.qt': self.mock_qt,
        })
        self.modules_patcher.start()

        # Configure mw
        self.mock_mw.pm.meta.get.return_value = 'en'
        self.mock_mw.col.path = "/path/to/collection.anki2"
        self.mock_mw.progress.busy.return_value = False
        # Link mw to aqt.mw
        self.mock_aqt.mw = self.mock_mw

        # Add config mock
        self.mock_config = {
            "japaneseDstField": "Expression",
            "translationDstField": "Meaning",
            "deck_preferences": {}
        }
        self.mock_mw.addonManager.getConfig.return_value = self.mock_config

        # Mock Qt constants
        self.mock_utils.Qt = MagicMock()
        self.mock_utils.Qt.WindowModality.WindowModal = 1
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore' # Simulate Qt5

        # Also need to mock japanese_examples because GUI imports from it
        self.mock_japanese_examples = MagicMock()
        sys.modules['src.core.japanese_examples'] = self.mock_japanese_examples

        # Import the module under test
        if 'src.ui.GUI' in sys.modules:
            del sys.modules['src.ui.GUI']
        import src.ui.GUI as GUI
        self.GUI = GUI

    def tearDown(self):
        self.modules_patcher.stop()
        if 'src.ui.GUI' in sys.modules:
            del sys.modules['src.ui.GUI']

    @patch('src.ui.GUI.find_japanese_sentence')
    @patch('src.ui.GUI.create_custom_dialog')
    @patch('src.ui.GUI.showInfo')
    def test_add_example_manually_dialog_flow(self, mock_showInfo, mock_create_custom_dialog, mock_find_japanese_sentence):
        # Setup mocks
        editor = MagicMock()
        editor.web.editor.currentField = 0 # Assuming index

        # Mock fields list
        editor.note.fields = ['test_word', '', '']

        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }

        # Mock create_custom_dialog returns
        # 1. Language selection: returns (0, False) because deck_id is detected (via mocks) so checkbox is shown
        # 2. Example selection: returns 0 (selected index)
        mock_create_custom_dialog.side_effect = [(0, False), 0]

        # Mock find_japanese_sentence result
        mock_results = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]
        mock_find_japanese_sentence.return_value = mock_results

        # Call the function
        self.GUI.add_example_manually_dialog(editor)

        # Verify initial dialog (Language selection)
        _ = self.GUI._

        # Verify call arguments
        args, kwargs = mock_create_custom_dialog.call_args_list[0]
        self.assertEqual(args[0], _("select_translation_language_dialog"))
        self.assertTrue(kwargs.get('with_checkbox')) # Checkbox should be True as deck_id is present

        # Verify QueryOp usage
        self.assertTrue(self.mock_operations.QueryOp.called, "QueryOp should be instantiated")

        # Get the instance
        op_instance = self.mock_operations.QueryOp.return_value

        # Check run_in_background was called
        op_instance.with_progress.return_value.run_in_background.assert_called_once()

        # Now we need to simulate the success callback
        call_args = self.mock_operations.QueryOp.call_args
        args, kwargs = call_args
        success_callback = kwargs['success']

        # Simulate success callback with results
        success_callback(mock_results)

        # Execute the scheduled function by QTimer
        # Verify singleShot called
        self.mock_qt.QTimer.singleShot.assert_called()
        timer_args = self.mock_qt.QTimer.singleShot.call_args[0]
        # singleShot(delay, func)
        scheduled_func = timer_args[1]
        scheduled_func()

        # Verify second dialog (Example selection)
        args2, kwargs2 = mock_create_custom_dialog.call_args_list[1]
        self.assertEqual(args2[0], _('select_sentence_dialog'))

        # Verify note update
        # japaneseDstField is 'Expression' (index 0)
        # translationDstField is 'Meaning' (index 1)
        self.assertEqual(editor.note.fields[0], 'JP1')
        self.assertEqual(editor.note.fields[1], 'TR1')

        # Verify flush and loadNote
        self.mock_mw.col.update_note.assert_called_with(editor.note)
        editor.loadNote.assert_called_once()

    def test_fallback_when_queryop_missing(self):
        # Unpatch operations to simulate older Anki
        # This requires reloading GUI without operations

        # Manually set QueryOp to None in GUI module for this test
        original_query_op = self.GUI.QueryOp
        self.GUI.QueryOp = None

        try:
             # Setup mocks
            editor = MagicMock()
            editor.web.editor.currentField = 0
            editor.note.fields = ['test_word', '', '']
            editor.note.note_type.return_value = {
                'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
            }

            with patch('src.ui.GUI.create_custom_dialog') as mock_dialog, \
                 patch('src.ui.GUI.create_multi_selection_dialog') as mock_multi_dialog, \
                 patch('src.ui.GUI.find_japanese_sentence') as mock_find:

                mock_dialog.side_effect = [(0, False), 0] # English, no save; then first index
                mock_find.return_value = [{'jp_sentence': 'JP1', 'tr_sentence': 'TR1'}]

                # Call
                self.GUI.add_example_manually_dialog(editor)

                # QTimer should have been called in on_success
                self.mock_qt.QTimer.singleShot.assert_called()
                timer_args = self.mock_qt.QTimer.singleShot.call_args[0]
                scheduled_func = timer_args[1]
                scheduled_func()

                # Verify find_japanese_sentence called directly (synchronously)
                mock_find.assert_called_with('test_word', 'eng', max_results=30)

                # Verify logic ran (check note update)
                self.assertEqual(editor.note.fields[0], 'JP1')
                self.assertEqual(editor.note.fields[1], 'TR1')

        finally:
            self.GUI.QueryOp = original_query_op

    # ── get_current_deck_id ──────────────────────────────────────────

    def test_get_current_deck_id_from_add_cards(self):
        """Should return deck ID from deckChooser in Add Cards dialog."""
        editor = MagicMock()
        editor.parentWindow.deckChooser.selectedId.return_value = 42

        result = self.GUI.get_current_deck_id(editor)
        self.assertEqual(result, 42)

    def test_get_current_deck_id_from_browser(self):
        """Should return deck ID from the first card when in Browser."""
        editor = MagicMock(spec=[])  # No deckChooser attribute
        editor.parentWindow = MagicMock(spec=[])  # No deckChooser
        editor.note = MagicMock()
        mock_card = MagicMock()
        mock_card.did = 99
        editor.note.cards.return_value = [mock_card]

        result = self.GUI.get_current_deck_id(editor)
        self.assertEqual(result, 99)

    def test_get_current_deck_id_returns_none_when_no_deck(self):
        """Should return None when no deck info is available."""
        editor = MagicMock(spec=[])
        editor.parentWindow = MagicMock(spec=[])
        editor.note = MagicMock()
        editor.note.cards.return_value = []

        result = self.GUI.get_current_deck_id(editor)
        self.assertIsNone(result)

    # ── Early return on empty field ─────────────────────────────────

    @patch('src.ui.GUI.showInfo')
    def test_add_example_manually_dialog_returns_early_if_no_field(self, mock_showInfo):
        """Should call showInfo and return when currentField is None."""
        editor = MagicMock()
        editor.web.editor.currentField = None

        self.GUI.add_example_manually_dialog(editor)

        mock_showInfo.assert_called_once()

class TestGUIAudioField(unittest.TestCase):

    def setUp(self):
        # Duplicate TestGUIAsync setUp verbatim
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_operations = MagicMock()
        self.mock_utils = MagicMock()
        self.mock_gui_hooks = MagicMock()
        self.mock_qt = MagicMock()

        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'aqt.operations': self.mock_operations,
            'aqt.utils': self.mock_utils,
            'aqt.gui_hooks': self.mock_gui_hooks,
            'aqt.qt': self.mock_qt,
        })
        self.modules_patcher.start()

        self.mock_mw.pm.meta.get.return_value = 'en'
        self.mock_mw.col.path = "/path/to/collection.anki2"
        self.mock_mw.progress.busy.return_value = False
        self.mock_aqt.mw = self.mock_mw

        self.mock_config = {
            "japaneseDstField": "Expression",
            "translationDstField": "Meaning",
            "audioDstField": "Audio",
            "deck_preferences": {}
        }
        self.mock_mw.addonManager.getConfig.return_value = self.mock_config

        self.mock_utils.Qt = MagicMock()
        self.mock_utils.Qt.WindowModality.WindowModal = 1
        self.mock_utils.Qt.__module__ = 'PyQt5.QtCore'

        self.mock_japanese_examples = MagicMock()
        sys.modules['src.core.japanese_examples'] = self.mock_japanese_examples

        # Mock audio_fetcher module at the boundary
        self.mock_audio_fetcher = MagicMock()
        self.mock_audio_fetcher.fetch_audio_to_temp = MagicMock(
            return_value="/tmp/x/8858176.mp3")
        self.mock_audio_fetcher.register_audio_file = MagicMock(
            return_value="8858176.mp3")
        sys.modules['src.core.audio_fetcher'] = self.mock_audio_fetcher

        if 'src.ui.GUI' in sys.modules:
            del sys.modules['src.ui.GUI']
        import src.ui.GUI as GUI
        self.GUI = GUI

    def tearDown(self):
        self.modules_patcher.stop()
        if 'src.ui.GUI' in sys.modules:
            del sys.modules['src.ui.GUI']
        if 'src.core.audio_fetcher' in sys.modules:
            del sys.modules['src.core.audio_fetcher']

    def _make_editor(self, fields=None, field_names=None):
        """Helper: build a mock editor with the given fields and field names."""
        editor = MagicMock()
        editor.web.editor.currentField = 0
        editor.note.fields = fields or ['test_word', '', '', '']
        editor.note.id = 1  # non-zero so update_note is called
        editor.note.note_type.return_value = {
            'flds': [
                {'name': n} for n in (field_names or ['Expression', 'Meaning', 'Reading', 'Audio'])
            ]
        }
        return editor

    def _run_flow(self, editor, examples_sentences, dialog_side_effects=None):
        """
        Simulate the full add_example_manually_dialog flow:
        1. Call add_example_manually_dialog(editor)
        2. Retrieve and invoke the QueryOp success callback with examples_sentences
        3. Invoke the QTimer scheduled callback to run show_result_dialog
        Returns the GUI module for further assertions.
        """
        with patch('src.ui.GUI.find_japanese_sentence', return_value=examples_sentences), \
             patch('src.ui.GUI.create_custom_dialog') as mock_dialog, \
             patch('src.ui.GUI.showInfo'):

            # Language dialog -> English no save; example selection -> index 0
            mock_dialog.side_effect = dialog_side_effects or [(0, False), 0]

            self.GUI.add_example_manually_dialog(editor)

            # Invoke QueryOp success callback
            call_args = self.mock_operations.QueryOp.call_args
            _, kwargs = call_args
            success_callback = kwargs['success']
            success_callback(examples_sentences)

            # Invoke QTimer scheduled function
            timer_call_args = self.mock_qt.QTimer.singleShot.call_args[0]
            timer_call_args[1]()

    def _run_audio_op(self, media_have=False):
        """Simulate the audio QueryOp: run its background op with a mock col,
        then feed the outcome to the success callback (as Anki would)."""
        audio_op_call = self.mock_operations.QueryOp.call_args_list[-1]
        _, audio_kwargs = audio_op_call
        bg_col = MagicMock()
        bg_col.media.have.return_value = media_have
        outcome = audio_kwargs['op'](bg_col)
        audio_kwargs['success'](outcome)
        return outcome

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_written_when_configured_and_recording_exists(self, mock_showInfo):
        """Audio field gets [sound:filename.mp3] when audioDstField configured and recording exists."""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        outcome = self._run_audio_op()

        self.assertEqual(outcome, ("fetched", "/tmp/x/8858176.mp3"))
        self.mock_audio_fetcher.register_audio_file.assert_called_once()
        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")
        mock_showInfo.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_written_without_fetch_when_already_in_media(self, mock_showInfo):
        """File already in col.media: tag written, no download performed."""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        outcome = self._run_audio_op(media_have=True)

        self.assertEqual(outcome, ("exists", "8858176.mp3"))
        self.mock_audio_fetcher.fetch_audio_to_temp.assert_not_called()
        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_empty_when_no_recording(self, mock_showInfo):
        """Audio field stays empty when the fetch reports no recording (404)."""
        self.mock_audio_fetcher.fetch_audio_to_temp.return_value = None
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': False}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self._run_audio_op()

        self.assertNotEqual(editor.note.fields[3], "[sound:8858176.mp3]")
        self.mock_audio_fetcher.register_audio_file.assert_not_called()
        mock_showInfo.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_disabled_in_config(self, mock_showInfo):
        """No audio fetch happens when audioDstField is explicitly empty."""
        self.mock_config["audioDstField"] = ""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.fetch_audio_to_temp.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_defaults_to_exampleaudio_when_key_absent(self, mock_showInfo):
        """Absent audioDstField key: the 'ExampleAudio' default applies, so a
        note that has that field gets audio out of the box."""
        self.mock_config.pop("audioDstField", None)
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor(
            field_names=['Expression', 'Meaning', 'Reading', 'ExampleAudio'])
        self._run_flow(editor, examples_sentences)

        self._run_audio_op()

        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_default_field_missing_from_note(self, mock_showInfo):
        """Absent key + note type without an 'ExampleAudio' field: no fetch."""
        self.mock_config.pop("audioDstField", None)
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()  # fields: Expression/Meaning/Reading/Audio
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.fetch_audio_to_temp.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_jpn_id_none(self, mock_showInfo):
        """No audio fetch happens when jpn_id is None."""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': None, 'has_audio': False}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.fetch_audio_to_temp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
