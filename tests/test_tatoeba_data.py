import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json
import datetime
import bz2
import io
import tarfile
import shutil
import sqlite3 as _sqlite3
import requests

try:
    import responses
except ImportError:
    responses = None

# Mock aqt before importing our module
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.mw'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['aqt.qt'] = MagicMock()

# Import freshly
if "src.core.tatoeba_data" in sys.modules:
    del sys.modules["src.core.tatoeba_data"]
import src.core.tatoeba_data as tatoeba_data


def _streaming_response(payload, status=200, headers=None, chunk=7):
    """Build a MagicMock response that streams ``payload`` in small chunks.

    Each call to ``iter_content`` returns a fresh iterator over the same chunk
    list, so the mock survives multiple retry attempts against the same object.
    """
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status.return_value = None
    resp.headers = headers if headers is not None else {}
    chunks = [payload[i:i + chunk] for i in range(0, len(payload), chunk)]
    resp.iter_content.side_effect = lambda *a, **k: iter(chunks)
    resp.close.return_value = None
    return resp


def _error_response(status):
    """Build a MagicMock response whose ``raise_for_status`` raises HTTPError(status)."""
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
        f"{status} error", response=resp)
    resp.headers = {}
    resp.close.return_value = None
    return resp


def _write_bz2(path, text):
    """Compress ``text`` (utf-8, newlines preserved) into a .bz2 file at ``path``."""
    with bz2.open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def _write_tar_bz2(path, members):
    """Build a tar.bz2 archive at ``path`` from a {filename: content_str} dict."""
    with tarfile.open(path, "w:bz2") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _tar_bz2_bytes(members):
    """Build an in-memory tar.bz2 from a {filename: content_str} dict."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _payloads(lang, jpn, target, links, audio_member):
    """Return {url: compressed-bytes} fixtures for a staged download_to_file mock."""
    urls = tatoeba_data.get_download_urls(lang)
    return {
        urls["jpn_sentences"]: bz2.compress(jpn.encode("utf-8")),
        urls["target_sentences"]: bz2.compress(target.encode("utf-8")),
        urls["links"]: bz2.compress(links.encode("utf-8")),
        tatoeba_data.AUDIO_INDEX_URL: _tar_bz2_bytes(
            {"sentences_with_audio.csv": audio_member}),
    }


def _patch_download(payloads_holder, fail_urls=()):
    """Patch download_to_file to write fixture bytes (from a 1-element list
    holder — swap the dict between runs for the double-download test) or raise
    for urls in ``fail_urls``."""
    def side_effect(url, dest_path, *args, **kwargs):
        if url in fail_urls:
            raise requests.exceptions.RequestException(
                "simulated download failure: " + url)
        with open(dest_path, "wb") as f:
            f.write(payloads_holder[0][url])
    return patch("src.core.tatoeba_data.download_to_file", side_effect=side_effect)


def _dict_join_reference(jpn_text, target_text, links_text, audio_text):
    """Reference implementation of the old dict-join, for golden equivalence.

    Reproduces the former build_pairs_tsv + has_audio semantics exactly:
    last-wins sentence id, every valid link row -> one output row (duplicates
    preserved), has_audio=1 iff jpn id is an all-digit audio id.
    """
    def _parse(text):
        d = {}
        if text and text.strip():
            for line in text.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    d[parts[0]] = parts[2]
        return d

    jpn = _parse(jpn_text)
    target = _parse(target_text)
    audio = set()
    if audio_text and audio_text.strip():
        for line in audio_text.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if parts and parts[0].isdigit():
                audio.add(parts[0])
    out = []
    if links_text and links_text.strip():
        for line in links_text.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                jid, tid = parts[0], parts[1]
                if jid in jpn and tid in target:
                    out.append((jid, jpn[jid], tid, target[tid],
                               1 if jid in audio else 0))
    return out


def _sql_join_via_staging(tmp, jpn_text, target_text, links_text, audio_member_text):
    """Build a staging DB from the same raw texts and run the production join."""
    db_path = os.path.join(tmp, "golden_staging.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    jp = os.path.join(tmp, "g_jpn.tsv.bz2"); _write_bz2(jp, jpn_text)
    tp = os.path.join(tmp, "g_target.tsv.bz2"); _write_bz2(tp, target_text)
    lp = os.path.join(tmp, "g_links.tsv.bz2"); _write_bz2(lp, links_text)
    ap = os.path.join(tmp, "g_audio.tar.bz2")
    _write_tar_bz2(ap, {"sentences_with_audio.csv": audio_member_text})

    conn = tatoeba_data._create_staging_db(db_path)
    try:
        tatoeba_data._import_sentences(jp, conn, "jpn")
        tatoeba_data._import_sentences(tp, conn, "target")
        tatoeba_data._import_links(lp, conn)
        tatoeba_data._import_audio(ap, conn)
        cur = conn.execute("""
            SELECT j.id, j.text, t.id, t.text,
                   CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END AS has_audio
            FROM links l
            JOIN jpn    j ON j.id = l.jpn_id
            JOIN target t ON t.id = l.target_id
            LEFT JOIN audio a ON a.id = j.id
            ORDER BY l.rowid
        """)
        return cur.fetchall()
    finally:
        conn.close()


class TestTatoebaData(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_user_files_dir = tatoeba_data.USER_FILES_DIR
        self.original_metadata_file = tatoeba_data.METADATA_FILE
        tatoeba_data.USER_FILES_DIR = self.temp_dir
        tatoeba_data.METADATA_FILE = os.path.join(self.temp_dir, "metadata.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        tatoeba_data.USER_FILES_DIR = self.original_user_files_dir
        tatoeba_data.METADATA_FILE = self.original_metadata_file

    def _no_leftover_workdirs(self):
        leftover = [n for n in os.listdir(self.temp_dir)
                    if n.startswith(tatoeba_data.WORKDIR_PREFIX)]
        self.assertEqual(leftover, [], f"leftover workdirs: {leftover}")

    def _pairs_tsv_path(self, lang):
        """The *old* pairs TSV path — must never be written by the new pipeline."""
        return os.path.join(self.temp_dir, f"jpn_{lang}_pairs.tsv")

    def test_registry_supports_all_five_languages(self):
        """The language registry covers all 5 supported languages with correct codes."""
        for code in ("eng", "fra", "spa", "cmn", "kor"):
            self.assertTrue(tatoeba_data.is_supported(code))
        self.assertFalse(tatoeba_data.is_supported("klingon"))
        self.assertEqual(tatoeba_data.get_localized_name("spa"), "Spanish")
        self.assertEqual(tatoeba_data.get_localized_name("cmn"), "Chinese (Simplified)")
        self.assertEqual(tatoeba_data.get_localized_name("kor"), "Korean")

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

    def test_get_db_path(self):
        """get_db_path should return a .db file path in USER_FILES_DIR."""
        path = tatoeba_data.get_db_path("eng")
        self.assertEqual(path, os.path.join(tatoeba_data.USER_FILES_DIR, "jpn_eng_index.db"))

    def test_is_data_available_true(self):
        """is_data_available is True when the index DB file exists."""
        with open(tatoeba_data.get_db_path("eng"), "wb") as f:
            f.write(b"")
        self.assertTrue(tatoeba_data.is_data_available("eng"))

    def test_is_data_available_false(self):
        """is_data_available is False when no index DB exists."""
        self.assertFalse(tatoeba_data.is_data_available("fra"))

    def test_is_data_available_unknown_language(self):
        """Unknown languages are never available even if a stray DB file exists."""
        self.assertFalse(tatoeba_data.is_data_available("klingon"))

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

        status = tatoeba_data.get_file_status("eng")
        self.assertEqual(status, now_str)
        self.assertIsNone(tatoeba_data.get_file_status("fra"))

    def test_get_file_status_corrupt_metadata(self):
        """Corrupt metadata.json is tolerated (get_file_status returns None)."""
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertIsNone(tatoeba_data.get_file_status("eng"))

    # ── SQLite index tests ──────────────────────────────────────────

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

        count = tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)
        self.assertEqual(count, 3)
        self.assertTrue(os.path.exists(db_path))

        # Search for 猫 — should find sentence 1
        results = tatoeba_data.search_word(db_path, "猫")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "猫が好きです。")
        self.assertEqual(results[0][2], "I like cats.")

        # Search for 好 — should find sentences 1 and 2 (shared kanji)
        results = tatoeba_data.search_word(db_path, "好")
        self.assertEqual(len(results), 2)

        # No temp build file may remain after a successful build
        self.assertFalse(os.path.exists(db_path + ".tmp"))

        os.remove(db_path)

    def test_build_sqlite_index_failure_preserves_previous_db(self):
        """A failed rebuild must leave the previous complete index untouched
        and not leave a partial .tmp file behind."""
        tsv_path = os.path.join(self.temp_dir, "atomic_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "atomic_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t猫が好きです。\t100\tI like cats.\n")
        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        # Rebuild from a missing TSV — must raise, old index must survive
        with self.assertRaises(Exception):
            tatoeba_data.build_sqlite_index(
                tatoeba_data._rows_from_tsv(
                    os.path.join(self.temp_dir, "does_not_exist.tsv")),
                db_path)

        self.assertFalse(os.path.exists(db_path + ".tmp"))
        results = tatoeba_data.search_word(db_path, "猫")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "猫が好きです。")

        os.remove(db_path)

    def test_build_sqlite_index_rebuild_replaces_old_content(self):
        """Rebuilding onto an existing db must fully replace its contents."""
        tsv_path = os.path.join(self.temp_dir, "rebuild_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "rebuild_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t猫が好きです。\t100\tI like cats.\n")
        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("2\t犬が好きです。\t101\tI like dogs.\n")
        count = tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        self.assertEqual(count, 1)
        self.assertEqual(tatoeba_data.search_word(db_path, "猫"), [])
        results = tatoeba_data.search_word(db_path, "犬")
        self.assertEqual(len(results), 1)

        os.remove(db_path)

    def test_search_word_strict_boundaries(self):
        """search_word must NOT match 花火 when searching for 火 (strict word boundary)."""
        tsv_path = os.path.join(self.temp_dir, "boundary_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "boundary_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("1\t花火が綺麗だ。\t100\tThe fireworks are beautiful.\n")
            f.write("2\t火が燃えている。\t101\tThe fire is burning.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        # 火 should NOT match 花火 (花火 is a single kanji-run token)
        results = tatoeba_data.search_word(db_path, "火")
        jpn_texts = [r[1] for r in results]
        self.assertIn("火が燃えている。", jpn_texts)
        self.assertNotIn("花火が綺麗だ。", jpn_texts)

        # 花火 should match sentence 1
        results = tatoeba_data.search_word(db_path, "花火")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "花火が綺麗だ。")

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

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        # 負ける should match 負けるな but not 負けました
        results = tatoeba_data.search_word(db_path, "負ける")
        jpn_texts = [r[1] for r in results]
        self.assertIn("試験に負けるな。", jpn_texts)
        self.assertNotIn("もう負けました。", jpn_texts)

        # 間もなく should match (mixed tokens '間' and 'もなく')
        results = tatoeba_data.search_word(db_path, "間もなく")
        jpn_texts = [r[1] for r in results]
        self.assertIn("間もなく電車が来ます。", jpn_texts)

        # ありがとう should match ありがとうございます (kana prefix match)
        results = tatoeba_data.search_word(db_path, "ありがとう")
        jpn_texts = [r[1] for r in results]
        self.assertIn("ありがとうございます。", jpn_texts)

        os.remove(db_path)

    def test_search_word_returns_jpn_id(self):
        """search_word must return (jpn_id, jpn_text, trans_text, has_audio) 4-tuples where jpn_id matches the sentences table."""
        tsv_path = os.path.join(self.temp_dir, "jpnid_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "jpnid_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("42\t猫が好きです。\t100\tI like cats.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)
        results = tatoeba_data.search_word(db_path, "猫")

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 4)           # 4-tuple, not triple
        self.assertEqual(results[0][0], "42")          # jpn_id matches sentences table
        self.assertEqual(results[0][1], "猫が好きです。")
        self.assertEqual(results[0][2], "I like cats.")
        self.assertEqual(results[0][3], 0)             # has_audio defaults to 0

        os.remove(db_path)

    def test_search_word_missing_db(self):
        """search_word should return empty list for nonexistent DB."""
        results = tatoeba_data.search_word("/nonexistent/path.db", "猫")
        self.assertEqual(results, [])

    def test_build_sqlite_index_with_audio_ids(self):
        """build_sqlite_index with audio_ids={"42"} sets has_audio=1 on matching row and 0 on non-matching."""
        tsv_path = os.path.join(self.temp_dir, "audio_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "audio_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("42\t猫が好きです。\t100\tI like cats.\n")
            f.write("99\t犬が好きです。\t101\tI like dogs.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path, audio_ids={"42"}), db_path)

        conn = _sqlite3.connect(db_path)
        rows = dict(conn.execute("SELECT jpn_id, has_audio FROM sentences").fetchall())
        conn.close()
        os.remove(db_path)

        self.assertEqual(rows["42"], 1)
        self.assertEqual(rows["99"], 0)

    def test_build_sqlite_index_without_audio_ids_backward_compat(self):
        """build_sqlite_index with no audio_ids leaves has_audio=0 on all rows."""
        tsv_path = os.path.join(self.temp_dir, "noaudio_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "noaudio_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("10\t花が咲く。\t200\tThe flowers bloom.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path), db_path)

        conn = _sqlite3.connect(db_path)
        rows = conn.execute("SELECT has_audio FROM sentences").fetchall()
        conn.close()
        os.remove(db_path)

        self.assertEqual(rows[0][0], 0)

    def test_search_word_returns_4_tuple_with_has_audio(self):
        """search_word returns 4-tuples where results[0][3] == 1 for audio-marked rows."""
        tsv_path = os.path.join(self.temp_dir, "s4t_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "s4t_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("42\t猫が好きです。\t100\tI like cats.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path, audio_ids={"42"}), db_path)
        results = tatoeba_data.search_word(db_path, "猫")
        os.remove(db_path)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 4)
        self.assertEqual(results[0][3], 1)

    def test_search_word_returns_has_audio_zero_for_non_audio(self):
        """search_word returns has_audio=0 for rows not in audio_ids."""
        tsv_path = os.path.join(self.temp_dir, "s4n_pairs.tsv")
        db_path = os.path.join(self.temp_dir, "s4n_index.db")

        with open(tsv_path, "w", encoding="utf-8") as f:
            f.write("99\t犬が好きです。\t101\tI like dogs.\n")

        tatoeba_data.build_sqlite_index(
            tatoeba_data._rows_from_tsv(tsv_path, audio_ids=set()), db_path)
        results = tatoeba_data.search_word(db_path, "犬")
        os.remove(db_path)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][3], 0)

    def test_build_sqlite_index_from_rows_direct_has_audio(self):
        """has_audio comes from the 5th tuple element, not a separate set (new rows API)."""
        db_path = os.path.join(self.temp_dir, "direct_index.db")
        rows = [
            ("42", "猫が好きです。", "100", "I like cats.", 1),
            ("99", "犬が好きです。", "101", "I like dogs.", 0),
        ]
        count = tatoeba_data.build_sqlite_index(rows, db_path)
        self.assertEqual(count, 2)

        conn = _sqlite3.connect(db_path)
        rows_map = dict(conn.execute("SELECT jpn_id, has_audio FROM sentences").fetchall())
        conn.close()
        os.remove(db_path)
        self.assertEqual(rows_map, {"42": 1, "99": 0})

    def test_build_sqlite_index_accepts_lazy_iterator(self):
        """build_sqlite_index consumes any one-shot iterable (e.g. a generator),
        not just a list — the staging SQL cursor relies on this."""
        db_path = os.path.join(self.temp_dir, "iter_index.db")

        def gen():
            yield ("1", "猫が好き。", "10", "I like cats.", 0)
            yield ("2", "犬が好き。", "11", "I like dogs.", 0)

        count = tatoeba_data.build_sqlite_index(gen(), db_path)
        self.assertEqual(count, 2)
        self.assertEqual(len(tatoeba_data.search_word(db_path, "犬")), 1)
        os.remove(db_path)

    # ── download_to_file tests (streaming plan step 1) ─────────────

    def test_download_to_file_writes_exact_bytes(self):
        """download_to_file streams multiple chunks and writes exact bytes to dest."""
        payload = bytes(range(256)) * 8  # 2048 bytes
        resp = _streaming_response(payload, chunk=17)
        dest = os.path.join(self.temp_dir, "out.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", return_value=resp) as mg:
            tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        self.assertEqual(mg.call_count, 1)
        self.assertTrue(os.path.exists(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        # No partial file may linger after success.
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_download_to_file_verifies_content_length(self):
        """When Content-Length matches the received bytes, the download succeeds."""
        payload = b"hello tatoeba\n" * 50
        resp = _streaming_response(
            payload, headers={"Content-Length": str(len(payload))})
        dest = os.path.join(self.temp_dir, "cl_ok.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", return_value=resp):
            tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_download_to_file_truncation_raises_and_retries(self):
        """A Content-Length mismatch raises ConnectionError, is retried, then fails.

        ConnectionError is a retriable condition, so all three attempts run before
        the error surfaces; the partial ``.part`` file must be cleaned up.
        """
        payload = b"only eleven"  # 11 bytes
        resp = _streaming_response(
            payload, headers={"Content-Length": "999"})  # claims 999 → mismatch
        dest = os.path.join(self.temp_dir, "trunc.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", return_value=resp) as mg, \
             patch("src.core.tatoeba_data.DOWNLOAD_RETRY_BACKOFF", (0, 0)):
            with self.assertRaises(requests.exceptions.ConnectionError):
                tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        self.assertEqual(mg.call_count, 3)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_download_to_file_retries_on_500_then_succeeds(self):
        """A 500 response is retried and a following 200 succeeds."""
        err = _error_response(500)
        ok = _streaming_response(b"good bytes")
        dest = os.path.join(self.temp_dir, "five00.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", side_effect=[err, ok]) as mg, \
             patch("src.core.tatoeba_data.DOWNLOAD_RETRY_BACKOFF", (0,)):
            tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        self.assertEqual(mg.call_count, 2)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"good bytes")

    def test_download_to_file_retries_on_timeout_then_succeeds(self):
        """A read Timeout during streaming is retried, then the next attempt succeeds."""
        bad = MagicMock()
        bad.status_code = 200
        bad.raise_for_status.return_value = None
        bad.headers = {}
        bad.iter_content.side_effect = lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ReadTimeout("read timed out"))
        bad.close.return_value = None

        ok = _streaming_response(b"good bytes")
        dest = os.path.join(self.temp_dir, "timeout.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", side_effect=[bad, bad, ok]) as mg, \
             patch("src.core.tatoeba_data.DOWNLOAD_RETRY_BACKOFF", (0, 0)):
            tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        self.assertEqual(mg.call_count, 3)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"good bytes")
        bad.close.assert_called()
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_download_to_file_no_retry_on_404(self):
        """A 404 is never retried and raises HTTPError immediately."""
        err = _error_response(404)
        dest = os.path.join(self.temp_dir, "notfound.tsv.bz2")

        with patch("src.core.tatoeba_data.requests.get", return_value=err) as mg, \
             patch("src.core.tatoeba_data.DOWNLOAD_RETRY_BACKOFF", (0, 0)):
            with self.assertRaises(requests.exceptions.HTTPError):
                tatoeba_data.download_to_file("https://example.org/x.bz2", dest)

        self.assertEqual(mg.call_count, 1)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    # ── staging import tests (streaming plan step 2) ───────────────

    def _new_staging_db(self):
        """Create a staging DB in the temp dir and return (conn, db_path)."""
        db_path = os.path.join(self.temp_dir, "staging.db")
        conn = tatoeba_data._create_staging_db(db_path)
        return conn, db_path

    def test_create_staging_db_schema(self):
        """_create_staging_db builds jpn/target/links/audio tables, all empty."""
        conn, db_path = self._new_staging_db()
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertEqual(tables, {"jpn", "target", "links", "audio"})
            for t in ("jpn", "target", "links", "audio"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0)
        finally:
            conn.close()
        self.assertTrue(os.path.exists(db_path))

    def test_import_sentences_writes_rows(self):
        """_import_sentences streams a bz2 sentence file into the jpn table."""
        fixture = "1\tjpn\t猫が好き\n2\tjpn\t犬が好き\n3\tjpn\t鳥が好き\n"
        path = os.path.join(self.temp_dir, "jpn.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_sentences(path, conn, "jpn")
            rows = dict(conn.execute("SELECT id, text FROM jpn ORDER BY id").fetchall())
            self.assertEqual(rows, {"1": "猫が好き", "2": "犬が好き", "3": "鳥が好き"})
        finally:
            conn.close()

    def test_import_sentences_duplicate_id_last_wins(self):
        """Duplicate sentence ids resolve last-wins via INSERT OR REPLACE."""
        fixture = "1\tjpn\t猫\n1\tjpn\t犬\n"  # id collision: 猫 then 犬
        path = os.path.join(self.temp_dir, "dup.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_sentences(path, conn, "jpn")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jpn").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT text FROM jpn").fetchone()[0], "犬")
        finally:
            conn.close()

    def test_import_sentences_skips_short_lines(self):
        """Lines with fewer than 3 tab columns and empty lines are skipped."""
        fixture = "1\tjpn\n\nx\n2\tjpn\t犬\n"  # '1\tjpn'(2 cols), empty, 'x'(1 col) skipped
        path = os.path.join(self.temp_dir, "short.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_sentences(path, conn, "jpn")
            rows = dict(conn.execute("SELECT id, text FROM jpn").fetchall())
            self.assertEqual(rows, {"2": "犬"})
        finally:
            conn.close()

    def test_import_sentences_target_table(self):
        """_import_sentences writes to the target table when asked."""
        fixture = "100\teng\tI like cats\n101\teng\tI like dogs\n"
        path = os.path.join(self.temp_dir, "eng.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_sentences(path, conn, "target")
            rows = dict(conn.execute("SELECT id, text FROM target ORDER BY id").fetchall())
            self.assertEqual(rows, {"100": "I like cats", "101": "I like dogs"})
            # jpn table must remain untouched
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jpn").fetchone()[0], 0)
        finally:
            conn.close()

    def test_import_sentences_rejects_bad_table(self):
        """A table name other than jpn/target raises ValueError (guards SQL interp)."""
        path = os.path.join(self.temp_dir, "bad.tsv.bz2")
        _write_bz2(path, "1\tjpn\t猫\n")
        conn, _ = self._new_staging_db()
        try:
            with self.assertRaises(ValueError):
                tatoeba_data._import_sentences(path, conn, "links")
        finally:
            conn.close()

    def test_import_sentences_empty_input(self):
        """An empty bz2 imports successfully with zero rows (parity with today)."""
        path = os.path.join(self.temp_dir, "empty.tsv.bz2")
        _write_bz2(path, "")
        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_sentences(path, conn, "jpn")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jpn").fetchone()[0], 0)
        finally:
            conn.close()

    def test_import_links_preserves_duplicates(self):
        """Duplicate link rows are kept (no PK/no dedup) so the join duplicates them."""
        fixture = "1\t100\n1\t100\n2\t101\n"  # 1->100 duplicated
        path = os.path.join(self.temp_dir, "links.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_links(path, conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0], 3)
            rows = conn.execute("SELECT jpn_id, target_id FROM links ORDER BY rowid").fetchall()
            self.assertEqual(rows, [("1", "100"), ("1", "100"), ("2", "101")])
        finally:
            conn.close()

    def test_import_links_skips_short_and_empty(self):
        """Empty lines and lines with fewer than 2 tab columns are skipped."""
        fixture = "1\t100\n\nx\n2\t101\n"  # empty and 'x' skipped
        path = os.path.join(self.temp_dir, "links_partial.tsv.bz2")
        _write_bz2(path, fixture)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_links(path, conn)
            rows = conn.execute("SELECT jpn_id, target_id FROM links").fetchall()
            self.assertEqual(rows, [("1", "100"), ("2", "101")])
        finally:
            conn.close()

    def test_import_audio_digit_filter(self):
        """Only the first column is kept when it is all digits; non-digits skipped."""
        member = "42\ttag\nabc\n1337\tcontent\n\n"
        path = os.path.join(self.temp_dir, "audio.tar.bz2")
        _write_tar_bz2(path, {"sentences_with_audio.csv": member})

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_audio(path, conn)
            ids = {r[0] for r in conn.execute("SELECT id FROM audio").fetchall()}
            self.assertEqual(ids, {"42", "1337"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audio").fetchone()[0], 2)
        finally:
            conn.close()

    def test_import_audio_multiple_members_and_dedup(self):
        """All tar members are read and duplicate ids dedup via the PRIMARY KEY."""
        members = {
            "a.csv": "10\ttag\n20\ttag\n",
            "b.csv": "20\ttag\n30\ttag\n",  # 20 duplicates across members
        }
        path = os.path.join(self.temp_dir, "audio_multi.tar.bz2")
        _write_tar_bz2(path, members)

        conn, _ = self._new_staging_db()
        try:
            tatoeba_data._import_audio(path, conn)
            ids = {r[0] for r in conn.execute("SELECT id FROM audio").fetchall()}
            self.assertEqual(ids, {"10", "20", "30"})
        finally:
            conn.close()

    def test_import_sentences_truncated_bz2_raises(self):
        """A truncated .bz2 stream surfaces EOFError/OSError (clean error parity, D2).

        Because the helper commits only after a full successful import, the
        uncommitted transaction is rolled back on close — no partial rows leak
        into the staging table.
        """
        data = bz2.compress("1\tjpn\t猫\n2\tjpn\t犬\n3\tjpn\t鳥\n".encode("utf-8"))
        path = os.path.join(self.temp_dir, "trunct.tsv.bz2")
        with open(path, "wb") as f:
            f.write(data[:-8])  # cut the tail of the bz2 stream

        conn, _ = self._new_staging_db()
        try:
            with self.assertRaises((EOFError, OSError)):
                tatoeba_data._import_sentences(path, conn, "jpn")
            # No partial import survived (transaction rolled back on the exception path).
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jpn").fetchone()[0], 0)
        finally:
            conn.close()

    # ── golden equivalence: dict-join vs SQL join (streaming plan §6.4) ──

    def test_golden_equivalence_dict_join_vs_sql_join(self):
        """The staging SQL join produces the same multiset as the reference dict-join."""
        cases = [
            {"name": "empty",
             "jpn": "", "target": "", "links": "", "audio": ""},
            {"name": "basic_with_audio",
             "jpn": "1\tjpn\t猫が好き\n2\tjpn\t犬が好き\n",
             "target": "10\teng\tI like cats\n11\teng\tI like dogs\n",
             "links": "1\t10\n2\t11\n",
             "audio": "1\taudio\n"},
            {"name": "missing_link_id",
             "jpn": "1\tjpn\t猫\n",
             "target": "10\teng\tcat\n",
             "links": "1\t99\n9\t10\n1\t10\n",  # only 1->10 valid
             "audio": ""},
            {"name": "duplicate_link_rows",
             "jpn": "1\tjpn\t猫\n",
             "target": "10\teng\tcat\n",
             "links": "1\t10\n1\t10\n",  # two identical output rows
             "audio": ""},
            {"name": "duplicate_sentence_id_last_wins",
             "jpn": "1\tjpn\t猫\n1\tjpn\t犬\n",  # id 1 -> last wins 犬
             "target": "10\teng\tcat\n",
             "links": "1\t10\n",
             "audio": ""},
            {"name": "audio_membership_mixed",
             "jpn": "1\tjpn\t猫\n2\tjpn\t犬\n",
             "target": "10\teng\tcat\n11\teng\tdog\n",
             "links": "1\t10\n2\t11\n",
             "audio": "1\taudio\n"},  # only id 1 has audio
        ]
        for c in cases:
            ref = _dict_join_reference(c["jpn"], c["target"], c["links"], c["audio"])
            sql = _sql_join_via_staging(self.temp_dir, c["jpn"], c["target"],
                                        c["links"], c["audio"])
            self.assertEqual(sorted(sql), sorted(ref),
                             f"golden mismatch [{c['name']}]: sql={sql} ref={ref}")

    # ── download_tatoeba_data integration tests (streaming plan §6) ──

    def test_download_tatoeba_data_success(self):
        """Full pipeline via a download_to_file mock: DB + searchable + metadata,
        no pairs TSV, no leftover workdir; has_audio flows end-to-end."""
        payloads = _payloads("eng",
            jpn="1\tjpn\t猫が好き\n2\tjpn\t犬が好き\n",
            target="10\teng\tI like cats\n11\teng\tI like dogs\n",
            links="1\t10\n2\t11\n",
            audio_member="1\taudio\n")
        with _patch_download([payloads]):
            success, msg = tatoeba_data.download_tatoeba_data("eng")

        self.assertTrue(success, msg)
        self.assertIn("2", msg)  # count == 2

        db = tatoeba_data.get_db_path("eng")
        self.assertTrue(os.path.exists(db))

        # Searchable; has_audio flows end-to-end (id 1 has audio, id 2 does not).
        cat = tatoeba_data.search_word(db, "猫")
        self.assertEqual(len(cat), 1)
        self.assertEqual(cat[0][1], "猫が好き")
        self.assertEqual(cat[0][3], 1)
        dog = tatoeba_data.search_word(db, "犬")
        self.assertEqual(len(dog), 1)
        self.assertEqual(dog[0][3], 0)

        # Metadata recorded under the right code with the right count.
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            md = json.load(f)
        self.assertEqual(md["eng"]["count"], 2)

        # No intermediate pairs TSV, no leftover workdir.
        self.assertFalse(os.path.exists(self._pairs_tsv_path("eng")))
        self._no_leftover_workdirs()

    def test_download_tatoeba_data_spanish(self):
        """download_tatoeba_data works for a non-default language (Spanish)."""
        payloads = _payloads("spa",
            jpn="1\tjpn\t猫\n",
            target="10\tspa\tgato\n",
            links="1\t10\n",
            audio_member="")
        with _patch_download([payloads]):
            success, msg = tatoeba_data.download_tatoeba_data("spa")

        self.assertTrue(success, msg)
        self.assertTrue(os.path.exists(tatoeba_data.get_db_path("spa")))
        self.assertEqual(len(tatoeba_data.search_word(tatoeba_data.get_db_path("spa"), "猫")), 1)
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["spa"]["count"], 1)
        self.assertFalse(os.path.exists(self._pairs_tsv_path("spa")))
        self._no_leftover_workdirs()

    def test_download_tatoeba_data_empty_datasets_succeed_with_count_zero(self):
        """Empty inputs succeed with count 0 and a valid (empty) DB (parity §4)."""
        payloads = _payloads("eng", jpn="", target="", links="", audio_member="")
        with _patch_download([payloads]):
            success, msg = tatoeba_data.download_tatoeba_data("eng")
        self.assertTrue(success, msg)
        self.assertIn("0", msg)
        db = tatoeba_data.get_db_path("eng")
        self.assertTrue(os.path.exists(db))
        self.assertEqual(os.path.getsize(db) > 0, True)  # valid, non-empty SQLite header
        self.assertEqual(tatoeba_data.search_word(db, "猫"), [])
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["eng"]["count"], 0)
        self.assertFalse(os.path.exists(self._pairs_tsv_path("eng")))
        self._no_leftover_workdirs()

    def test_download_tatoeba_data_unknown_language(self):
        """download_tatoeba_data rejects unknown language codes before any work."""
        success, msg = tatoeba_data.download_tatoeba_data("klingon")
        self.assertFalse(success)
        self.assertIn("klingon", msg)
        self._no_leftover_workdirs()

    def test_download_tatoeba_data_calls_progress_callback_six_times(self):
        """progress_callback fires exactly once per pipeline step (6 steps, same
        order/strings as before — UI log lines unchanged)."""
        payloads = _payloads("eng",
            jpn="1\tjpn\t猫\n", target="10\teng\tcat\n",
            links="1\t10\n", audio_member="")
        callback = MagicMock()
        with _patch_download([payloads]):
            tatoeba_data.download_tatoeba_data("eng", progress_callback=callback)
        # Six steps: fetch jpn, fetch target, fetch links, fetch audio, build pairs,
        # build index — the same six strings in the same order as the old pipeline.
        self.assertEqual(callback.call_count, 6)
        msgs = [c.args[0] for c in callback.call_args_list]
        self.assertTrue(all(isinstance(m, str) and m for m in msgs))

    @unittest.skipUnless(responses, "responses not installed")
    @responses.activate
    def test_download_tatoeba_data_end_to_end_with_responses(self):
        """True end-to-end through the real download_to_file over a mocked HTTP
        layer (responses), serving small real bz2 / tar.bz2 payloads."""
        urls = tatoeba_data.get_download_urls("eng")
        jpn = "1\tjpn\t猫が好き\n"
        target = "10\teng\tI like cats\n"
        links = "1\t10\n"
        audio_member = "1\taudio\n"

        def _add(url, body):
            responses.add(responses.GET, url, body=body,
                          headers={"Content-Length": str(len(body))},
                          content_type="application/octet-stream")

        _add(urls["jpn_sentences"], bz2.compress(jpn.encode("utf-8")))
        _add(urls["target_sentences"], bz2.compress(target.encode("utf-8")))
        _add(urls["links"], bz2.compress(links.encode("utf-8")))
        _add(tatoeba_data.AUDIO_INDEX_URL,
             _tar_bz2_bytes({"sentences_with_audio.csv": audio_member}))

        success, msg = tatoeba_data.download_tatoeba_data("eng")
        self.assertTrue(success, msg)
        db = tatoeba_data.get_db_path("eng")
        self.assertTrue(os.path.exists(db))
        results = tatoeba_data.search_word(db, "猫")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "猫が好き")
        self.assertEqual(results[0][3], 1)
        self.assertFalse(os.path.exists(self._pairs_tsv_path("eng")))
        self._no_leftover_workdirs()

    def test_download_tatoeba_data_network_error(self):
        """A failed download surfaces (False, msg) and leaves no workdir/db."""
        payloads = _payloads("eng", jpn="", target="", links="", audio_member="")
        fail = (tatoeba_data.get_download_urls("eng")["jpn_sentences"],)
        with _patch_download([payloads], fail_urls=fail):
            success, msg = tatoeba_data.download_tatoeba_data("eng")
        self.assertFalse(success)
        self.assertIn("simulated download failure", msg)
        self.assertFalse(os.path.exists(tatoeba_data.get_db_path("eng")))
        self.assertFalse(tatoeba_data.is_data_available("eng"))
        self._no_leftover_workdirs()

    def test_download_failure_preserves_old_db_and_metadata(self):
        """A failure mid-download leaves the previous DB searchable + metadata
        untouched (the old TSV/DB inconsistency regression, plan §6.6)."""
        # Pre-seed an existing index + metadata.
        old_db = tatoeba_data.get_db_path("eng")
        if os.path.exists(old_db):
            os.remove(old_db)
        tatoeba_data.build_sqlite_index(
            iter([("1", "猫が好き", "100", "I like cats", 0)]), old_db)
        old_meta = {"eng": {"downloaded_at": "2020-01-01T00:00:00", "count": 1}}
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(old_meta, f)

        # A download that fails on the links URL (after jpn+target already staged).
        payloads = _payloads("eng",
            jpn="2\tjpn\t犬\n", target="101\teng\tI like dogs\n",
            links="2\t101\n", audio_member="")
        fail = (tatoeba_data.get_download_urls("eng")["links"],)
        with _patch_download([payloads], fail_urls=fail):
            success, msg = tatoeba_data.download_tatoeba_data("eng")
        self.assertFalse(success)

        # Old DB intact and still searchable.
        self.assertTrue(os.path.exists(old_db))
        self.assertEqual(len(tatoeba_data.search_word(old_db, "猫")), 1)
        self.assertEqual(tatoeba_data.search_word(old_db, "犬"), [])
        # Metadata unchanged.
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            md = json.load(f)
        self.assertEqual(md["eng"]["count"], 1)
        self.assertEqual(md["eng"]["downloaded_at"], "2020-01-01T00:00:00")
        # No leftover workdir.
        self._no_leftover_workdirs()

    def test_double_sequential_download_replaces_previous(self):
        """Two sequential downloads cleanly replace the DB; search works after each."""
        holder = [_payloads("eng",
            jpn="1\tjpn\t猫\n", target="10\teng\tcat\n",
            links="1\t10\n", audio_member="")]
        with _patch_download(holder):
            s1, m1 = tatoeba_data.download_tatoeba_data("eng")
        self.assertTrue(s1, m1)
        db = tatoeba_data.get_db_path("eng")
        self.assertEqual(len(tatoeba_data.search_word(db, "猫")), 1)

        # Second run with different sentences replaces the index entirely.
        holder[0] = _payloads("eng",
            jpn="1\tjpn\t犬\n", target="10\teng\tdog\n",
            links="1\t10\n", audio_member="")
        with _patch_download(holder):
            s2, m2 = tatoeba_data.download_tatoeba_data("eng")
        self.assertTrue(s2, m2)
        self.assertEqual(len(tatoeba_data.search_word(db, "犬")), 1)
        self.assertEqual(tatoeba_data.search_word(db, "猫"), [])
        self._no_leftover_workdirs()

    # ── atomic metadata write (streaming plan §6.8) ──

    def test_write_metadata_atomic_success(self):
        """_write_metadata_atomic writes valid JSON and leaves no .tmp behind."""
        tatoeba_data._write_metadata_atomic(
            {"eng": {"downloaded_at": "x", "count": 5}})
        self.assertTrue(os.path.exists(tatoeba_data.METADATA_FILE))
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f),
                             {"eng": {"downloaded_at": "x", "count": 5}})
        self.assertFalse(os.path.exists(tatoeba_data.METADATA_FILE + ".tmp"))

    def test_write_metadata_atomic_crash_leaves_old_valid(self):
        """If os.replace fails mid-atomic-write, the old metadata stays valid
        and the .tmp is cleaned up."""
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"fra": {"downloaded_at": "old", "count": 2}}, f)
        with open(tatoeba_data.METADATA_FILE, "rb") as f:
            old_bytes = f.read()

        with patch("src.core.tatoeba_data.os.replace", side_effect=OSError("boom")), \
             patch("src.core.tatoeba_data.time.sleep"):
            with self.assertRaises(OSError):
                tatoeba_data._write_metadata_atomic(
                    {"eng": {"downloaded_at": "new", "count": 9}})

        # Old file untouched and still valid JSON.
        with open(tatoeba_data.METADATA_FILE, "rb") as f:
            self.assertEqual(f.read(), old_bytes)
        with open(tatoeba_data.METADATA_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f),
                             {"fra": {"downloaded_at": "old", "count": 2}})
        self.assertFalse(os.path.exists(tatoeba_data.METADATA_FILE + ".tmp"))

    def test_read_metadata_handles_missing_and_corrupt(self):
        """_read_metadata returns {} for a missing or corrupt file."""
        self.assertEqual(tatoeba_data._read_metadata(), {})
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(tatoeba_data._read_metadata(), {})
        with open(tatoeba_data.METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"eng": {"downloaded_at": "x", "count": 1}}, f)
        self.assertEqual(tatoeba_data._read_metadata(),
                         {"eng": {"downloaded_at": "x", "count": 1}})

    # ── stale-workdir sweep (streaming plan §5) ──

    def test_sweep_stale_workdirs_removes_old_only(self):
        """A workdir older than the threshold is swept; a fresh one is left alone."""
        import time as _time
        t = _time.time()
        old_dir = os.path.join(self.temp_dir, "download_old")
        fresh_dir = os.path.join(self.temp_dir, "download_fresh")
        os.makedirs(old_dir)
        os.makedirs(fresh_dir)
        os.utime(old_dir, (t - 7200, t - 7200))  # 2 h old

        removed = tatoeba_data._sweep_stale_workdirs(base_dir=self.temp_dir, now=t)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(old_dir))
        self.assertTrue(os.path.exists(fresh_dir))

    def test_sweep_stale_workdirs_ignores_non_matching_dirs(self):
        """Dirs not matching the workdir prefix are never swept."""
        other = os.path.join(self.temp_dir, "other_dir")
        os.makedirs(other)
        import time as _time
        removed = tatoeba_data._sweep_stale_workdirs(
            base_dir=self.temp_dir, now=_time.time())
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(other))


if __name__ == '__main__':
    unittest.main()