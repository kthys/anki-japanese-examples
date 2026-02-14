import os
import json
from aqt import mw

def get_current_language():
    language = mw.pm.meta.get('defaultLang', 'en')
    return language

class SimpleTranslator:
    def __init__(self, lang_code):
        self.lang_code = lang_code
        self.translations = {}
        self.load_translations()

    def load_translations(self):
        locale_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')

        # Always load English first as base
        en_path = os.path.join(locale_dir, 'en.json')
        if os.path.exists(en_path):
            with open(en_path, 'r', encoding='utf-8') as f:
                self.translations.update(json.load(f))

        # Determine target language file
        target_lang = self.lang_code
        if '_' in target_lang:
            target_lang = target_lang.split('_')[0]

        if target_lang != 'en':
            target_path = os.path.join(locale_dir, f"{target_lang}.json")
            if os.path.exists(target_path):
                with open(target_path, 'r', encoding='utf-8') as f:
                    self.translations.update(json.load(f))

    def gettext(self, message):
        return self.translations.get(message, message)

# Initialize translation
current_lang = get_current_language()
translator = SimpleTranslator(current_lang)
_ = translator.gettext
