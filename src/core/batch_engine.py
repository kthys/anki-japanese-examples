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

logger = logging.getLogger(__name__)


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
    - audio_added (int): Populated by on_success after main-thread download; sentences with audio
      successfully registered.
    - audio_skipped (int): Populated by on_success; sentences where Tatoeba returned 404
      (no recording).
    - audio_errors (int): Populated by on_success; sentences where download raised
      AudioDownloadError.
    - pending_audio (list[tuple[str, int, str]]): Staging list of (jpn_id, note_id, audio_field)
      triples accumulated by run_batch() and drained by on_success on the main thread.
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


def process_pending_audio(result: BatchResult, col) -> None:
    """Drain result.pending_audio on the main thread.

    PRECONDITION: Must be called from the main thread.
    Called by batch_ui.py's on_success handler after run_batch() completes.

    For each (jpn_id, note_id, audio_field) triple in result.pending_audio:
    - Calls audio_fetcher.download_audio(jpn_id, col)
    - On success: writes [sound:fname] verbatim to the audio field, calls col.update_note,
      increments result.audio_added
    - On None (404): leaves audio field empty, increments result.audio_skipped
    - On AudioDownloadError: leaves audio field empty, logs error, increments result.audio_errors
    - On missing audio field: increments result.audio_errors, logs warning

    Clears result.pending_audio after processing.
    """
    if audio_fetcher is None:
        logger.error("audio_fetcher module not available — skipping audio processing")
        return

    for jpn_id, note_id, audio_field in result.pending_audio:
        try:
            note = col.get_note(note_id)
            field_names = [fld["name"] for fld in note.note_type()["flds"]]
            if audio_field not in field_names:
                result.audio_errors += 1
                logger.warning("Audio field %r not found on note %d", audio_field, note_id)
                continue
            fname = audio_fetcher.download_audio(jpn_id, col)
            if fname is None:
                result.audio_skipped += 1
            else:
                audio_idx = field_names.index(audio_field)
                note.fields[audio_idx] = f"[sound:{fname}]"
                col.update_note(note)
                result.audio_added += 1
        except AudioDownloadError as exc:
            result.audio_errors += 1
            logger.error("Audio download error for jpn_id %s: %s", jpn_id, exc)
    result.pending_audio.clear()


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
    - deck_id (int): The ID of the deck to process.
    - lang_label (str): Language label for Tatoeba data (e.g. 'English', 'French').
    - source_field (str): Name of the note field containing the word to search.
    - dest_field_pairs (list[tuple[str, str, str | None]]): List of triples of (Japanese,
      Translation, Audio) destination field names. The audio element is the destination field name
      for the [sound:] tag, or None if no audio is configured for this pair.
    - skip_existing (bool): If True, skip notes that already have content in the
      FIRST destination field pair. Default is True.

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

    # Get all note IDs in the deck
    note_ids = col.find_notes(f"did:{deck_id}")

    db_path = tatoeba_data.get_db_path(lang_code)
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    try:
        for nid in note_ids:
            try:
                note = col.get_note(nid)
                field_names = [fld["name"] for fld in note.note_type()["flds"]]

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

                # Check skip_existing based on the FIRST field pair
                first_jpn_dest, first_trans_dest, _ = dest_field_pairs[0]
                first_jpn_idx = field_names.index(first_jpn_dest)
                first_trans_idx = field_names.index(first_trans_dest)

                if skip_existing:
                    if note.fields[first_jpn_idx].strip() and note.fields[first_trans_idx].strip():
                        result.skipped_existing += 1
                        continue

                # Search for matches
                matches = tatoeba_data.search_word(db_path, word, conn=conn)
                if not matches:
                    result.skipped_no_match += 1
                    continue

                # Select unique random matches up to the number of pairs requested or available
                num_to_select = min(len(dest_field_pairs), len(matches))
                selected_matches = random.sample(matches, num_to_select)

                # Write HTML-escaped results
                for i, (jpn_id, jpn_text, trans_text) in enumerate(selected_matches):
                    jpn_field, trans_field, audio_field = dest_field_pairs[i]
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
