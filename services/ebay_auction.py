"""Turning a fixed-price listing into an auction.

eBay does not allow a listing's format to be changed in place, so a conversion is always
end-the-fixed-price then create-the-auction. There is no true "straight convert": the
only real choice is whether the original offer is kept (so it can be republished if the
auction fails) or deleted outright.

Only listings this app manages through the Inventory API can be converted. A listing
created in Seller Hub has no offer here to withdraw or recreate.
"""

from typing import Any, Dict

from config import settings
from services.ebay_auth import ebay_request

OFFER_URL = "https://api.ebay.com/sell/inventory/v1/offer"

DURATIONS = ["DAYS_1", "DAYS_3", "DAYS_5", "DAYS_7", "DAYS_10"]
DEFAULT_DURATION = "DAYS_7"

# How much of the market price to open at, decided by how well understood that price is.
LIQUID_COMPS = 8
THIN_COMPS = 3


def recommend_start_price(price_data: Dict[str, Any], current_price: float) -> Dict[str, Any]:
    """An opening bid, with the reasoning attached.

    A card with many recent sales can open low: bidders turn up and the market finds the
    price. A card with two sales cannot - if one bidder shows up, a low opener IS the
    price you get. So confidence in the comps decides how much room to leave.
    """
    try:
        current = float(current_price or 0)
    except (TypeError, ValueError):
        current = 0.0

    try:
        market = float(price_data.get("estimated_price") or 0) or current
    except (TypeError, ValueError):
        market = current

    count = price_data.get("count")
    basis = price_data.get("basis")
    solid = basis == "sold" and not price_data.get("needs_review")

    if solid and isinstance(count, int) and count >= LIQUID_COMPS:
        ratio = 0.25
        why = "{} recent sales, so bidding should find the price".format(count)
    elif solid and isinstance(count, int) and count >= THIN_COMPS:
        ratio = 0.50
        why = "{} recent sales - half leaves room without giving it away".format(count)
    elif basis == "sold":
        ratio = 0.80
        why = "few completed sales, so open near the market price"
    else:
        ratio = 0.90
        why = "no sold comps - open near your current price rather than gamble"

    start = price_point(market * ratio)
    if start < settings.AUCTION_MIN_START:
        start = round(settings.AUCTION_MIN_START, 2)
        why += "; raised to your ${:.2f} minimum".format(start)

    return {
        "start_price": start,
        "market_price": round(market, 2),
        "ratio": ratio,
        "reason": why,
        "confident": solid,
        "net_at_start": net_after_fees(start),
    }


def price_point(value: float) -> float:
    """Round an opening bid to the nearest .99 ending: 3.00 -> 2.99, 3.75 -> 3.99."""
    whole = int(float(value) + 0.5)
    return 0.99 if whole < 1 else round(whole - 0.01, 2) if whole > 1 else 0.99


def net_after_fees(sale_price: float) -> float:
    """What a sale at this price leaves after eBay's final value fee."""
    fixed = settings.EBAY_FEE_FIXED if sale_price > 10 else max(0.0, settings.EBAY_FEE_FIXED - 0.10)
    return round(sale_price * (1 - settings.EBAY_FEE_RATE) - fixed, 2)


def choose_listing_format(price_data: Dict[str, Any], price: float) -> Dict[str, Any]:
    """Auction or Buy It Now for a new draft, with the opening bid when it is an auction.

    Cheap cards sit for months at a fixed price and cost attention every time they get
    repriced; an auction clears them in a week at whatever the market pays. Above the
    threshold a wrong opening bid costs real money, so those stay Buy It Now with offers.
    """
    threshold = settings.AUCTION_UNDER
    try:
        value = float(price or 0)
    except (TypeError, ValueError):
        value = 0.0

    if threshold <= 0 or value <= 0 or value >= threshold:
        return {"format": "FIXED_PRICE", "start_price": None,
                "reason": "priced at ${:.2f}, at or above the ${:.0f} auction line".format(
                    value, threshold) if threshold > 0 else "auctions are turned off"}

    if (price_data or {}).get("needs_review"):
        # An auction sells at whatever the opening bid is when one bidder turns up, so a
        # price nobody trusts yet - possibly a $1,000 card priced as its $1 base - must
        # not default to one. Fixed price waits for the seller to look.
        return {"format": "FIXED_PRICE", "start_price": None,
                "reason": "price needs review, so not defaulted to auction"}

    recommendation = recommend_start_price(price_data or {}, value)
    recommendation["format"] = "AUCTION"
    recommendation["duration"] = settings.AUCTION_DURATION
    return recommendation


def read_offer(offer_id: str) -> Dict[str, Any]:
    response = ebay_request("GET", "{}/{}".format(OFFER_URL, offer_id), timeout=30)
    return response.json() if response.text else {}


def withdraw_offer(offer_id: str) -> Dict[str, Any]:
    """End the live listing but keep the offer, so nothing is lost if the next step fails."""
    response = ebay_request(
        "POST", "{}/{}/withdraw".format(OFFER_URL, offer_id),
        headers={"Content-Type": "application/json"}, timeout=30,
    )
    return response.json() if response.text else {}


def delete_offer(offer_id: str) -> None:
    """Remove the offer entirely. Only for the delete-and-relist path."""
    ebay_request("DELETE", "{}/{}".format(OFFER_URL, offer_id), timeout=30)


def create_auction_offer(sku: str, category_id: str, start_price: float,
                         duration: str = DEFAULT_DURATION,
                         listing_description: str = "") -> Dict[str, Any]:
    """A new auction offer for a SKU that already carries images and item specifics."""
    if duration not in DURATIONS:
        duration = DEFAULT_DURATION

    price = float(start_price)

    payload = {
        "sku": sku,
        "marketplaceId": settings.EBAY_MARKETPLACE_ID,
        "format": "AUCTION",
        # No availableQuantity: eBay rejects it on auctions (error 25762).
        "categoryId": category_id,
        "listingDuration": duration,
        "merchantLocationKey": settings.EBAY_MERCHANT_LOCATION_KEY,
        "pricingSummary": {
            "auctionStartPrice": {"value": "{:.2f}".format(price), "currency": "USD"},
        },
        "listingPolicies": {
            "paymentPolicyId": settings.EBAY_PAYMENT_POLICY_ID,
            "fulfillmentPolicyId": (
                settings.EBAY_FULFILLMENT_POLICY_ID_OVER_20 if price >= 20
                else settings.EBAY_FULFILLMENT_POLICY_ID_UNDER_20
            ),
            "returnPolicyId": settings.EBAY_RETURN_POLICY_ID,
        },
    }

    if listing_description:
        payload["listingDescription"] = listing_description

    response = ebay_request(
        "POST", OFFER_URL,
        headers={"Content-Type": "application/json", "Content-Language": "en-US"},
        json=payload, timeout=30,
    )
    return response.json() if response.text else {}


def publish_offer(offer_id: str) -> Dict[str, Any]:
    response = ebay_request(
        "POST", "{}/{}/publish".format(OFFER_URL, offer_id),
        headers={"Content-Type": "application/json"}, timeout=45,
    )
    return response.json() if response.text else {}


def convert_to_auction(offer_id: str, start_price: float,
                       duration: str = DEFAULT_DURATION,
                       delete_original: bool = False) -> Dict[str, Any]:
    """End a fixed-price listing and put the same card up as an auction.

    Ordering matters: the fixed-price listing is ended first, because eBay will not run
    two live listings for one SKU. If the auction then fails to publish, the original
    offer is still there to republish - unless it was deliberately deleted.
    """
    original = read_offer(offer_id)
    if not original:
        raise RuntimeError("Offer {} could not be read".format(offer_id))

    sku = original.get("sku", "")
    category_id = original.get("categoryId", "")
    description = original.get("listingDescription", "")

    if not sku or not category_id:
        raise RuntimeError("Offer {} has no sku or category to rebuild from".format(offer_id))

    steps = ["read the original offer"]

    withdraw_offer(offer_id)
    steps.append("ended the fixed-price listing")

    if delete_original:
        delete_offer(offer_id)
        steps.append("deleted the original offer")

    created = create_auction_offer(sku, category_id, start_price, duration, description)
    auction_offer_id = created.get("offerId")

    if not auction_offer_id:
        raise RuntimeError(
            "The fixed-price listing was ended but the auction offer was not created. "
            "The card is not listed right now. eBay said: {}".format(created)
        )

    steps.append("created auction offer " + str(auction_offer_id))

    published = publish_offer(auction_offer_id)
    listing_id = published.get("listingId", "")
    steps.append("published auction listing " + str(listing_id))

    return {
        "sku": sku,
        "original_offer_id": offer_id,
        "auction_offer_id": auction_offer_id,
        "listing_id": listing_id,
        "start_price": round(float(start_price), 2),
        "duration": duration,
        "original_deleted": delete_original,
        "steps": steps,
    }
