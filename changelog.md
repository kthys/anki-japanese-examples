# Japanese Examples - Changelog

## v1.5.0 Update
Example sentences are no longer limited to English and French! The add-on now supports Spanish, Chinese (simplified), and Korean — in the manual picker and in batch mode — and the whole interface is translated into those three languages.

- **New Feature:** Spanish, Chinese (simplified), and Korean translations — pick them in the editor dialog or as a per-deck default, and download their batch data like any other language.
- **New Feature:** The add-on interface is now fully translated into Spanish, Chinese (simplified), and Korean (it follows your Anki UI language automatically).
- **New Feature:** "Test Connection" button in the settings — verify that the Tatoeba API is reachable from your machine.
- **Improvement:** Supported languages are now defined in a single registry, so adding more languages is much easier in the future.
- **Housekeeping:** Removed the deprecated in-memory `load_index` code path (superseded by the SQLite index since v1.3.0).

## v1.4.0 Update
Audio is here! Example sentences can now come with their native-speaker recordings from Tatoeba, in both manual and batch mode.

- **New Feature:** Audio support — sentences with recordings are preferred, and the audio is saved into a field of your choice (🔊 marks them in the manual picker).
- **New Feature:** Batch mode can now fill up to 3 example/translation field pairs per card.
- **New Feature:** New deck and subdeck selectors — pick a whole deck tree or a single subdeck, with a confirmation before processing an entire tree.
- **New Feature:** Batch runs are now undoable — press Ctrl+Z to revert added examples and audio.
- **Improvement:** Audio downloads run in the background with a progress counter, so Anki no longer freezes.
- **Improvement:** Faster data index build after downloads, and re-running a batch on an already-processed deck is now nearly instant.
- **Improvement:** Decks mixing several note types now offer all their fields in the batch dialog.
- **Improvement:** The batch report now shows audio results, including errors.
- **Fix:** Batch processing now works on non-English Anki interfaces (was completely broken in French).
- **Fix:** One failed audio download no longer aborts the remaining downloads.
- **Fix:** Cards in subdecks are now included when processing a parent deck.
- **Fix:** Removed a confusing configuration warning that could appear during Anki startup.

**Thank you for using the Japanese Examples add-on!**

## v1.3.3 Update
- **Improvement:** Optimized batch processing performance with faster database lookups.
- **Fix:** Corrected various typos in the UI and French locales.

**Thank you for using the Japanese Examples add-on!**

## v1.3.0 Update
Batch processing is here ! You can now go in the "Tools" on the menu at the top of the main window and select "Batch process examples" to add examples to multiple cards at once.

- **New Feature:** Added a batch processing engine and UI for automatically adding examples from Tatoeba.
- **New Feature:** Implemented a new background data download dialog with live progress updates.
- **Fix:** Fixed pronunciation matching for words containing brackets inside them.
- **Improvement:** Updated UI strings and translation examples for better clarity.


**Thank you for using the Japanese Examples add-on!**

## v1.2.0 Update
- **New Feature:** Implemented an automatic changelog popup on plugin update. This will notify you of any new features or bug fixes.
- **Improvement:** Better connection handling for retrieving example sentences from Tatoeba.
- **Fix:** Minor UI adjustments and bug fixes under the hood.

**Thank you for using the Japanese Examples add-on!**
