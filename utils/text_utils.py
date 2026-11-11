"""Phrase helpers shared by title building and comp queries."""

import re
import unicodedata
from typing import Any, Iterable, List

STARS = re.compile(r"\s*([★☆✦✧✩✪✫✬✭✮✯✰⭐]+)")
MARKS = re.compile(r"[™®©℠]")
ASCII_PUNCTUATION = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
                     "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "⁄": "/",
                     "·": " ", "•": " ", "×": "x"}


def ebay_safe(text: Any) -> str:
    """Text eBay accepts in a title or item specific.

    eBay rejects the whole listing (25019, "LP_SNB_CutsieCharacters") over symbols such
    as ★, ™, ♥ or ½. Union Arena prints its rarity with stars, so "SR★★" becomes
    "SR 2 Star" - the way sellers spell it - rather than losing the stars' meaning.
    Accented letters (Pokémon) are fine and kept.
    """
    value = STARS.sub(lambda m: " {} Star".format(len(m.group(1))), str(text or ""))
    value = MARKS.sub("", value)
    # Fullwidth letters, superscripts and fractions fold to plain ones (², ½ -> 2, 1/2).
    value = unicodedata.normalize("NFKC", value)
    out = []
    for char in value:
        char = ASCII_PUNCTUATION.get(char, char)
        if ord(char) < 128 or len(char) > 1 or unicodedata.category(char)[0] in "LN":
            out.append(char)
        else:
            out.append(" ")     # symbols, emoji, arrows, dingbats
    return " ".join("".join(out).split())


def _tokens(value: Any) -> List[str]:
    """Lowercase word/number tokens, ignoring punctuation and case."""
    return [token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token]


def _contains_sequence(haystack: List[str], needle: List[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[start:start + len(needle)] == needle
        for start in range(len(haystack) - len(needle) + 1)
    )


def dedupe_phrases(parts: Iterable[Any]) -> List[str]:
    """Drop blank, repeated, and redundant phrases, keeping the most specific wording.

    Card data repeats itself constantly: brand "Topps" arrives alongside set
    "Topps Chrome", and year "2025" alongside "2025-26 Topps Chrome Updates". Comparing
    whole strings misses those, which is how titles ended up reading "Topps Topps Chrome".

    A phrase is dropped when its tokens appear as a run inside a longer phrase, so
    "Topps" gives way to "Topps Chrome" while "Silver Prizm" and "Panini Prizm" both
    survive. Matching on tokens rather than substrings keeps year 2012 from swallowing
    card number 12. Order is preserved.
    """
    cleaned = []
    for part in parts:
        text = " ".join(str(part or "").split())
        if text:
            cleaned.append(text)

    # Collapse repeats that differ only by case or punctuation, keeping the first.
    unique: List[str] = []
    token_lists: List[List[str]] = []
    seen = set()

    for text in cleaned:
        tokens = _tokens(text)
        if not tokens:
            continue
        key = " ".join(tokens)
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
        token_lists.append(tokens)

    # Drop phrases wholly contained in a strictly longer one. The length rule stops two
    # equal-token phrases from eliminating each other.
    result = []
    for index, text in enumerate(unique):
        tokens = token_lists[index]
        redundant = any(
            len(other) > len(tokens) and _contains_sequence(other, tokens)
            for position, other in enumerate(token_lists)
            if position != index
        )
        if not redundant:
            result.append(text)

    return result


# Seller-speak that says nothing about which card this is. Multi-word phrases are
# removed before single words so "read desc" is handled as a unit.
COMP_NOISE_PHRASES = [
    "read description", "read desc", "please read", "read first", "read below",
    "see description", "see desc", "see photos", "see photo", "see pics",
    "see pictures", "see scans", "see scan", "check photos", "check pics",
    "free shipping", "fast shipping", "ships fast", "ship fast", "combined shipping",
    "smoke free", "pet free", "must see", "buy it now", "great deal", "no reserve",
    "look", "wow", "nice card", "sharp", "invest",
]

# Also removed from the search, but these change what a card is worth, so callers are
# told about them rather than the removal happening silently.
DAMAGE_TERMS = [
    "poor condition", "played condition", "off center", "off-center", "as is",
    "damaged", "damage", "creased", "crease", "bent", "dinged", "ding",
    "scratched", "scratch", "miscut", "flawed", "wear",
]

# Hyphens and slashes are kept: they carry "2025-26", "LL-7" and print runs like /50.
_DROPPED_PUNCTUATION = set(chr(c) for c in [44, 33, 63, 42, 124, 59, 58, 40, 41, 91, 93, 34, 39])


def _normalise(text: str) -> str:
    """Lowercase, ASCII-only, punctuation-free, single-spaced, padded for phrase matching."""
    out = []
    for char in str(text or "").lower():
        if ord(char) > 127 or char in _DROPPED_PUNCTUATION:
            out.append(" ")
        else:
            out.append(char)

    flattened = "".join(out).replace("l@@k", " ").replace("l@k", " ")
    return " " + " ".join(flattened.split()) + " "


def damage_terms(text: str) -> List[str]:
    """Damage wording found in a title."""
    normalised = _normalise(text)
    return [term for term in DAMAGE_TERMS if " " + term + " " in normalised]


def clean_comp_query(text: str) -> str:
    """Strip seller-speak from a title so it can be used as a comp search.

    A title reading "... #12 READ DESC" is a card plus a note to the buyer. Left in, that
    note becomes a required search word - it appears in almost no completed sale - so the
    card comes back with no comps at all. Damage wording is stripped for the same reason
    and surfaced separately, so the price can be discounted deliberately instead of the
    card silently being compared against undamaged copies.
    """
    cleaned = _normalise(text)

    for phrase in COMP_NOISE_PHRASES + DAMAGE_TERMS:
        padded = " " + phrase + " "
        while padded in cleaned:
            cleaned = cleaned.replace(padded, " ")

    words = [word for word in cleaned.split() if word not in ("-", "/", "--")]
    return " ".join(words).strip(" -/")
