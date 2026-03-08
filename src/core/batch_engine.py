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
    """
    updated: int = 0
    skipped_existing: int = 0
    skipped_no_match: int = 0
    skipped_missing_fields: int = 0
    errors: int = 0

    @property
    def total_processed(self) -> int:
        """Return the total number of notes that were processed."""
        return (self.updated + self.skipped_existing + self.skipped_no_match
                + self.skipped_missing_fields + self.errors)


def run_batch(
    col,
    deck_id: int,
    lang_label: str,
    source_field: str,
    dest_field_pairs: list[tuple[str, str]],
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
    - dest_field_pairs (list[tuple[str, str]]): List of tuples containing the (Japanese, Translation)
      destination field names.
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
                for jpn_dest, trans_dest in dest_field_pairs:
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
                first_jpn_dest, first_trans_dest = dest_field_pairs[0]
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
                for i, (jpn_text, trans_text) in enumerate(selected_matches):
                    jpn_idx = field_names.index(dest_field_pairs[i][0])
                    trans_idx = field_names.index(dest_field_pairs[i][1])
                    
                    note.fields[jpn_idx] = html.escape(jpn_text)
                    note.fields[trans_idx] = html.escape(trans_text)
                    
                col.update_note(note)
                result.updated += 1

            except Exception as e:
                logger.error(f"Error processing note {nid}: {e}", exc_info=True)
                result.errors += 1
    finally:
        conn.close()

    return result
