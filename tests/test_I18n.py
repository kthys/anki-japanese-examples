import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestI18n(unittest.TestCase):
    def setUp(self):
        # Create mocks for aqt and mw
        self.mock_mw = MagicMock()
        self.mock_aqt = MagicMock()
        self.mock_aqt.mw = self.mock_mw
        
        # Patch sys.modules to simulate aqt existence
        self.patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw
        })
        self.patcher.start()
        
        # Ensure i18n is not in sys.modules so it's re-imported for each test
        if 'src.utils.i18n' in sys.modules:
            del sys.modules['src.utils.i18n']

    def tearDown(self):
        self.patcher.stop()
        if 'src.utils.i18n' in sys.modules:
            del sys.modules['src.utils.i18n']

    def test_get_current_language_specified(self):
        """Test that get_current_language returns the language specified in Anki's config."""
        self.mock_mw.pm.meta.get.return_value = 'fr'
        import src.utils.i18n as i18n
        self.assertEqual(i18n.get_current_language(), 'fr')
        self.mock_mw.pm.meta.get.assert_called_with('defaultLang', 'en')

    def test_get_current_language_default(self):
        """Test that get_current_language returns 'en' when no language is specified."""
        # mw.pm.meta.get('defaultLang', 'en') returns the second arg if key not found
        self.mock_mw.pm.meta.get.side_effect = lambda key, default: default
        import src.utils.i18n as i18n
        self.assertEqual(i18n.get_current_language(), 'en')

    def test_all_locale_files_cover_english_key_set(self):
        """es/zh/ko locale files must contain every key from the English base file.

        Enforces the "full key set" requirement for each UI language (I18N-01/02/03)
        and protects against key drift between locale files.
        """
        import json
        import os
        locale_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'locale')
        with open(os.path.join(locale_dir, 'en.json'), encoding='utf-8') as f:
            en_keys = set(json.load(f).keys())
        for lang in ('es', 'zh', 'ko'):
            with self.subTest(lang=lang):
                path = os.path.join(locale_dir, f'{lang}.json')
                self.assertTrue(os.path.exists(path), f"{lang}.json is missing")
                with open(path, encoding='utf-8') as f:
                    keys = set(json.load(f).keys())
                self.assertEqual(en_keys - keys, set(), f"Missing keys in {lang}.json")
                self.assertEqual(keys - en_keys, set(), f"Unexpected extra keys in {lang}.json")

    def test_zh_locale_serves_zh_cn_and_zh_tw(self):
        """zh.json must be loaded for both zh_CN and zh_TW via the locale overlay."""
        import src.utils.i18n as i18n_mod
        for lang in ('zh_CN', 'zh_TW'):
            with self.subTest(lang=lang):
                translator = i18n_mod.SimpleTranslator(lang)
                self.assertEqual(translator.gettext('language_name_cmn'), '中文（简体）')

    def test_language_name_keys_localized_in_each_ui_language(self):
        """Every UI locale must localize the 5 registry display-name keys."""
        import json
        import os
        locale_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'locale')
        name_keys = ['language_name_eng', 'language_name_fra', 'language_name_spa',
                     'language_name_cmn', 'language_name_kor']
        for lang in ('en', 'fr', 'es', 'zh', 'ko'):
            with self.subTest(lang=lang):
                with open(os.path.join(locale_dir, f'{lang}.json'), encoding='utf-8') as f:
                    data = json.load(f)
                for key in name_keys:
                    self.assertIn(key, data)
                    self.assertNotEqual(data[key], key, f"{key} untranslated in {lang}.json")


    def test_simple_translator_load_translations_en(self):
        """Test that SimpleTranslator loads English translations."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('en')
        # 'searching' is a key in en.json
        self.assertEqual(translator.gettext('searching'), 'Searching...')

    def test_simple_translator_load_translations_fr(self):
        """Test that SimpleTranslator loads French translations when requested."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('fr')
        # 'searching' is 'Recherche...' in fr.json
        self.assertEqual(translator.gettext('searching'), 'Recherche...')

    def test_simple_translator_gettext_unknown_key(self):
        """Test that gettext returns the key itself if no translation is found."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('en')
        self.assertEqual(translator.gettext('unknown_key_123'), 'unknown_key_123')

    def test_simple_translator_handle_region_code(self):
        """Test that SimpleTranslator handles region codes by using the base language."""
        import src.utils.i18n as i18n
        # 'en_US' should fall back to 'en'
        translator = i18n.SimpleTranslator('en_US')
        self.assertEqual(translator.gettext('searching'), 'Searching...')
        
        # 'fr_FR' should fall back to 'fr'
        translator_fr = i18n.SimpleTranslator('fr_FR')
        self.assertEqual(translator_fr.gettext('searching'), 'Recherche...')

class TestLocaleAudioKeys(unittest.TestCase):
    """Verify that all new audio-feature locale keys are present in both en.json and fr.json."""

    def setUp(self):
        # same patcher pattern as TestI18n
        self.mock_mw = MagicMock()
        self.mock_aqt = MagicMock()
        self.mock_aqt.mw = self.mock_mw
        self.patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
        })
        self.patcher.start()
        if 'src.utils.i18n' in sys.modules:
            del sys.modules['src.utils.i18n']

    def tearDown(self):
        self.patcher.stop()
        if 'src.utils.i18n' in sys.modules:
            del sys.modules['src.utils.i18n']

    def test_audio_label_keys_present_in_en(self):
        """batch_audio_field_label_1/_2/_3 must be translateable in en locale."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('en')
        for idx in range(1, 4):
            key = f"batch_audio_field_label_{idx}"
            value = translator.gettext(key)
            # If key is absent, gettext returns the key itself — fail the test
            self.assertNotEqual(
                value, key,
                f"Missing en.json key: {key}"
            )

    def test_audio_label_keys_present_in_fr(self):
        """batch_audio_field_label_1/_2/_3 must be translateable in fr locale."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('fr')
        for idx in range(1, 4):
            key = f"batch_audio_field_label_{idx}"
            value = translator.gettext(key)
            self.assertNotEqual(
                value, key,
                f"Missing fr.json key: {key}"
            )

    def test_report_body_has_audio_added_placeholder_en(self):
        """batch_report_body in en.json must contain {audio_added} and {audio_skipped}."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('en')
        body = translator.gettext('batch_report_body')
        self.assertIn('{audio_added}', body)
        self.assertIn('{audio_skipped}', body)

    def test_report_body_has_audio_added_placeholder_fr(self):
        """batch_report_body in fr.json must contain {audio_added} and {audio_skipped}."""
        import src.utils.i18n as i18n
        translator = i18n.SimpleTranslator('fr')
        body = translator.gettext('batch_report_body')
        self.assertIn('{audio_added}', body)
        self.assertIn('{audio_skipped}', body)


if __name__ == '__main__':
    unittest.main()
