import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from config import settings
except Exception:  # pragma: no cover
    settings = None

CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "pokemon_card_cache.json"
POKEMON_API_URL = "https://api.pokemontcg.io/v2/cards"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("pokémon", "pokemon")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def pokemon_number(value: Any) -> str:
    """Return the left side of a printed Pokemon number, e.g. 179 from 179/132."""
    text = clean_text(value)
    if not text:
        return ""
    text = text.split("/")[0]
    text = re.sub(r"[^A-Za-z0-9-]", "", text)
    return text


def load_cache() -> Dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: Dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def build_queries(card_data: Dict[str, Any]) -> List[str]:
    name = clean_text(
        card_data.get("name")
        or card_data.get("card_name")
        or card_data.get("character")
    )
    number = pokemon_number(card_data.get("card_number") or card_data.get("number"))
    raw_set = clean_text(card_data.get("set") or card_data.get("pokemon_set"))

    queries: List[str] = []

    if name and number:
        queries.append(f'name:"{name}" number:{number}')
        # A looser variant helps when the API uses punctuation slightly differently.
        name_terms = " ".join(normalize(name).split()[:5])
        if name_terms:
            queries.append(f'{name_terms} number:{number}')

    if number and raw_set:
        queries.append(f'number:{number} set.name:"{raw_set}"')

    if name:
        queries.append(f'name:"{name}"')

    # De-dupe while preserving order.
    seen = set()
    result = []
    for q in queries:
        if q not in seen:
            result.append(q)
            seen.add(q)
    return result


def api_headers() -> Dict[str, str]:
    api_key = getattr(settings, "POKEMONTCG_API_KEY", "") if settings else ""
    return {"X-Api-Key": api_key} if api_key else {}


def search_api(query: str, page_size: int = 10) -> List[Dict[str, Any]]:
    response = requests.get(
        POKEMON_API_URL,
        params={"q": query, "pageSize": page_size},
        headers=api_headers(),
        timeout=12,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def score_candidate(card_data: Dict[str, Any], candidate: Dict[str, Any]) -> int:
    score = 0
    extracted_name = normalize(
        card_data.get("name")
        or card_data.get("card_name")
        or card_data.get("character")
    )
    candidate_name = normalize(candidate.get("name"))

    extracted_number = pokemon_number(card_data.get("card_number") or card_data.get("number"))
    candidate_number = pokemon_number(candidate.get("number"))

    extracted_set = normalize(card_data.get("set") or card_data.get("pokemon_set"))
    candidate_set = normalize((candidate.get("set") or {}).get("name"))

    if extracted_number and candidate_number and extracted_number == candidate_number:
        score += 70

    if extracted_name and candidate_name:
        if extracted_name == candidate_name:
            score += 60
        else:
            extracted_words = set(extracted_name.split())
            candidate_words = set(candidate_name.split())
            overlap = len(extracted_words & candidate_words)
            score += overlap * 10

    if extracted_set and candidate_set:
        if extracted_set == candidate_set:
            score += 40
        elif extracted_set in candidate_set or candidate_set in extracted_set:
            score += 25

    return score


def choose_best_match(card_data: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    scored: List[Tuple[int, Dict[str, Any]]] = [
        (score_candidate(card_data, candidate), candidate) for candidate in candidates
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best = scored[0]

    # Require enough confidence to avoid making titles worse.
    # A matching card number + partial name usually clears this.
    if best_score < 80:
        return None

    return best


def year_from_release_date(release_date: str) -> str:
    match = re.match(r"(\d{4})", clean_text(release_date))
    return match.group(1) if match else ""


def enrich_from_candidate(card_data: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(card_data)
    set_data = candidate.get("set") or {}
    printed_total = clean_text(set_data.get("printedTotal"))
    number = clean_text(candidate.get("number"))

    if number and printed_total and "/" not in number:
        display_number = f"{number}/{printed_total}"
    else:
        display_number = number or clean_text(enriched.get("card_number"))

    release_year = year_from_release_date(set_data.get("releaseDate", ""))

    # Validated fields should drive titles. Keep original fields if the lookup is unavailable.
    enriched["card_category"] = "pokemon"
    enriched["game"] = "Pokemon TCG"
    enriched["name"] = clean_text(candidate.get("name")) or clean_text(enriched.get("name"))
    enriched["card_name"] = enriched["name"]
    enriched["character"] = enriched["name"]
    enriched["set"] = clean_text(set_data.get("name")) or clean_text(enriched.get("set"))
    enriched["card_number"] = display_number
    enriched["rarity"] = clean_text(candidate.get("rarity")) or clean_text(enriched.get("rarity"))
    enriched["year"] = release_year or clean_text(enriched.get("year"))
    enriched["year_manufactured"] = enriched["year"]
    enriched["manufacturer"] = "The Pokemon Company"
    enriched["pokemon_lookup_verified"] = True
    enriched["pokemon_lookup_id"] = clean_text(candidate.get("id"))
    enriched["pokemon_lookup_source"] = "pokemontcg.io"

    # TCGplayer market prices are computed from completed sales and arrive free with
    # this lookup. Keeping them turns Pokemon pricing into a real sold comp.
    tcgplayer = candidate.get("tcgplayer") or {}
    if tcgplayer.get("prices"):
        enriched["tcgplayer_prices"] = tcgplayer["prices"]
        enriched["tcgplayer_url"] = clean_text(tcgplayer.get("url"))
        enriched["tcgplayer_updated"] = clean_text(tcgplayer.get("updatedAt"))

    return enriched


def is_probably_pokemon(card_data: Dict[str, Any]) -> bool:
    combined = " ".join(
        clean_text(card_data.get(k))
        for k in ["card_category", "game", "brand", "manufacturer", "set", "notes", "name", "card_name"]
    ).lower()
    return "pokemon" in combined or "pokémon" in combined


def enrich_pokemon_card_data(card_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate Pokemon identity/set/number/rarity against PokemonTCG API when possible.

    If lookup fails or confidence is low, returns the original card_data unchanged.
    This prevents bad API matches from overwriting card identification.
    """
    if not is_probably_pokemon(card_data):
        return card_data

    queries = build_queries(card_data)
    if not queries:
        return card_data

    cache = load_cache()
    cache_key = "|".join(queries)

    if cache_key in cache:
        cached = cache[cache_key]
        if cached:
            return enrich_from_candidate(card_data, cached)
        return card_data

    all_candidates: List[Dict[str, Any]] = []
    for query in queries:
        try:
            all_candidates.extend(search_api(query))
            time.sleep(0.15)
        except Exception:
            continue

    # De-dupe by card id.
    unique = {}
    for candidate in all_candidates:
        card_id = candidate.get("id")
        if card_id:
            unique[card_id] = candidate

    best = choose_best_match(card_data, list(unique.values()))
    cache[cache_key] = best or {}
    save_cache(cache)

    if not best:
        return card_data

    return enrich_from_candidate(card_data, best)
