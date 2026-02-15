import requests
import logging
from aqt import mw
try:
    from .i18n import _
except ImportError:
    try:
        from i18n import _
    except ImportError:
        _ = lambda x: x

#############################################
#  Logging setup
logger = logging.getLogger(__name__)

#  Fetch config
config = mw.addonManager.getConfig(__name__)

DST_FIELD_JAP = config["japaneseDstField"]
DST_FIELD_TRANSLATION = config["translationDstField"]

#############################################

def find_japanese_sentence(word, translation_language='eng'):
    """
    Find Japanese sentences containing a given word using the Tatoeba API.

    Args:
    - word (str): The word to search for in Japanese sentences.
    - translation_language (str): The language code for the translation language. Default is 'eng' for English. Possibilities are 'eng' or 'fra'.

    Returns:
    - A list of dictionaries containing the Japanese sentence and its translation in the specified language.
    - If no sentences are found, returns a string indicating that no sentences were found.
    - If there is an error connecting to the Tatoeba API, returns an error message.
    """
    # Construct the URL for the Tatoeba API search.
    url = "https://tatoeba.org/en/api_v0/search"
    params = {
        "query": f"={word}",
        "from": "jpn",
        "to": translation_language
    }
    # Send a GET request to the Tatoeba API.
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        # Parse the response JSON data.
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        logger.exception("Tatoeba API request failed")
        return _("error_tatoeba_connection")

    # Check if any results were returned.
    if data and 'results' in data:
        # Initialize an empty list to store the sentences.
        sentences = []
        # Loop through each result and extract the Japanese sentence text.
        for result in data['results']:
            # Check if the sentence needs review before adding it to the list.
            # Use explicit presence checks for better performance and reliability
            transcriptions = result.get('transcriptions', [])
            translations = result.get('translations', [])

            jp_sentence = None
            if transcriptions and not transcriptions[0].get('needsReview'):
                jp_sentence = result.get('text')

            tr_sentence = None
            if translations and translations[0]:
                tr_sentence = translations[0][0].get('text')

            if jp_sentence and tr_sentence:
                sentences.append({'jp_sentence': jp_sentence, 'tr_sentence': tr_sentence})

        # Check if any sentences were found.
        if sentences:
            return sentences

    return _("no_japanese_sentence_found").format(word=word)
