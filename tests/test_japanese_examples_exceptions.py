import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestJapaneseExamplesExceptions(unittest.TestCase):

    def setUp(self):
        # Create mocks
        self.mock_aqt = MagicMock()
        self.mock_requests = MagicMock()

        # Setup exceptions
        class RequestException(Exception): pass
        class ConnectionError(RequestException): pass
        class Timeout(RequestException): pass

        self.mock_requests.exceptions.RequestException = RequestException
        self.mock_requests.exceptions.ConnectionError = ConnectionError
        self.mock_requests.exceptions.Timeout = Timeout

        # Mock Session
        self.mock_session = MagicMock()
        self.mock_requests.Session.return_value = self.mock_session
        self.mock_requests.get = MagicMock()

        # Mock Logging
        self.mock_logging = MagicMock()

        # Patch sys.modules
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'requests': self.mock_requests,
            'logging': self.mock_logging
        })
        self.modules_patcher.start()

        # Import the module under test
        # We need to ensure it's imported with our mocks
        if 'japanese_examples' in sys.modules:
            del sys.modules['japanese_examples']
        import japanese_examples
        self.japanese_examples = japanese_examples

    def tearDown(self):
        self.modules_patcher.stop()
        if 'japanese_examples' in sys.modules:
            del sys.modules['japanese_examples']

    def test_find_japanese_sentence_request_exception(self):
        # Configure the mock to raise a RequestException
        # We need to use the exception class from our mock
        RequestException = self.mock_requests.exceptions.RequestException
        self.mock_session.get.side_effect = RequestException("Network error")

        # Call the function
        result = self.japanese_examples.find_japanese_sentence("word", "eng")

        # Verify result
        self.assertTrue(isinstance(result, str))
        self.assertTrue(result.startswith("Error:"), f"Expected error message, got: {result}")

    def test_find_japanese_sentence_timeout(self):
        # Configure mock for successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        self.mock_session.get.side_effect = None
        self.mock_session.get.return_value = mock_response

        self.japanese_examples.find_japanese_sentence("word", "eng")

        # Check if timeout was passed
        if not self.mock_session.get.called:
             self.fail("session.get was not called")

        args, kwargs = self.mock_session.get.call_args
        self.assertIn('timeout', kwargs, "Timeout parameter missing in session.get call")
        self.assertEqual(kwargs['timeout'], 10, "Timeout should be 10 seconds")

if __name__ == '__main__':
    unittest.main()
