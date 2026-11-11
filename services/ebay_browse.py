from statistics import median
from typing import Any, Dict, List

import requests

from config.settings import EBAY_MARKETPLACE_ID
from services.ebay_auth import get_ebay_app_token


def ebay_browse_search(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    token = get_ebay_app_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }

    response = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers=headers,
        params={"q": query, "limit": limit, "sort": "bestMatch"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("itemSummaries", [])


def summarize_comps(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    prices = []
    examples = []

    for item in items:
        raw = item.get("price", {}).get("value")
        try:
            if raw is not None:
                prices.append(float(raw))
        except Exception:
            pass

    for item in items[:5]:
        examples.append({
            "title": item.get("title"),
            "price": item.get("price", {}).get("value"),
            "currency": item.get("price", {}).get("currency"),
            "condition": item.get("condition"),
            "itemWebUrl": item.get("itemWebUrl"),
        })

    if not prices:
        return {"count": 0, "median": None, "low": None, "high": None, "examples": examples}

    return {
        "count": len(prices),
        "median": round(median(prices), 2),
        "low": round(min(prices), 2),
        "high": round(max(prices), 2),
        "examples": examples,
    }
