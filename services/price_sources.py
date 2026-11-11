"""Where a price can come from, in order of how much it can be trusted.

Every source returns the same shape so the pricing service can compare them:

    {"source", "basis", "price", "low", "high", "count", "detail", "url"}

`basis` is the field that matters. "sold" means the number came from completed sales.
"ask" means it came from what sellers are currently asking, which runs high and is
labelled as an estimate everywhere it is shown.
"""

import json
import os
import re
from statistics import median
from typing import Any, Dict, List, Optional

import requests

from config import settings
from core.listing_builder import build_comp_query, is_tcg_card
from services.card_aspects import clean_text, is_non_sport_card
from services.ebay_browse import ebay_browse_search, summarize_comps
from services.soldcomps import fetch_sold

OWN_SALES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "own_sales.json"
)

PRICECHARTING_HOST = "https://www.pricecharting.com"
SPORTSCARDSPRO_HOST = "https://www.sportscardspro.com"

# PriceCharting / SportsCardsPro return integer cents, one field per grade.
# Verify this mapping against a known card with tools/check_pricecharting.py.
GRADE_FIELDS = [
    ("10", "manual-only-price"),
    ("9.5", "box-only-price"),
    ("9", "graded-price"),
    ("8", "new-price"),
    ("7", "cib-price"),
]
UNGRADED_FIELD = "loose-price"


def _quote(source, basis, price, low=None, high=None, count=None, detail="", url=""):
    """Normalised result, or None when the source has nothing usable to say."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None

    if value <= 0:
        return None

    return {
        "source": source,
        "basis": basis,
        "price": round(value, 2),
        "low": round(float(low), 2) if low else None,
        "high": round(float(high), 2) if high else None,
        "count": count,
        "detail": detail,
        "url": url,
    }


# ----------------------------------------------------------------- sold sources

def own_sales(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What this exact card actually sold for on your own account.

    The strongest comp available: same market, same photos, same seller, same shipping.
    Populated by tools/backfill_sales.py; unavailable until that has been run.
    """
    sales = _load_own_sales()
    if not sales:
        return None

    terms = [term for term in build_comp_query(card_data).lower().split() if len(term) > 2]
    if len(terms) < 3:
        return None

    needed = max(3, int(len(terms) * 0.7))
    matches = []

    for sale in sales:
        title = clean_text(sale.get("title")).lower()
        if not title:
            continue
        if sum(1 for term in terms if term in title) < needed:
            continue
        try:
            matches.append(float(sale.get("price")))
        except (TypeError, ValueError):
            continue

    if not matches:
        return None

    return _quote(
        "own_sales", "sold", median(matches),
        low=min(matches), high=max(matches), count=len(matches),
        detail="{} of your own past sale(s)".format(len(matches)),
    )


def tcgplayer_market(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """TCGplayer market price, which is computed from completed TCGplayer sales.

    Free: these values arrive with the PokemonTCG lookup the app already performs.
    """
    prices = card_data.get("tcgplayer_prices")
    if not isinstance(prices, dict) or not prices:
        return None

    wanted = clean_text(card_data.get("finish") or card_data.get("variant")).lower()
    wanted = wanted.replace(" ", "")

    def preference(item):
        name = item[0].lower()
        return (0 if wanted and wanted in name else 1, name)

    for variant, values in sorted(prices.items(), key=preference):
        if not isinstance(values, dict):
            continue

        market = values.get("market") or values.get("mid")
        quote = _quote(
            "tcgplayer_market", "sold", market,
            low=values.get("low"), high=values.get("high"),
            detail="TCGplayer market price ({})".format(variant),
            url=clean_text(card_data.get("tcgplayer_url")),
        )
        if quote:
            return quote

    return None




def essential_name_terms(card_data: Dict[str, Any]) -> List[str]:
    """The one word a comp must contain to be about this card.

    Sellers write names every which way - "James Harden", "Harden", "J. Harden" - so
    requiring the whole name throws away real sales. The longest word in it is the
    surname or the character, which survives nearly every spelling, and it is enough to
    keep a different player out of the comps when there are too few results to tell
    common words from distinguishing ones.
    """
    for field in ("player_athlete", "name", "card_name", "character"):
        value = clean_text(card_data.get(field))
        if not value:
            continue

        words = [word for word in re.split(r"[^A-Za-z0-9]+", value) if len(word) > 2]
        if words:
            return [max(words, key=len)]

    return []


def soldcomps(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Median of real eBay completed sales for this card.

    The only source that reports individual sales, so the confidence gate gets a genuine
    comp count and spread instead of having to trust a single aggregated number.
    """
    query = build_comp_query(card_data)
    if not query.strip():
        return None

    summary = fetch_sold(query, required_terms=essential_name_terms(card_data))
    if not summary:
        return None

    detail = "{} eBay sale(s) in the last {} days".format(
        summary["count"], settings.SOLDCOMPS_DAYS)

    offers = summary.get("offer_sales") or 0
    if offers:
        detail += ", {} via accepted offers".format(offers)
    if summary.get("latest_sale"):
        detail += ", latest {}".format(summary["latest_sale"])

    quote = _quote(
        "soldcomps", "sold", summary["price"],
        low=summary["low"], high=summary["high"], count=summary["count"],
        detail=detail,
    )

    if quote:
        # eBay publishes the accepted amount on Best Offer sales, so these record what a
        # buyer paid rather than what a seller asked. Carried through so the UI can show
        # how negotiable a card is.
        quote["offers"] = {
            "offer_sales": offers,
            "bin_sales": summary.get("bin_sales") or 0,
            "offer_share": summary.get("offer_share") or 0.0,
            "offer_median": summary.get("offer_median"),
            "bin_median": summary.get("bin_median"),
            "mode": summary.get("offer_mode", "include"),
        }

    return quote



def onepiece_market(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Market price from the One Piece catalogue, captured during identification.

    Free, and no request quota: it arrives with the set lookup the app already performs.
    """
    price = card_data.get("onepiece_market_price")
    if not price:
        return None

    return _quote(
        "onepiece_market", "sold", price,
        detail="One Piece catalogue market price ({})".format(
            clean_text(card_data.get("card_number")) or "matched card"),
    )


def _pricecharting_field(card_data: Dict[str, Any]) -> str:
    """Which price field matches this card's graded state."""
    if not card_data.get("is_graded"):
        return UNGRADED_FIELD

    grade = clean_text(card_data.get("grade")).upper().replace("PSA", "").strip()
    for value, field in GRADE_FIELDS:
        if grade.startswith(value):
            return field

    return "graded-price"


def pricecharting(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sold-based prices from PriceCharting / SportsCardsPro (one token covers both).

    Sports cards live on sportscardspro.com, TCG and entertainment on pricecharting.com.
    Returns None when no token is configured, so the app still runs without a
    subscription - it just falls back to asking prices.
    """
    token = settings.PRICECHARTING_TOKEN
    if not token:
        return None

    query = build_comp_query(card_data)
    if not query.strip():
        return None

    sports = not (is_tcg_card(card_data) or is_non_sport_card(card_data))
    host = SPORTSCARDSPRO_HOST if sports else PRICECHARTING_HOST

    try:
        response = requests.get(
            host + "/api/product", params={"t": token, "q": query}, timeout=20
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
    except Exception:
        return None

    if not isinstance(payload, dict) or "id" not in payload:
        return None

    field = _pricecharting_field(card_data)
    cents = payload.get(field) or payload.get(UNGRADED_FIELD)
    if not cents:
        return None

    matched = " ".join(part for part in [
        clean_text(payload.get("product-name")),
        clean_text(payload.get("console-name")),
    ] if part)

    return _quote(
        "sportscardspro" if sports else "pricecharting",
        "sold",
        float(cents) / 100.0,
        detail="{} [{}]".format(matched or "matched product", field.replace("-", " ")),
        url="{}/game/{}".format(host, payload.get("id")),
    )


# ------------------------------------------------------------------ ask source

def loosen_query(card_data: Dict[str, Any]) -> str:
    """The query rebuilt from the card's fields, ignoring any listing-title override.

    A full listing title is the right search for completed sales, where the relevance
    filter needs every distinguishing word. It is too specific for active listings: a
    card with no copy currently listed under that exact wording returns nothing.
    """
    without_override = {key: value for key, value in card_data.items()
                        if key != "comp_query_override"}
    return build_comp_query(without_override)


def ebay_active(card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Median of current asking prices. Not a comp: sellers ask what they hope for."""
    query = build_comp_query(card_data)
    if not query.strip():
        return None

    summary = summarize_comps(ebay_browse_search(query, limit=12))

    if summary["median"] is None:
        # Nothing is listed under that exact wording. Fall back to the looser query
        # rather than dropping the card to the default price.
        looser = loosen_query(card_data)
        if looser.strip() and looser.strip().lower() != query.strip().lower():
            summary = summarize_comps(ebay_browse_search(looser, limit=12))
            query = looser

    if summary["median"] is None:
        return None

    detail = "median of {} active listing(s)".format(summary["count"])
    if settings.ASK_TO_SOLD_FACTOR != 1.0:
        detail += ", x{} calibration".format(settings.ASK_TO_SOLD_FACTOR)

    return _quote(
        "ebay_active", "ask", summary["median"] * settings.ASK_TO_SOLD_FACTOR,
        low=summary["low"], high=summary["high"], count=summary["count"],
        detail=detail,
    )


def _load_own_sales() -> List[Dict[str, Any]]:
    try:
        with open(OWN_SALES_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    return data.get("sales", []) if isinstance(data, dict) else []


# Highest trust first: the first source that answers with a price wins.
SOLD_SOURCES = [own_sales, tcgplayer_market, onepiece_market, soldcomps,
                pricecharting]
