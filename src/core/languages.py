"""
Data-driven language registry.

Single source of truth for the target translation languages supported by the
add-on. The manual picker (``src/ui/GUI.py``), the batch dialog
(``src/ui/batch_ui.py``), and the engine (``src/core/tatoeba_data.py``,
``src/core/batch_engine.py``) all read from here — adding a language requires
one registry entry plus a ``language_name_*`` key in each locale file, with
no UI code edits.

The registry is keyed by ISO 639-3 language code, the canonical identifier
used by Tatoeba exports, the search API, per-deck preferences, and the local
data files (``jpn_{code}_pairs.tsv``, ``jpn_{code}_index.db``,
``metadata.json``).
"""

try:
    from ..utils.i18n import _
except ImportError:
    try:
        from src.utils.i18n import _
    except Exception:
        _ = lambda x: x

# ISO 639-3 code -> display info.
#   label:    English fallback display name (used when a locale key is missing)
#   name_key: locale key holding the localized display name in each locale file
LANGUAGES = {
    "eng": {"label": "English", "name_key": "language_name_eng"},
    "fra": {"label": "French", "name_key": "language_name_fra"},
    "spa": {"label": "Spanish", "name_key": "language_name_spa"},
    "cmn": {"label": "Chinese (Simplified)", "name_key": "language_name_cmn"},
    "kor": {"label": "Korean", "name_key": "language_name_kor"},
}

# Display order in the picker/dialogs. English first: index 0 = English keeps
# the pre-existing default behavior and index-based test mocks stable.
_ORDER = ["eng", "fra", "spa", "cmn", "kor"]


def get_codes() -> list:
    """Return the supported ISO 639-3 codes in display order."""
    return list(_ORDER)


def is_supported(code: str) -> bool:
    """Return True if code is a supported ISO 639-3 language code."""
    return code in LANGUAGES


def get_label(code: str) -> str:
    """Return the English display label for a language code."""
    info = LANGUAGES.get(code)
    return info["label"] if info else code


def get_localized_name(code: str) -> str:
    """
    Return the localized display name for a language code.

    Resolves the language's ``name_key`` through the active UI language and
    falls back to the English label when the key is missing, so a partially
    translated locale never shows a raw key.
    """
    info = LANGUAGES.get(code)
    if not info:
        return code
    translated = _(info["name_key"])
    if translated != info["name_key"]:
        return translated
    return info["label"]


def code_from_label(label: str):
    """
    Return the ISO 639-3 code for a display label, or None.

    Legacy resolution shim: older callers passed the English label (e.g.
    'English'); current code passes ISO codes everywhere.
    """
    for code, info in LANGUAGES.items():
        if info["label"] == label:
            return code
    return None
