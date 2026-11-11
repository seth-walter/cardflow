import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from services.ebay_browse import ebay_browse_search
from utils.file_utils import clean_value

IGNORE_VALUES = {"", "unknown", "none", "n/a", "na", "base", "[base]"}

SPORTS_MARKERS = {
    "nba", "basketball", "nfl", "football", "mlb", "baseball", "nhl", "hockey",
    "soccer", "ufc", "wwe", "wrestling", "nascar", "racing",
}

KNOWN_BRANDS = [
    "Topps Chrome", "Topps Finest", "Topps Now", "Topps Pristine", "Topps Heritage",
    "Topps Stadium Club", "Topps Allen & Ginter", "Topps", "Bowman Chrome", "Bowman",
    "Panini Prizm", "Donruss Optic", "Donruss", "Select", "Mosaic", "Phoenix",
    "Absolute", "Score", "Contenders", "Chronicles", "National Treasures", "Immaculate",
    "Flawless", "Obsidian", "Certified", "Origins", "Hoops", "Recon", "Revolution",
    "Upper Deck", "O-Pee-Chee", "Fleer", "Skybox", "Leaf", "Sage", "Wild Card",
]

SAFE_VARIANTS = [
    "Red White Blue", "Black Gold", "Orange Ice", "Green Ice", "Blue Ice", "Purple Ice",
    "X-Fractor", "Xfractor", "Refractor", "Prizm", "Silver", "Holo", "Optic Holo",
    "Checkerboard", "Pulsar", "Mojo", "Wave", "Laser", "Disco", "Shimmer",
    "Cracked Ice", "Negative", "Sepia", "Aqua", "Pink", "Purple", "Blue", "Green",
    "Red", "Orange", "Gold", "Black", "White", "Genesis", "Downtown", "Kaboom",
    "Color Blast", "Stained Glass", "Rookie Variation", "Variation", "Auto", "Autograph",
]

FALSE_VARIANT_PHRASES = {
    "rookie", "rc", "base", "card", "chrome", "topps", "panini", "bowman", "donruss",
}

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)(?:[-/](\d{2}))?\b")


def _norm(value: Any) -> str:
    text = clean_value(value).strip()
    if text.lower() in IGNORE_VALUES:
        return ""
    return text


def is_sports_card(card_data: Dict[str, Any]) -> bool:
    category = _norm(card_data.get("card_category")).lower()
    if category == "sports":
        return True

    combined = " ".join(
        _norm(card_data.get(k)).lower()
        for k in ["sport", "league", "team", "brand", "manufacturer", "set", "notes"]
    )
    return any(marker in combined for marker in SPORTS_MARKERS)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _player_tokens(name: str) -> List[str]:
    return [t for t in re.sub(r"[^a-zA-Z0-9 ]", " ", name).lower().split() if len(t) > 1]


def _title_matches_player(title: str, player_name: str) -> bool:
    tokens = _player_tokens(player_name)
    if not tokens:
        return False
    title_lower = title.lower()
    # Require all meaningful name tokens when possible. This avoids matching unrelated card #s.
    if len(tokens) >= 2:
        return all(t in title_lower for t in tokens[:2])
    return tokens[0] in title_lower


def _title_matches_card_number(title: str, number: str) -> bool:
    number = _norm(number)
    if not number:
        return True
    candidates = {number, f"#{number}"}
    if number.startswith("#"):
        candidates.add(number[1:])
    title_lower = title.lower()
    return any(c.lower() in title_lower for c in candidates)


def _candidate_items(card_data: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
    player = _norm(card_data.get("player_athlete") or card_data.get("name") or card_data.get("card_name"))
    number = _norm(card_data.get("card_number") or card_data.get("number"))
    brand = _norm(card_data.get("brand") or card_data.get("manufacturer"))
    card_set = _norm(card_data.get("set") or card_data.get("card_set"))
    team = _norm(card_data.get("team"))

    queries = []
    if player and number:
        queries.append(f'"{player}" {number} {brand} {card_set}'.strip())
        queries.append(f'"{player}" #{number} sports card'.strip())
    if player and card_set:
        queries.append(f'"{player}" {card_set} sports card'.strip())
    if player and team:
        queries.append(f'"{player}" {team} sports card'.strip())

    seen = set()
    items: List[Dict[str, Any]] = []

    for query in queries:
        query = " ".join(query.split())
        if not query:
            continue
        try:
            for item in ebay_browse_search(query, limit=limit):
                item_id = item.get("itemId") or item.get("legacyItemId") or item.get("itemWebUrl") or item.get("title")
                if item_id in seen:
                    continue
                seen.add(item_id)
                title = item.get("title", "")
                if player and not _title_matches_player(title, player):
                    continue
                if number and not _title_matches_card_number(title, number):
                    continue
                items.append(item)
        except Exception:
            # Validation should never break listing creation.
            continue

    return items


def _extract_years(titles: List[str]) -> List[str]:
    years = []
    for title in titles:
        for match in YEAR_RE.finditer(title):
            first = match.group(1)
            second = match.group(2)
            if second:
                # For titles like 2025-26, use the start year for title consistency.
                years.append(first)
            else:
                years.append(first)
    return years


def _extract_brand_set(titles: List[str]) -> List[str]:
    found = []
    for title in titles:
        title_lower = title.lower()
        for brand in KNOWN_BRANDS:
            if brand.lower() in title_lower:
                found.append(brand)
                break
    return found


def normalize_sports_variant(value: Any) -> str:
    text = _norm(value)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower().replace("x refractor", "x-fractor").replace("xfractor", "x-fractor")

    if low in FALSE_VARIANT_PHRASES:
        return ""

    for variant in SAFE_VARIANTS:
        v_low = variant.lower().replace("xfractor", "x-fractor")
        if low == v_low:
            return "X-Fractor" if v_low == "x-fractor" else variant

    # Accept obvious combined color/parallel terms only if they contain a known variant word.
    if any(token in low for token in ["refractor", "prizm", "holo", "wave", "mojo", "pulsar", "laser", "disco", "shimmer"]):
        return text.replace("X Refractor", "X-Fractor").replace("Xfractor", "X-Fractor")

    return ""


def _extract_variants(titles: List[str]) -> List[str]:
    variants = []
    for title in titles:
        title_lower = title.lower()
        for variant in SAFE_VARIANTS:
            if variant.lower() in title_lower:
                variants.append(normalize_sports_variant(variant))
    return [v for v in variants if v]


def _majority(values: List[str], min_count: int = 2, min_ratio: float = 0.40) -> Tuple[str, int, float]:
    cleaned = [v for v in values if v]
    if not cleaned:
        return "", 0, 0.0
    counter = Counter(cleaned)
    value, count = counter.most_common(1)[0]
    ratio = count / len(cleaned)
    if count >= min_count and ratio >= min_ratio:
        return value, count, ratio
    return "", count, ratio


def enrich_sports_card_data(card_data: Dict[str, Any]) -> Dict[str, Any]:
    """Fill fields the scan could not answer, using eBay active listings as a hint.

    Never overrides what was read off the card. Active listings for a player and card
    number span every parallel of that card, so their majority is almost always plain
    "Refractor" - letting it win turned X-Refractors, Ray Waves and numbered colours into
    generic titles. The same goes for year and product: the card itself is the authority.
    """
    if not is_sports_card(card_data):
        return card_data

    enriched = dict(card_data or {})
    player = _norm(enriched.get("player_athlete") or enriched.get("name") or enriched.get("card_name"))
    if not player:
        return enriched

    missing_year = not _norm(enriched.get("year") or enriched.get("season"))
    missing_set = not _norm(enriched.get("set"))
    if not (missing_year or missing_set):
        enriched["sports_validation_source"] = "vision"
        return enriched

    items = _candidate_items(enriched)
    titles = [item.get("title", "") for item in items if item.get("title")]
    if len(titles) < 2:
        enriched["sports_validation_source"] = "vision_only"
        return enriched

    notes = []
    if missing_year:
        year, count, _ = _majority(_extract_years(titles), min_count=2, min_ratio=0.40)
        if year:
            enriched["year"] = year
            enriched["season"] = year
            notes.append("year:{}/{}".format(count, len(titles)))

    if missing_set:
        brand_set, count, _ = _majority(_extract_brand_set(titles), min_count=2, min_ratio=0.35)
        if brand_set:
            enriched["set"] = brand_set
            if not _norm(enriched.get("brand")):
                enriched["brand"] = brand_set.split(" ", 1)[0]
            notes.append("set:{}/{}".format(count, len(titles)))

    enriched["sports_validation_source"] = "ebay_active" if notes else "vision_only"
    enriched["sports_validation_notes"] = ", ".join(notes)
    enriched["sports_validation_example_titles"] = titles[:5]
    return enriched
