import requests
import logging
from aqt import mw

# Use a global session for connection pooling to improve performance
_session = requests.Session()
try:
    from ..utils.i18n import _
except ImportError:
    try:
        from src.utils.i18n import _
    except ImportError:
        _ = lambda x: x

#############################################
#  Logging setup
logger = logging.getLogger(__name__)

#  Fetch config
config = mw.addonManager.getConfig(__name__)

if not isinstance(config, dict) or "japaneseDstField" not in config or "translationDstField" not in config:
    logger.warning("Configuration is missing or invalid.")
    try:
        from aqt.utils import showWarning
        showWarning(
            "Japanese Examples add-on:\n\n"
            "Configuration is missing or invalid.\n"
            "Please check the add-on configuration.\n"
            "Using default fields ('ExampleJapanese' and 'ExampleTranslated')."
        )
    except Exception as e:
        logger.error(f"Could not show warning dialog: {e}")
        
    config = config if isinstance(config, dict) else {}

DST_FIELD_JAP = config.get("japaneseDstField", "ExampleJapanese")
DST_FIELD_TRANSLATION = config.get("translationDstField", "ExampleTranslated")

#############################################

def _parse_api_results(data):
    """Parse Tatoeba API results into sentence dicts."""
    sentences = []
    if not data or 'results' not in data:
        return sentences
    for result in data['results']:
        transcriptions = result.get('transcriptions', [])
        translations = result.get('translations', [])

        jp_sentence = None
        if transcriptions and not transcriptions[0].get('needsReview'):
            jp_sentence = result.get('text')

        tr_sentence = None
        if translations and translations[0]:
            tr_sentence = translations[0][0].get('text')

        if jp_sentence and tr_sentence:
            raw_id = result.get('id')
            sentences.append({
                'jp_sentence': jp_sentence,
                'tr_sentence': tr_sentence,
                'jpn_id': str(raw_id) if raw_id is not None else None,
                'has_audio': bool(result.get('audios')),
            })
    return sentences


def find_japanese_sentence(word, translation_language='eng', max_results=50):
    """
    Find Japanese sentences containing a given word using the Tatoeba API.
    Audio-bearing sentences are prioritized first, then non-audio sentences fill
    remaining slots — matching the audio-first logic used in batch mode.

    Args:
    - word (str): The word to search for in Japanese sentences.
    - translation_language (str): The language code for the translation language. Default is 'eng' for English. Possibilities are 'eng' or 'fra'.
    - max_results (int): The maximum number of results to return. Default is 50.

    Returns:
    - A list of dictionaries containing the Japanese sentence and its translation in the specified language.
    - If no sentences were found, returns a string indicating that no sentences were found.
    - If there is an error connecting to the Tatoeba API, returns an error message.
    """
    url = "https://tatoeba.org/en/api_v0/search"
    base_params = {
        "query": f"={word}",
        "from": "jpn",
        "to": translation_language,
    }

    audio_sentences = []
    try:
        params = {**base_params, "has_audio": "yes", "limit": max_results}
        response = _session.get(url, params=params, timeout=10)
        response.raise_for_status()
        audio_sentences = _parse_api_results(response.json())
        audio_sentences.sort(key=lambda s: 0 if s['has_audio'] else 1)
    except (requests.exceptions.RequestException, ValueError):
        logger.exception("Tatoeba API request (audio) failed")
        return _("error_tatoeba_connection")

    if len(audio_sentences) >= max_results:
        return audio_sentences[:max_results]

    seen_ids = {s['jpn_id'] for s in audio_sentences if s['jpn_id']}
    fill_sentences = []
    try:
        params = {**base_params, "limit": max_results}
        response = _session.get(url, params=params, timeout=10)
        response.raise_for_status()
        all_results = _parse_api_results(response.json())
        fill_sentences = [s for s in all_results if s['jpn_id'] not in seen_ids]
    except (requests.exceptions.RequestException, ValueError):
        logger.exception("Tatoeba API request (fill) failed")
        if audio_sentences:
            return audio_sentences
        return _("error_tatoeba_connection")

    combined = audio_sentences + fill_sentences
    if combined:
        combined.sort(key=lambda s: 0 if s['has_audio'] else 1)
        return combined[:max_results]

    return _("no_japanese_sentence_found").format(word=word)
