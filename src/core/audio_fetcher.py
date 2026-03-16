"""
audio_fetcher.py — Download Tatoeba sentence audio and register it in Anki's media collection.

THREADING PRECONDITION:
    download_audio() is a synchronous function that MUST be called from the main thread.
    col.media.add_file() requires main-thread execution (Anki architectural constraint).
    Callers are responsible for ensuring this precondition.
    - Single-card mode: called directly in the editor callback (main thread OK).
    - Batch mode: called from QueryOp.on_success(), which Anki guarantees
      runs on the main thread.
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


def download_audio(jpn_id: str, col) -> "str | None":
    """Download the Tatoeba audio for jpn_id and register it in col.media.

    PRECONDITION: Must be called from the main thread.
    col.media.add_file() requires main-thread execution (Anki architectural constraint).

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

    # Write the downloaded bytes to a temporary file named {jpn_id}.mp3, then hand
    # the path to col.media.add_file() which copies it into the media folder (with
    # dedup rename on hash collision) and returns the canonical stored filename.
    #
    # We use a temp directory (not NamedTemporaryFile) so that the file can be named
    # "{jpn_id}.mp3". col.media.add_file() uses os.path.basename(path) as the
    # destination filename — the name must be correct before the call.
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, expected_fname)
    try:
        with open(tmp_path, "wb") as tmp:
            tmp.write(response.content)

        # add_file() reads the file, copies it to the media directory, renames on
        # hash collision, and returns the final filename. NEVER return expected_fname
        # here — always return what add_file gives back.
        stored_fname = col.media.add_file(tmp_path)
    finally:
        # Clean up the temp file and directory regardless of whether add_file succeeded.
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("Could not delete temp file %s", tmp_path)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            logger.warning("Could not delete temp dir %s", tmp_dir)

    return stored_fname
