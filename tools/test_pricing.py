"""Pricing layer tests: source ranking, confidence, and the review gate. No network.

Every external call is stubbed, so this runs free and offline.

Run: python tools/test_pricing.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import services.price_sources as sources
import services.pricing_service as pricing

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


SPORTS = {"card_category": "sports", "year": "2025", "brand": "Topps",
          "set": "Topps Chrome", "player_athlete": "James Harden", "sport": "Basketball",
          "card_number": "SE-7"}
POKEMON = {"card_category": "pokemon", "game": "Pokemon TCG", "name": "Charizard ex",
           "set": "151", "card_number": "199/165", "finish": "Holofoil",
           "tcgplayer_prices": {"holofoil": {"market": 378.16, "low": 327.5, "high": 500.0}},
           "tcgplayer_url": "https://tcgplayer.com/x"}


def stub_active(count=8, median_price=30.0, low=24.0, high=40.0):
    def _stub(query, limit=12):
        return [{"price": {"value": str(median_price)}} for _ in range(count)]

    def _summary(items):
        return {"count": count, "median": median_price, "low": low, "high": high,
                "examples": []}

    sources.ebay_browse_search = _stub
    sources.summarize_comps = _summary


def main():
    # Keys live in .env, so the networked sources must be disabled or this suite calls
    # them for real and spends request quota.
    settings.SOLDCOMPS_API_KEY = ""
    settings.PRICECHARTING_TOKEN = ""
    sources.OWN_SALES_PATH = str(Path(tempfile.mkdtemp()) / "no_sales.json")

    print("\n1. A sold source outranks asking prices")
    stub_active()
    result = pricing.get_price_estimate(POKEMON)
    check("pokemon priced from TCGplayer market",
          result["source"] == "tcgplayer_market", result["source"])
    check("marked as sold-based", result["basis"] == "sold", result["basis"])
    check("uses the market price", result["estimated_price"] == 378.16,
          result["estimated_price"])
    check("high confidence", result["confidence"] == "high", result["confidence"])
    check("not flagged for review", result["needs_review"] is False)

    print("\n2. Sports falls back to asking prices, labelled honestly")
    result = pricing.get_price_estimate(SPORTS)
    check("priced from active listings", result["source"] == "ebay_active", result["source"])
    check("basis is ask, not sold", result["basis"] == "ask", result["basis"])
    check("confidence capped at medium", result["confidence"] == "medium", result["confidence"])
    check("not flagged when comps are plentiful and tight", result["needs_review"] is False,
          result["review_reasons"])
    check("detail explains the source", "active listing" in result["detail"], result["detail"])

    print("\n3. Thin comps are flagged")
    stub_active(count=2, median_price=30.0, low=28.0, high=32.0)
    thin = pricing.get_price_estimate(SPORTS)
    check("flagged with only 2 comps", thin["needs_review"] is True)
    check("reason names the comp count",
          any("2 comp" in r for r in thin["review_reasons"]), thin["review_reasons"])

    print("\n4. Wildly scattered comps are flagged")
    stub_active(count=9, median_price=100.0, low=20.0, high=900.0)
    wild = pricing.get_price_estimate(SPORTS)
    check("flagged on a 45x spread", wild["needs_review"] is True)
    check("reason names the spread",
          any("range" in r for r in wild["review_reasons"]), wild["review_reasons"])
    check("spread ratio recorded", wild["spread_ratio"] == 45.0, wild["spread_ratio"])

    print("\n5. No comps at all is a flagged fallback, never a silent number")
    def _empty(query, limit=12):
        return []

    def _empty_summary(items):
        return {"count": 0, "median": None, "low": None, "high": None, "examples": []}

    sources.ebay_browse_search = _empty
    sources.summarize_comps = _empty_summary
    nothing = pricing.get_price_estimate(SPORTS)
    check("falls back to 9.99", nothing["estimated_price"] == 9.99, nothing["estimated_price"])
    check("source says fallback", nothing["source"] == "fallback")
    check("flagged for review", nothing["needs_review"] is True)
    check("basis is none", nothing["basis"] == "none")

    print("\n6. Your own past sales outrank every other source")
    store = Path(tempfile.mkdtemp()) / "own_sales.json"
    store.write_text(json.dumps({"sales": [
        {"title": "2025 Topps Chrome James Harden SE-7 Clippers", "price": 22.0},
        {"title": "2025 Topps Chrome James Harden SE-7", "price": 26.0},
        {"title": "unrelated 1990 Score Football card", "price": 3.0},
    ]}), encoding="utf-8")
    original_path = sources.OWN_SALES_PATH
    sources.OWN_SALES_PATH = str(store)

    stub_active(count=8, median_price=99.0, low=80.0, high=120.0)
    own = pricing.get_price_estimate(SPORTS)
    check("priced from own sales", own["source"] == "own_sales", own["source"])
    check("uses the median of matching sales", own["estimated_price"] == 24.0,
          own["estimated_price"])
    check("counts only real matches", own["count"] == 2, own["count"])
    check("ignores the unrelated sale", own["high"] == 26.0, own["high"])
    check("beats the inflated asking price", own["estimated_price"] < 99.0)

    sources.OWN_SALES_PATH = original_path

    print("\n7. PriceCharting stays out of the way until a token exists")
    check("no token means no quote", sources.pricecharting(SPORTS) is None)
    check("token flag is off by default", settings.PRICECHARTING_TOKEN == "",
          repr(settings.PRICECHARTING_TOKEN))

    print("\n8. Graded cards ask for the matching grade field")
    check("PSA 10 -> manual-only-price",
          sources._pricecharting_field({"is_graded": True, "grade": "10"}) == "manual-only-price")
    check("PSA 9 -> graded-price",
          sources._pricecharting_field({"is_graded": True, "grade": "9"}) == "graded-price")
    check("raw -> loose-price",
          sources._pricecharting_field({"is_graded": False}) == "loose-price")


    print("\n9. Seller notes in a title never reach the comp search")
    from core.listing_builder import build_comp_query
    from utils.text_utils import clean_comp_query, damage_terms

    noisy = "2025 Topps Chrome Jonathan Taylor #LL-7 Lightning Lads READ DESC"
    cleaned = build_comp_query({"comp_query_override": noisy})
    check("READ DESC removed", "read" not in cleaned and "desc" not in cleaned, cleaned)
    check("the card itself survives",
          all(word in cleaned for word in ["jonathan", "taylor", "ll-7", "lightning"]), cleaned)

    check("free shipping stripped",
          "shipping" not in clean_comp_query("1998 Topps Kobe Bryant #68 FREE SHIPPING"))
    check("attention grabbers stripped",
          "@" not in clean_comp_query("1998 Topps Kobe Bryant #68 L@@K"))
    check("emoji stripped",
          clean_comp_query("2025 Topps Chrome Maxey SSP fire") == "2025 topps chrome maxey ssp fire")

    print("\n10. Card details are never mistaken for noise")
    for keeper in ["2025-26 topps chrome update ray allen true gold /50",
                   "2025 topps chrome maxey paradox ssp #px-16",
                   "1998 topps kobe bryant #68 refractor psa 10"]:
        check("preserved: " + keeper[:40], clean_comp_query(keeper) == keeper,
              clean_comp_query(keeper))

    print("\n11. Damage is stripped from the search but reported")
    damaged = "2024 Prizm Caleb Williams RC damaged corner"
    check("damage word removed from query",
          "damaged" not in build_comp_query({"comp_query_override": damaged}))
    check("damage detected", damage_terms(damaged) == ["damaged"], damage_terms(damaged))

    settings.SOLDCOMPS_API_KEY = ""
    stub_active(count=9, median_price=30.0, low=28.0, high=33.0)
    result = pricing.get_price_estimate({"comp_query_override": damaged})
    check("damaged card is flagged", result["needs_review"] is True, result["review_reasons"])
    check("reason explains why",
          any("undamaged" in reason for reason in result["review_reasons"]),
          result["review_reasons"])

    clean_result = pricing.get_price_estimate(
        {"comp_query_override": "2024 Prizm Caleb Williams RC"})
    check("an undamaged card is not flagged", clean_result["needs_review"] is False,
          clean_result["review_reasons"])

    print("\n12. A too-specific query falls back to a looser one")
    # Sold comps are searched on the full listing title, which is right for completed
    # sales but often matches nothing currently listed. Without a retry the card drops
    # to the default price instead of an honest estimate.
    from services.price_sources import loosen_query

    card = {"player_athlete": "Jonathan Taylor", "year": "2025", "brand": "Topps Chrome",
            "card_number": "LL-7", "card_category": "sports",
            "comp_query_override": "2025 topps chrome jonathan taylor #ll-7 lightning lads"}

    check("the loose query drops the title override",
          "lightning" not in loosen_query(card).lower(), loosen_query(card))
    check("and still describes the card",
          all(word in loosen_query(card) for word in ["2025", "Jonathan Taylor", "LL-7"]),
          loosen_query(card))

    searched = []

    def picky_search(query, limit=12):
        searched.append(query)
        # Only the looser wording matches anything, as on the live site.
        return [] if "lightning" in query.lower() else [{"price": {"value": "28.49"}}] * 12

    def summarise_them(items):
        return {"count": len(items), "median": 28.49 if items else None,
                "low": 24.0 if items else None, "high": 33.0 if items else None,
                "examples": []}

    sources.ebay_browse_search = picky_search
    sources.summarize_comps = summarise_them

    result = pricing.get_price_estimate(card)
    check("both queries were tried", len(searched) == 2, searched)
    check("priced from the looser search", result["source"] == "ebay_active", result["source"])
    check("not the default price", result["estimated_price"] == 28.49,
          result["estimated_price"])

    searched[:] = []
    plain = {"player_athlete": "Jonathan Taylor", "year": "2025", "brand": "Topps Chrome",
             "card_number": "LL-7", "card_category": "sports"}
    pricing.get_price_estimate(plain)
    check("no retry when the first search works", len(searched) == 1, searched)

    def nothing_matches(query, limit=12):
        searched.append(query)
        return []

    sources.ebay_browse_search = nothing_matches
    searched[:] = []
    empty = pricing.get_price_estimate(card)
    check("a card with nothing listed still falls back to 9.99",
          empty["estimated_price"] == 9.99, empty["estimated_price"])
    check("and is flagged", empty["needs_review"] is True)
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
