"""Lenient, case-insensitive word matching for local name searches."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKD', text.casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"['\u2018\u2019]", '', text)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def matches(query: str, text: str) -> bool:
    needle, haystack = normalize(query), normalize(text)
    if not needle:
        return False
    if needle in haystack or needle.replace(' ', '') in haystack.replace(' ', ''):
        return True
    words = haystack.split()
    return all(any(token in word or (len(token) >= 4 and
                   SequenceMatcher(None, token, word).ratio() >= .75)
                   for word in words) for token in needle.split())
