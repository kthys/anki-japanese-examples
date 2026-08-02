import requests
import logging

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

# NOTE: destination field names are deliberately NOT read here. The config is
# re-read at use time (see GUI.py's add flow), so settings changes apply
# without restarting Anki, and importing this module has no side effects —
# no collection access, no dialogs. Missing-field errors are reported
# contextually at use time via the localized "no_valid_dst_fields" message.

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


_API_URL = "https://tatoeba.org/en/api_v0/search"
# The api_v0 search endpoint returns at most 10 results per request no matter
# what "limit" is sent — "limit" only shifts the page offsets. Fetching more
# than 10 sentences therefore requires walking the "page" parameter.
_PER_PAGE = 10
_MAX_PAGES = 10


def _fetch_sentences(base_params, max_results, exclude_ids=None):
    """
    Fetch up to max_results parsed sentences, walking api_v0 pagination.

    Args:
    - base_params (dict): Query parameters common to every page request.
    - max_results (int): Stop once this many sentences have been collected.
    - exclude_ids (set, optional): jpn_ids to skip (already-collected sentences).

    Returns:
    - A list of sentence dicts (see _parse_api_results). May overshoot
      max_results by up to a page; callers cap after audio-first sorting so
      the cap never drops an audio sentence in favor of a non-audio one.

    Raises requests/JSON errors only if the first page fails; a failure on a
    later page logs the error and returns the sentences gathered so far.
    """
    sentences = []
    page = 1
    while len(sentences) < max_results and page <= _MAX_PAGES:
        params = {**base_params, "limit": _PER_PAGE, "page": page}
        try:
            response = _session.get(_API_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError):
            if page == 1:
                raise
            logger.exception("Tatoeba API request failed on page %d", page)
            break
        parsed = _parse_api_results(data)
        if exclude_ids:
            parsed = [s for s in parsed if s['jpn_id'] not in exclude_ids]
        sentences.extend(parsed)
        paging = (data.get('paging') or {}).get('Sentences') or {}
        if not paging.get('nextPage'):
            break
        page += 1
    return sentences


def find_japanese_sentence(word, translation_language='eng', max_results=50):
    """
    Find Japanese sentences containing a given word using the Tatoeba API.
    Audio-bearing sentences are prioritized first, then non-audio sentences fill
    remaining slots — matching the audio-first logic used in batch mode.

    Args:
    - word (str): The word to search for in Japanese sentences.
    - translation_language (str): The ISO 639-3 language code for the translation language.
      Default is 'eng' for English. Any registry code is valid (e.g. 'eng', 'fra', 'spa', 'cmn', 'kor').
    - max_results (int): The maximum number of results to return. Default is 50.

    Returns:
    - A list of dictionaries containing the Japanese sentence and its translation in the specified language.
    - If no sentences were found, returns a string indicating that no sentences were found.
    - If there is an error connecting to the Tatoeba API, returns an error message.
    """
    base_params = {
        "query": f"={word}",
        "from": "jpn",
        "to": translation_language,
    }

    try:
        audio_sentences = _fetch_sentences(
            {**base_params, "has_audio": "yes"}, max_results)
        audio_sentences.sort(key=lambda s: 0 if s['has_audio'] else 1)
    except (requests.exceptions.RequestException, ValueError):
        logger.exception("Tatoeba API request (audio) failed")
        return _("error_tatoeba_connection")

    if len(audio_sentences) >= max_results:
        return audio_sentences[:max_results]

    seen_ids = {s['jpn_id'] for s in audio_sentences if s['jpn_id']}
    try:
        fill_sentences = _fetch_sentences(
            base_params, max_results - len(audio_sentences), exclude_ids=seen_ids)
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
