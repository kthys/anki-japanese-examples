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
        if 'src.core.japanese_examples' in sys.modules:
            del sys.modules['src.core.japanese_examples']
        import src.core.japanese_examples as japanese_examples
        self.japanese_examples = japanese_examples

    def tearDown(self):
        self.modules_patcher.stop()
        if 'src.core.japanese_examples' in sys.modules:
            del sys.modules['src.core.japanese_examples']

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

    # ── test_tatoeba_connection ────────────────────────────────────

    def _mock_ok_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"results": []}
        self.mock_session.get.side_effect = None
        self.mock_session.get.return_value = mock_response
        return mock_response

    def test_tatoeba_connection_success(self):
        """test_tatoeba_connection should report success on a reachable API."""
        self._mock_ok_response()
        success, msg = self.japanese_examples.test_tatoeba_connection()
        self.assertTrue(success)
        self.assertIn("reachable", msg)
        # A minimal real search is sent with a short timeout
        _, kwargs = self.mock_session.get.call_args
        self.assertEqual(kwargs["params"]["query"], "猫")
        self.assertEqual(kwargs["timeout"], 10)

    def test_tatoeba_connection_request_exception(self):
        """test_tatoeba_connection should report failure on a network error."""
        RequestException = self.mock_requests.exceptions.RequestException
        self.mock_session.get.side_effect = RequestException("Network error")
        success, msg = self.japanese_examples.test_tatoeba_connection()
        self.assertFalse(success)
        self.assertIn("Network error", msg)

    def test_tatoeba_connection_invalid_json(self):
        """test_tatoeba_connection should report failure when the response is not JSON."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("bad json")
        self.mock_session.get.side_effect = None
        self.mock_session.get.return_value = mock_response
        success, msg = self.japanese_examples.test_tatoeba_connection()
        self.assertFalse(success)
        self.assertIn("bad json", msg)

    def test_tatoeba_connection_http_error(self):
        """test_tatoeba_connection should report failure on a non-200 response."""
        RequestException = self.mock_requests.exceptions.RequestException
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RequestException("500 Server Error")
        self.mock_session.get.side_effect = None
        self.mock_session.get.return_value = mock_response
        success, msg = self.japanese_examples.test_tatoeba_connection()
        self.assertFalse(success)
        self.assertIn("500 Server Error", msg)


if __name__ == '__main__':
    unittest.main()
