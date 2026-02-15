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
        if 'i18n' in sys.modules:
            del sys.modules['i18n']

    def tearDown(self):
        self.patcher.stop()
        if 'i18n' in sys.modules:
            del sys.modules['i18n']

    def test_get_current_language_specified(self):
        """Test that get_current_language returns the language specified in Anki's config."""
        self.mock_mw.pm.meta.get.return_value = 'fr'
        import i18n
        self.assertEqual(i18n.get_current_language(), 'fr')
        self.mock_mw.pm.meta.get.assert_called_with('defaultLang', 'en')

    def test_get_current_language_default(self):
        """Test that get_current_language returns 'en' when no language is specified."""
        # mw.pm.meta.get('defaultLang', 'en') returns the second arg if key not found
        self.mock_mw.pm.meta.get.side_effect = lambda key, default: default
        import i18n
        self.assertEqual(i18n.get_current_language(), 'en')

    def test_simple_translator_load_translations_en(self):
        """Test that SimpleTranslator loads English translations."""
        import i18n
        translator = i18n.SimpleTranslator('en')
        # 'searching' is a key in en.json
        self.assertEqual(translator.gettext('searching'), 'Searching...')

    def test_simple_translator_load_translations_fr(self):
        """Test that SimpleTranslator loads French translations when requested."""
        import i18n
        translator = i18n.SimpleTranslator('fr')
        # 'searching' is 'Recherche...' in fr.json
        self.assertEqual(translator.gettext('searching'), 'Recherche...')

    def test_simple_translator_gettext_unknown_key(self):
        """Test that gettext returns the key itself if no translation is found."""
        import i18n
        translator = i18n.SimpleTranslator('en')
        self.assertEqual(translator.gettext('unknown_key_123'), 'unknown_key_123')

    def test_simple_translator_handle_region_code(self):
        """Test that SimpleTranslator handles region codes by using the base language."""
        import i18n
        # 'en_US' should fall back to 'en'
        translator = i18n.SimpleTranslator('en_US')
        self.assertEqual(translator.gettext('searching'), 'Searching...')
        
        # 'fr_FR' should fall back to 'fr'
        translator_fr = i18n.SimpleTranslator('fr_FR')
        self.assertEqual(translator_fr.gettext('searching'), 'Recherche...')

if __name__ == '__main__':
    unittest.main()
