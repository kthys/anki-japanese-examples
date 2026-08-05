# Japanese Examples

Japanese Examples is a plugin for the Anki flashcard software that adds example sentences and their translations to your Japanese flashcards, sourcing them directly from the Tatoeba project.

## Overview

### Single-Card Addition
![Usage example](https://github.com/kthys/anki-japanese-examples/assets/32138080/7f7e758b-48da-4c33-916a-8614f46c7c68)

### Batch Addition
![Batch Addition example](https://github.com/user-attachments/assets/1c8e4e22-d9af-4919-8242-25c206198591)

## Features

- **Interactive Single-Card Addition**: Find and select the perfect example sentence while editing a single flashcard. Sentences with a native-speaker recording are marked with 🔊 in the picker.
- **High-Performance Batch Addition**: Automatically add example sentences to an entire deck of cards at once. The plugin downloads and indexes Tatoeba's database locally using SQLite, enabling incredibly fast bulk processing.
- **Native-Speaker Audio**: Example sentences can come with their native-speaker recordings from Tatoeba, in both manual and batch mode. Sentences with audio are preferred, and recordings are saved into a field of your choice. Downloads run in the background with a progress counter, so Anki stays responsive.
- **Multiple Field Pairs**: Batch mode can fill up to 3 example/translation field pairs per card, and decks mixing several note types offer all their fields in the dialog.
- **Deck and Subdeck Selection**: Process a whole deck tree or a single subdeck, with a confirmation before processing an entire tree.
- **Undoable Batch Runs**: Press Ctrl+Z to revert added examples and audio after a batch run, and get a report of the results, including any audio errors.
- **Always Up-to-Date**: For single additions, the plugin connects directly to the Tatoeba API to ensure you have the latest available examples.
- **Multilingual UI**: The plugin interface and menus are automatically translated into English, French, Spanish, Chinese (simplified), or Korean based on your Anki language settings.
- **Five Translation Languages**: Get example sentence translations in English, French, Spanish, Chinese (simplified), or Korean — choose a language in the editor dialog, set a per-deck default, or pick the language in the batch dialog.

## Installation

### **Recommended:** Install via AnkiWeb to receive automatic updates.
[View the add-on on AnkiWeb](https://ankiweb.net/shared/info/1204068184) or use the code `1204068184` in Anki (**Tools > Add-ons > Get Add-ons...**).

### Manual Installation

1. Download the latest version of the plugin from the [releases page](https://github.com/kthys/anki-japanese-examples/releases).
2. Open Anki and go to **Tools > Add-ons > Install Add-on from File**.
3. Select the downloaded file and click **Open**.
4. Restart Anki to complete the installation.

## Usage

### Initial Setup

1. Open the plugin's configuration in the addon manager.
2. Define the names of the fields used for your Japanese examples, translated examples, and audio recordings.
3. Restart Anki to apply these settings.

### Adding Examples Manually

1. Create a new card or open an existing one in the editor.
2. Click on the "Add an example sentence manually" button among the editor's formatting buttons.
3. Follow the instructions to choose your preferred sentence and insert it into the card.

### Batch Processing

1. In the main Anki window, go to **Tools > Batch process examples**.
2. In the "**Data Management**" section, choose your target language — English, French, Spanish, Chinese (simplified), or Korean — and click **Download data**. Wait for the progress to complete.
3. In the "**Execution**" section, select your target Deck — you can process a whole deck tree (including its subdecks) or narrow it down to a single subdeck.
4. Map the Source field (containing your lookup word), then the Japanese example, Translated example, and Audio fields. You can add up to 3 example/translation field pairs to fill several examples per card.
5. Check "Skip cards with existing examples" if you only want to process empty cards.
6. Click **Run batch process** and let the engine populate your deck! A report shows the results when it finishes, including audio downloads, and you can press **Ctrl+Z** to undo the whole run.

## FAQ / Troubleshooting

### "No example found" when I search for a word — why?

Sentence matching is approximate: the plugin tokenizes Japanese with a regex-based tokenizer (no morphological analyzer), so conjugated or unusual word forms may not match. Try a simpler form of the word (e.g. the dictionary form 食べる instead of 食べた). For single additions, results come live from Tatoeba — a rare word may genuinely have no sentence with a translation in your target language.

### Does the add-on need internet access?

Yes, for two things: single-card additions query the Tatoeba API live, and batch mode must download the Tatoeba datasets before its first run. If you suspect connectivity problems, open the add-on settings and use the **Test Connection** button to check that the Tatoeba API is reachable.

### How much disk space do the downloaded datasets take, and where are they stored?

Batch mode downloads per-language Tatoeba sentence exports and builds a local SQLite index. Everything is stored in the `user_files/` folder inside the add-on directory (roughly a few hundred MB per language). You can delete a language's files at any time to free space — the batch dialog will simply ask you to re-download them.

### Why is audio missing for some sentences?

Audio is best-effort: only a subset of Tatoeba sentences have native-speaker recordings. In both modes the plugin prefers sentences that have recordings, but if the chosen sentence has none (or Tatoeba returns "no recording"), the audio field is simply left empty. This is expected, not an error.

### Can I undo a batch run?

Yes. Each batch run is wrapped in a single undo entry — press **Ctrl+Z** right after the run to revert the added examples and audio. Audio file downloads are not covered by undo, but the `[sound:]` tags in your cards are.

### My Anki interface is in a language the add-on doesn't support yet — what happens?

The add-on follows your Anki UI language. English, French, Spanish, Chinese (simplified), and Korean are fully translated; any other language falls back to English.

### The editor button or the settings don't work as expected

The editor button lives in the card editor's formatting bar. If it is missing, check that the add-on is enabled in **Tools > Add-ons**. For "field not found" errors, open the add-on settings (**Tools > Add-ons > select "Japanese example sentences" > Config**) and make sure the configured field names match your note type exactly. Use **Test Connection** in the same dialog to verify Tatoeba is reachable.

### Why were some cards skipped during batch processing?

Cards are skipped when: they already have examples in every destination pair and "Skip cards with existing examples" is checked; no matching sentence was found in the local index; or the note type lacks the configured source or destination fields. The final report shows the exact count for each skip reason.

## License

Japanese Examples is licensed under the GPLv3 License.

## Contributing

If you would like to contribute to Japanese Examples, please follow these steps:

1. Fork the repository.
2. Create a new branch: `git checkout -b my-new-feature`.
3. Make your changes and commit them: `git commit -am 'Add some feature'`.
4. Push to the branch: `git push origin my-new-feature`.
5. Submit a pull request.
