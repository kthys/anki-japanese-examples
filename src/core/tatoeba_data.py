import os
import bz2
import io
import tarfile
import logging
import requests
import datetime
import json
import sqlite3
import re
from typing import Optional

try:
    from aqt import mw
except ImportError:
    mw = None  # for testing outside Anki

try:
    from ..utils.i18n import _
except ImportError:
    try:
        from src.utils.i18n import _
    except Exception:
        _ = lambda x: x

TATOEBA_BASE_URL = "https://downloads.tatoeba.org/exports/per_language"
AUDIO_INDEX_URL = "https://downloads.tatoeba.org/exports/sentences_with_audio.tar.bz2"
LANG_MAP = {"English": "eng", "French": "fra"}
USER_FILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "user_files")
METADATA_FILE = os.path.join(USER_FILES_DIR, "metadata.json")

# Regex pattern for tokenizing Japanese text: matches runs of kanji, katakana, or hiragana
_TOKEN_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+|[\u30a0-\u30ff]+|[\u3040-\u309f]+')

def get_data_file_path(lang_code: str) -> str:
    """
    Returns the path to the processed TSV file.

    Args:
    - lang_code (str): The ISO 639-3 language code.

    Returns:
    - The absolute string path to the processed TSV file.
    """
    return os.path.join(USER_FILES_DIR, f"jpn_{lang_code}_pairs.tsv")

def get_db_path(lang_code: str) -> str:
    """
    Return the path to the SQLite index database for a given language pair.

    Args:
    - lang_code (str): The ISO 639-3 language code (e.g. 'eng', 'fra').

    Returns:
    - The absolute string path to the SQLite database file.
    """
    return os.path.join(USER_FILES_DIR, f"jpn_{lang_code}_index.db")

def tokenize_japanese(text: str) -> list[str]:
    """
    Extract Japanese tokens (kanji runs, katakana runs, hiragana runs) from a string.

    This is a regex-based tokenizer that splits text into meaningful Japanese
    sub-strings. It does NOT use a morphological analyzer; instead it relies on
    Unicode ranges to identify contiguous runs of kanji, katakana, or hiragana.

    Args:
    - text (str): The Japanese text to tokenize.

    Returns:
    - A list of unique token strings found in the text.
    """
    return list(dict.fromkeys(_TOKEN_RE.findall(text)))

def build_sqlite_index(pairs_tsv_path: str, db_path: str, audio_ids: set[str] | None = None) -> int:
    """
    Build a SQLite index database from a pairs TSV file.

    Creates two tables:
      - ``sentences`` — stores (jpn_id, jpn_text, trans_id, trans_text)
      - ``words``     — maps each token to the sentence row id for fast lookup

    An index is created on ``words(word)`` to enable efficient exact-match queries.

    The build is atomic: rows are written to ``db_path + ".tmp"`` and moved
    onto ``db_path`` only after the final commit, so ``db_path`` always holds
    either the previous complete index or the new one — never a partial file.

    Args:
    - pairs_tsv_path (str): Path to the TSV file produced by build_pairs_tsv().
    - db_path (str): Path where the SQLite database will be created (overwritten if exists).

    Returns:
    - The number of sentences inserted into the database.
    """
    tmp_db_path = db_path + ".tmp"
    if os.path.exists(tmp_db_path):
        os.remove(tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    count = 0
    try:
        cur = conn.cursor()
        # The temp file is discarded on any failure, so crash-safety pragmas
        # buy nothing here — trade them for a much faster bulk load.
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute("PRAGMA journal_mode = MEMORY")
        cur.execute("""
            CREATE TABLE sentences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jpn_id TEXT,
                jpn_text TEXT,
                trans_id TEXT,
                trans_text TEXT,
                has_audio INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE words (
                word TEXT,
                sentence_id INTEGER,
                FOREIGN KEY (sentence_id) REFERENCES sentences(id)
            )
        """)

        # Accumulate rows and flush in chunks with executemany — sentence ids
        # are assigned explicitly so word rows can reference them without a
        # per-row lastrowid round trip.
        CHUNK_SIZE = 1000
        sentence_rows: list[tuple] = []
        word_rows: list[tuple] = []

        def flush():
            if sentence_rows:
                cur.executemany(
                    "INSERT INTO sentences (id, jpn_id, jpn_text, trans_id, trans_text, has_audio) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    sentence_rows,
                )
                sentence_rows.clear()
            if word_rows:
                cur.executemany(
                    "INSERT INTO words (word, sentence_id) VALUES (?, ?)",
                    word_rows,
                )
                word_rows.clear()

        with open(pairs_tsv_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) < 4:
                    continue
                jpn_id, jpn_text, trans_id, trans_text = parts[0], parts[1], parts[2], parts[3]
                has_audio_val = 1 if audio_ids and jpn_id in audio_ids else 0
                count += 1
                sentence_id = count
                sentence_rows.append(
                    (sentence_id, jpn_id, jpn_text, trans_id, trans_text, has_audio_val))
                word_rows.extend(
                    (token, sentence_id) for token in tokenize_japanese(jpn_text))
                if len(sentence_rows) >= CHUNK_SIZE:
                    flush()
        flush()

        cur.execute("CREATE INDEX IF NOT EXISTS idx_words_word ON words(word)")
        conn.commit()
        conn.close()
        conn = None
        # Atomic swap: same directory, so os.replace is a single rename.
        os.replace(tmp_db_path, db_path)
    except Exception as e:
        logging.error(f"Error building SQLite index: {e}", exc_info=True)
        raise
    finally:
        if conn is not None:
            conn.close()
        if os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except OSError:
                logging.warning(f"Could not remove temp index {tmp_db_path}")
    return count

def search_word(db_path: str, word: str, conn: Optional[sqlite3.Connection] = None) -> list[tuple[str, str, str, int]]:
    """
    Search the SQLite index for sentences containing the word.

    Performs a strict boundary search for kanji roots, but allows kana
    inflections (e.g., searching '負ける' will correctly find the sentence
    '試験に負けるな。'). For pure kanji queries like '火', it strictly
    avoids matching '花火'.

    Args:
    - db_path (str): Path to the SQLite index database.
    - word (str): The Japanese word to search for.
    - conn (Optional[sqlite3.Connection]): An optional active database connection to reuse.

    Returns:
    - A list of (jpn_id, jpn_text, trans_text, has_audio) tuples for all matching sentences,
      where jpn_id is the Tatoeba sentence ID from the sentences table.
      Returns an empty list if the database does not exist or an error occurs.
    """
    if not os.path.exists(db_path):
        return []

    tokens = tokenize_japanese(word)
    if not tokens:
        return []

    # Heuristic: the first kanji token is the best index key because kanji
    # are semantic and highly specific. If no kanji, use the longest token.
    kanji_tokens = [t for t in tokens if re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', t)]
    if kanji_tokens:
        primary_token = kanji_tokens[0]
        # For kanji, exact match respects strict boundaries for the root
        token_query = "w.word = ?"
    else:
        primary_token = max(tokens, key=len)
        # For kana, use prefix match to catch inflections
        token_query = "w.word LIKE ? || '%'"

    try:
        local_conn = conn if conn is not None else sqlite3.connect(db_path)
        cur = local_conn.cursor()
        safe_word = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        cur.execute(f"""
            SELECT DISTINCT s.jpn_id, s.jpn_text, s.trans_text, s.has_audio
            FROM words w
            JOIN sentences s ON w.sentence_id = s.id
            WHERE {token_query}
              AND s.jpn_text LIKE ? ESCAPE '\\'
        """, (primary_token, f"%{safe_word}%"))
        results = cur.fetchall()
        if conn is None:
            local_conn.close()
        return results
    except Exception as e:
        logging.error(f"Error searching SQLite index: {e}", exc_info=True)
        return []

def get_download_urls(lang_code: str) -> dict:
    """
    Returns a dict of URLs for the required datasets.

    Args:
    - lang_code (str): The ISO 639-3 language code.

    Returns:
    - A dictionary containing URLs for 'jpn_sentences', 'target_sentences', and 'links'.
    """
    return {
        "jpn_sentences": f"{TATOEBA_BASE_URL}/jpn/jpn_sentences.tsv.bz2",
        "target_sentences": f"{TATOEBA_BASE_URL}/{lang_code}/{lang_code}_sentences.tsv.bz2",
        "links": f"{TATOEBA_BASE_URL}/jpn/jpn-{lang_code}_links.tsv.bz2"
    }

def download_and_extract_bz2(url: str) -> str:
    """
    Downloads a bz2 file and returns the decompressed content as a string.

    Args:
    - url (str): The URL of the bz2 file to download.

    Returns:
    - A string containing the decompressed contents of the file.
    """
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return bz2.decompress(response.content).decode("utf-8")

def download_audio_ids(url: str) -> set[str]:
    """
    Downloads sentences_with_audio.tar.bz2 and returns the set of sentence IDs
    that have audio recordings.

    The archive contains a single CSV file where the first tab-separated column
    is the numeric Tatoeba sentence ID.

    Args:
    - url (str): The URL of the tar.bz2 audio index archive.

    Returns:
    - A set of sentence ID strings (e.g. {"42", "1337", ...}).
    """
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()
    audio_ids: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:bz2") as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            if f is None:
                continue
            for raw_line in f:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                parts = line.split("\t")
                if parts and parts[0].isdigit():
                    audio_ids.add(parts[0])
    return audio_ids

def build_pairs_tsv(jpn_sentences_tsv: str, target_sentences_tsv: str, links_tsv: str) -> str:
    """
    Joins the datasets and returns a single TSV string.

    Args:
    - jpn_sentences_tsv (str): The TSV string of Japanese sentences.
    - target_sentences_tsv (str): The TSV string of target language sentences.
    - links_tsv (str): The TSV string mapping Japanese sentence IDs to target sentence IDs.

    Returns:
    - A single TSV string containing the joined sentence pairs.
    """
    jpn_dict = {}
    if jpn_sentences_tsv:
        for line in jpn_sentences_tsv.strip().split("\n"):
            if not line: continue
            parts = line.split("\t")
            if len(parts) >= 3:
                jpn_dict[parts[0]] = parts[2]

    target_dict = {}
    if target_sentences_tsv:
        for line in target_sentences_tsv.strip().split("\n"):
            if not line: continue
            parts = line.split("\t")
            if len(parts) >= 3:
                target_dict[parts[0]] = parts[2]

    output_lines = []
    if links_tsv:
        for line in links_tsv.strip().split("\n"):
            if not line: continue
            parts = line.split("\t")
            if len(parts) >= 2:
                jpn_id, target_id = parts[0], parts[1]
                if jpn_id in jpn_dict and target_id in target_dict:
                    output_lines.append(f"{jpn_id}\t{jpn_dict[jpn_id]}\t{target_id}\t{target_dict[target_id]}")

    if not output_lines:
        return ""
    return "\n".join(output_lines) + "\n"

def download_tatoeba_data(lang_label: str, progress_callback=None) -> tuple[bool, str]:
    """
    Main entry point to download data for a given language.

    Args:
    - lang_label (str): The language label (e.g. 'English', 'French') to download data for.
    - progress_callback (callable, optional): A callback function to receive progress updates.

    Returns:
    - A tuple containing a boolean success flag and a status message string.
    """
    if lang_label not in LANG_MAP:
        return False, f"Unknown language: {lang_label}"
    
    lang_code = LANG_MAP[lang_label]
    
    try:
        os.makedirs(USER_FILES_DIR, exist_ok=True)
        
        urls = get_download_urls(lang_code)
        
        if progress_callback:
            progress_callback(_("batch_step_fetch_jpn"))
        jpn_content = download_and_extract_bz2(urls["jpn_sentences"])

        if progress_callback:
            progress_callback(_("batch_step_fetch_target").format(lang=lang_label))
        target_content = download_and_extract_bz2(urls["target_sentences"])

        if progress_callback:
            progress_callback(_("batch_step_fetch_links"))
        links_content = download_and_extract_bz2(urls["links"])

        if progress_callback:
            progress_callback(_("batch_step_fetch_audio_index"))
        audio_ids: set[str] = download_audio_ids(AUDIO_INDEX_URL)

        if progress_callback:
            progress_callback(_("batch_step_build_tsv"))
        pairs_tsv = build_pairs_tsv(jpn_content, target_content, links_content)
        
        output_file = get_data_file_path(lang_code)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(pairs_tsv)

        if progress_callback:
            progress_callback(_("batch_step_build_index"))
        # Build SQLite index for strict word-boundary matching
        db_path = get_db_path(lang_code)
        build_sqlite_index(output_file, db_path, audio_ids=audio_ids)
            
        metadata = {}
        if os.path.exists(METADATA_FILE):
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                try:
                    metadata = json.load(f)
                except json.JSONDecodeError:
                    pass
                    
        num_pairs = len(pairs_tsv.strip().split("\n")) if pairs_tsv.strip() else 0
                    
        metadata[lang_code] = {
            "downloaded_at": datetime.datetime.now().isoformat(),
            "count": num_pairs
        }
        
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            
        # Get localized success message
        # We need to fall back cleanly if translation is missing
        translated = _("batch_download_success")
        if translated != "batch_download_success":
            success_msg = translated.format(count=num_pairs, lang=lang_label)
        else:
            success_msg = f"Download complete. {num_pairs} sentence pairs loaded for {lang_label}."
            
        return True, success_msg
        
    except Exception as e:
        translated = _("batch_download_error")
        if translated != "batch_download_error":
            error_msg = translated.format(error=str(e))
        else:
            error_msg = f"Download failed: {e}"
            
        logging.error(f"Error downloading Tatoeba data: {e}", exc_info=True)
        return False, error_msg

def load_index(lang_label: str) -> Optional[dict]:
    """
    Loads the processed TSV into an in-memory dict.

    .. deprecated::
        Use :func:`search_word` with the SQLite index instead.
        Kept for backward compatibility.

    Args:
    - lang_label (str): The language label to load the index for.

    Returns:
    - A dictionary containing the loaded index, or None if the file does not exist.
    """
    if lang_label not in LANG_MAP:
        return None
        
    lang_code = LANG_MAP[lang_label]
    file_path = get_data_file_path(lang_code)
    
    if not os.path.exists(file_path):
        return None
        
    index = {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 4:
                    jpn_text = parts[1]
                    trans_text = parts[3]
                    if jpn_text not in index:
                        index[jpn_text] = []
                    index[jpn_text].append((jpn_text, trans_text))
        return index
    except Exception as e:
        logging.error(f"Error loading index: {e}", exc_info=True)
        return None

def get_file_status(lang_label: str) -> Optional[str]:
    """
    Returns the download date string from metadata.

    Args:
    - lang_label (str): The language label to get the status for.

    Returns:
    - A string representing the download date, or None if the metadata is not available.
    """
    if lang_label not in LANG_MAP:
        return None
        
    lang_code = LANG_MAP[lang_label]
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                metadata = json.load(f)
                if lang_code in metadata:
                    return metadata[lang_code].get("downloaded_at")
        except json.JSONDecodeError:
            pass
    return None

def is_data_available(lang_label: str) -> bool:
    """
    Returns True if the data file exists.

    Args:
    - lang_label (str): The language label to check for data availability.

    Returns:
    - A boolean indicating whether the processed data file exists.
    """
    if lang_label not in LANG_MAP:
        return False
        
    lang_code = LANG_MAP[lang_label]
    file_path = get_data_file_path(lang_code)
    return os.path.exists(file_path)
