"""
audio_fetcher.py — Download Tatoeba sentence audio and register it in Anki's media collection.

THREADING MODEL:
    The work is split into two phases so the slow part can run off the main thread:
    - fetch_audio_to_temp() / resolve_audio(): pure network + temp file (+ a
      read-only col.media.have() check in resolve_audio()). Safe to call from a
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
from typing import NamedTuple, Optional

import requests

try:
    from aqt import mw
except ImportError:
    mw = None  # for testing outside Anki

logger = logging.getLogger(__name__)

AUDIO_URL_TEMPLATE = "https://audio.tatoeba.org/sentences/jpn/{jpn_id}.mp3"
REQUEST_TIMEOUT = 10  # seconds

# Status values returned by fetch_audio_to_temp(). resolve_audio() consumes them
# internally to tell a permanent 403 (author restricts reuse — try another
# sentence) apart from a 404 (no recording exists — nothing to re-select for).
# Callers of resolve_audio() do not need to branch on these.
FETCH_OK = "ok"                    # payload: temp file path
FETCH_NO_RECORDING = "no_recording"  # HTTP 404 — sentence has no recording
FETCH_RESTRICTED = "restricted"    # HTTP 403 — author disallows reuse

# How many alternative sentences one 403-restricted selection may try before
# giving up. Bounds worst-case latency while still surviving the common case
# where a single restricted author recorded every sentence for the word.
MAX_RESELECT_ATTEMPTS = 3


class AudioDownloadError(Exception):
    """Raised when a Tatoeba audio download fails for a non-404/403 reason.

    Callers (e.g. batch engine) should catch this exception,
    increment an audio_errors counter, and surface a retry prompt to the user.
    HTTP 404 (no recording exists) and HTTP 403 (the author does not allow
    reuse outside of Tatoeba) do NOT raise this — they are reported as fetch
    statuses instead.
    """


class ResolvedAudio(NamedTuple):
    """A sentence whose audio was obtained, plus where that audio lives.

    source == "media": ``payload`` is a filename already registered in
        col.media (no download happened; nothing to clean up).
    source == "temp":  ``payload`` is a temp file path that must be passed to
        register_audio_file() on the main thread (which also owns its cleanup).

    ``jpn_text`` / ``trans_text`` are the replacement sentence's text when the
    audio came from a fallback candidate (a 403 re-selection), or None when the
    primary sentence's own audio succeeded — in which case no text swap is needed.
    """
    jpn_id: str
    jpn_text: Optional[str]
    trans_text: Optional[str]
    source: str
    payload: str


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


def fetch_audio_to_temp(jpn_id: str) -> "tuple[str, str | None]":
    """Download the Tatoeba audio for jpn_id, reporting *why* it did not arrive.

    BACKGROUND-SAFE: pure network + local file I/O, no collection access.
    This is the slow phase — run it off the main thread whenever possible.

    Most callers should use resolve_audio() instead, which turns a 403 into a
    fallback sentence automatically. Use this directly only when the raw HTTP
    outcome matters (e.g. probing whether a specific sentence is 403-restricted).

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


def _probe_candidate(jpn_id: str, col) -> "tuple[str, str | None, str | None]":
    """Return ``(status, source, payload)`` for one candidate sentence.

    BACKGROUND-SAFE: reads col.media and does network + temp-file I/O only.

    Checks col.media first (a previously registered file needs no download),
    then fetch_audio_to_temp(). ``source``/``payload`` are only meaningful when
    status is FETCH_OK.
    """
    expected_fname = f"{jpn_id}.mp3"
    if col.media.have(expected_fname):
        return FETCH_OK, "media", expected_fname
    status, tmp_path = fetch_audio_to_temp(jpn_id)
    if status == FETCH_OK:
        return FETCH_OK, "temp", tmp_path
    return status, None, None


def resolve_audio(primary_jpn_id: str, candidates: list, col,
                  restricted_ids=None, used_ids=None) -> "ResolvedAudio | None":
    """Resolve playable audio for primary_jpn_id, re-selecting on a 403.

    BACKGROUND-SAFE: media.have() + network + temp-file I/O only.

    A 404 (no recording) is final — there is nothing to re-select for, so this
    returns None immediately. A 403 means the author restricts reuse, so the
    first candidate whose audio is available (already in col.media, or fetched
    with HTTP 200) is selected instead, and its text is returned so the caller
    can swap the pair. Shared by batch mode (which passes the other audio
    sentences for the word) and manual mode (which passes [] to probe one
    sentence at a time for its list icon).

    Args:
    - primary_jpn_id (str): The sentence the caller would prefer.
    - candidates (list[tuple]): ``(jpn_id, jpn_text, trans_text)`` fallbacks in
      preference order, tried only after a 403 on the primary.
    - col: The Anki collection object (for col.media.have).
    - restricted_ids (set, optional): jpn_ids already known 403-restricted.
      Mutated so sibling calls can skip them. Defaults to a fresh set.
    - used_ids (set, optional): jpn_ids already selected by a sibling call.
      Mutated so two selections never resolve to the same sentence.

    Returns:
    - None when neither the primary nor a candidate could be obtained.
    - Otherwise a ResolvedAudio describing the chosen sentence and audio source.
      ``jpn_text``/``trans_text`` are None when the primary succeeded.

    Raises:
        AudioDownloadError: On a network failure for any attempted sentence.
    """
    if restricted_ids is None:
        restricted_ids = set()
    if used_ids is None:
        used_ids = set()

    status, source, payload = _probe_candidate(primary_jpn_id, col)
    if status == FETCH_OK:
        return ResolvedAudio(primary_jpn_id, None, None, source, payload)
    if status == FETCH_NO_RECORDING:
        return None
    # FETCH_RESTRICTED: the primary's text is fine but its recording may not be
    # reused outside Tatoeba. Only a candidate that actually yields audio is a
    # valid swap, so the pair keeps its original text otherwise.
    restricted_ids.add(primary_jpn_id)

    attempts = 0
    for cand_jpn_id, cand_jpn_text, cand_trans_text in candidates:
        if cand_jpn_id in restricted_ids or cand_jpn_id in used_ids:
            continue
        if attempts >= MAX_RESELECT_ATTEMPTS:
            break
        attempts += 1

        status, source, payload = _probe_candidate(cand_jpn_id, col)
        if status == FETCH_OK:
            used_ids.add(cand_jpn_id)
            return ResolvedAudio(
                cand_jpn_id, cand_jpn_text, cand_trans_text, source, payload)
        if status == FETCH_RESTRICTED:
            # Remember it so another selection does not retry it.
            restricted_ids.add(cand_jpn_id)
        # FETCH_NO_RECORDING: try the next candidate.
    return None


def register_audio_file(tmp_path: str, col) -> str:
    """Register a fetched temp audio file in col.media and clean up the temp file.

    PRECONDITION: Must be called from the main thread.
    col.media.add_file() requires main-thread execution (Anki architectural constraint).

    Args:
        tmp_path: Path returned by fetch_audio_to_temp() (ResolvedAudio.payload
                  when source == "temp").
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
        None if Tatoeba returns HTTP 404 (sentence has no recording — not an error)
        or HTTP 403 (reuse restricted — skipped).

    Raises:
        AudioDownloadError: For all other network failures (timeout, connection
            refused, DNS failure, non-200/non-404/non-403 HTTP status codes).
    """
    resolved = resolve_audio(jpn_id, [], col)
    if resolved is None:
        return None
    if resolved.source == "media":
        return resolved.payload
    return register_audio_file(resolved.payload, col)
