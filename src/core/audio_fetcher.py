"""
audio_fetcher.py — Download Tatoeba sentence audio and register it in Anki's media collection.

THREADING MODEL:
    The work is split into two phases so the slow part can run off the main thread:
    - fetch_audio_to_temp(): pure network + temp file. Safe to call from a
      background thread (e.g. inside a QueryOp op).
    - register_audio_file(): calls col.media.add_file(), which requires
      main-thread execution (Anki architectural constraint). MUST be called
      from the main thread (e.g. a QueryOp success callback).
    download_audio() composes both phases synchronously and therefore MUST be
    called from the main thread. Callers are responsible for these preconditions.
"""

import logging
import os
import tempfile

import requests

try:
    from aqt import mw
except ImportError:
    mw = None  # for testing outside Anki

logger = logging.getLogger(__name__)

AUDIO_URL_TEMPLATE = "https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3"
REQUEST_TIMEOUT = 10  # seconds


class AudioDownloadError(Exception):
    """Raised when a Tatoeba audio download fails for a non-404 reason.

    Callers (e.g. batch engine) should catch this exception,
    increment an audio_errors counter, and surface a retry prompt to the user.
    HTTP 404 (no recording exists) does NOT raise this — it returns None instead.
    """


def cleanup_temp_audio(tmp_path: str) -> None:
    """Delete a temp audio file created by fetch_audio_to_temp() and its directory.

    Safe to call multiple times; missing files only produce a log warning.
    """
    tmp_dir = os.path.dirname(tmp_path)
    try:
        os.unlink(tmp_path)
    except OSError:
        logger.warning("Could not delete temp file %s", tmp_path)
    try:
        os.rmdir(tmp_dir)
    except OSError:
        logger.warning("Could not delete temp dir %s", tmp_dir)


def fetch_audio_to_temp(jpn_id: str) -> "str | None":
    """Download the Tatoeba audio for jpn_id into a temporary file.

    BACKGROUND-SAFE: pure network + local file I/O, no collection access.
    This is the slow phase — run it off the main thread whenever possible.

    Args:
        jpn_id: The Tatoeba sentence ID (e.g. "12345"). Used to construct the
                audio URL and the temp filename "{jpn_id}.mp3".

    Returns:
        The path to a temp file named "{jpn_id}.mp3" (in its own temp directory,
        because col.media.add_file() uses the basename as the destination name).
        The caller owns the file: pass it to register_audio_file() or clean it
        up with cleanup_temp_audio().
        None if Tatoeba returns HTTP 404 (sentence has no recording — not an error).

    Raises:
        AudioDownloadError: For all other network failures (timeout, connection
            refused, DNS failure, non-200/non-404 HTTP status codes).
    """
    url = AUDIO_URL_TEMPLATE.format(jpn_id=jpn_id)

    # Attempt the HTTP download. Network-level errors (DNS, timeout, connection
    # refused) raise requests.exceptions.RequestException before we get a response.
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise AudioDownloadError(
            f"Network error fetching audio for sentence {jpn_id}: {exc}"
        ) from exc

    # 404 means Tatoeba has no recording for this sentence — this is normal
    # (many sentences have no audio). Return None so callers can handle gracefully.
    if response.status_code == 404:
        return None

    # All other non-2xx responses are unexpected failures.
    try:
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise AudioDownloadError(
            f"HTTP {response.status_code} fetching audio for sentence {jpn_id}: {exc}"
        ) from exc

    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, f"{jpn_id}.mp3")
    try:
        with open(tmp_path, "wb") as tmp:
            tmp.write(response.content)
    except OSError:
        cleanup_temp_audio(tmp_path)
        raise
    return tmp_path


def register_audio_file(tmp_path: str, col) -> str:
    """Register a fetched temp audio file in col.media and clean up the temp file.

    PRECONDITION: Must be called from the main thread.
    col.media.add_file() requires main-thread execution (Anki architectural constraint).

    Args:
        tmp_path: Path returned by fetch_audio_to_temp().
        col:      The Anki collection object (mw.col).

    Returns:
        The filename returned by col.media.add_file() — add_file() copies the
        file into the media directory, renames on hash collision, and returns
        the final stored name. NEVER assume the basename survived unchanged.

    The temp file is deleted whether or not add_file() succeeds.
    """
    try:
        stored_fname = col.media.add_file(tmp_path)
    finally:
        cleanup_temp_audio(tmp_path)
    return stored_fname


def download_audio(jpn_id: str, col) -> "str | None":
    """Download the Tatoeba audio for jpn_id and register it in col.media.

    PRECONDITION: Must be called from the main thread (composes both phases
    synchronously, including register_audio_file()).

    Checks col.media.have() before downloading — repeated calls for the same
    jpn_id are idempotent and will not re-download an already-registered file.

    Args:
        jpn_id: The Tatoeba sentence ID (e.g. "12345"). Used to construct the
                audio URL and the expected filename "{jpn_id}.mp3".
        col:    The Anki collection object (mw.col). Must expose col.media.have()
                and col.media.add_file() with the standard Anki MediaManager signatures.

    Returns:
        The filename returned by col.media.add_file() on successful download.
        None if Tatoeba returns HTTP 404 (sentence has no recording — not an error).

    Raises:
        AudioDownloadError: For all other network failures (timeout, connection
            refused, DNS failure, non-200/non-404 HTTP status codes).
    """
    expected_fname = f"{jpn_id}.mp3"

    # Pre-download dedup check: if the file is already in col.media, return immediately.
    # This covers re-runs without re-downloading. The filename "{jpn_id}.mp3" is what
    # add_file would have returned the first time (Anki only renames on hash collision,
    # which is exceedingly unlikely for globally-unique Tatoeba IDs).
    if col.media.have(expected_fname):
        return expected_fname

    tmp_path = fetch_audio_to_temp(jpn_id)
    if tmp_path is None:
        return None
    return register_audio_file(tmp_path, col)
