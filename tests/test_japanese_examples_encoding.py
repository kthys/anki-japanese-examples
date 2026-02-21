import unittest
from unittest.mock import MagicMock
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock modules before importing the module under test
sys.modules['aqt'] = MagicMock()
sys.modules['requests'] = MagicMock()

# Now import the module to test
import japanese_examples
import importlib

class TestJapaneseExamples(unittest.TestCase):
    def setUp(self):
        # Ensure the module is in sys.modules before reloading
        if 'japanese_examples' not in sys.modules:
            sys.modules['japanese_examples'] = japanese_examples
        # Reload the module to ensure it uses the current mock for requests
        importlib.reload(japanese_examples)
        # Reset mocks before each test
        japanese_examples._session.get.reset_mock()

    def test_find_japanese_sentence_encoding(self):
        word = "foo&bar"
        lang = "eng"

        # Call the function
        japanese_examples.find_japanese_sentence(word, lang)

        # Get the call arguments
        # If the function was called, call_args should not be None
        if japanese_examples._session.get.called:
            args, kwargs = japanese_examples._session.get.call_args
        else:
            self.fail("session.get was not called")

        # Assertions
        # The first argument should be the base URL
        self.assertEqual(args[0], "https://tatoeba.org/en/api_v0/search",
                         f"Expected base URL, got {args[0]}")

        # The params argument should be present
        self.assertIn('params', kwargs, "params argument missing in requests.get call")

        expected_params = {
            'query': f'={word}',
            'from': 'jpn',
            'to': lang,
            'limit': 50
        }
        self.assertEqual(kwargs['params'], expected_params)

if __name__ == '__main__':
    unittest.main()
