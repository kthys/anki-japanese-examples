import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from collections import namedtuple

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mirrors audio_fetcher.ResolvedAudio without importing the (mocked) module.
ResolvedAudio = namedtuple(
    "ResolvedAudio", "jpn_id jpn_text trans_text source payload")

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

    # ── Missing destination fields: fail fast ───────────────────────

    def _make_abort_editor(self):
        """Editor whose note type lacks the configured destination fields."""
        editor = MagicMock()
        editor.web.editor.currentField = 0
        editor.note.fields = ['test_word', '', '']
        editor.note.note_type.return_value = {
            'flds': [{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Reading'}]
        }
        return editor

    @patch('src.ui.GUI.showInfo')
    @patch('src.ui.GUI.create_custom_dialog')
    @patch('src.ui.GUI.find_japanese_sentence')
    def test_missing_japanese_field_aborts_before_dialog_and_search(
            self, mock_find, mock_dialog, mock_showInfo):
        """A missing Japanese destination field stops the flow on click."""
        editor = self._make_abort_editor()
        self.mock_config['japaneseDstField'] = 'MissingJapanese'

        self.GUI.add_example_manually_dialog(editor)

        mock_showInfo.assert_called_once_with(
            self.GUI._("no_valid_dst_fields").format(
                missing="'MissingJapanese' (Japanese)",
                available="Expression, Meaning, Reading",
            )
        )
        mock_dialog.assert_not_called()
        mock_find.assert_not_called()
        self.mock_operations.QueryOp.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    @patch('src.ui.GUI.create_custom_dialog')
    @patch('src.ui.GUI.find_japanese_sentence')
    def test_missing_translation_field_aborts_before_dialog_and_search(
            self, mock_find, mock_dialog, mock_showInfo):
        """A missing translation destination field stops the flow on click."""
        editor = self._make_abort_editor()
        self.mock_config['translationDstField'] = 'MissingMeaning'

        self.GUI.add_example_manually_dialog(editor)

        mock_showInfo.assert_called_once_with(
            self.GUI._("no_valid_dst_fields").format(
                missing="'MissingMeaning' (Translation)",
                available="Expression, Meaning, Reading",
            )
        )
        mock_dialog.assert_not_called()
        mock_find.assert_not_called()
        self.mock_operations.QueryOp.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    @patch('src.ui.GUI.create_custom_dialog')
    @patch('src.ui.GUI.find_japanese_sentence')
    def test_missing_both_fields_lists_both(
            self, mock_find, mock_dialog, mock_showInfo):
        """Both missing fields are named in the message."""
        editor = self._make_abort_editor()
        self.mock_config['japaneseDstField'] = 'MissingJapanese'
        self.mock_config['translationDstField'] = 'MissingMeaning'

        self.GUI.add_example_manually_dialog(editor)

        message = mock_showInfo.call_args[0][0]
        self.assertIn("'MissingJapanese' (Japanese)", message)
        self.assertIn("'MissingMeaning' (Translation)", message)
        mock_find.assert_not_called()
        self.mock_operations.QueryOp.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    @patch('src.ui.GUI.create_custom_dialog')
    @patch('src.ui.GUI.find_japanese_sentence')
    def test_unreadable_note_type_aborts_with_missing_message(
            self, mock_find, mock_dialog, mock_showInfo):
        """A note type that cannot be read degrades to the missing message."""
        editor = self._make_abort_editor()
        editor.note.note_type.side_effect = AttributeError("no note type")

        self.GUI.add_example_manually_dialog(editor)

        mock_showInfo.assert_called_once()
        mock_find.assert_not_called()
        self.mock_operations.QueryOp.assert_not_called()


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
        # Default: no sentence has usable audio (403/404 probe returns None).
        self.mock_audio_fetcher.resolve_audio = MagicMock(return_value=None)
        self.mock_audio_fetcher.register_audio_file = MagicMock(
            return_value="8858176.mp3")
        self.mock_audio_fetcher.cleanup_temp_audio = MagicMock()
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
        """Simulate the full add_example_manually_dialog flow.

        1. Call add_example_manually_dialog(editor)
        2. Run the search+audio-probe background op and feed its
           (examples, audio_cache) result to the success callback
        3. Run the QTimer-scheduled show_result_dialog

        Stores the create_custom_dialog mock on self.mock_dialog so tests can
        inspect the rendered example list.
        """
        with patch('src.ui.GUI.find_japanese_sentence', return_value=examples_sentences), \
             patch('src.ui.GUI.create_custom_dialog') as mock_dialog:

            # Language dialog -> English no save; example selection -> index 0
            mock_dialog.side_effect = dialog_side_effects or [(0, False), 0]

            self.GUI.add_example_manually_dialog(editor)

            # Only the search QueryOp is created now; run it to build the cache.
            _, kwargs = self.mock_operations.QueryOp.call_args
            bg_col = MagicMock()
            outcome = kwargs['op'](bg_col)
            kwargs['success'](outcome)

            timer_call_args = self.mock_qt.QTimer.singleShot.call_args[0]
            timer_call_args[1]()
            self.mock_dialog = mock_dialog

    def _rendered_examples(self):
        """The list of display strings passed to the selection dialog."""
        return self.mock_dialog.call_args_list[1].args[1]

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_written_when_configured_and_recording_exists(self, mock_showInfo):
        """Audio field gets [sound:filename.mp3] when the probe resolved audio."""
        self.mock_audio_fetcher.resolve_audio.return_value = ResolvedAudio(
            '8858176', None, None, 'temp', '/tmp/x/8858176.mp3')
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.register_audio_file.assert_called_once_with(
            '/tmp/x/8858176.mp3', self.mock_mw.col)
        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")
        mock_showInfo.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_written_without_fetch_when_already_in_media(self, mock_showInfo):
        """A media-source resolution writes the tag without registering a file."""
        self.mock_audio_fetcher.resolve_audio.return_value = ResolvedAudio(
            '8858176', None, None, 'media', '8858176.mp3')
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.register_audio_file.assert_not_called()
        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")

    @patch('src.ui.GUI.showInfo')
    def test_audio_field_empty_when_no_recording(self, mock_showInfo):
        """A probe that resolves nothing leaves the audio field empty."""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.assertNotEqual(editor.note.fields[3], "[sound:8858176.mp3]")
        self.mock_audio_fetcher.register_audio_file.assert_not_called()
        mock_showInfo.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_403_example_is_rendered_without_speaker_icon(self, mock_showInfo):
        """An example whose audio 403s (probe returns None) gets no speaker icon."""
        self.mock_audio_fetcher.resolve_audio.side_effect = (
            lambda jpn_id, candidates, col: None)
        examples_sentences = [
            {'jp_sentence': 'A', 'tr_sentence': 'a', 'jpn_id': '111', 'has_audio': True},
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.assertFalse(self._rendered_examples()[0].startswith('🔊'))

    @patch('src.ui.GUI.showInfo')
    def test_speaker_icon_shown_only_when_audio_is_available(self, mock_showInfo):
        """The icon reflects the probe result, not the API's has_audio hint."""
        self.mock_audio_fetcher.resolve_audio.side_effect = (
            lambda jpn_id, candidates, col: (
                ResolvedAudio(jpn_id, None, None, 'temp', f'/tmp/x/{jpn_id}.mp3')
                if jpn_id == '111' else None))
        examples_sentences = [
            {'jp_sentence': 'A', 'tr_sentence': 'a', 'jpn_id': '111', 'has_audio': True},
            {'jp_sentence': 'B', 'tr_sentence': 'b', 'jpn_id': '222', 'has_audio': True},
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        rendered = self._rendered_examples()
        self.assertTrue(rendered[0].startswith('🔊'))
        self.assertFalse(rendered[1].startswith('🔊'))

    @patch('src.ui.GUI.showInfo')
    def test_unused_cached_audio_is_cleaned_up(self, mock_showInfo):
        """Only the chosen sentence's temp file is kept; the rest are deleted."""
        self.mock_audio_fetcher.resolve_audio.side_effect = (
            lambda jpn_id, candidates, col: ResolvedAudio(
                jpn_id, None, None, 'temp', f'/tmp/x/{jpn_id}.mp3'))
        examples_sentences = [
            {'jp_sentence': 'A', 'tr_sentence': 'a', 'jpn_id': '111', 'has_audio': True},
            {'jp_sentence': 'B', 'tr_sentence': 'b', 'jpn_id': '222', 'has_audio': True},
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)  # chooses index 0

        self.mock_audio_fetcher.cleanup_temp_audio.assert_called_once_with(
            '/tmp/x/222.mp3')

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_disabled_in_config(self, mock_showInfo):
        """No probe happens when audioDstField is explicitly empty."""
        self.mock_config["audioDstField"] = ""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.resolve_audio.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_defaults_to_exampleaudio_when_key_absent(self, mock_showInfo):
        """Absent audioDstField key: the 'ExampleAudio' default applies, so a
        note that has that field gets audio out of the box."""
        self.mock_config.pop("audioDstField", None)
        self.mock_audio_fetcher.resolve_audio.return_value = ResolvedAudio(
            '8858176', None, None, 'temp', '/tmp/x/8858176.mp3')
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor(
            field_names=['Expression', 'Meaning', 'Reading', 'ExampleAudio'])
        self._run_flow(editor, examples_sentences)

        self.assertEqual(editor.note.fields[3], "[sound:8858176.mp3]")

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_default_field_missing_from_note(self, mock_showInfo):
        """Absent key + note type without an 'ExampleAudio' field: no probe."""
        self.mock_config.pop("audioDstField", None)
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()  # fields: Expression/Meaning/Reading/Audio
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.resolve_audio.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_audio_skipped_when_jpn_id_none(self, mock_showInfo):
        """No probe happens when jpn_id is None."""
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': None, 'has_audio': True}
        ]
        editor = self._make_editor()
        self._run_flow(editor, examples_sentences)

        self.mock_audio_fetcher.resolve_audio.assert_not_called()

    @patch('src.ui.GUI.showInfo')
    def test_missing_pair_field_skips_audio_probe(self, mock_showInfo):
        """With a required destination field missing, no search or probe runs."""
        self.mock_config['japaneseDstField'] = 'MissingJapanese'
        examples_sentences = [
            {'jp_sentence': 'JP1', 'tr_sentence': 'TR1', 'jpn_id': '8858176', 'has_audio': True}
        ]
        editor = self._make_editor()
        with patch('src.ui.GUI.find_japanese_sentence', return_value=examples_sentences), \
             patch('src.ui.GUI.create_custom_dialog'):
            self.GUI.add_example_manually_dialog(editor)

        self.mock_audio_fetcher.resolve_audio.assert_not_called()
        self.mock_operations.QueryOp.assert_not_called()
        mock_showInfo.assert_called_once()


if __name__ == '__main__':
    unittest.main()
