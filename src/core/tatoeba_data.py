import os
import bz2
import tarfile
import logging
import requests
import datetime
import json
import sqlite3
import re
import time
import shutil
import tempfile
from typing import Optional, Iterable

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

# Backoff before each retry, excluding the initial attempt (so three attempts
# total). Tests patch this to (0, 0) to avoid real sleeps.
DOWNLOAD_RETRY_BACKOFF = (1.0, 4.0)

# Workdir prefix for download_tatoeba_data. Unique per run so overlapping
# builds of the same language never share intermediates.
WORKDIR_PREFIX = "download_"
# Left-behind workdirs older than this are swept at the start of the next
# download. A legitimate run can take 30+ min, so 1 h is deliberately generous.
WORKDIR_MAX_AGE_SECONDS = 3600

# Retried by download_to_file. A Content-Length mismatch is raised as a
# ConnectionError so it lands here too; HTTP 5xx is retried as well but is
# detected separately via HTTPError.response.status_code.
_RETRIABLE_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Rows buffered before each staging-table executemany flush. Bounds a single
# insert batch in memory while amortizing per-statement overhead across the
# ~4 M-row worst case (eng).
STAGING_CHUNK_SIZE = 5000

# Regex pattern for tokenizing Japanese text: matches runs of kanji, katakana, or hiragana
_TOKEN_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+|[\u30a0-\u30ff]+|[\u3040-\u309f]+')

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

def build_sqlite_index(rows: Iterable[tuple], db_path: str) -> int:
    """
    Build a SQLite index database from an iterable of sentence-pair rows.

    Creates a ``sentences`` table plus a ``words`` token -> sentence-id table
    indexed on ``word``, which is what makes ``search_word`` fast.

    The build is atomic: rows are written to ``db_path + ".tmp"`` and moved onto
    ``db_path`` only after the final commit, so ``db_path`` always holds either
    the previous complete index or the new one — never a partial file.

    Args:
        rows: An iterable of ``(jpn_id, jpn_text, trans_id, trans_text,
            has_audio)`` 5-tuples, consumed exactly once. ``has_audio`` is
            coerced to 0/1. An exception raised by the iterable aborts the build
            and leaves the previous ``db_path`` intact, with no ``.tmp`` behind.
        db_path: Path where the SQLite database will be created (overwritten if
            it exists).

    Returns:
        The number of sentences inserted into the database.
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

        for jpn_id, jpn_text, trans_id, trans_text, has_audio_val in rows:
            count += 1
            sentence_id = count
            sentence_rows.append(
                (sentence_id, jpn_id, jpn_text, trans_id, trans_text,
                 1 if has_audio_val else 0))
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

    Writes the body in ``chunk_size``-byte chunks so peak memory stays bounded
    by the chunk buffer rather than the full file. A ``Content-Length`` that
    does not match the bytes received is raised as a
    ``requests.exceptions.ConnectionError`` so the caller's retry layer treats
    the truncation as transient.

    No retry happens here — the retry policy lives in :func:`download_to_file`.
    """
    response = requests.get(url, stream=True, timeout=(15, 30))
    try:
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if response.headers.get("Content-Encoding"):
            # Content-Length counts encoded bytes, but requests transparently
            # decodes the body, so the two legitimately differ here.
            expected = None
        elif content_length and content_length.isdigit():
            expected = int(content_length)
        else:
            expected = None
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

    The body lands in a sibling ``dest_path + ".part"`` that is atomically
    renamed onto ``dest_path`` only on success, so a partial file never appears
    at ``dest_path`` after a failure.

    Up to three attempts (initial + two) with ``DOWNLOAD_RETRY_BACKOFF``, retried
    only on connection errors, timeouts, chunked-encoding failures, and HTTP 5xx.
    Any 4xx surfaces immediately: a 404 means Tatoeba renamed or moved the file,
    and retrying only delays the real error.

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
    """Create the per-run staging SQLite DB and its four tables.

    ``jpn`` and ``target`` use ``id TEXT PRIMARY KEY`` so an ``INSERT OR
    REPLACE`` import resolves duplicate sentence ids last-wins. ``links`` has
    deliberately **no primary key and no dedup**: a duplicate link row must
    still produce a duplicate output row. ``audio`` replaces what used to be a
    ~100 MB in-RAM set of sentence ids.

    The pragmas trade durability for speed. That is safe because the staging DB
    lives in a throwaway workdir that is ``rmtree``-d on any failure — nothing
    here is ever read back after a crash.

    Args:
        db_path: Path to create the staging DB at. Parent directory must exist.

    Returns:
        An open ``sqlite3.Connection``; the caller owns its lifecycle.
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
    """Import a Tatoeba ``*_sentences.tsv.bz2`` into the ``jpn`` or ``target`` table.

    Iterates :class:`bz2.BZ2File` line-by-line straight into SQLite, so the
    ~1.5 GB decompressed corpus is never materialized as a file or a string.

    Only the trailing ``\\n`` is stripped, so a carriage return from a ``\\r\\n``
    file is preserved in ``text`` — matching what the previous whole-content
    ``strip()`` + ``split("\\n")`` produced.

    Args:
        compressed_path: Path to a ``.tsv.bz2`` sentence file.
        conn: A connection to a staging DB created by :func:`_create_staging_db`.
        table: ``"jpn"`` or ``"target"``.

    Raises:
        ValueError: if ``table`` is not one of the two allowed names.
        EOFError/OSError: if the ``.bz2`` stream is truncated or corrupt.
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
    """Import a Tatoeba ``jpn-<lang>_links.tsv.bz2`` into the ``links`` table.

    Duplicate link rows are inserted as-is, not deduped: each one must yield its
    own output row from the join, exactly as the former dict-join did.

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
    """Import ``sentences_with_audio.tar.bz2`` into the ``audio`` table.

    Streams each tar member line-by-line so the ~1.5 M audio ids are never held
    in RAM as a Python set. A first column that is not all digits is skipped
    (the export carries a header row).

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


def _read_metadata() -> dict:
    """Read metadata.json as a dict, tolerating a missing or corrupt file.

    A missing file or invalid JSON yields ``{}`` so a fresh download always
    starts from an empty record rather than crashing.
    """
    if not os.path.exists(METADATA_FILE):
        return {}
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_metadata_atomic(metadata: dict) -> None:
    """Write ``metadata`` to :data:`METADATA_FILE` atomically.

    Writes to a ``.tmp`` sibling and :func:`os.replace`-s it into place, so a
    crash mid-write never leaves a truncated ``metadata.json`` behind.
    """
    tmp_path = METADATA_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        os.replace(tmp_path, METADATA_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _atomic_replace_db(src: str, dst: str) -> None:
    """Atomically move the freshly built index ``src`` onto the final ``dst``.

    Retries briefly because on Windows :func:`os.replace` raises
    :class:`PermissionError` while ``dst`` is open in another process.
    ``search_word`` connections are short-lived and the batch dialog is modal,
    so a brief wait usually lets the handle close.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5)
    assert last_exc is not None
    raise last_exc


def _sweep_stale_workdirs(
    base_dir: Optional[str] = None,
    prefix: str = WORKDIR_PREFIX,
    max_age_seconds: int = WORKDIR_MAX_AGE_SECONDS,
    now: Optional[float] = None,
) -> int:
    """Remove left-behind ``<prefix>*`` workdirs older than ``max_age_seconds``.

    A hard kill mid-run leaves a ``download_*`` directory behind; the next
    download sweeps it. The threshold is generous so a legitimately long-running
    download is never swept out from under itself.

    Args:
        base_dir: Directory holding the workdirs. Defaults to
            :data:`USER_FILES_DIR`, resolved at call time so tests that redirect
            the module constant are honored.
        prefix: Workdir name prefix (defaults to :data:`WORKDIR_PREFIX`).
        max_age_seconds: Remove dirs whose mtime is older than ``now - this``.
        now: Override for the current time (mainly for tests).

    Returns:
        The number of directories removed.
    """
    if base_dir is None:
        base_dir = USER_FILES_DIR
    if not os.path.isdir(base_dir):
        return 0
    current = time.time() if now is None else now
    cutoff = current - max_age_seconds
    removed = 0
    for name in os.listdir(base_dir):
        if not name.startswith(prefix):
            continue
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def remove_legacy_pairs_files(base_dir: Optional[str] = None) -> int:
    """Delete the ``jpn_<lang>_pairs.tsv`` files written by versions up to 1.5.0.

    Those releases kept the decompressed sentence-pair TSV next to the index.
    The streaming importer never writes one, and nothing else deletes them, so
    without this sweep an upgrading user carries a few hundred MB of dead data
    per language forever.

    Runs at add-on start and again before every download. Never raises: a
    missing directory or an unlink failure is logged and ignored, because
    reclaiming disk space must not be able to break the add-on.

    Args:
        base_dir: Directory to sweep. Defaults to :data:`USER_FILES_DIR`,
            resolved at call time so tests that redirect the module constant
            are honored.

    Returns:
        The number of files removed.
    """
    if base_dir is None:
        base_dir = USER_FILES_DIR
    removed = 0
    try:
        if not os.path.isdir(base_dir):
            return 0
        for name in os.listdir(base_dir):
            if not (name.startswith("jpn_") and name.endswith("_pairs.tsv")):
                continue
            path = os.path.join(base_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                removed += 1
                logging.info("Removed legacy Tatoeba pairs file: %s", path)
            except OSError as e:
                logging.warning("Could not remove legacy pairs file %s: %s", path, e)
    except OSError as e:
        logging.warning("Could not sweep legacy pairs files in %s: %s", base_dir, e)
    return removed


def download_tatoeba_data(lang_code: str, progress_callback=None) -> tuple[bool, str]:
    """
    Download and build the search index for a language, with near-constant memory.

    Streams each compressed Tatoeba file to a per-run workdir, imports it
    line-by-line into a throwaway SQLite *staging* DB (never materializing a
    decompressed corpus), then joins sentences + links + audio in SQL and feeds
    the cursor straight into :func:`build_sqlite_index`. The completed index is
    atomically renamed onto the final ``jpn_<lang>_index.db``; on any failure
    the workdir is removed and the previous index is left untouched.

    Args:
        lang_code: The ISO 639-3 language code (e.g. 'eng', 'spa').
        progress_callback: Optional callable receiving a localized status string.

    Returns:
        ``(success, message)``. On failure the previous index and metadata are
        left intact and any partial workdir is cleaned up.
    """
    if not is_supported(lang_code):
        return False, f"Unknown language: {lang_code}"

    lang_label = get_localized_name(lang_code)

    try:
        os.makedirs(USER_FILES_DIR, exist_ok=True)
        _sweep_stale_workdirs()
        remove_legacy_pairs_files()

        workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=USER_FILES_DIR)
        staging_path = os.path.join(workdir, "staging.db")
        final_db_path = get_db_path(lang_code)
        conn = None
        try:
            urls = get_download_urls(lang_code)

            if progress_callback:
                progress_callback(_("batch_step_fetch_jpn"))
            jpn_path = os.path.join(workdir, "jpn_sentences.tsv.bz2")
            download_to_file(urls["jpn_sentences"], jpn_path)
            conn = _create_staging_db(staging_path)
            _import_sentences(jpn_path, conn, "jpn")
            os.remove(jpn_path)

            if progress_callback:
                progress_callback(_("batch_step_fetch_target").format(lang=lang_label))
            target_path = os.path.join(workdir, "target_sentences.tsv.bz2")
            download_to_file(urls["target_sentences"], target_path)
            _import_sentences(target_path, conn, "target")
            os.remove(target_path)

            if progress_callback:
                progress_callback(_("batch_step_fetch_links"))
            links_path = os.path.join(workdir, "links.tsv.bz2")
            download_to_file(urls["links"], links_path)
            _import_links(links_path, conn)
            os.remove(links_path)

            if progress_callback:
                progress_callback(_("batch_step_fetch_audio_index"))
            audio_path = os.path.join(workdir, "sentences_with_audio.tar.bz2")
            download_to_file(AUDIO_INDEX_URL, audio_path)
            _import_audio(audio_path, conn)
            os.remove(audio_path)

            if progress_callback:
                progress_callback(_("batch_step_build_tsv"))
            # ORDER BY l.rowid reproduces the old link-file ordering. SQLite
            # scans links in rowid order and does PK lookups on the other
            # tables, so this adds no sort step.
            join_cursor = conn.cursor()
            join_cursor.execute("""
                SELECT j.id, j.text, t.id, t.text,
                       CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END AS has_audio
                FROM links l
                JOIN jpn    j ON j.id = l.jpn_id
                JOIN target t ON t.id = l.target_id
                LEFT JOIN audio a ON a.id = j.id
                ORDER BY l.rowid
            """)

            if progress_callback:
                progress_callback(_("batch_step_build_index"))
            workdir_db = os.path.join(workdir, "index.db")
            count = build_sqlite_index(join_cursor, workdir_db)

            conn.close()
            conn = None
            _atomic_replace_db(workdir_db, final_db_path)

            metadata = _read_metadata()
            metadata[lang_code] = {
                "downloaded_at": datetime.datetime.now().isoformat(),
                "count": count,
            }
            _write_metadata_atomic(metadata)

            translated = _("batch_download_success")
            if translated != "batch_download_success":
                success_msg = translated.format(count=count, lang=lang_label)
            else:
                success_msg = f"Download complete. {count} sentence pairs loaded for {lang_label}."
            return True, success_msg
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            shutil.rmtree(workdir, ignore_errors=True)
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
    Return True if a built search index exists for the language.

    The SQLite index is the sole data artifact and the source of truth, so a
    metadata record alone is not enough — this checks the DB file directly.

    Args:
    - lang_code (str): The ISO 639-3 language code to check for data availability.

    Returns:
    - True if the search index database exists.
    """
    if not is_supported(lang_code):
        return False
    return os.path.exists(get_db_path(lang_code))
