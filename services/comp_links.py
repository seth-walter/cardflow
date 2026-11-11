"""Deep links for checking a card's price by hand.

No scraping and no subscription: these are the same search pages a person would open,
prefilled with the card's comp query. eBay sold listings come first because that is the
market the card is actually being listed into.
"""

from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

from core.listing_builder import build_comp_query, is_tcg_card
from services.card_aspects import is_non_sport_card

EBAY_SEARCH = "https://www.ebay.com/sch/i.html"
PRICECHARTING_SEARCH = "https://www.pricecharting.com/search-products"
SPORTSCARDSPRO_SEARCH = "https://www.sportscardspro.com/search-products"


def ebay_sold_url(query: str) -> str:
    """eBay search restricted to completed + sold listings."""
    return "{}?{}".format(
        EBAY_SEARCH,
        urlencode({
            "_nkw": query,
            "_sacat": 0,
            "LH_Sold": 1,       # sold only
            "LH_Complete": 1,   # completed listings
            "_ipg": 60,         # more comps per page
        }),
    )


def ebay_active_url(query: str) -> str:
    """The active listings the app's own estimate is currently built from."""
    return "{}?{}".format(EBAY_SEARCH, urlencode({"_nkw": query, "_sacat": 0}))


def price_guide_url(query: str, use_pricecharting: bool) -> Tuple[str, str]:
    """PriceCharting carries TCG and entertainment; SportsCardsPro carries sport."""
    base = PRICECHARTING_SEARCH if use_pricecharting else SPORTSCARDSPRO_SEARCH
    label = "PriceCharting" if use_pricecharting else "SportsCardsPro"
    return label, "{}?{}".format(base, urlencode({"q": query, "type": "prices"}))


def build_comp_links(card_data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """[(label, url)] for manually checking this card's price. Empty if unidentified."""
    query = build_comp_query(card_data)
    if not query.strip():
        return []

    on_pricecharting = is_tcg_card(card_data) or is_non_sport_card(card_data)
    guide_label, guide_url = price_guide_url(query, on_pricecharting)

    return [
        ("eBay sold", ebay_sold_url(query)),
        (guide_label, guide_url),
        ("eBay active", ebay_active_url(query)),
    ]
