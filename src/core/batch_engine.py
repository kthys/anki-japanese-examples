import re
import html
import random
import logging
import sqlite3
import os
import time
from dataclasses import dataclass, field

try:
    from . import tatoeba_data
except ImportError:
    import tatoeba_data

try:
    from . import languages
except ImportError:
    try:
        import languages
    except ImportError:
        languages = None  # type: ignore

try:
    from . import audio_fetcher
    from .audio_fetcher import AudioDownloadError
except ImportError:
    try:
        import audio_fetcher
        from audio_fetcher import AudioDownloadError
    except ImportError:
        audio_fetcher = None  # type: ignore
        AudioDownloadError = Exception  # type: ignore

try:
    from anki.collection import SearchNode
except Exception:
    SearchNode = None  # type: ignore

logger = logging.getLogger(__name__)


def build_deck_search(col, deck_id: int) -> str:
    """Return a search string matching a deck AND all of its subdecks.

    Uses Anki's deck: operator (via SearchNode for proper name escaping),
    which — unlike did: — also matches descendants and cards temporarily
    moved to a filtered deck (matched by their home deck).

    Falls back to a manually-escaped deck:"name" string when SearchNode is
    unavailable (old Anki) or fails.
    """
    deck_name = col.decks.name(deck_id)
    if SearchNode is not None:
        try:
            return col.build_search_string(SearchNode(deck=deck_name))
        except Exception:
            pass
    escaped = str(deck_name)
    for ch in ("\\", '"', "*", "_"):
        escaped = escaped.replace(ch, "\\" + ch)
    return f'deck:"{escaped}"'


def clean_word(word: str) -> str:
    """
    Clean the word by removing HTML tags and reading annotations
    (e.g., brackets `[...]`, `(...)`, `（...）`).
    """
    # Remove HTML tags
    word = re.sub(r'<[^>]+>', '', word)
    # Remove standard brackets and contents
    word = re.sub(r'\[.*?\]', '', word)
    # Remove half-width parentheses and contents
    word = re.sub(r'\(.*?\)', '', word)
    # Remove full-width parentheses and contents
    word = re.sub(r'（.*?）', '', word)
    
    return word.strip()

@dataclass
class BatchResult:
    """
    Stores the outcome counters of a batch processing run.

    Args:
    - updated (int): Number of notes successfully updated with examples.
    - skipped_existing (int): Notes skipped because they already had examples.
    - skipped_no_match (int): Notes skipped because no matching sentence was found.
    - skipped_missing_fields (int): Notes skipped due to missing source or destination fields.
    - errors (int): Notes where an unexpected error occurred during processing.
    - audio_added (int): Populated by register_pending_audio(); sentences with audio
      successfully registered.
    - audio_skipped (int): Populated by register_pending_audio(); sentences where
      Tatoeba returned 404 (no recording) or whose 403 re-selection found no
      usable alternative.
    - audio_errors (int): Populated by register_pending_audio(); sentences where the
      download or registration failed.
    - audio_reselected (int): Populated by apply_audio_fields(); pairs whose audio
      came back 403 (author restricts reuse) and whose text + audio were swapped
      to a different sentence that downloaded successfully. Informational only —
      these pairs also count in audio_added.
    - pending_audio (list[tuple]): Staging list of re-selection-ready 6-tuples
      ``(jpn_id, note_id, jpn_field, trans_field, audio_field, alt_candidates)``
      accumulated by run_batch(), downloaded in a background op by
      download_pending_audio(), and drained on the main thread by
      register_pending_audio(). ``alt_candidates`` is a list of
      ``(jpn_id, jpn_text, trans_text)`` fallbacks (all with a recording in the
      index, none already selected or present on the note) to try when the
      primary sentence's audio is 403-restricted.
    - audio_error_details (list[str]): One human-readable line per item counted in
      audio_errors, recorded wherever the counter is incremented. The download phase
      captures the reason on a background thread; write_audio_error_log() later
      appends these lines to a file so failures survive after the report dialog closes.
    - changes: The OpChanges returned by the final merge_undo_entries() call when
      run_batch() was given an undo_name; None otherwise. A CollectionOp op must
      return this so Anki refreshes open windows and updates the Undo menu.
    """
    updated: int = 0
    skipped_existing: int = 0
    skipped_no_match: int = 0
    skipped_missing_fields: int = 0
    errors: int = 0
    audio_added: int = 0
    audio_skipped: int = 0
    audio_errors: int = 0
    audio_reselected: int = 0
    pending_audio: list = field(default_factory=list)
    audio_error_details: list = field(default_factory=list)
    changes: object = None

    @property
    def total_processed(self) -> int:
        """Return the total number of notes that were processed."""
        return (self.updated + self.skipped_existing + self.skipped_no_match
                + self.skipped_missing_fields + self.errors)


# Per-item outcome statuses produced by download_pending_audio() and
# consumed by register_audio_media().
# AUDIO_RESOLVED: audio is available. payload is an audio_fetcher.ResolvedAudio
#   carrying the chosen sentence + source; register_audio_media() registers a
#   temp file or passes a media filename through, and apply_audio_fields() swaps
#   the pair's text when the resolution came from a 403 re-selection.
AUDIO_RESOLVED = "resolved"
AUDIO_NO_RECORDING = "no_audio"  # payload: None (Tatoeba 404, or 403 with no usable alternative)
AUDIO_FETCH_ERROR = "error"      # payload: None


def download_pending_audio(result: BatchResult, col, progress_cb=None) -> list:
    """Download phase: fetch every pending audio item to a temp file.

    BACKGROUND-SAFE: only reads the collection (get_note, media.have) and does
    network + temp-file I/O. Designed to run inside a QueryOp op. All counter
    accounting happens later in register_pending_audio(), on the main thread.

    For each 6-tuple
    (jpn_id, note_id, jpn_field, trans_field, audio_field, alt_candidates) in
    result.pending_audio, produces one
    (note_id, jpn_field, trans_field, audio_field, jpn_id, status, payload) tuple:
    - AUDIO_RESOLVED: audio is available (already in col.media, or downloaded).
      payload is an audio_fetcher.ResolvedAudio naming the chosen sentence and
      the source; a 403 on the primary is resolved by re-selecting from
      alt_candidates, and the ResolvedAudio then carries the replacement text.
    - AUDIO_NO_RECORDING: Tatoeba returned 404, or returned 403 and no alternative
      candidate's audio could be obtained; payload is None.
    - AUDIO_FETCH_ERROR: download failed, audio field missing from the note
      type, or the note could not be loaded; payload is None. One bad item
      never aborts the remaining downloads.

    Args:
    - result: The BatchResult whose pending_audio list is read (not cleared here).
    - col: The Anki collection object.
    - progress_cb (callable, optional): Called as progress_cb(current, total)
      before each item, for UI progress updates.

    Returns:
    - The list of per-item outcome tuples, in pending_audio order.
    """
    items: list = []
    if audio_fetcher is None:
        logger.error("audio_fetcher module not available — skipping audio downloads")
        return items

    field_names_cache: dict[int, list[str]] = {}
    # Per-note bookkeeping for 403 re-selection, shared across the pairs of one
    # note so two restricted pairs never swap to the same sentence and no pair
    # retries a recording already known to be restricted.
    note_restricted_ids: dict[int, set] = {}
    note_used_ids: dict[int, set] = {}

    total = len(result.pending_audio)
    for i, entry in enumerate(result.pending_audio, start=1):
        if progress_cb:
            progress_cb(i, total)
        (jpn_id, note_id, jpn_field, trans_field,
         audio_field, alt_candidates) = entry
        try:
            note = col.get_note(note_id)
            ntid = note.mid
            if ntid not in field_names_cache:
                field_names_cache[ntid] = [fld["name"] for fld in note.note_type()["flds"]]
            if audio_field not in field_names_cache[ntid]:
                logger.warning("Audio field %r not found on note %d", audio_field, note_id)
                result.audio_error_details.append(
                    f"note {note_id}, sentence {jpn_id}: audio field "
                    f"{audio_field!r} not found on the note type"
                )
                items.append((note_id, jpn_field, trans_field, audio_field,
                              jpn_id, AUDIO_FETCH_ERROR, None))
                continue

            resolved = audio_fetcher.resolve_audio(
                jpn_id, alt_candidates, col,
                restricted_ids=note_restricted_ids.setdefault(note_id, set()),
                used_ids=note_used_ids.setdefault(note_id, set()),
            )
            if resolved is None:
                items.append((note_id, jpn_field, trans_field, audio_field,
                              jpn_id, AUDIO_NO_RECORDING, None))
            else:
                items.append((note_id, jpn_field, trans_field, audio_field,
                              jpn_id, AUDIO_RESOLVED, resolved))
        except AudioDownloadError as exc:
            logger.error("Audio download error for jpn_id %s: %s", jpn_id, exc)
            result.audio_error_details.append(
                f"note {note_id}, sentence {jpn_id}: {exc}"
            )
            items.append((note_id, jpn_field, trans_field, audio_field,
                          jpn_id, AUDIO_FETCH_ERROR, None))
        except Exception as exc:
            logger.exception(
                "Unexpected error downloading audio for note %d (jpn_id %s)",
                note_id, jpn_id,
            )
            result.audio_error_details.append(
                f"note {note_id}, sentence {jpn_id}: unexpected error: {exc}"
            )
            items.append((note_id, jpn_field, trans_field, audio_field,
                          jpn_id, AUDIO_FETCH_ERROR, None))
    return items


def register_audio_media(items: list, result: BatchResult, col) -> list:
    """Media half of audio registration: store fetched files in col.media.

    PRECONDITION: Must be called from the main thread
    (col.media.add_file constraint). Fast — local file copies only.

    Media files are outside Anki's undo system, which is why this phase is
    deliberately separate from the note-field writes in apply_audio_fields()
    (those run inside a CollectionOp so they land in a named undo entry).

    Consumes the outcome tuples produced by download_pending_audio():
    - AUDIO_RESOLVED: registers the temp file via register_audio_file() (which
      owns temp cleanup even on failure) when the source is "temp", or passes
      the existing filename through when it is "media". The sentence text is
      carried along so apply_audio_fields() can swap the pair when the audio
      came from a 403 re-selection. The file is stored under the resolved
      sentence's own jpn_id.
    - AUDIO_NO_RECORDING: increments result.audio_skipped.
    - AUDIO_FETCH_ERROR: increments result.audio_errors.
    - Any unexpected exception: logs, increments audio_errors, continues.

    Returns:
    - A list of (note_id, audio_field, jpn_id, fname, jpn_field, trans_field,
      new_jpn_text, new_trans_text) 8-tuples for apply_audio_fields(). The last
      four elements are None for a normal (non-reselected) item.
    """
    registered: list = []
    for (note_id, jpn_field, trans_field, audio_field,
         jpn_id, status, payload) in items:
        try:
            if status == AUDIO_FETCH_ERROR:
                result.audio_errors += 1
            elif status == AUDIO_NO_RECORDING:
                result.audio_skipped += 1
            else:  # AUDIO_RESOLVED
                resolved = payload
                if resolved.source == "media":
                    fname = resolved.payload
                else:
                    fname = audio_fetcher.register_audio_file(resolved.payload, col)
                registered.append(
                    (note_id, audio_field, resolved.jpn_id, fname,
                     jpn_field, trans_field,
                     resolved.jpn_text, resolved.trans_text))
        except Exception as exc:
            result.audio_errors += 1
            result.audio_error_details.append(
                f"note {note_id}, sentence {jpn_id}: failed to register audio "
                f"file {payload!r}: {exc}"
            )
            logger.exception(
                "Failed to register audio media for note %d (jpn_id %s)",
                note_id, jpn_id,
            )
    return registered


def apply_audio_fields(registered: list, result: BatchResult, col,
                       undo_name: "str | None" = None):
    """Field half of audio registration: write [sound:fname] tags to notes.

    BACKGROUND-SAFE: collection writes only, no media access — designed to
    run inside a CollectionOp op so the writes land in one named undo entry
    and open windows refresh afterwards.

    For each (note_id, audio_field, jpn_id, fname, jpn_field, trans_field,
    new_jpn_text, new_trans_text) from register_audio_media():
    - Writes [sound:fname] verbatim, calls col.update_note, increments
      result.audio_added.
    - When new_jpn_text is not None (a 403 re-selection), first overwrites the
      pair's Japanese/Translation fields with the HTML-escaped replacement text,
      in the same update_note call so the swap is part of the audio undo entry,
      and increments result.audio_reselected.
    - On missing fields or any unexpected exception: logs, increments
      result.audio_errors, continues with the next item.

    Clears result.pending_audio after processing.

    Returns:
    - The OpChanges from the final merge_undo_entries() when undo_name is set
      (return this from the CollectionOp op), otherwise None.
    """
    undo_pos = None
    if undo_name is not None:
        try:
            undo_pos = col.add_custom_undo_entry(undo_name)
        except Exception:
            logger.warning("Custom undo entry unavailable — audio tags will not be undoable",
                           exc_info=True)

    field_names_cache: dict[int, list[str]] = {}
    applied = 0
    for (note_id, audio_field, jpn_id, fname,
         jpn_field, trans_field, new_jpn_text, new_trans_text) in registered:
        try:
            note = col.get_note(note_id)
            ntid = note.mid
            if ntid not in field_names_cache:
                field_names_cache[ntid] = [fld["name"] for fld in note.note_type()["flds"]]
            field_names = field_names_cache[ntid]
            if audio_field not in field_names:
                result.audio_errors += 1
                result.audio_error_details.append(
                    f"note {note_id}, sentence {jpn_id}: audio field "
                    f"{audio_field!r} not found on the note type"
                )
                logger.warning("Audio field %r not found on note %d", audio_field, note_id)
                continue

            # 403 re-selection: swap the pair's example text as well, so the
            # sentence the audio belongs to is the one shown. The fields were
            # validated in run_batch, but re-check defensively.
            if new_jpn_text is not None:
                if jpn_field not in field_names or trans_field not in field_names:
                    result.audio_errors += 1
                    result.audio_error_details.append(
                        f"note {note_id}, sentence {jpn_id}: cannot re-select — "
                        f"field {jpn_field!r}/{trans_field!r} not found on the note type"
                    )
                    logger.warning(
                        "Re-selection fields %r/%r not found on note %d",
                        jpn_field, trans_field, note_id)
                    continue
                note.fields[field_names.index(jpn_field)] = html.escape(new_jpn_text)
                note.fields[field_names.index(trans_field)] = html.escape(new_trans_text)

            audio_idx = field_names.index(audio_field)
            note.fields[audio_idx] = f"[sound:{fname}]"
            col.update_note(note)
            result.audio_added += 1
            if new_jpn_text is not None:
                result.audio_reselected += 1
            applied += 1
            # Same periodic merge as run_batch — see comment there.
            if undo_pos is not None and applied % 30 == 0:
                col.merge_undo_entries(undo_pos)
        except Exception as exc:
            result.audio_errors += 1
            result.audio_error_details.append(
                f"note {note_id}, sentence {jpn_id}: unexpected error applying "
                f"audio field: {exc}"
            )
            logger.exception(
                "Unexpected error applying audio field for note %d (jpn_id %s)",
                note_id, jpn_id,
            )

    changes = None
    if undo_pos is not None:
        try:
            changes = col.merge_undo_entries(undo_pos)
        except Exception:
            logger.warning("Could not merge audio undo entries", exc_info=True)
    result.pending_audio.clear()
    return changes


def register_pending_audio(items: list, result: BatchResult, col) -> None:
    """Register phase: store fetched files in col.media and write [sound:] tags.

    PRECONDITION: Must be called from the main thread
    (col.media.add_file constraint).

    Synchronous composition of register_audio_media() and apply_audio_fields()
    without undo bracketing — kept for callers that don't run inside a
    CollectionOp (batch_ui splits the phases itself to get undoable writes).
    Outcome semantics are documented on the two halves.
    """
    registered = register_audio_media(items, result, col)
    apply_audio_fields(registered, result, col)


def process_pending_audio(result: BatchResult, col) -> None:
    """Drain result.pending_audio synchronously: download then register.

    PRECONDITION: Must be called from the main thread (the register phase
    requires it). Convenience composition of download_pending_audio() and
    register_pending_audio() — note that the downloads block the calling
    thread, so prefer running the download phase in a background op and
    only the register phase on the main thread (as batch_ui.py does).

    Outcome semantics (counters on result, [sound:] tags, per-item error
    containment) are documented on the two phase functions.
    """
    if audio_fetcher is None:
        logger.error("audio_fetcher module not available — skipping audio processing")
        return
    items = download_pending_audio(result, col)
    register_pending_audio(items, result, col)


def write_audio_error_log(result: BatchResult, log_path: "str | None" = None) -> "str | None":
    """Append this run's audio error details to a log file.

    Called on the main thread after a batch run (e.g. from the report dialog),
    so the per-item reasons captured in result.audio_error_details survive after
    the transient report dialog closes. Each call appends a timestamped block,
    keeping a history across runs; only errors are written, so the file grows
    negligibly.

    Never raises: a logging failure must not break the calling UI flow. Failures
    are logged via logger.exception and reported as a None return.

    Args:
    - result: The BatchResult whose audio_error_details are written.
    - log_path (str | None): Destination file. When None (default), the path is
      user_files/batch_errors.log (resolved from tatoeba_data.USER_FILES_DIR at
      call time, so tests can redirect it).

    Returns:
    - The log file path on success, or None when there are no errors to write or
      the write failed.
    """
    if not result.audio_error_details:
        return None

    if log_path is None:
        try:
            user_files_dir = tatoeba_data.USER_FILES_DIR
        except Exception:
            logger.exception("Could not resolve user_files directory for audio error log")
            return None
        log_path = os.path.join(user_files_dir, "batch_errors.log")

    try:
        parent = os.path.dirname(log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        count = len(result.audio_error_details)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"=== {timestamp} — {count} audio error(s) ===\n")
            for detail in result.audio_error_details:
                fh.write(f"{detail}\n")
            fh.write("\n")
        return log_path
    except Exception:
        logger.exception("Could not write audio error log to %s", log_path)
        return None


def run_batch(
    col,
    deck_id: int,
    lang_code: str,
    source_field: str,
    dest_field_pairs: "list[tuple[str, str, str | None]]",
    skip_existing: bool = True,
    undo_name: "str | None" = None,
) -> BatchResult:
    """
    Run batch processing on all notes of a selected deck.

    Iterates over every note in the given deck, reads a Japanese word from the
    source field, looks it up in the local SQLite index, and writes randomly
    selected unique matching sentences + translations (HTML-escaped) into the 
    destination field pairs.

    Args:
    - col: The Anki collection object (``mw.col``).
    - deck_id (int): The ID of the deck to process. The deck's subdecks are
      included (see build_deck_search).
    - lang_code (str): ISO 639-3 language code for Tatoeba data (e.g. 'eng', 'spa').
    - source_field (str): Name of the note field containing the word to search.
    - dest_field_pairs (list[tuple[str, str, str | None]]): List of triples of (Japanese,
      Translation, Audio) destination field names. The audio element is the destination field name
      for the [sound:] tag, or None if no audio is configured for this pair.
    - skip_existing (bool): If True, field pairs that already have content in both
      Japanese and Translation fields are excluded before sentence selection, so
      matches are only spent on pairs that need them; sentences already present in
      a filled pair are not re-selected for another pair. Notes where all pairs are
      filled count as skipped_existing and skip the database lookup entirely.
      Default is True.
    - undo_name (str | None): When set, all note updates are collected into a
      single named undo entry (add_custom_undo_entry + merge_undo_entries) and
      the resulting OpChanges is stored on result.changes — pass this when
      running inside a CollectionOp. When None (default), no undo bracketing
      is done.

    Returns:
    - A BatchResult dataclass with counters for updated, skipped, and errored notes.
    """
    result = BatchResult()

    if not dest_field_pairs:
        return result

    # Resolve the language and database path
    if languages is None or not languages.is_supported(lang_code):
        logger.error(f"Unknown language code: {lang_code}")
        return result

    db_path = tatoeba_data.get_db_path(lang_code)
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return result

    note_ids = col.find_notes(build_deck_search(col, deck_id))
    field_names_cache: dict[int, list[str]] = {}

    undo_pos = None
    if undo_name is not None:
        try:
            undo_pos = col.add_custom_undo_entry(undo_name)
        except Exception:
            logger.warning("Custom undo entry unavailable — batch will not be undoable",
                           exc_info=True)

    conn = sqlite3.connect(db_path)
    try:
        for nid in note_ids:
            try:
                note = col.get_note(nid)
                ntid = note.mid
                if ntid not in field_names_cache:
                    field_names_cache[ntid] = [fld["name"] for fld in note.note_type()["flds"]]
                field_names = field_names_cache[ntid]

                # Check that required fields exist on this note type
                if source_field not in field_names:
                    result.skipped_missing_fields += 1
                    continue
                
                # Verify all destination fields exist
                fields_missing = False
                for jpn_dest, trans_dest, audio_dest in dest_field_pairs:
                    if jpn_dest not in field_names or trans_dest not in field_names:
                        fields_missing = True
                        break
                
                if fields_missing:
                    result.skipped_missing_fields += 1
                    continue

                # Read source word
                source_idx = field_names.index(source_field)
                raw_word = note.fields[source_idx].strip()
                word = clean_word(raw_word)
                if not word:
                    result.skipped_missing_fields += 1
                    continue

                # Keep only pairs that still need content. "Filled" means both
                # Japanese and Translation fields are non-empty; half-filled
                # pairs are overwritten. Filtering before the SQLite query means
                # fully-filled notes skip the search entirely, and filled pairs
                # never consume a match that an empty pair could use.
                if skip_existing:
                    target_pairs = []
                    filled_texts = set()
                    for jpn_field, trans_field, audio_field in dest_field_pairs:
                        jpn_val = note.fields[field_names.index(jpn_field)].strip()
                        trans_val = note.fields[field_names.index(trans_field)].strip()
                        if jpn_val and trans_val:
                            filled_texts.add(jpn_val)
                        else:
                            target_pairs.append((jpn_field, trans_field, audio_field))
                    if not target_pairs:
                        result.skipped_existing += 1
                        continue
                else:
                    target_pairs = list(dest_field_pairs)
                    filled_texts = set()

                # Search for matches
                matches = tatoeba_data.search_word(db_path, word, conn=conn)
                # Drop sentences already present in a filled pair so re-runs
                # don't duplicate an existing example in another slot.
                if filled_texts:
                    matches = [m for m in matches if html.escape(m[1]) not in filled_texts]
                if not matches:
                    result.skipped_no_match += 1
                    continue

                # Audio-first selection: prefer sentences with Tatoeba recordings.
                # Pairs with an audio field configured are sorted first so that
                # audio sentences are assigned to those slots — ensuring the text
                # written to a pair and the audio queued for that same pair come
                # from the same sentence.
                n = min(len(target_pairs), len(matches))
                audio = [m for m in matches if m[3]]
                non_audio = [m for m in matches if not m[3]]
                selected_matches = random.sample(audio, min(n, len(audio)))
                if len(selected_matches) < n:
                    selected_matches += random.sample(
                        non_audio, min(n - len(selected_matches), len(non_audio))
                    )

                # Sort target pairs so audio-configured pairs come first
                # (sorted() is stable, so relative order within each group is kept).
                ordered_pairs = sorted(
                    target_pairs[:n],
                    key=lambda p: 0 if p[2] is not None else 1,
                )

                # Fallback pool for 403 re-selection: recordings the note is not
                # already using. Only audio-bearing matches qualify (a non-audio
                # sentence would just 404), and any sentence already selected for
                # this note is excluded so a swap never duplicates another pair.
                # filled_texts was already applied to `matches` above.
                selected_jpn_ids = {m[0] for m in selected_matches}
                alt_candidates = [
                    (m[0], m[1], m[2]) for m in audio if m[0] not in selected_jpn_ids
                ]

                # Write HTML-escaped results. Every selected match lands in a
                # pair: filled pairs were excluded above, so no skips remain here.
                for match, (jpn_field, trans_field, audio_field) in zip(
                        selected_matches, ordered_pairs):
                    jpn_id, jpn_text, trans_text, _has_audio = match
                    jpn_idx = field_names.index(jpn_field)
                    trans_idx = field_names.index(trans_field)
                    note.fields[jpn_idx] = html.escape(jpn_text)
                    note.fields[trans_idx] = html.escape(trans_text)
                    if audio_field is not None:
                        result.pending_audio.append(
                            (jpn_id, nid, jpn_field, trans_field, audio_field,
                             alt_candidates))

                col.update_note(note)
                result.updated += 1
                # Fold the per-note "Update Note" entries into the custom
                # entry as we go — Anki's undo queue holds only ~30 steps,
                # so merging solely at the end would drop entries on large decks.
                if undo_pos is not None and result.updated % 30 == 0:
                    col.merge_undo_entries(undo_pos)

            except Exception as e:
                logger.error(f"Error processing note {nid}: {e}", exc_info=True)
                result.errors += 1
    finally:
        conn.close()

    if undo_pos is not None:
        try:
            result.changes = col.merge_undo_entries(undo_pos)
        except Exception:
            logger.warning("Could not merge undo entries", exc_info=True)

    return result
