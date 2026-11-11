"""Reading and repricing the listings you already have live on eBay.

Repricing is deliberately read-modify-write: the existing offer is fetched, only the
price fields are changed, and the whole thing is sent back. Rebuilding an offer from
scratch (the way publishing does) would silently drop anything this app does not model,
such as quantity, listing duration, or policies set elsewhere.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from services.ebay_auth import ebay_request
from services.ebay_trading import fetch_all_active_listings, fetch_item_category

INVENTORY_URL = "https://api.ebay.com/sell/inventory/v1/inventory_item"
OFFER_URL = "https://api.ebay.com/sell/inventory/v1/offer"
PAGE_SIZE = 100


def _json(response):
    return response.json() if response is not None and response.text else {}


def fetch_inventory_page(offset: int, limit: int = PAGE_SIZE) -> List[Dict[str, Any]]:
    """One page of inventory items."""
    payload = _json(ebay_request(
        "GET", INVENTORY_URL, params={"limit": limit, "offset": offset}, timeout=45,
    ))
    return payload.get("inventoryItems", [])


def fetch_offer_for_sku(sku: str) -> Optional[Dict[str, Any]]:
    """The first offer attached to a SKU, or None when the SKU was never offered."""
    try:
        payload = _json(ebay_request("GET", OFFER_URL, params={"sku": sku}, timeout=30))
    except Exception:
        return None

    offers = payload.get("offers", [])
    return offers[0] if offers else None


def listing_row(item: Dict[str, Any], offer: Dict[str, Any]) -> Dict[str, Any]:
    product = item.get("product", {}) or {}
    pricing = (offer.get("pricingSummary") or {}).get("price") or {}

    try:
        price = float(pricing.get("value"))
    except (TypeError, ValueError):
        price = None

    return {
        "sku": item.get("sku", ""),
        "offer_id": offer.get("offerId", ""),
        "listing_id": (offer.get("listing") or {}).get("listingId", ""),
        "status": offer.get("status", ""),
        "title": product.get("title", ""),
        "aspects": product.get("aspects", {}) or {},
        "image": (product.get("imageUrls") or [""])[0],
        "category_id": offer.get("categoryId", ""),
        "price": price,
        "currency": pricing.get("currency", "USD"),
        "quantity": offer.get("availableQuantity"),
        "listing_type": "Chinese" if offer.get("format") == "AUCTION" else "FixedPriceItem",
    }


def is_auction(row: Dict[str, Any]) -> bool:
    """Trading calls an auction "Chinese"; the Inventory API calls it "AUCTION"."""
    return str(row.get("listing_type", "")).lower() in {"chinese", "auction"}


def fetch_active_listings(max_listings: Optional[int] = None, workers: int = 6,
                          on_progress=None, include_legacy: bool = True,
                          include_auctions: bool = False) -> List[Dict[str, Any]]:
    """Every active listing, whether or not this app created it.

    The Inventory API only returns listings created through it, so anything listed from
    Seller Hub or the eBay app is invisible there. The Trading API sees them all, so the
    live list comes from Trading and is then enriched with Inventory data (offer id,
    category, item specifics) for the listings that have it - those are the ones this
    app can reprice through the Inventory API.

    The limit counts LISTINGS, not inventory items. Auctions are left out by default:
    they end on their own, and a fixed-price revise fails on them.
    """
    if not include_legacy:
        rows = _inventory_only_listings(max_listings, workers, on_progress)
        return rows if include_auctions else [r for r in rows if not is_auction(r)]

    try:
        base = fetch_all_active_listings()
    except Exception:
        # Trading unavailable: fall back to what the Inventory API can see.
        rows = _inventory_only_listings(max_listings, workers, on_progress)
        return rows if include_auctions else [r for r in rows if not is_auction(r)]

    if not include_auctions:
        base = [row for row in base if not is_auction(row)]

    if max_listings:
        base = base[:max_listings]

    def enrich(row):
        sku = row.get("sku") or ""
        offer = fetch_offer_for_sku(sku) if sku else None

        if offer and offer.get("status") == "PUBLISHED":
            pricing = (offer.get("pricingSummary") or {}).get("price") or {}
            row["offer_id"] = offer.get("offerId", "")
            row["category_id"] = offer.get("categoryId", "") or row.get("category_id", "")
            row["source"] = "inventory"
            try:
                row["price"] = float(pricing.get("value", row["price"]))
            except (TypeError, ValueError):
                pass
        elif not row.get("category_id"):
            # Legacy listing: the category has to be asked for separately.
            row["category_id"] = fetch_item_category(row.get("listing_id", ""))

        return row

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, row in enumerate(pool.map(enrich, base), start=1):
            rows.append(row)
            if on_progress:
                on_progress(len(rows), index)

    return rows


def _inventory_only_listings(max_listings, workers, on_progress) -> List[Dict[str, Any]]:
    """Original path: walk the Inventory API alone."""
    rows: List[Dict[str, Any]] = []
    offset, scanned = 0, 0

    while True:
        page = fetch_inventory_page(offset)
        if not page:
            return rows

        skus = [item.get("sku", "") for item in page]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            offers = list(pool.map(fetch_offer_for_sku, skus))

        for item, offer in zip(page, offers):
            scanned += 1
            if offer:
                row = listing_row(item, offer)
                if row["price"] is not None and row["status"] == "PUBLISHED":
                    row["source"] = "inventory"
                    rows.append(row)
            if on_progress:
                on_progress(len(rows), scanned)
            if max_listings and len(rows) >= max_listings:
                return rows[:max_listings]

        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def update_offer_price(offer_id: str, new_price: float,
                       min_offer_price: Optional[float] = None,
                       keep_existing_ratio: bool = False) -> Dict[str, Any]:
    """Change only the price on an existing offer, leaving everything else intact."""
    offer = _json(ebay_request("GET", "{}/{}".format(OFFER_URL, offer_id), timeout=30))
    if not offer:
        raise RuntimeError("Offer {} could not be read".format(offer_id))

    price_value = "{:.2f}".format(float(new_price))

    pricing = offer.get("pricingSummary") or {}
    old_price = None
    try:
        old_price = float((pricing.get("price") or {}).get("value"))
    except (TypeError, ValueError):
        pass

    pricing.setdefault("price", {})
    pricing["price"]["value"] = price_value
    pricing["price"].setdefault("currency", "USD")
    offer["pricingSummary"] = pricing

    # Move the best-offer floor with the price. Leaving it behind either strands it above
    # the new price (eBay rejects that) or leaves a floor far below a raised price.
    #
    # autoAcceptPrice is never touched: it decides what sells instantly without you
    # seeing the offer, and it is not this tool's business to set that.
    terms = (offer.get("listingPolicies") or {}).get("bestOfferTerms")
    if terms and terms.get("autoDeclinePrice"):
        new_floor = None

        try:
            current_floor = float(terms["autoDeclinePrice"]["value"])
        except (TypeError, ValueError):
            current_floor = None

        if min_offer_price is not None and not keep_existing_ratio:
            # An explicit floor wins: this is how a listing carrying an old policy gets
            # moved onto the current one.
            new_floor = round(float(min_offer_price), 2)
        elif current_floor and old_price:
            # Otherwise keep whatever ratio the seller had set.
            new_floor = round(current_floor / old_price * float(new_price), 2)
        elif min_offer_price is not None:
            new_floor = round(float(min_offer_price), 2)

        if new_floor:
            # eBay rejects a floor at or above the asking price.
            new_floor = min(new_floor, round(float(new_price) * 0.95, 2))
            terms["autoDeclinePrice"]["value"] = "{:.2f}".format(new_floor)

    # Only genuinely read-only fields are stripped. sku/marketplaceId/format are kept
    # because the publish path already sends them on PUT successfully.
    for field in ("offerId", "listing", "status"):
        offer.pop(field, None)

    response = ebay_request(
        "PUT", "{}/{}".format(OFFER_URL, offer_id),
        headers={"Content-Type": "application/json", "Content-Language": "en-US"},
        json=offer, timeout=30,
    )
    return _json(response)
