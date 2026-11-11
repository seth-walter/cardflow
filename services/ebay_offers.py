from typing import Any, Dict, List, Optional

from config import settings
from services.ebay_auth import ebay_request


def get_fulfillment_policy_id(price_value: float) -> str:
    if price_value <= 20:
        policy_id = settings.EBAY_FULFILLMENT_POLICY_ID_UNDER_20
    else:
        policy_id = settings.EBAY_FULFILLMENT_POLICY_ID_OVER_20

    if not policy_id:
        raise RuntimeError("Missing fulfillment policy ID in .env")

    return policy_id

def get_listing_url(offer_id: str) -> str:
    response = ebay_request(
        "GET",
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    data = response.json()
    return data.get("listing", {}).get("listingWebUrl", "")



def suggested_min_offer(price_value: float) -> float:
    """The Best Offer floor for a price, from MIN_OFFER_RATIO.

    Kept in one place so drafts, publishes and repricing cannot drift apart. Clamped to
    eBay's dollar minimum, and always below the asking price.
    """
    price = float(price_value)
    floor = round(price * settings.MIN_OFFER_RATIO, 2)
    floor = max(floor, settings.MIN_OFFER_ABSOLUTE)
    return min(floor, round(price * 0.95, 2))


def build_best_offer_terms(best_offer_enabled: bool, min_offer_price: Optional[float]) -> Dict[str, Any]:
    if not best_offer_enabled:
        return {"bestOfferEnabled": False}

    if min_offer_price is None:
        raise ValueError("min_offer_price is required when best offers are enabled")

    return {
        "bestOfferEnabled": True,
        "autoDeclinePrice": {
            "value": f"{float(min_offer_price):.2f}",
            "currency": "USD",
        },
    }

FIXED_PRICE = "FIXED_PRICE"
AUCTION = "AUCTION"
AUCTION_DURATIONS = ["DAYS_1", "DAYS_3", "DAYS_5", "DAYS_7", "DAYS_10"]


def build_offer_payload(
    sku: str,
    category_id: str,
    price_value: float,
    best_offer_enabled: bool = True,
    min_offer_price: Optional[float] = None,
    listing_format: str = FIXED_PRICE,
    auction_start_price: Optional[float] = None,
    auction_duration: str = "",
) -> Dict[str, Any]:
    """The offer body for either format.

    For an auction, `price_value` is still the card's market price: it picks the
    shipping policy, because the parcel a card ships in depends on what it is worth,
    not on where the bidding opens.
    """
    if not settings.EBAY_MERCHANT_LOCATION_KEY:
        raise RuntimeError("Missing EBAY_MERCHANT_LOCATION_KEY in .env")

    policies = {
        "paymentPolicyId": settings.EBAY_PAYMENT_POLICY_ID,
        "fulfillmentPolicyId": get_fulfillment_policy_id(price_value),
        "returnPolicyId": settings.EBAY_RETURN_POLICY_ID,
    }

    payload = {
        "sku": sku,
        "marketplaceId": settings.EBAY_MARKETPLACE_ID,
        "categoryId": category_id,
        "merchantLocationKey": settings.EBAY_MERCHANT_LOCATION_KEY,
        "listingPolicies": policies,
    }

    if listing_format == AUCTION:
        start = float(auction_start_price if auction_start_price is not None else price_value)
        duration = auction_duration or settings.AUCTION_DURATION
        payload["format"] = AUCTION
        payload["listingDuration"] = duration if duration in AUCTION_DURATIONS else "DAYS_7"
        payload["pricingSummary"] = {
            "auctionStartPrice": {"value": f"{start:.2f}", "currency": "USD"},
        }
        # Best Offer does not exist on auctions, and an auction is always one item:
        # eBay rejects both the terms and availableQuantity (error 25762).
        return payload

    payload["availableQuantity"] = 1

    if min_offer_price is None:
        min_offer_price = suggested_min_offer(price_value)

    payload["format"] = FIXED_PRICE
    payload["pricingSummary"] = {"price": {"value": f"{price_value:.2f}", "currency": "USD"}}
    policies["bestOfferTerms"] = build_best_offer_terms(best_offer_enabled, min_offer_price)
    return payload


def _offer_request(method: str, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = ebay_request(
        method, url,
        headers={"Content-Type": "application/json", "Content-Language": "en-US"},
        json=payload, timeout=30,
    )
    return response.json() if response.text else {}


def create_draft_offer(
    sku: str,
    category_id: str,
    price_value: float,
    best_offer_enabled: bool = True,
    min_offer_price: Optional[float] = None,
    listing_format: str = FIXED_PRICE,
    auction_start_price: Optional[float] = None,
    auction_duration: str = "",
) -> Dict[str, Any]:
    return _offer_request("POST", "https://api.ebay.com/sell/inventory/v1/offer", build_offer_payload(
        sku, category_id, price_value,
        best_offer_enabled=best_offer_enabled, min_offer_price=min_offer_price,
        listing_format=listing_format, auction_start_price=auction_start_price,
        auction_duration=auction_duration,
    ))


def update_offer_full(
    offer_id: str,
    sku: str,
    category_id: str,
    price_value: float,
    best_offer_enabled: bool = True,
    min_offer_price: Optional[float] = None,
    listing_format: str = FIXED_PRICE,
    auction_start_price: Optional[float] = None,
    auction_duration: str = "",
) -> Dict[str, Any]:
    return _offer_request("PUT", f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}", build_offer_payload(
        sku, category_id, price_value,
        best_offer_enabled=best_offer_enabled, min_offer_price=min_offer_price,
        listing_format=listing_format, auction_start_price=auction_start_price,
        auction_duration=auction_duration,
    ))


def delete_draft_offer(offer_id: str) -> None:
    """Drop an unpublished offer, used when a draft switches format.

    eBay does not let an offer change format in place, so a draft that moves between
    auction and Buy It Now is deleted and created again under the same SKU.
    """
    ebay_request("DELETE", f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}", timeout=30)


def bulk_publish_offers(offer_ids: List[str]) -> Dict[str, Any]:
    if not offer_ids:
        raise ValueError("No offer IDs provided.")
    if len(offer_ids) > 25:
        raise ValueError("eBay bulk publish supports up to 25 offers per call.")

    response = ebay_request(
        "POST",
        "https://api.ebay.com/sell/inventory/v1/bulk_publish_offer",
        headers={"Content-Type": "application/json", "Content-Language": "en-US"},
        json={"requests": [{"offerId": offer_id} for offer_id in offer_ids]},
        timeout=30,
    )
    return response.json()
