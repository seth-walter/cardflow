"""Suggesting a fresh price for a listing that is already live.

The listing's own item specifics are turned back into the card data the pricing layer
expects, so a live listing is priced exactly the way a freshly scanned card is - same
sources, same confidence rules, same review flag.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from core.reprice_history import days_since
from services.pricing_service import get_price_estimate

# eBay aspect -> the card_data field the pricing layer reads.
ASPECT_MAP = {
    "Player/Athlete": "player_athlete",
    "Character": "character",
    "Franchise": "franchise",
    "Card Name": "card_name",
    "Set": "set",
    "Card Number": "card_number",
    "Manufacturer": "brand",
    "Year Manufactured": "year",
    "Season": "season",
    "Sport": "sport",
    "League": "league",
    "Team": "team",
    "Parallel/Variety": "variant",
    "Features": "features",
    "Rarity": "rarity",
    "Finish": "finish",
    "Grade": "grade",
    "Professional Grader": "grader",
    "Card Condition": "condition_estimate",
    "Game": "game",
}

TYPE_TO_CATEGORY = {
    "sports trading card": "sports",
    "non-sport trading card": "other",
    "collectible card game": "pokemon",
}

MIN_USEFUL_FIELDS = 3


def first(values) -> str:
    if isinstance(values, list):
        return str(values[0]).strip() if values else ""
    return str(values or "").strip()


def card_data_from_listing(listing: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild pricing input from a live listing's item specifics.

    Falls back to the listing title as the comp query when the specifics are too thin
    to build a sensible search from.
    """
    aspects = listing.get("aspects") or {}
    card_data: Dict[str, Any] = {}

    for aspect, field in ASPECT_MAP.items():
        value = first(aspects.get(aspect))
        if value:
            card_data[field] = value

    card_type = first(aspects.get("Type")).lower()
    card_data["card_category"] = TYPE_TO_CATEGORY.get(card_type, "")

    if card_data["card_category"] == "pokemon" and not card_data.get("game"):
        card_data["game"] = "Pokemon TCG"

    card_data["is_graded"] = first(aspects.get("Graded")).lower() == "yes"

    if card_data.get("season") and not card_data.get("year"):
        card_data["year"] = card_data["season"]

    # Always search on the listing title. Item specifics record "Refractor" where the
    # title says "True Gold /50", and that one phrase is the difference between a $500
    # card and a $12 one.
    card_data["comp_query_override"] = listing.get("title", "")

    return card_data


def suggest_for_listing(listing: Dict[str, Any]) -> Dict[str, Any]:
    """Current price against a freshly computed one, with the reasoning attached."""
    card_data = card_data_from_listing(listing)

    try:
        price_data = get_price_estimate(card_data)
    except Exception as exc:
        return dict(listing, error=str(exc), suggested=None, delta=None, delta_pct=None)

    suggested = float(price_data["estimated_price"])
    current = listing.get("price")

    delta = delta_pct = None
    if current:
        delta = round(suggested - float(current), 2)
        delta_pct = round((suggested / float(current) - 1) * 100, 1)

    return dict(
        listing,
        repriced_days_ago=days_since(
            listing.get("offer_id") or "item" + str(listing.get("listing_id", ""))),
        card_data=card_data,
        price_data=price_data,
        suggested=suggested,
        delta=delta,
        delta_pct=delta_pct,
        basis=price_data.get("basis"),
        confidence=price_data.get("confidence"),
        needs_review=bool(price_data.get("needs_review")),
        review_reasons=price_data.get("review_reasons", []),
        detail=price_data.get("detail", ""),
        error="",
    )


def suggest_for_listings(listings: List[Dict[str, Any]], workers: int = 6,
                         on_progress=None) -> List[Dict[str, Any]]:
    """Price a batch of listings in parallel; each one hits the comp sources."""
    results: List[Dict[str, Any]] = []
    total = len(listings)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, row in enumerate(pool.map(suggest_for_listing, listings), start=1):
            results.append(row)
            if on_progress:
                on_progress(index, total)

    return results


def movers(rows: List[Dict[str, Any]], min_change_pct: float = 10.0,
           sold_only: bool = False, cooldown_days: float = 0.0) -> List[Dict[str, Any]]:
    """Listings whose suggested price differs enough to be worth acting on.

    `cooldown_days` hides listings repriced recently. Comps move a little every week, so
    without it the same cards resurface every run and get churned for pennies.
    """
    picked = []

    for row in rows:
        if row.get("error") or row.get("delta_pct") is None:
            continue
        if sold_only and row.get("basis") != "sold":
            continue
        if abs(row["delta_pct"]) < min_change_pct:
            continue

        age = row.get("repriced_days_ago")
        if cooldown_days and age is not None and age < cooldown_days:
            continue

        picked.append(row)

    return sorted(picked, key=lambda r: abs(r["delta_pct"]), reverse=True)
