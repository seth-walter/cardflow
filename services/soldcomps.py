"""Real eBay completed sales, via sold-comps.com.

This is the source the app was missing: individual sold listings rather than a derived
number, which means a price can be reported with how many sales it rests on, how far
apart they were, and how recent they are.

Responses are cached on disk because the free plan allows only 100 requests a month and
the same card is often priced more than once (scan, then reprice).
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

import requests

from config import settings

API_URL = "https://api.sold-comps.com/v1/scrape"
CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "soldcomps_cache.json",
)

_cache_lock = threading.Lock()


def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError:
        pass


def _fresh(entry: Dict[str, Any]) -> bool:
    try:
        fetched = datetime.strptime(entry["fetched_at"], "%Y-%m-%dT%H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.utcnow() - fetched < timedelta(days=settings.SOLDCOMPS_CACHE_DAYS)



STOP_WORDS = {"the", "a", "of", "in", "and", "card", "cards", "rc", "sp", "nm", "mint"}


def tokens(text: str) -> List[str]:
    """Comparable words from a title. Print runs like /50 stay attached, plurals fold."""
    found = re.findall(r"[a-z0-9]+(?:/[0-9]+)?", str(text).lower())
    out = []
    for token in found:
        token = token.rstrip("s") if len(token) > 3 else token
        if token and token not in STOP_WORDS:
            out.append(token)
    return out


def relevance(query_tokens: List[str], title: str) -> float:
    """How much of the searched card this listing's title actually contains."""
    if not query_tokens:
        return 0.0
    title_tokens = set(tokens(title))
    return sum(1 for token in query_tokens if token in title_tokens) / len(query_tokens)


def percentile(values: List[float], fraction: float) -> float:
    """Simple nearest-rank percentile; enough for spotting a scattered market."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]



def distinguishing_tokens(query_tokens: List[str], items: List[Dict[str, Any]]) -> List[str]:
    """Query words that only some results contain - the parallel, the print run, the SSP.

    Words appearing in nearly every result (topps, chrome, the player's name) say nothing
    about which version of the card sold, so they are not required.
    """
    if not query_tokens or len(items) < 4:
        return []

    titles = [set(tokens(item.get("title") or "")) for item in items]
    rare = []

    for token in query_tokens:
        appearances = sum(1 for title in titles if token in title)
        share = appearances / len(titles)
        # 0.75 rather than 0.5: "true" and "gold" each appear in two thirds of a Ray
        # Allen result set (True Blue, Gold Geometric), yet together they are the card.
        if 0 < share < 0.75:
            rare.append(token)

    return rare


def split_by_sale_type(items: List[Dict[str, Any]], keyword: str = "",
                       required_terms: Optional[List[str]] = None):
    """Prices of the comps that really describe this card, split by how they sold.

    eBay reveals the accepted amount on Best Offer sales, so an offer sale records what
    a buyer actually paid rather than what a seller hoped for. Keeping the two groups
    apart lets the caller see whether this card sells at ask or gets negotiated down.
    """
    query_tokens = tokens(keyword) if keyword else []
    sold = [i for i in items if i.get("listingType") in (None, "sold")]

    # Frequency analysis needs a few results to work with. Terms the caller marks as
    # essential - the player or character name - are required either way, because a
    # three-comp result set offers no frequencies to learn from and a near-miss on a
    # different player would otherwise set the price.
    required = list(distinguishing_tokens(query_tokens, sold))
    for term in tokens(" ".join(required_terms or [])):
        if term not in required:
            required.append(term)

    offer_prices, bin_prices, recent, considered = [], [], [], 0

    for item in sold:
        considered += 1
        title = item.get("title") or ""

        if query_tokens:
            if relevance(query_tokens, title) < settings.SOLDCOMPS_MIN_RELEVANCE:
                continue
            # Every word that makes this card different from its siblings must appear.
            # Without this a "True Gold /50" keeps matching "Gold Geometric /50", which
            # differs by one word and scores 0.91 on fraction alone.
            title_tokens = set(tokens(title))
            if any(token not in title_tokens for token in required):
                continue

        raw = item.get("soldPrice")
        if raw in (None, ""):
            continue
        try:
            value = float(str(raw).replace("$", "").replace(",", ""))
        except ValueError:
            continue
        if value <= 0:
            continue

        if item.get("bestOfferAccepted"):
            offer_prices.append(value)
        else:
            bin_prices.append(value)

        if item.get("endedAt"):
            recent.append(item["endedAt"])

    return offer_prices, bin_prices, recent, considered


def choose_prices(offer_prices: List[float], bin_prices: List[float]) -> List[float]:
    """Which sales the headline price is built from, per SOLDCOMPS_OFFER_MODE."""
    mode = settings.SOLDCOMPS_OFFER_MODE

    if mode == "exclude":
        return list(bin_prices)

    if mode == "prefer" and len(offer_prices) >= max(1, settings.SOLDCOMPS_MIN_COMPS):
        return list(offer_prices)

    return list(offer_prices) + list(bin_prices)


def summarise(items: List[Dict[str, Any]], keyword: str = "",
              required_terms: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Turn raw sold listings into a price, a range, and a count.

    Items that do not actually describe the searched card are dropped first. eBay answers
    a thin search with loosely related listings, and without this filter a True Gold /50
    gets priced from base cards - measured on a real listing, $500 became $18.

    The range uses the middle half of the sales rather than the absolute min and max, so
    one damaged copy or one mispriced grail does not make an ordinary card look wild.
    """
    offer_prices, bin_prices, recent, considered = split_by_sale_type(
        items, keyword, required_terms)
    prices = choose_prices(offer_prices, bin_prices)

    if len(prices) < max(1, settings.SOLDCOMPS_MIN_COMPS):
        return None

    kept_total = len(offer_prices) + len(bin_prices)

    return {
        "price": round(median(prices), 2),
        "low": round(percentile(prices, 0.25), 2),
        "high": round(percentile(prices, 0.75), 2),
        "count": len(prices),
        "floor": round(min(prices), 2),
        "ceiling": round(max(prices), 2),
        "latest_sale": max(recent) if recent else "",
        "considered": considered,
        "kept": kept_total,
        "offer_sales": len(offer_prices),
        "bin_sales": len(bin_prices),
        "offer_share": round(len(offer_prices) / kept_total, 2) if kept_total else 0.0,
        "offer_median": round(median(offer_prices), 2) if offer_prices else None,
        "bin_median": round(median(bin_prices), 2) if bin_prices else None,
        "offer_mode": settings.SOLDCOMPS_OFFER_MODE,
    }



KEPT_ITEM_FIELDS = ("title", "soldPrice", "totalPrice", "endedAt", "listingType",
                    "bestOfferAccepted", "condition", "url")


def trim_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """The parts of a sold listing worth keeping on disk.

    Storing the comps rather than only the number they produced means the filter can be
    changed, or the best-offer policy revisited, without paying for the data twice.
    """
    return {field: item.get(field) for field in KEPT_ITEM_FIELDS if field in item}



def fetch_sold(keyword: str, count: int = 40,
               required_terms: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Completed eBay sales for a search term, cached to conserve the request quota."""
    key = settings.SOLDCOMPS_API_KEY
    if not key or not keyword.strip():
        return None

    cache_key = "{}|{}d".format(keyword.strip().lower(), settings.SOLDCOMPS_DAYS)

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get(cache_key)
        if entry and _fresh(entry):
            # Recompute from the stored comps so a change to the filter or to the
            # best-offer policy takes effect without spending another request.
            if entry.get("items"):
                return summarise(entry["items"], keyword, required_terms)
            return entry.get("summary")

    sold_after = (datetime.utcnow() - timedelta(days=settings.SOLDCOMPS_DAYS)).strftime("%Y-%m-%d")

    # Deeper pages are the documented way to get more comps. Each one costs a request,
    # so extra pages are only fetched while the card still looks short of data.
    items: List[Dict[str, Any]] = []
    total_results = ""

    for page in range(1, max(1, settings.SOLDCOMPS_MAX_PAGES) + 1):
        try:
            response = requests.get(
                API_URL,
                headers={"Authorization": "Bearer " + key},
                params={
                    "keyword": keyword,
                    "sold": "true",
                    "count": count,
                    "page": page,
                    "soldAfter": sold_after,
                    "sortOrder": "endedRecently",
                },
                timeout=30,
            )
        except Exception:
            break

        # 429 is quota or rate limiting: fail quiet and let pricing fall through.
        if response.status_code >= 400:
            break

        try:
            payload = response.json()
        except ValueError:
            break

        page_items = payload.get("items", []) or []
        items.extend(page_items)
        total_results = payload.get("totalResults", total_results)

        if not payload.get("hasNextPage") or not page_items:
            break
        if len(items) >= settings.SOLDCOMPS_ENOUGH_COMPS:
            break

    if not items:
        return None

    summary = summarise(items, keyword, required_terms)

    with _cache_lock:
        cache = _load_cache()
        cache[cache_key] = {
            "fetched_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "summary": summary,
            "total_results": total_results,
            "items": [trim_item(item) for item in items],
        }
        _save_cache(cache)

    return summary


def cache_stats() -> Dict[str, int]:
    """How much of the request quota the cache is saving."""
    cache = _load_cache()
    fresh = sum(1 for entry in cache.values() if _fresh(entry))
    return {"entries": len(cache), "fresh": fresh, "stale": len(cache) - fresh}
