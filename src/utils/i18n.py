import os
import json
import re
from aqt import mw

# Untrusted lang codes: 2-3 letters, optional -/_ separated region (zh_CN, pt-BR)
_LANG_CODE_RE = re.compile(r'[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?')

def _base_lang(lang_code):
    """Reduce an untrusted lang code to its base form ('zh_CN' -> 'zh').

    Returns None for malformed input (traversal strings, path separators,
    absolute paths, non-strings) so it can never reach a filesystem path.
    """
    if not isinstance(lang_code, str):
        return None
    lang = lang_code.strip()
    if not _LANG_CODE_RE.fullmatch(lang):
        return None
    return lang.replace('-', '_').split('_')[0].lower()

def get_current_language():
    language = mw.pm.meta.get('defaultLang', 'en')
    return language

class SimpleTranslator:
    def __init__(self, lang_code):
        self.lang_code = lang_code
        self.translations = {}
        self.load_translations()

    def load_translations(self):
        locale_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'locale')

        # Always load English first as base
        en_path = os.path.join(locale_dir, 'en.json')
        if os.path.exists(en_path):
            with open(en_path, 'r', encoding='utf-8') as f:
                self.translations.update(json.load(f))

        # Determine target language file: validate the untrusted lang code and
        # only allow files that actually ship in locale/
        base_lang = _base_lang(self.lang_code)
        if base_lang and base_lang != 'en':
            try:
                available = {f[:-5] for f in os.listdir(locale_dir) if f.endswith('.json')}
            except OSError:
                available = set()
            if base_lang in available:
                target_path = os.path.join(locale_dir, f"{base_lang}.json")
                with open(target_path, 'r', encoding='utf-8') as f:
                    self.translations.update(json.load(f))

    def gettext(self, message):
        return self.translations.get(message, message)

# Initialize translation
current_lang = get_current_language()
translator = SimpleTranslator(current_lang)
_ = translator.gettext
