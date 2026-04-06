import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJapaneseExamples(unittest.TestCase):

    def setUp(self):
        # Create mocks
        self.mock_aqt = MagicMock()
        self.mock_mw = MagicMock()
        self.mock_aqt.mw = self.mock_mw
        self.mock_requests = MagicMock()

        # Setup request exceptions
        class RequestException(Exception): pass
        self.mock_requests.exceptions.RequestException = RequestException
        self.mock_requests.exceptions.ConnectionError = type('ConnectionError', (RequestException,), {})
        self.mock_requests.exceptions.Timeout = type('Timeout', (RequestException,), {})

        # Mock Session
        self.mock_session = MagicMock()
        self.mock_requests.Session.return_value = self.mock_session

        # Patch sys.modules
        self.modules_patcher = patch.dict(sys.modules, {
            'aqt': self.mock_aqt,
            'aqt.mw': self.mock_mw,
            'requests': self.mock_requests
        })
        self.modules_patcher.start()

        # Provide a valid config so japanese_examples doesn't warn
        self.mock_mw.addonManager.getConfig.return_value = {
            "japaneseDstField": "Expression",
            "translationDstField": "Meaning"
        }

        # Import the module under test
        if 'src.core.japanese_examples' in sys.modules:
            del sys.modules['src.core.japanese_examples']
        import src.core.japanese_examples as japanese_examples
        self.japanese_examples = japanese_examples

    def tearDown(self):
        self.modules_patcher.stop()
        if 'src.core.japanese_examples' in sys.modules:
            del sys.modules['src.core.japanese_examples']

    # ── URL & params encoding ───────────────────────────────────────

    def test_find_japanese_sentence_encoding(self):
        """Should pass correct base URL and params to session.get."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        self.mock_session.get.return_value = mock_response

        word = "foo&bar"
        lang = "eng"
        self.japanese_examples.find_japanese_sentence(word, lang)

        self.assertTrue(self.mock_session.get.called, "session.get was not called")
        args, kwargs = self.mock_session.get.call_args

        self.assertEqual(args[0], "https://tatoeba.org/en/api_v0/search")
        self.assertIn('params', kwargs)

        expected_params = {
            'query': f'={word}',
            'from': 'jpn',
            'to': lang,
            'limit': 50
        }
        self.assertEqual(kwargs['params'], expected_params)

    def test_find_japanese_sentence_custom_max_results(self):
        """Should pass the max_results value as the limit param."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        self.mock_session.get.return_value = mock_response

        self.japanese_examples.find_japanese_sentence("word", "eng", max_results=10)

        _, kwargs = self.mock_session.get.call_args
        self.assertEqual(kwargs['params']['limit'], 10)

    # ── Response parsing ────────────────────────────────────────────

    def test_parse_valid_results(self):
        """Should extract jp_sentence and tr_sentence from valid API results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'text': '日本語の文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'A Japanese sentence'}]]
                }
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['jp_sentence'], '日本語の文')
        self.assertEqual(result[0]['tr_sentence'], 'A Japanese sentence')

    def test_parse_multiple_results(self):
        """Should return all valid sentence pairs."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'text': '文1',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Sentence 1'}]]
                },
                {
                    'text': '文2',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Sentence 2'}]]
                }
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['jp_sentence'], '文1')
        self.assertEqual(result[1]['jp_sentence'], '文2')

    # ── needsReview filtering ───────────────────────────────────────

    def test_filters_sentences_needing_review(self):
        """Should skip sentences where transcriptions[0].needsReview is True."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'text': '良い文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Good sentence'}]]
                },
                {
                    'text': 'レビュー必要',
                    'transcriptions': [{'needsReview': True}],
                    'translations': [[{'text': 'Needs review'}]]
                }
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['jp_sentence'], '良い文')

    # ── Missing translation ─────────────────────────────────────────

    def test_skips_result_without_translation(self):
        """Should skip results that have no translation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'text': '翻訳なし',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[]]
                },
                {
                    'text': '翻訳あり',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Has translation'}]]
                }
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['jp_sentence'], '翻訳あり')

    # ── No results ──────────────────────────────────────────────────

    def test_no_results_returns_message(self):
        """Should return a 'not found' string when API returns empty results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'results': []}
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("nonexistent", "eng")

        self.assertIsInstance(result, str)

    def test_no_results_key_returns_message(self):
        """Should return a 'not found' string when API response has no 'results' key."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("nonexistent", "eng")

        self.assertIsInstance(result, str)

    # ── jpn_id and has_audio fields ─────────────────────────────────

    def test_sentence_dict_contains_jpn_id(self):
        """jpn_id should be the str-cast sentence id from API response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 8858176,
                    'text': '猫だ！',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'It is a cat!'}]],
                    'audios': [],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response
        result = self.japanese_examples.find_japanese_sentence("猫", "eng")
        self.assertIsInstance(result, list)
        self.assertIn('jpn_id', result[0])
        self.assertEqual(result[0]['jpn_id'], "8858176")
        self.assertIsInstance(result[0]['jpn_id'], str)

    def test_sentence_dict_jpn_id_none_when_id_absent(self):
        """jpn_id should be None when API result has no 'id' key."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'text': '猫だ！',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'It is a cat!'}]],
                    'audios': [],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response
        result = self.japanese_examples.find_japanese_sentence("猫", "eng")
        self.assertIsInstance(result, list)
        self.assertIsNone(result[0]['jpn_id'])

    def test_sentence_dict_has_audio_true_when_audios_non_empty(self):
        """has_audio should be True when audios array is non-empty."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 8858176,
                    'text': '猫だ！',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'It is a cat!'}]],
                    'audios': [{'id': 1046383, 'author': 'CVjpn1'}],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response
        result = self.japanese_examples.find_japanese_sentence("猫", "eng")
        self.assertIsInstance(result, list)
        self.assertTrue(result[0]['has_audio'])
        self.assertIsInstance(result[0]['has_audio'], bool)

    def test_sentence_dict_has_audio_false_when_audios_empty(self):
        """has_audio should be False when audios array is empty."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 8858176,
                    'text': '猫だ！',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'It is a cat!'}]],
                    'audios': [],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response
        result = self.japanese_examples.find_japanese_sentence("猫", "eng")
        self.assertIsInstance(result, list)
        self.assertFalse(result[0]['has_audio'])
        self.assertIsInstance(result[0]['has_audio'], bool)

    def test_sentence_dict_has_audio_false_when_audios_key_absent(self):
        """has_audio should be False when API result has no 'audios' key."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 8858176,
                    'text': '猫だ！',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'It is a cat!'}]],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response
        result = self.japanese_examples.find_japanese_sentence("猫", "eng")
        self.assertIsInstance(result, list)
        self.assertFalse(result[0]['has_audio'])

    # ── max_results capping ─────────────────────────────────────────

    def test_results_capped_at_max_results(self):
        """Should return at most max_results sentences."""
        results_data = [
            {
                'text': f'文{i}',
                'transcriptions': [{'needsReview': False}],
                'translations': [[{'text': f'Sentence {i}'}]]
            }
            for i in range(10)
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'results': results_data}
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng", max_results=3)

        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 3)

    # ── audio-first sort ────────────────────────────────────────────

    def test_audio_sentences_sorted_before_non_audio(self):
        """Sentences with audio should appear before sentences without audio."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 1,
                    'text': '音声なし文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'No audio sentence'}]],
                    'audios': [],
                },
                {
                    'id': 2,
                    'text': '音声あり文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Audio sentence'}]],
                    'audios': [{'id': 99, 'author': 'speaker'}],
                },
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        # Audio sentence must come first regardless of API order
        self.assertTrue(result[0]['has_audio'], "First result should have audio")
        self.assertFalse(result[1]['has_audio'], "Second result should not have audio")

    def test_all_audio_sentences_preserve_relative_order(self):
        """When all sentences have audio, their relative order should be preserved."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 10,
                    'text': '最初の文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'First sentence'}]],
                    'audios': [{'id': 1, 'author': 'a'}],
                },
                {
                    'id': 20,
                    'text': '二番目の文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Second sentence'}]],
                    'audios': [{'id': 2, 'author': 'b'}],
                },
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['jpn_id'], '10')
        self.assertEqual(result[1]['jpn_id'], '20')

    def test_all_non_audio_sentences_preserve_relative_order(self):
        """When no sentences have audio, their relative order should be preserved."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 10,
                    'text': '最初の文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'First sentence'}]],
                    'audios': [],
                },
                {
                    'id': 20,
                    'text': '二番目の文',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Second sentence'}]],
                    'audios': [],
                },
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['jpn_id'], '10')
        self.assertEqual(result[1]['jpn_id'], '20')

    def test_audio_sort_applies_before_max_results_cap(self):
        """Audio-first sort should happen before the max_results slice so that
        audio sentences are not accidentally dropped by the cap."""
        # API returns 3 non-audio then 1 audio; with max_results=3 the audio
        # sentence must still appear (at index 0) after sort+cap.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': i,
                    'text': f'非音声{i}',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': f'No audio {i}'}]],
                    'audios': [],
                }
                for i in range(1, 4)
            ] + [
                {
                    'id': 4,
                    'text': '音声あり',
                    'transcriptions': [{'needsReview': False}],
                    'translations': [[{'text': 'Has audio'}]],
                    'audios': [{'id': 99, 'author': 'x'}],
                }
            ]
        }
        self.mock_session.get.return_value = mock_response

        result = self.japanese_examples.find_japanese_sentence("test", "eng", max_results=3)

        self.assertEqual(len(result), 3)
        self.assertTrue(result[0]['has_audio'], "Audio sentence should be first after sort+cap")


if __name__ == '__main__':
    unittest.main()
