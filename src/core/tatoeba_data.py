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
import time
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

try:
    from ..core.languages import is_supported, get_localized_name
except ImportError:
    try:
        from src.core.languages import is_supported, get_localized_name
    except Exception:
        is_supported = lambda code: code in ("eng", "fra")
        get_localized_name = lambda code: code

TATOEBA_BASE_URL = "https://downloads.tatoeba.org/exports/per_language"
AUDIO_INDEX_URL = "https://downloads.tatoeba.org/exports/sentences_with_audio.tar.bz2"
USER_FILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "user_files")
METADATA_FILE = os.path.join(USER_FILES_DIR, "metadata.json")

# Seconds to wait before retrying a failed download_to_file attempt, excluding
# the initial attempt. Three attempts total (initial + these two). Tests patch
# this to (0, 0) to avoid real sleeps.
DOWNLOAD_RETRY_BACKOFF = (1.0, 4.0)

# Transient conditions download_to_file retries: connection drops, timeouts,
# chunked-encoding failures, and incomplete reads (a Content-Length mismatch
# is raised as ConnectionError so it lands here too). HTTP 5xx is retried as
# well but is detected separately via HTTPError.response.status_code.
_RETRIABLE_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Rows buffered before each staging-table executemany flush. Chosen to bound a
# single insert batch in memory while amortizing per-statement overhead for
# the ~4 M-row worst-case (eng) import.
STAGING_CHUNK_SIZE = 5000

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

def build_sqlite_index(pairs_tsv_path: str, db_path: str, audio_ids: "set[str] | None" = None) -> int:
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

def _stream_url_to_file(url: str, dest_path: str, chunk_size: int) -> None:
    """Perform a single streaming download of ``url`` to ``dest_path``.

    Writes the response body to ``dest_path`` in ``chunk_size``-byte chunks so
    peak memory stays bounded by the chunk buffer rather than the full file. If
    the server advertises a ``Content-Length`` that does not match the bytes
    actually received, a ``requests.exceptions.ConnectionError`` is raised
    (silent truncation / incomplete read) so the caller's retry layer treats it
    as a transient failure.

    Requests' default ``decode_content`` behavior is left intact: if a server
    ever gzip-wraps a ``.bz2``, transparent decoding still yields a valid bz2
    stream for the importer.

    No retry happens here — the retry policy lives in :func:`download_to_file`.
    """
    response = requests.get(url, stream=True, timeout=(15, 30))
    try:
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        expected = int(content_length) if content_length and content_length.isdigit() else None
        written = 0
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
        if expected is not None and written != expected:
            raise requests.exceptions.ConnectionError(
                f"Truncated download for {url}: wrote {written} bytes, "
                f"Content-Length reported {expected}"
            )
    finally:
        response.close()


def download_to_file(url: str, dest_path: str, chunk_size: int = 1 << 16) -> None:
    """Stream ``url`` to ``dest_path`` on disk, with retry and truncation checks.

    The body is streamed chunk-by-chunk (peak RAM ~ ``chunk_size``) and lands in
    a sibling ``dest_path + ".part"`` file that is atomically renamed onto
    ``dest_path`` only on success, so a partial file never appears at
    ``dest_path`` after a failure.

    Retry policy (streaming plan, decision D1): up to three attempts total
    (initial + two retries) with ``DOWNLOAD_RETRY_BACKOFF`` backoff, retried
    *only* on connection errors, timeouts, chunked-encoding failures, and HTTP
    5xx responses. Any 4xx — including 404 — surfaces immediately with no retry:
    a 404 means Tatoeba renamed or moved the file, and retrying only delays the
    real error. A ``Content-Length`` mismatch is raised as a ``ConnectionError``
    and is therefore retried as a transient failure.

    Args:
        url: The URL to download.
        dest_path: Absolute path to write the downloaded bytes to. The parent
            directory must already exist.
        chunk_size: Chunk size in bytes forwarded to ``response.iter_content``.

    Raises:
        requests.exceptions.HTTPError: for non-retriable (4xx) HTTP errors.
        requests.exceptions.RequestException: for retriable errors that persist
            after all attempts are exhausted.
    """
    part_path = dest_path + ".part"
    total_attempts = len(DOWNLOAD_RETRY_BACKOFF) + 1
    success = False
    try:
        for attempt in range(total_attempts):
            try:
                _stream_url_to_file(url, part_path, chunk_size)
                os.replace(part_path, dest_path)
                success = True
                return
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 500 <= status < 600 and attempt < total_attempts - 1:
                    logging.warning(
                        "download_to_file: HTTP %s for %s (attempt %d/%d); retrying",
                        status, url, attempt + 1, total_attempts)
                    time.sleep(DOWNLOAD_RETRY_BACKOFF[attempt])
                    continue
                raise  # 4xx (incl. 404) or final 5xx: do not retry
            except _RETRIABLE_EXC as exc:
                if attempt < total_attempts - 1:
                    logging.warning(
                        "download_to_file: transient error for %s (attempt %d/%d): %s; retrying",
                        url, attempt + 1, total_attempts, exc)
                    time.sleep(DOWNLOAD_RETRY_BACKOFF[attempt])
                    continue
                raise
    finally:
        if not success and os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                logging.warning("download_to_file: could not remove %s", part_path)


def _create_staging_db(db_path: str) -> sqlite3.Connection:
    """Create the per-run staging SQLite DB with four tables and bulk-load pragmas.

    Tables (streaming plan, decision D4) mirror today's dict semantics exactly:

      - ``jpn(id TEXT PRIMARY KEY, text TEXT)``     last-wins on duplicate ids
      - ``target(id TEXT PRIMARY KEY, text TEXT)``   same
      - ``links(jpn_id TEXT, target_id TEXT)``        NO primary key, no dedup —
                                                    duplicate link rows are
                                                    preserved so the join emits
                                                    duplicate output rows
      - ``audio(id TEXT PRIMARY KEY)``               replaces the ~100 MB in-RAM set

    Both sentence tables use ``id TEXT PRIMARY KEY`` so an ``INSERT OR REPLACE``
    import makes duplicate sentence ids resolve last-wins, matching today's
    ``jpn_dict[parts[0]] = parts[2]``. A truncated ``.bz2`` surfaces here as an
    ``EOFError``/``OSError`` from :func:`_import_sentences` (decision D2) — no
    decompressed temp file is ever written.

    The pragmas trade durability for speed (``synchronous=OFF``,
    ``journal_mode=OFF``): the staging DB lives in a throwaway workdir that is
    ``rmtree``-d on failure, so crash-safety is irrelevant and one transaction
    per table is all that matters (plan §5 C6).

    Args:
        db_path: Path to create the staging DB at. Parent directory must exist.

    Returns:
        An open ``sqlite3.Connection``; the caller owns its lifecycle (close
        it in a ``finally`` — a connection closed with an uncommitted
        transaction rolls back, so a failed import never persists partial rows).
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA synchronous = OFF")
    cur.execute("PRAGMA journal_mode = OFF")
    cur.execute("CREATE TABLE jpn (id TEXT PRIMARY KEY, text TEXT)")
    cur.execute("CREATE TABLE target (id TEXT PRIMARY KEY, text TEXT)")
    cur.execute("CREATE TABLE links (jpn_id TEXT, target_id TEXT)")
    cur.execute("CREATE TABLE audio (id TEXT PRIMARY KEY)")
    conn.commit()
    return conn


def _import_sentences(compressed_path: str, conn: sqlite3.Connection, table: str) -> None:
    """Import a Tatoeba ``*_sentences.tsv.bz2`` file into the ``jpn`` or ``target``
    staging table.

    Iterates :class:`bz2.BZ2File` line-by-line straight into SQLite, so the
    ~1.5 GB decompressed corpus is never materialized as a file or string —
    peak memory is bounded by ``STAGING_CHUNK_SIZE`` rows (decision D2).

    Parity with today's ``build_pairs_tsv``:
      - a line with fewer than 3 tab columns is skipped (``len(parts) < 3``);
      - empty lines are skipped;
      - ``id = parts[0]`` and ``text = parts[2]``;
      - duplicate sentence ids resolve last-wins via ``INSERT OR REPLACE``
        (matching ``jpn_dict[parts[0]] = parts[2]``).

    Only the trailing ``\n`` is stripped (``rstrip("\n")``) so any carriage
    return from a ``\r\n`` file is preserved in ``text`` exactly as today,
    which relies on a whole-content ``strip()`` + ``split("\n")``.

    Args:
        compressed_path: Path to a ``.tsv.bz2`` sentence file.
        conn: A connection to a staging DB created by :func:`_create_staging_db`.
        table: ``"jpn"`` or ``"target"``.

    Raises:
        ValueError: if ``table`` is not one of the two allowed names.
        EOFError/OSError: if the ``.bz2`` stream is truncated or corrupt
            (surfaced to the caller for a clean error message).
    """
    if table not in ("jpn", "target"):
        raise ValueError(f"_import_sentences: table must be 'jpn' or 'target', got {table!r}")
    cur = conn.cursor()
    insert_sql = f"INSERT OR REPLACE INTO {table} (id, text) VALUES (?, ?)"
    chunk: list[tuple[str, str]] = []
    with bz2.BZ2File(compressed_path) as f:
        for raw in f:
            line = raw.decode("utf-8").rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            chunk.append((parts[0], parts[2]))
            if len(chunk) >= STAGING_CHUNK_SIZE:
                cur.executemany(insert_sql, chunk)
                chunk.clear()
    if chunk:
        cur.executemany(insert_sql, chunk)
    conn.commit()


def _import_links(compressed_path: str, conn: sqlite3.Connection) -> None:
    """Import a Tatoeba ``jpn-<lang>_links.tsv.bz2`` file into the ``links`` table.

    The ``links`` table has **no primary key and no deduplication** (decision
    D4): a duplicate link row today produces a duplicate output row, and the
    join in :func:`download_tatoeba_data` must preserve that exactly.

    Parity with today's ``build_pairs_tsv``:
      - empty lines are skipped;
      - a line with fewer than 2 tab columns is skipped (``len(parts) < 2``);
      - ``jpn_id = parts[0]`` and ``target_id = parts[1]``.

    Args:
        compressed_path: Path to a ``.tsv.bz2`` links file.
        conn: A connection to a staging DB created by :func:`_create_staging_db`.
    """
    cur = conn.cursor()
    insert_sql = "INSERT INTO links (jpn_id, target_id) VALUES (?, ?)"
    chunk: list[tuple[str, str]] = []
    with bz2.BZ2File(compressed_path) as f:
        for raw in f:
            line = raw.decode("utf-8").rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            chunk.append((parts[0], parts[1]))
            if len(chunk) >= STAGING_CHUNK_SIZE:
                cur.executemany(insert_sql, chunk)
                chunk.clear()
    if chunk:
        cur.executemany(insert_sql, chunk)
    conn.commit()


def _import_audio(tar_compressed_path: str, conn: sqlite3.Connection) -> None:
    """Import ``sentences_with_audio.tar.bz2`` into the ``audio`` staging table.

    Streams each tar member line-by-line so the audio index (≈1.5 M ids) is
    never held in RAM as a Python set (decision D3). The parse rule is
    byte-identical to today's :func:`download_audio_ids`: every member is read,
    each line is fully stripped, and the first tab-separated column is kept as
    an audio id only when it is all digits (``parts[0].isdigit()``).

    ``INSERT OR REPLACE`` into ``audio(id PRIMARY KEY)`` dedups ids exactly as
    today's ``set.add`` did.

    Args:
        tar_compressed_path: Path to a downloaded ``sentences_with_audio.tar.bz2``.
        conn: A connection to a staging DB created by :func:`_create_staging_db`.
    """
    cur = conn.cursor()
    insert_sql = "INSERT OR REPLACE INTO audio (id) VALUES (?)"
    chunk: list[tuple[str]] = []
    with tarfile.open(tar_compressed_path, mode="r:bz2") as tar:
        for member in tar.getmembers():
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            for raw in extracted:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                parts = line.split("\t")
                if parts and parts[0].isdigit():
                    chunk.append((parts[0],))
                    if len(chunk) >= STAGING_CHUNK_SIZE:
                        cur.executemany(insert_sql, chunk)
                        chunk.clear()
    if chunk:
        cur.executemany(insert_sql, chunk)
    conn.commit()


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

def download_tatoeba_data(lang_code: str, progress_callback=None) -> tuple[bool, str]:
    """
    Main entry point to download data for a given language.

    Args:
    - lang_code (str): The ISO 639-3 language code (e.g. 'eng', 'spa') to download data for.
    - progress_callback (callable, optional): A callback function to receive progress updates.

    Returns:
    - A tuple containing a boolean success flag and a status message string.
    """
    if not is_supported(lang_code):
        return False, f"Unknown language: {lang_code}"

    lang_label = get_localized_name(lang_code)
    
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

def get_file_status(lang_code: str) -> Optional[str]:
    """
    Returns the download date string from metadata.

    Args:
    - lang_code (str): The ISO 639-3 language code to get the status for.

    Returns:
    - A string representing the download date, or None if the metadata is not available.
    """
    if not is_supported(lang_code):
        return None
        
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                metadata = json.load(f)
                if lang_code in metadata:
                    return metadata[lang_code].get("downloaded_at")
        except json.JSONDecodeError:
            pass
    return None

def is_data_available(lang_code: str) -> bool:
    """
    Returns True if the data file exists.

    Args:
    - lang_code (str): The ISO 639-3 language code to check for data availability.

    Returns:
    - A boolean indicating whether the processed data file exists.
    """
    if not is_supported(lang_code):
        return False
        
    file_path = get_data_file_path(lang_code)
    return os.path.exists(file_path)
