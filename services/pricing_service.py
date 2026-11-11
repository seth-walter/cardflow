"""Turns card data into a price, and is honest about how much to trust it.

Sold comps are tried first, in order of trust. Asking prices are the last resort and are
always labelled as an estimate rather than a comp. Whatever the source, the result says
how confident it is, and thin or wildly scattered data sets `needs_review`, which the UI
turns into a NEEDS PRICE REVIEW badge and holds back from bulk publishing.
"""

from typing import Any, Dict, Optional

from config import settings
from core.listing_builder import build_comp_query
from services.price_sources import SOLD_SOURCES, ebay_active
from utils.text_utils import damage_terms

FALLBACK_PRICE = 9.99

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


def spread_ratio(quote: Dict[str, Any]) -> Optional[float]:
    """How far apart the cheapest and dearest comps are, relative to the price."""
    low, high, price = quote.get("low"), quote.get("high"), quote.get("price")
    if not low or not high or not price or low <= 0:
        return None
    return round(float(high) / float(low), 2)


def score_quote(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Attach a confidence level and a reason a human can act on."""
    count = quote.get("count")
    spread = spread_ratio(quote)
    sold = quote.get("basis") == "sold"
    reasons = []

    if sold:
        confidence = CONFIDENCE_HIGH
    else:
        confidence = CONFIDENCE_MEDIUM
        reasons.append("based on asking prices, not completed sales")

    if count is not None and count < settings.MIN_COMPS_FOR_CONFIDENCE:
        confidence = CONFIDENCE_LOW
        reasons.append("only {} comp(s)".format(count))

    if spread is not None and spread > settings.MAX_SPREAD_RATIO:
        confidence = CONFIDENCE_LOW
        reasons.append("comps range {}x from low to high".format(spread))

    quote["confidence"] = confidence
    quote["spread_ratio"] = spread
    quote["review_reasons"] = reasons
    quote["needs_review"] = confidence == CONFIDENCE_LOW
    return quote


def get_price_estimate(card_data: Dict[str, Any]) -> Dict[str, Any]:
    """Best available price for a card, with its provenance and confidence.

    Doubts about which card this is (card_data["identity_review"]) flag the price too:
    a confident price for the wrong version is the most expensive mistake there is.
    """
    price_data = _estimate(card_data)
    doubts = [d for d in card_data.get("identity_review") or [] if d]
    if doubts:
        price_data["review_reasons"] = list(price_data.get("review_reasons") or []) + doubts
        price_data["needs_review"] = True
    return price_data


def _estimate(card_data: Dict[str, Any]) -> Dict[str, Any]:
    query = build_comp_query(card_data)

    # Damage wording is stripped from the search, so the comps describe an undamaged
    # copy. Say so rather than letting a creased card inherit a mint card's price.
    flaws = damage_terms(card_data.get("comp_query_override") or "")
    flaws += [t for t in damage_terms(card_data.get("condition_estimate") or "")
              if t not in flaws]

    for source in SOLD_SOURCES:
        try:
            quote = source(card_data)
        except Exception:
            quote = None

        if quote:
            quote = score_quote(quote)
            return _as_price_data(_note_damage(quote, flaws), query)

    try:
        ask_quote = ebay_active(card_data)
    except Exception:
        ask_quote = None

    if ask_quote:
        return _as_price_data(_note_damage(score_quote(ask_quote), flaws), query)

    return {
        "source": "fallback",
        "basis": "none",
        "query_used": query,
        "estimated_price": FALLBACK_PRICE,
        "low": None,
        "high": None,
        "count": 0,
        "confidence": CONFIDENCE_LOW,
        "spread_ratio": None,
        "needs_review": True,
        "review_reasons": ["no comps found for this card"],
        "detail": "no comparable listings found",
        "url": "",
        "offers": {},
    }


def _note_damage(quote: Dict[str, Any], flaws) -> Dict[str, Any]:
    """Flag a card whose title mentions damage: its comps are for clean copies."""
    if not flaws:
        return quote

    quote["review_reasons"] = list(quote.get("review_reasons", [])) + [
        "title mentions {} - comps are for undamaged copies".format(", ".join(flaws))
    ]
    quote["needs_review"] = True
    return quote


def _as_price_data(quote: Dict[str, Any], query: str) -> Dict[str, Any]:
    return {
        "source": quote["source"],
        "basis": quote["basis"],
        "query_used": query,
        "estimated_price": quote["price"],
        "low": quote.get("low"),
        "high": quote.get("high"),
        "count": quote.get("count"),
        "confidence": quote["confidence"],
        "spread_ratio": quote.get("spread_ratio"),
        "needs_review": quote["needs_review"],
        "review_reasons": quote.get("review_reasons", []),
        "detail": quote.get("detail", ""),
        "url": quote.get("url", ""),
        "offers": quote.get("offers") or {},
    }
