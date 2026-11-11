from typing import Any, Dict, List

import requests

from config.settings import EBAY_MARKETPLACE_ID
from services.ebay_auth import get_ebay_app_token


def get_default_category_tree_id() -> str:
    token = get_ebay_app_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }

    response = requests.get(
        "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id",
        headers=headers,
        params={"marketplace_id": EBAY_MARKETPLACE_ID},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["categoryTreeId"]


def get_category_suggestions(query: str) -> List[Dict[str, Any]]:
    token = get_ebay_app_token()
    tree_id = get_default_category_tree_id()

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }

    response = requests.get(
        f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
        headers=headers,
        params={"q": query},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("categorySuggestions", [])
