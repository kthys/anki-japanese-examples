import re
import html
import random
import logging
import sqlite3
import os
from dataclasses import dataclass, field

try:
    from . import tatoeba_data
except ImportError:
    import tatoeba_data

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
      Tatoeba returned 404 (no recording).
    - audio_errors (int): Populated by register_pending_audio(); sentences where the
      download or registration failed.
    - pending_audio (list[tuple[str, int, str]]): Staging list of (jpn_id, note_id, audio_field)
      triples accumulated by run_batch(), downloaded in a background op by
      download_pending_audio(), and drained on the main thread by register_pending_audio().
    """
    updated: int = 0
    skipped_existing: int = 0
    skipped_no_match: int = 0
    skipped_missing_fields: int = 0
    errors: int = 0
    audio_added: int = 0
    audio_skipped: int = 0
    audio_errors: int = 0
    pending_audio: list = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        """Return the total number of notes that were processed."""
        return (self.updated + self.skipped_existing + self.skipped_no_match
                + self.skipped_missing_fields + self.errors)


# Per-item outcome statuses produced by download_pending_audio() and
# consumed by register_pending_audio().
AUDIO_FETCHED = "fetched"        # payload: temp file path
AUDIO_EXISTS = "exists"          # payload: filename already in col.media
AUDIO_NO_RECORDING = "no_audio"  # payload: None (Tatoeba 404)
AUDIO_FETCH_ERROR = "error"      # payload: None


def download_pending_audio(result: BatchResult, col, progress_cb=None) -> list:
    """Download phase: fetch every pending audio item to a temp file.

    BACKGROUND-SAFE: only reads the collection (get_note, media.have) and does
    network + temp-file I/O. Designed to run inside a QueryOp op. All counter
    accounting happens later in register_pending_audio(), on the main thread.

    For each (jpn_id, note_id, audio_field) triple in result.pending_audio,
    produces one (note_id, audio_field, jpn_id, status, payload) tuple:
    - AUDIO_EXISTS: file already registered in col.media; payload is the filename.
    - AUDIO_FETCHED: downloaded; payload is the temp file path (owned by the
      caller until register_pending_audio consumes or cleans it).
    - AUDIO_NO_RECORDING: Tatoeba returned 404; payload is None.
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
    total = len(result.pending_audio)
    for i, (jpn_id, note_id, audio_field) in enumerate(result.pending_audio, start=1):
        if progress_cb:
            progress_cb(i, total)
        try:
            note = col.get_note(note_id)
            ntid = note.mid
            if ntid not in field_names_cache:
                field_names_cache[ntid] = [fld["name"] for fld in note.note_type()["flds"]]
            if audio_field not in field_names_cache[ntid]:
                logger.warning("Audio field %r not found on note %d", audio_field, note_id)
                items.append((note_id, audio_field, jpn_id, AUDIO_FETCH_ERROR, None))
                continue

            expected_fname = f"{jpn_id}.mp3"
            if col.media.have(expected_fname):
                items.append((note_id, audio_field, jpn_id, AUDIO_EXISTS, expected_fname))
                continue

            tmp_path = audio_fetcher.fetch_audio_to_temp(jpn_id)
            if tmp_path is None:
                items.append((note_id, audio_field, jpn_id, AUDIO_NO_RECORDING, None))
            else:
                items.append((note_id, audio_field, jpn_id, AUDIO_FETCHED, tmp_path))
        except AudioDownloadError as exc:
            logger.error("Audio download error for jpn_id %s: %s", jpn_id, exc)
            items.append((note_id, audio_field, jpn_id, AUDIO_FETCH_ERROR, None))
        except Exception:
            logger.exception(
                "Unexpected error downloading audio for note %d (jpn_id %s)",
                note_id, jpn_id,
            )
            items.append((note_id, audio_field, jpn_id, AUDIO_FETCH_ERROR, None))
    return items


def register_pending_audio(items: list, result: BatchResult, col) -> None:
    """Register phase: store fetched files in col.media and write [sound:] tags.

    PRECONDITION: Must be called from the main thread
    (col.media.add_file constraint). Fast — local file copy + DB writes only.

    Consumes the outcome tuples produced by download_pending_audio():
    - AUDIO_FETCHED: registers the temp file, writes [sound:fname] verbatim,
      calls col.update_note, increments result.audio_added.
    - AUDIO_EXISTS: writes the tag without re-registering, increments audio_added.
    - AUDIO_NO_RECORDING: increments result.audio_skipped.
    - AUDIO_FETCH_ERROR: increments result.audio_errors.
    - Any unexpected exception: logs the traceback, increments audio_errors,
      cleans up the temp file, continues with the next item.

    Clears result.pending_audio after processing.
    """
    field_names_cache: dict[int, list[str]] = {}
    for note_id, audio_field, jpn_id, status, payload in items:
        tmp_path = payload if status == AUDIO_FETCHED else None
        try:
            if status == AUDIO_FETCH_ERROR:
                result.audio_errors += 1
                continue
            if status == AUDIO_NO_RECORDING:
                result.audio_skipped += 1
                continue

            note = col.get_note(note_id)
            ntid = note.mid
            if ntid not in field_names_cache:
                field_names_cache[ntid] = [fld["name"] for fld in note.note_type()["flds"]]
            field_names = field_names_cache[ntid]
            if audio_field not in field_names:
                result.audio_errors += 1
                logger.warning("Audio field %r not found on note %d", audio_field, note_id)
                continue

            if status == AUDIO_EXISTS:
                fname = payload
            else:
                # register_audio_file always cleans up the temp file, even on
                # failure — hand off ownership before calling it.
                pending_path, tmp_path = tmp_path, None
                fname = audio_fetcher.register_audio_file(pending_path, col)

            audio_idx = field_names.index(audio_field)
            note.fields[audio_idx] = f"[sound:{fname}]"
            col.update_note(note)
            result.audio_added += 1
        except Exception:
            result.audio_errors += 1
            logger.exception(
                "Unexpected error registering audio for note %d (jpn_id %s)",
                note_id, jpn_id,
            )
        finally:
            if tmp_path is not None:
                try:
                    audio_fetcher.cleanup_temp_audio(tmp_path)
                except Exception:
                    pass
    result.pending_audio.clear()


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


def run_batch(
    col,
    deck_id: int,
    lang_label: str,
    source_field: str,
    dest_field_pairs: list[tuple[str, str, str | None]],
    skip_existing: bool = True,
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
    - lang_label (str): Language label for Tatoeba data (e.g. 'English', 'French').
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

    Returns:
    - A BatchResult dataclass with counters for updated, skipped, and errored notes.
    """
    result = BatchResult()

    if not dest_field_pairs:
        return result

    # Resolve language code and database path
    lang_code = tatoeba_data.LANG_MAP.get(lang_label)
    if not lang_code:
        logger.error(f"Unknown language label: {lang_label}")
        return result

    db_path = tatoeba_data.get_db_path(lang_code)
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return result

    note_ids = col.find_notes(build_deck_search(col, deck_id))
    field_names_cache: dict[int, list[str]] = {}

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
                        result.pending_audio.append((jpn_id, nid, audio_field))

                col.update_note(note)
                result.updated += 1

            except Exception as e:
                logger.error(f"Error processing note {nid}: {e}", exc_info=True)
                result.errors += 1
    finally:
        conn.close()

    return result
