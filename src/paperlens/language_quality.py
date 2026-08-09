from __future__ import annotations

import re

_PERSIAN_SCRIPT = re.compile(r"[\u0600-\u06ff]")
_PERSIAN_SIGNAL = re.compile(
    r"[پچژگکی]|(?:^|\s)(?:است|این|برای|زیرا|اگر|می‌?توان)(?:\s|$)"
)
_UNRELATED_SCRIPTS = re.compile(
    r"[\u0900-\u0d7f\u0e00-\u0e7f\u1100-\u11ff\u2e80-\u9fff"
    r"\ua960-\ua97f\uac00-\ud7af\uf900-\ufaff]"
)
_ARABIC_LETTER = r"\u0621-\u063a\u0641-\u064a\u0671-\u06d3\u06fa-\u06ff"
_SPLICED_LATIN = re.compile(
    rf"(?<=[{_ARABIC_LETTER}])[_A-Za-z]|[_A-Za-z](?=[{_ARABIC_LETTER}])"
)
_LATIN_EXTENDED = re.compile(r"[\u0100-\u024f\u1e00-\u1eff]")
_URDU_ONLY = re.compile(r"[\u0679\u0688\u0691\u06ba\u06be\u06c1\u06d2]")
_FOREIGN_PROSE = re.compile(
    r"\b(?:exact|therefore|however|causa|mejores|analysis)\b", re.IGNORECASE
)


def has_persian_script(text: str) -> bool:
    return bool(_PERSIAN_SCRIPT.search(text))


def is_clean_persian(text: str, *, minimum_characters: int = 8) -> bool:
    """Reject observed cross-script gateway corruption in Persian prose."""
    if len(_PERSIAN_SCRIPT.findall(text)) < minimum_characters:
        return False
    if any(
        pattern.search(text)
        for pattern in (
            _UNRELATED_SCRIPTS,
            _SPLICED_LATIN,
            _LATIN_EXTENDED,
            _URDU_ONLY,
            _FOREIGN_PROSE,
        )
    ):
        return False
    return bool(_PERSIAN_SIGNAL.search(text))
