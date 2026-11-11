"""The legacy Trading API, used for listings the Inventory API cannot see.

Anything listed from Seller Hub or the eBay app lives outside the Inventory model, so
`sell/inventory` simply does not return it. Trading's GetMyeBaySelling sees every active
listing however it was created, and ReviseFixedPriceItem can change the price of one.
"""

import re
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import requests

from services.ebay_auth import refresh_ebay_user_token

TRADING_URL = "https://api.ebay.com/ws/api.dll"
COMPATIBILITY_LEVEL = "1155"
SITE_ID = "0"          # US
PAGE_SIZE = 200


def _call(call_name: str, body: str, token: Optional[str] = None) -> str:
    token = token or refresh_ebay_user_token()
    response = requests.post(
        TRADING_URL,
        data=body.encode("utf-8"),
        headers={
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-SITEID": SITE_ID,
            "X-EBAY-API-COMPATIBILITY-LEVEL": COMPATIBILITY_LEVEL,
            "X-EBAY-API-IAF-TOKEN": token,
            "Content-Type": "text/xml",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.text


def _tag(xml: str, name: str) -> str:
    match = re.search(r"<{0}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{0}>".format(name), xml, re.S)
    return match.group(1).strip() if match else ""


def _blocks(xml: str, name: str) -> List[str]:
    return re.findall(r"<{0}>(.*?)</{0}>".format(name), xml, re.S)


def _ack_ok(xml: str) -> bool:
    return _tag(xml, "Ack") in ("Success", "Warning")


def _error(xml: str) -> str:
    return _tag(xml, "LongMessage") or _tag(xml, "ShortMessage") or "unknown Trading API error"


def parse_active_item(block: str) -> Dict[str, Any]:
    """One ActiveList item, shaped like an Inventory API row so both can be mixed."""
    price_text = ""
    price_match = re.search(r'<CurrentPrice[^>]*>([\d.]+)</CurrentPrice>', block)
    if price_match:
        price_text = price_match.group(1)

    try:
        price = float(price_text)
    except ValueError:
        price = None

    listing_type = _tag(block, "ListingType")

    return {
        "sku": _tag(block, "SKU"),
        "offer_id": "",                       # legacy listings have no Inventory offer
        "listing_id": _tag(block, "ItemID"),
        "status": "PUBLISHED",
        "title": _tag(block, "Title"),
        "aspects": {},
        "image": _tag(block, "GalleryURL"),
        "category_id": _tag(block, "PrimaryCategoryID") or _tag(block, "CategoryID"),
        "price": price,
        "currency": "USD",
        "quantity": _tag(block, "Quantity"),
        "listing_type": listing_type or "FixedPriceItem",
        "start_time": _tag(block, "StartTime"),
        "watch_count": int(_tag(block, "WatchCount") or 0),
        "time_left": _tag(block, "TimeLeft"),
        "view_url": _tag(block, "ViewItemURL"),
        "source": "trading",
    }


def fetch_all_active_listings(on_progress=None) -> List[Dict[str, Any]]:
    """Every active listing on the account, whatever created it."""
    token = refresh_ebay_user_token()
    rows: List[Dict[str, Any]] = []
    page = 1

    while True:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<ActiveList><Include>true</Include>"
            "<Pagination><EntriesPerPage>{}</EntriesPerPage>"
            "<PageNumber>{}</PageNumber></Pagination>"
            "</ActiveList></GetMyeBaySellingRequest>"
        ).format(PAGE_SIZE, page)

        xml = _call("GetMyeBaySelling", body, token)
        if not _ack_ok(xml):
            raise RuntimeError("GetMyeBaySelling failed: " + _error(xml))

        active = re.search(r"<ActiveList>(.*?)</ActiveList>", xml, re.S)
        blocks = _blocks(active.group(1), "Item") if active else []

        for block in blocks:
            row = parse_active_item(block)
            if row["listing_id"]:
                rows.append(row)

        if on_progress:
            on_progress(len(rows))

        total_pages = _tag(xml, "TotalNumberOfPages")
        try:
            if page >= int(total_pages or 1):
                return rows
        except ValueError:
            return rows

        page += 1


_category_cache: Dict[str, str] = {}


def fetch_item_category(item_id: str) -> str:
    """Primary category of one listing. ActiveList does not carry it."""
    if not item_id:
        return ""
    if item_id in _category_cache:
        return _category_cache[item_id]

    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<ItemID>{}</ItemID>"
        "<OutputSelector>Item.PrimaryCategory.CategoryID</OutputSelector>"
        "</GetItemRequest>"
    ).format(escape(str(item_id)))

    try:
        xml = _call("GetItem", body)
    except Exception:
        return ""

    category = _tag(xml, "CategoryID") if _ack_ok(xml) else ""
    _category_cache[item_id] = category
    return category


def revise_listing_price(item_id: str, new_price: float) -> Dict[str, Any]:
    """Change the price of a listing the Inventory API cannot touch."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Item><ItemID>{}</ItemID>"
        '<StartPrice currencyID="USD">{:.2f}</StartPrice>'
        "</Item></ReviseFixedPriceItemRequest>"
    ).format(escape(str(item_id)), float(new_price))

    xml = _call("ReviseFixedPriceItem", body)
    if not _ack_ok(xml):
        raise RuntimeError("ReviseFixedPriceItem failed: " + _error(xml))

    return {"item_id": item_id, "price": round(float(new_price), 2),
            "fees_xml_present": "<Fees>" in xml}
