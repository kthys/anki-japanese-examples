import requests
from aqt import mw

#############################################
#  Fetch config
config = mw.addonManager.getConfig(__name__)

#SOURCE_FIELDS = config["srcField"]
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
    url = f"https://tatoeba.org/en/api_v0/search?query=%3D{word}&from=jpn&to={translation_language}"
    # Send a GET request to the Tatoeba API.
    response = requests.get(url)

    # Check if the response status code is 200 (OK).
    if response.status_code == 200:
        # Parse the response JSON data.
        data = response.json()
        # Check if any results were returned.
        if data:
            # Initialize an empty list to store the sentences.
            sentences = []
            # Loop through each result and extract the Japanese sentence text.
            for result in data['results']:
                # Check if the sentence needs review before adding it to the list.
                try:
                    jp_sentence = result['text'] if not result['transcriptions'][0]['needsReview'] else None
                    tr_sentence = result['translations'][0][0]['text']
                except IndexError:
                    jp_sentence = None
                    tr_sentence = None
                
                if jp_sentence and tr_sentence:
                    sentences.append({'jp_sentence': jp_sentence, 'tr_sentence': tr_sentence})

            # Check if any sentences were found.
            if sentences:
                return sentences
            else:
                return f"No Japanese sentence found containing the word '{word}'."
        else:
            return f"No Japanese sentence found containing the word '{word}'."
    else:
        return "Error: Unable to connect to Tatoeba API."
