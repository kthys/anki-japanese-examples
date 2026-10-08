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

# Status values returned by fetch_audio_to_temp_ex(). They let callers tell a
# permanent 403 (author restricts reuse — re-select another sentence) apart from
# a 404 (no recording exists — nothing to re-select for).
FETCH_OK = "ok"                    # payload: temp file path
FETCH_NO_RECORDING = "no_recording"  # HTTP 404 — sentence has no recording
FETCH_RESTRICTED = "restricted"    # HTTP 403 — author disallows reuse


class AudioDownloadError(Exception):
    """Raised when a Tatoeba audio download fails for a non-404/403 reason.

    Callers (e.g. batch engine) should catch this exception,
    increment an audio_errors counter, and surface a retry prompt to the user.
    HTTP 404 (no recording exists) and HTTP 403 (the author does not allow
    reuse outside of Tatoeba) do NOT raise this — they return None instead.
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


def fetch_audio_to_temp_ex(jpn_id: str) -> "tuple[str, str | None]":
    """Download the Tatoeba audio for jpn_id, reporting *why* it did not arrive.

    BACKGROUND-SAFE: pure network + local file I/O, no collection access.
    This is the slow phase — run it off the main thread whenever possible.

    This is the status-returning counterpart of fetch_audio_to_temp() and exists
    so batch mode can distinguish a permanent 403 (author restricts reuse outside
    Tatoeba) from a 404 (the sentence simply has no recording): only the former
    justifies re-selecting a different sentence for the same word.

    Args:
        jpn_id: The Tatoeba sentence ID (e.g. "12345"). Used to construct the
                audio URL and the temp filename "{jpn_id}.mp3".

    Returns:
        A ``(status, tmp_path)`` tuple where:
        - ``(FETCH_OK, path)``: the file was downloaded to ``path`` (a temp file
          named "{jpn_id}.mp3" in its own temp directory, because
          col.media.add_file() uses the basename as the destination name). The
          caller owns the file: pass it to register_audio_file() or clean it up
          with cleanup_temp_audio().
        - ``(FETCH_NO_RECORDING, None)``: Tatoeba returned HTTP 404.
        - ``(FETCH_RESTRICTED, None)``: Tatoeba returned HTTP 403 — the audio
          author does not allow reuse outside of Tatoeba. A permanent licensing
          restriction, deliberately not worked around through any alternative
          endpoint.

    Raises:
        AudioDownloadError: For all other network failures (timeout, connection
            refused, DNS failure, non-200/non-404/non-403 HTTP status codes).
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
    # (many sentences have no audio).
    if response.status_code == 404:
        return FETCH_NO_RECORDING, None

    # 403 means the audio author does not allow reuse outside of Tatoeba (the
    # server's response body states this explicitly). This is a permanent
    # licensing restriction, not a failure — callers skip it. Deliberately not
    # fetched through any alternative endpoint: the author's restriction is
    # respected.
    if response.status_code == 403:
        logger.info(
            "Recording for sentence %s is restricted to Tatoeba-only reuse — skipping",
            jpn_id,
        )
        return FETCH_RESTRICTED, None

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
    return FETCH_OK, tmp_path


def fetch_audio_to_temp(jpn_id: str) -> "str | None":
    """Download the Tatoeba audio for jpn_id into a temporary file.

    Thin wrapper over fetch_audio_to_temp_ex() that discards the reason: it
    returns the temp path on success and None for both a 404 (no recording) and
    a 403 (reuse restricted). Callers that need to tell the two apart (batch
    re-selection) should use fetch_audio_to_temp_ex() directly.

    BACKGROUND-SAFE: see fetch_audio_to_temp_ex().

    Raises:
        AudioDownloadError: For all other network failures.
    """
    status, tmp_path = fetch_audio_to_temp_ex(jpn_id)
    if status == FETCH_OK:
        return tmp_path
    return None


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
