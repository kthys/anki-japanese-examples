import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json
import datetime
import bz2

# Mock aqt before importing our module
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.mw'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['aqt.qt'] = MagicMock()

# Import freshly
if "tatoeba_data" in sys.modules:
    del sys.modules["tatoeba_data"]
import tatoeba_data

class TestTatoebaData(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_user_files_dir = tatoeba_data.USER_FILES_DIR
        self.original_metadata_file = tatoeba_data.METADATA_FILE
        tatoeba_data.USER_FILES_DIR = self.temp_dir
        tatoeba_data.METADATA_FILE = os.path.join(self.temp_dir, "metadata.json")

    def tearDown(self):
        # Clean up temp dir
        for filename in os.listdir(self.temp_dir):
            file_path = os.path.join(self.temp_dir, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
        os.rmdir(self.temp_dir)
        tatoeba_data.USER_FILES_DIR = self.original_user_files_dir
        tatoeba_data.METADATA_FILE = self.original_metadata_file

    def test_lang_map_contains_english_and_french(self):
        self.assertIn("English", tatoeba_data.LANG_MAP)
        self.assertIn("French", tatoeba_data.LANG_MAP)
        self.assertEqual(tatoeba_data.LANG_MAP["English"], "eng")
        self.assertEqual(tatoeba_data.LANG_MAP["French"], "fra")

    def test_get_download_urls_english(self):
        urls = tatoeba_data.get_download_urls("eng")
        self.assertEqual(urls["jpn_sentences"], f"{tatoeba_data.TATOEBA_BASE_URL}/jpn/jpn_sentences.tsv.bz2")
        self.assertEqual(urls["target_sentences"], f"{tatoeba_data.TATOEBA_BASE_URL}/eng/eng_sentences.tsv.bz2")
        self.assertEqual(urls["links"], f"{tatoeba_data.TATOEBA_BASE_URL}/jpn/jpn-eng_links.tsv.bz2")

    def test_get_download_urls_french(self):
        urls = tatoeba_data.get_download_urls("fra")
        self.assertEqual(urls["jpn_sentences"], f"{tatoeba_data.TATOEBA_BASE_URL}/jpn/jpn_sentences.tsv.bz2")
        self.assertEqual(urls["target_sentences"], f"{tatoeba_data.TATOEBA_BASE_URL}/fra/fra_sentences.tsv.bz2")
        self.assertEqual(urls["links"], f"{tatoeba_data.TATOEBA_BASE_URL}/jpn/jpn-fra_links.tsv.bz2")

    def test_get_data_file_path(self):
        path = tatoeba_data.get_data_file_path("eng")
        self.assertEqual(path, os.path.join(tatoeba_data.USER_FILES_DIR, "jpn_eng_pairs.tsv"))

    def test_build_pairs_tsv_basic(self):
        jpn_sentences = "1\tjpn\t猫が好き\n2\tjpn\t犬が好き"
        eng_sentences = "100\teng\tI like cats\n101\teng\tI like dogs"
        links = "1\t100\n2\t101"
        
        result = tatoeba_data.build_pairs_tsv(jpn_sentences, eng_sentences, links)
        expected = "1\t猫が好き\t100\tI like cats\n2\t犬が好き\t101\tI like dogs\n"
        self.assertEqual(result, expected)

    def test_build_pairs_tsv_missing_link(self):
        jpn_sentences = "1\tjpn\t猫が好き"
        eng_sentences = "100\teng\tI like cats"
        links = "1\t101\n2\t100" # Mismatched IDs
        
        result = tatoeba_data.build_pairs_tsv(jpn_sentences, eng_sentences, links)
        self.assertEqual(result, "")

    def test_build_pairs_tsv_empty_input(self):
        self.assertEqual(tatoeba_data.build_pairs_tsv("", "", ""), "")

    @patch('tatoeba_data.requests.get')
    def test_download_tatoeba_data_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = bz2.compress(b"mock tsv content")
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with patch('tatoeba_data.build_pairs_tsv', return_value="1\t猫\t100\tcat\n"):
            success, msg = tatoeba_data.download_tatoeba_data("English")
            self.assertTrue(success)
            self.assertIn("1", msg) # The count should be 1
            
            # Verify file was written
            file_path = tatoeba_data.get_data_file_path("eng")
            self.assertTrue(os.path.exists(file_path))
            with open(file_path, "r", encoding="utf-8") as f:
                 self.assertEqual(f.read(), "1\t猫\t100\tcat\n")

            # Verify metadata was written
            self.assertTrue(os.path.exists(tatoeba_data.METADATA_FILE))
            with open(tatoeba_data.METADATA_FILE, "r", encoding="utf-8") as f:
                md = json.load(f)
                self.assertIn("eng", md)
                self.assertEqual(md["eng"]["count"], 1)

    @patch('tatoeba_data.requests.get')
    def test_download_tatoeba_data_network_error(self, mock_get):
        mock_get.side_effect = tatoeba_data.requests.exceptions.RequestException("Connection refused")
        
        success, msg = tatoeba_data.download_tatoeba_data("English")
        self.assertFalse(success)
        self.assertIn("Connection", msg)

    def test_load_index_builds_dict(self):
        file_path = tatoeba_data.get_data_file_path("eng")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("1\t猫が好き\t100\tI like cats\n1\t猫が好き\t101\tI really like cats\n")
            
        index = tatoeba_data.load_index("English")
        self.assertIsNotNone(index)
        self.assertIn("猫が好き", index)
        self.assertEqual(len(index["猫が好き"]), 2)
        self.assertEqual(index["猫が好き"][0], ("猫が好き", "I like cats"))
        self.assertEqual(index["猫が好き"][1], ("猫が好き", "I really like cats"))

    def test_load_index_missing_file(self):
        index = tatoeba_data.load_index("French")
        self.assertIsNone(index)

    def test_is_data_available_true(self):
        file_path = tatoeba_data.get_data_file_path("eng")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("dummy")
        self.assertTrue(tatoeba_data.is_data_available("English"))

    def test_is_data_available_false(self):
        self.assertFalse(tatoeba_data.is_data_available("French"))

    def test_get_file_status_after_download(self):
        now_str = datetime.datetime.now().isoformat()
        metadata = {
            "eng": {
                "downloaded_at": now_str,
                "count": 42
            }
        }
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f)
            
        status = tatoeba_data.get_file_status("English")
        self.assertEqual(status, now_str)
        self.assertIsNone(tatoeba_data.get_file_status("French"))

    # ── SQLite index tests ──────────────────────────────────────────

    def test_get_db_path(self):
        """get_db_path should return a .db file path in USER_FILES_DIR."""
        path = tatoeba_data.get_db_path("eng")
        self.assertEqual(path, os.path.join(tatoeba_data.USER_FILES_DIR, "jpn_eng_index.db"))

    def test_tokenize_japanese_basic(self):
        """tokenize_japanese should extract kanji, katakana, and hiragana runs."""
        tokens = tatoeba_data.tokenize_japanese("猫が好きです。")
        self.assertIn("猫", tokens)
        self.assertIn("が", tokens)
        self.assertIn("好", tokens)
        self.assertIn("きです", tokens)

    def test_tokenize_japanese_unique(self):
        """tokenize_japanese should return unique tokens only."""
        tokens = tatoeba_data.tokenize_japanese("猫と猫")
        self.assertEqual(tokens.count("猫"), 1)

    def test_build_sqlite_index_and_search(self):
        """build_sqlite_index should create a searchable DB from TSV data."""
        tsv_path = os.path.join(self.temp_dir, "test_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "test_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t猫が好きです。\t100\tI like cats.\n")
            f.write("2\t犬が好きです。\t101\tI like dogs.\n")
            f.write("3\t花火が綺麗だ。\t102\tThe fireworks are beautiful.\n")

        count = tatoeba_data.build_sqlite_index(tsv_path, db_path)
        self.assertEqual(count, 3)
        self.assertTrue(os.path.exists(db_path))

        # Search for 猫 — should find sentence 1
        results = tatoeba_data.search_word(db_path, "猫")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "猫が好きです。")
        self.assertEqual(results[0][1], "I like cats.")

        # Search for 好 — should find sentences 1 and 2 (shared kanji)
        results = tatoeba_data.search_word(db_path, "好")
        self.assertEqual(len(results), 2)

        # Clean up db
        os.remove(db_path)

    def test_search_word_strict_boundaries(self):
        """search_word must NOT match 花火 when searching for 火 (strict word boundary)."""
        tsv_path = os.path.join(self.temp_dir, "boundary_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "boundary_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t花火が綺麗だ。\t100\tThe fireworks are beautiful.\n")
            f.write("2\t火が燃えている。\t101\tThe fire is burning.\n")

        tatoeba_data.build_sqlite_index(tsv_path, db_path)

        # 火 should NOT match 花火 (花火 is a single kanji-run token)
        results = tatoeba_data.search_word(db_path, "火")
        jpn_texts = [r[0] for r in results]
        self.assertIn("火が燃えている。", jpn_texts)
        self.assertNotIn("花火が綺麗だ。", jpn_texts)

        # 花火 should match sentence 1
        results = tatoeba_data.search_word(db_path, "花火")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "花火が綺麗だ。")

        # Clean up
        os.remove(db_path)

    def test_search_word_mixed_token_and_inflection(self):
        """search_word must support mixed tokens and inflections (e.g. 負ける)."""
        tsv_path = os.path.join(self.temp_dir, "inflection_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "inflection_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t試験に負けるな。\t100\tDon't lose the exam.\n")
            f.write("2\tもう負けました。\t101\tI gave up.\n")
            f.write("3\t間もなく電車が来ます。\t102\tTrain comes shortly.\n")
            f.write("4\tありがとうございます。\t103\tThank you.\n")

        tatoeba_data.build_sqlite_index(tsv_path, db_path)

        # 負ける should match 負けるな but not 負けました
        results = tatoeba_data.search_word(db_path, "負ける")
        jpn_texts = [r[0] for r in results]
        self.assertIn("試験に負けるな。", jpn_texts)
        self.assertNotIn("もう負けました。", jpn_texts)

        # 間もなく should match (mixed tokens '間' and 'もなく')
        results = tatoeba_data.search_word(db_path, "間もなく")
        jpn_texts = [r[0] for r in results]
        self.assertIn("間もなく電車が来ます。", jpn_texts)

        # ありがとう should match ありがとうございます (kana prefix match)
        results = tatoeba_data.search_word(db_path, "ありがとう")
        jpn_texts = [r[0] for r in results]
        self.assertIn("ありがとうございます。", jpn_texts)

        # Clean up
        os.remove(db_path)

    def test_search_word_missing_db(self):
        """search_word should return empty list for nonexistent DB."""
        results = tatoeba_data.search_word("/nonexistent/path.db", "猫")
        self.assertEqual(results, [])

    def test_download_tatoeba_data_calls_progress_callback(self):
        """progress_callback should be called at least 5 times during download."""
        mock_response = MagicMock()
        mock_response.content = bz2.compress(b"mock tsv content")
        mock_response.raise_for_status.return_value = None

        callback = MagicMock()

        with patch('tatoeba_data.requests.get', return_value=mock_response):
            with patch('tatoeba_data.build_pairs_tsv', return_value="1\t猫\t100\tcat\n"):
                tatoeba_data.download_tatoeba_data("English", progress_callback=callback)

        self.assertGreaterEqual(callback.call_count, 5)

if __name__ == '__main__':
    unittest.main()

