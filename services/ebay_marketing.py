"""Promoted Listings (general, cost-per-sale) through eBay's Marketing API.

Everything CardFlow promotes goes into one campaign of its own, so its ads never touch a
campaign set up by hand in Seller Hub. Cost-per-sale means the ad rate is charged only
when a buyer clicks the ad and buys within 30 days; nothing is paid for impressions.

Needs the sell.marketing scope: run tools/ebay_consent.py once to grant it.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from config import settings
from services.ebay_auth import ebay_request

MARKETING_URL = "https://api.ebay.com/sell/marketing/v1"
CAMPAIGN_NAME = "CardFlow Promoted"
SCOPE = "https://api.ebay.com/oauth/api_scope/sell.marketing"

# eBay's bounds for a general campaign's ad rate.
MIN_AD_RATE = 2.0
MAX_AD_RATE = 100.0
BULK_LIMIT = 500

_campaign_id: Optional[str] = None


def has_marketing_scope() -> bool:
    return SCOPE in settings.EBAY_SCOPES


def clamp_rate(rate: float) -> float:
    return round(min(MAX_AD_RATE, max(MIN_AD_RATE, float(rate))), 1)


def _json(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json() if response.text else {}
    except ValueError:
        return {}


def find_campaign() -> Optional[Dict[str, Any]]:
    try:
        response = ebay_request("GET", MARKETING_URL + "/ad_campaign/get_campaign_by_name",
                                params={"campaign_name": CAMPAIGN_NAME}, timeout=30)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None
        raise
    return _json(response) or None


def campaign_id() -> str:
    """The CardFlow campaign's id, creating the campaign the first time."""
    global _campaign_id
    if _campaign_id:
        return _campaign_id

    found = find_campaign()
    if found and found.get("campaignStatus") not in ("ENDED", "ENDING_SOON"):
        _campaign_id = found["campaignId"]
        return _campaign_id

    response = ebay_request(
        "POST", MARKETING_URL + "/ad_campaign",
        headers={"Content-Type": "application/json"},
        json={
            "campaignName": CAMPAIGN_NAME,
            "marketplaceId": settings.EBAY_MARKETPLACE_ID,
            "startDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
        },
        timeout=30,
    )
    # eBay answers 201 with the new id only in the Location header.
    location = response.headers.get("Location", "")
    _campaign_id = location.rstrip("/").rsplit("/", 1)[-1] or (find_campaign() or {}).get("campaignId")
    if not _campaign_id:
        raise RuntimeError("eBay created the campaign but did not say its id")
    return _campaign_id


def _bulk(path: str, requests_body: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One bulk call per 500 listings; the result keyed by listing id."""
    results: Dict[str, Dict[str, Any]] = {}
    url = "{}/ad_campaign/{}/{}".format(MARKETING_URL, campaign_id(), path)
    for start in range(0, len(requests_body), BULK_LIMIT):
        chunk = requests_body[start:start + BULK_LIMIT]
        try:
            response = ebay_request("POST", url, headers={"Content-Type": "application/json"},
                                    json={"requests": chunk}, timeout=60)
            body = _json(response)
        except requests.HTTPError as error:
            # A 207 is a partial success and arrives normally; a 400 can still carry the
            # per-listing reasons, which are worth more than the bare status.
            body = _json(error.response) if error.response is not None else {}
            if not body.get("responses"):
                raise
        for item in body.get("responses") or []:
            results[str(item.get("listingId", ""))] = item
    return results


def _error_text(item: Dict[str, Any]) -> str:
    errors = item.get("errors") or []
    return "; ".join("{}: {}".format(e.get("errorId", "?"), e.get("longMessage") or e.get("message", ""))
                     for e in errors) or "status {}".format(item.get("statusCode"))


def promote_listings(rates: Dict[str, float]) -> Dict[str, Dict[str, Any]]:
    """Promote each listing at its ad rate (percent). Already-promoted ones are re-rated.

    Returns {listing_id: {"ok": bool, "rate": float, "error": str}}.
    """
    wanted = {str(lid): clamp_rate(rate) for lid, rate in rates.items() if lid}
    body = [{"listingId": lid, "bidPercentage": str(rate)} for lid, rate in wanted.items()]
    created = _bulk("bulk_create_ads_by_listing_id", body)

    outcome: Dict[str, Dict[str, Any]] = {}
    rerate = []
    for lid, rate in wanted.items():
        item = created.get(lid, {})
        if item.get("statusCode") in (200, 201):
            outcome[lid] = {"ok": True, "rate": rate, "error": ""}
        elif "already" in _error_text(item).lower():
            rerate.append({"listingId": lid, "bidPercentage": str(rate)})
        else:
            outcome[lid] = {"ok": False, "rate": rate, "error": _error_text(item)}

    if rerate:
        updated = _bulk("bulk_update_ads_bid_by_listing_id", rerate)
        for request in rerate:
            lid = request["listingId"]
            item = updated.get(lid, {})
            ok = item.get("statusCode") in (200, 204)
            outcome[lid] = {"ok": ok, "rate": wanted[lid], "error": "" if ok else _error_text(item)}
    return outcome


def stop_promoting(listing_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    body = [{"listingId": str(lid)} for lid in listing_ids if lid]
    deleted = _bulk("bulk_delete_ads_by_listing_id", body)
    return {request["listingId"]: {
        "ok": deleted.get(request["listingId"], {}).get("statusCode") in (200, 204),
        "error": _error_text(deleted.get(request["listingId"], {}))} for request in body}


def current_ads() -> Dict[str, float]:
    """{listing_id: ad rate} for everything in the CardFlow campaign."""
    if not find_campaign():
        return {}
    ads: Dict[str, float] = {}
    url = "{}/ad_campaign/{}/ad".format(MARKETING_URL, campaign_id())
    offset = 0
    while True:
        body = _json(ebay_request("GET", url, params={"limit": 500, "offset": offset}, timeout=30))
        for ad in body.get("ads") or []:
            try:
                ads[str(ad.get("listingId"))] = float(ad.get("bidPercentage") or 0)
            except (TypeError, ValueError):
                pass
        offset += 500
        if offset >= int(body.get("total") or 0):
            return ads
