import sys
import unittest
from unittest.mock import MagicMock

# Mock aqt before importing our modules
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.mw'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['aqt.qt'] = MagicMock()

# Import freshly
if "src.core.languages" in sys.modules:
    del sys.modules["src.core.languages"]
import src.core.languages as languages


class TestLanguageRegistry(unittest.TestCase):
    """Tests for the data-driven language registry."""

    def test_get_codes_returns_all_five_in_order(self):
        """get_codes should return the 5 supported codes in display order."""
        self.assertEqual(languages.get_codes(), ["eng", "fra", "spa", "cmn", "kor"])

    def test_is_supported(self):
        """is_supported should accept every registry code and reject unknown codes."""
        for code in languages.get_codes():
            self.assertTrue(languages.is_supported(code))
        self.assertFalse(languages.is_supported("klingon"))
        self.assertFalse(languages.is_supported(""))

    def test_get_label(self):
        """get_label should return the English display label for each code."""
        self.assertEqual(languages.get_label("eng"), "English")
        self.assertEqual(languages.get_label("fra"), "French")
        self.assertEqual(languages.get_label("spa"), "Spanish")
        self.assertEqual(languages.get_label("cmn"), "Chinese (Simplified)")
        self.assertEqual(languages.get_label("kor"), "Korean")

    def test_get_label_unknown_returns_code(self):
        """get_label should return the code itself for unknown codes."""
        self.assertEqual(languages.get_label("xyz"), "xyz")

    def test_get_localized_name_falls_back_to_label(self):
        """get_localized_name should fall back to the English label when the
        locale key is missing (e.g. partial locale files)."""
        # Force the translator to return the raw key (missing translation)
        original = languages._
        try:
            languages._ = lambda key: key
            self.assertEqual(languages.get_localized_name("spa"), "Spanish")
            self.assertEqual(languages.get_localized_name("kor"), "Korean")
        finally:
            languages._ = original

    def test_get_localized_name_uses_locale_key(self):
        """get_localized_name should resolve through the locale overlay."""
        original = languages._
        try:
            languages._ = lambda key: {"language_name_eng": "Anglais"}.get(key, key)
            self.assertEqual(languages.get_localized_name("eng"), "Anglais")
        finally:
            languages._ = original

    def test_get_localized_name_unknown_returns_code(self):
        """get_localized_name should return the code itself for unknown codes."""
        self.assertEqual(languages.get_localized_name("xyz"), "xyz")

    def test_code_from_label(self):
        """code_from_label should resolve legacy English labels to codes."""
        self.assertEqual(languages.code_from_label("English"), "eng")
        self.assertEqual(languages.code_from_label("French"), "fra")
        self.assertEqual(languages.code_from_label("Spanish"), "spa")
        self.assertIsNone(languages.code_from_label("Klingon"))


if __name__ == '__main__':
    unittest.main()
