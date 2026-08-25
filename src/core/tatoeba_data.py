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

# Seconds to wait before retrying a failed download_to_file attempt, excluding
# the initial attempt. Three attempts total (initial + these two). Tests patch
# this to (0, 0) to avoid real sleeps.
DOWNLOAD_RETRY_BACKOFF = (1.0, 4.0)

# Per-run workdir prefix for download_tatoeba_data (streaming plan D6): unique
# per run so overlapping builds of the same language never share intermediates.
WORKDIR_PREFIX = "download_"
# Crash-recovery sweep: download_* workdirs older than this are removed at the
# start of the next download (a legit run can take 30+ min, so 1 h is generous).
WORKDIR_MAX_AGE_SECONDS = 3600

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

    ``rows`` yields 5-tuples ``(jpn_id, jpn_text, trans_id, trans_text,
    has_audio)`` — exactly the shape produced by the staging SQL join in
    :func:`download_tatoeba_data` (streaming plan decision D5). ``has_audio``
    is supplied per row by the caller (the join's ``LEFT JOIN audio``), so this
    function no longer takes an ``audio_ids`` set; it coerces the value to 0/1.

    Creates two tables:
      - ``sentences`` — stores (jpn_id, jpn_text, trans_id, trans_text, has_audio)
      - ``words``     — maps each token to the sentence row id for fast lookup

    An index is created on ``words(word)`` to enable efficient exact-match queries.

    The build is atomic: rows are written to ``db_path + ".tmp"`` and moved
    onto ``db_path`` only after the final commit, so ``db_path`` always holds
    either the previous complete index or the new one — never a partial file.

    Tokenization, the chunked ``executemany`` flush, the explicit sequential
    sentence ids, and the ``synchronous=OFF / journal_mode=MEMORY`` pragmas are
    byte-identical to the previous TSV-reading implementation — the boundary and
    inflection tests are the regression guards (streaming plan §6).

    Args:
        rows: An iterable of 5-tuples ``(jpn_id, jpn_text, trans_id, trans_text,
            has_audio)``. Consumed exactly once. An exception raised by the
            iterable aborts the build and leaves the previous ``db_path`` intact
            (and no ``.tmp`` file behind).
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


def _rows_from_tsv(pairs_tsv_path: str, audio_ids: Optional[set] = None) -> Iterable[tuple]:
    """Yield :func:`build_sqlite_index` 5-tuples by reading a legacy pairs TSV file.

    Test-only bridge adapter (streaming plan decision D5): the generator wrapper
    that can still feed :func:`build_sqlite_index` from a TSV. It reproduces the
    exact parsing the old ``build_sqlite_index`` did internally:

      - whole-line ``strip()`` then ``split("\\t")`` (so a trailing ``\\r`` from
        a ``\\r\\n`` file is stripped, matching prior behavior);
      - lines with fewer than 4 tab columns are skipped;
      - ``jpn_id, jpn_text, trans_id, trans_text = parts[0:4]``;
      - ``has_audio = 1`` iff ``audio_ids`` is truthy and ``jpn_id`` is in it,
        else ``0`` — identical to the former
        ``1 if audio_ids and jpn_id in audio_ids else 0``.

    The live :func:`download_tatoeba_data` pipeline now feeds
    :func:`build_sqlite_index` a staging SQL-join cursor instead; this helper is
    kept so the boundary/inflection regression tests can build an index from a
    small TSV fixture without standing up the whole download pipeline.

    Args:
        pairs_tsv_path: Path to a TSV with one
            ``jpn_id\\tjpn_text\\ttrans_id\\ttrans_text`` row per line.
        audio_ids: Optional set/collection of jpn sentence ids that have audio.

    Yields:
        ``(jpn_id, jpn_text, trans_id, trans_text, has_audio)`` 5-tuples.
    """
    with open(pairs_tsv_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue
            jpn_id, jpn_text, trans_id, trans_text = parts[0], parts[1], parts[2], parts[3]
            has_audio = 1 if audio_ids and jpn_id in audio_ids else 0
            yield (jpn_id, jpn_text, trans_id, trans_text, has_audio)


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
    import makes duplicate sentence ids resolve last-wins, matching the former
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

    Parity with the former dict-join pipeline:
      - a line with fewer than 3 tab columns is skipped (``len(parts) < 3``);
      - empty lines are skipped;
      - ``id = parts[0]`` and ``text = parts[2]``;
      - duplicate sentence ids resolve last-wins via ``INSERT OR REPLACE``
        (matching ``jpn_dict[parts[0]] = parts[2]``).

    Only the trailing ``\\n`` is stripped (``rstrip("\\n")``) so any carriage
    return from a ``\\r\\n`` file is preserved in ``text`` exactly as the previous
    whole-content ``strip()`` + ``split("\\n")`` code did.

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
    D4): a duplicate link row previously produced a duplicate output row, and
    the join in :func:`download_tatoeba_data` must preserve that exactly.

    Parity with the former dict-join pipeline:
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

    Streams each tar member line-by-line so the audio index (~1.5 M ids) is
    never held in RAM as a Python set (decision D3). The parse rule is
    byte-identical to the former audio-id parser: every member is read, each
    line is fully stripped, and the first tab-separated column is kept as an
    audio id only when it is all digits (``parts[0].isdigit()``).

    ``INSERT OR REPLACE`` into ``audio(id PRIMARY KEY)`` dedups ids exactly as
    the previous ``set.add`` did.

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

    Used by :func:`download_tatoeba_data` for the read-modify-write of the
    per-language download record. A missing file or invalid JSON yields ``{}``
    so a fresh download always starts from an empty record rather than crashing
    (matching the previous inline ``try/except json.JSONDecodeError`` behavior).
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
    """Write ``metadata`` to :data:`METADATA_FILE` atomically (streaming plan §5).

    Writes to ``METADATA_FILE + ".tmp"`` and :func:`os.replace`-s it into place,
    so a crash mid-write never leaves a truncated/corrupt ``metadata.json`` —
    readers always see either the previous complete file or the new one. The
    ``.tmp`` is removed if the write or replace fails.
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

    Wraps :func:`os.replace` in a short retry loop (streaming plan §5 C2): on
    Windows the replace fails with :class:`PermissionError` if ``dst`` is open
    in another process; ``search_word`` connections are short-lived and the
    batch dialog is modal, so a brief wait usually lets the handle close.
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
    base_dir: str = USER_FILES_DIR,
    prefix: str = WORKDIR_PREFIX,
    max_age_seconds: int = WORKDIR_MAX_AGE_SECONDS,
    now: Optional[float] = None,
) -> int:
    """Remove left-behind ``<prefix>*`` workdirs older than ``max_age_seconds``.

    Crash recovery for :func:`download_tatoeba_data` (streaming plan §5 C4): a
    hard kill mid-run leaves a ``download_*`` directory; the next download
    sweeps any such dir whose mtime is older than the threshold. The threshold
    is generous (1 h) so a legitimately long-running download is never swept
    out from under itself — newborn workdirs always survive.

    Args:
        base_dir: Directory holding the workdirs (defaults to :data:`USER_FILES_DIR`).
        prefix: Workdir name prefix (defaults to :data:`WORKDIR_PREFIX`).
        max_age_seconds: Remove dirs whose mtime is older than ``now - this``.
        now: Override for the current time (mainly for tests).

    Returns:
        The number of directories removed.
    """
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


def download_tatoeba_data(lang_code: str, progress_callback=None) -> tuple[bool, str]:
    """
    Download and build the search index for a language, with near-constant memory.

    Streams each compressed Tatoeba file straight to a per-run workdir, imports
    it line-by-line into a throwaway SQLite *staging* DB (never materializing a
    decompressed corpus), then joins sentences + links + audio in SQL and feeds
    the cursor straight into :func:`build_sqlite_index`. The completed index is
    atomically renamed onto the final ``jpn_<lang>_index.db``; on any failure
    the workdir is removed and the previous index is left untouched.

    Progress callbacks are emitted in the same order and with the same strings
    as before (UI log lines unchanged): fetch jpn -> fetch target -> fetch links
    -> fetch audio index -> build pairs -> build search index. Each "fetch" now
    also performs the staging import for that file; "build pairs" prepares the
    SQL join cursor and "build search index" runs the index build.

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

    The SQLite index (``jpn_<lang>_index.db``) is now the sole data artifact —
    the intermediate pairs TSV no longer exists (streaming plan §4). A present
    metadata record is not enough on its own: the built index is the source of
    truth, so this checks the DB file directly.

    Args:
    - lang_code (str): The ISO 639-3 language code to check for data availability.

    Returns:
    - True if the search index database exists.
    """
    if not is_supported(lang_code):
        return False
    return os.path.exists(get_db_path(lang_code))