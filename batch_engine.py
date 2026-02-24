import re
import html
import random
import logging
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
    jpn_dest_field: str,
    trans_dest_field: str,
    skip_existing: bool = True,
) -> BatchResult:
    """
    Run batch processing on all notes of a selected deck.

    Iterates over every note in the given deck, reads a Japanese word from the
    source field, looks it up in the local SQLite index, and writes a randomly
    selected matching sentence + translation (HTML-escaped) into the destination
    fields.

    Args:
    - col: The Anki collection object (``mw.col``).
    - deck_id (int): The ID of the deck to process.
    - lang_label (str): Language label for Tatoeba data (e.g. 'English', 'French').
    - source_field (str): Name of the note field containing the word to search.
    - jpn_dest_field (str): Name of the note field to write the Japanese example sentence.
    - trans_dest_field (str): Name of the note field to write the translated sentence.
    - skip_existing (bool): If True, skip notes that already have content in the
      destination fields. Default is True.

    Returns:
    - A BatchResult dataclass with counters for updated, skipped, and errored notes.
    """
    result = BatchResult()

    # Resolve language code and database path
    lang_code = tatoeba_data.LANG_MAP.get(lang_label)
    if not lang_code:
        logger.error(f"Unknown language label: {lang_label}")
        return result

    db_path = tatoeba_data.get_db_path(lang_code)

    # Get all note IDs in the deck
    note_ids = col.find_notes(f"did:{deck_id}")

    for nid in note_ids:
        try:
            note = col.get_note(nid)
            field_names = [fld["name"] for fld in note.note_type()["flds"]]

            # Check that required fields exist on this note type
            if source_field not in field_names:
                result.skipped_missing_fields += 1
                continue
            if jpn_dest_field not in field_names or trans_dest_field not in field_names:
                result.skipped_missing_fields += 1
                continue

            # Read source word
            source_idx = field_names.index(source_field)
            raw_word = note.fields[source_idx].strip()
            word = clean_word(raw_word)
            if not word:
                result.skipped_missing_fields += 1
                continue

            # Check skip_existing
            jpn_idx = field_names.index(jpn_dest_field)
            trans_idx = field_names.index(trans_dest_field)

            if skip_existing:
                if note.fields[jpn_idx].strip() and note.fields[trans_idx].strip():
                    result.skipped_existing += 1
                    continue

            # Search for matches
            matches = tatoeba_data.search_word(db_path, word)
            if not matches:
                result.skipped_no_match += 1
                continue

            # Select a random match
            jpn_text, trans_text = random.choice(matches)

            # Write HTML-escaped results
            note.fields[jpn_idx] = html.escape(jpn_text)
            note.fields[trans_idx] = html.escape(trans_text)
            col.update_note(note)
            result.updated += 1

        except Exception as e:
            logger.error(f"Error processing note {nid}: {e}", exc_info=True)
            result.errors += 1

    return result
