"""Best Offer handling in comps. Fixtures only - no network, no quota.

eBay now publishes the accepted amount on Best Offer sales, so these are records of what
a buyer actually paid. This pins down how they are counted, reported and cached.

Run: python tools/test_best_offers.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import services.price_sources as sources
import services.soldcomps as sc

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


TITLE = "2025 topps chrome harden refractor"


def sale(price, offer=False, title=TITLE, ended="2026-09-01"):
    return {"listingType": "sold", "soldPrice": str(price), "title": title,
            "bestOfferAccepted": offer, "endedAt": ended}


def main():
    settings.SOLDCOMPS_OFFER_MODE = "include"
    settings.SOLDCOMPS_MIN_COMPS = 2

    print("\n1. Offer sales and Buy It Now sales are counted separately")
    items = [sale(50), sale(48), sale(35, offer=True), sale(32, offer=True)]
    summary = sc.summarise(items, TITLE)
    check("both kinds counted", summary["count"] == 4, summary["count"])
    check("offer sales identified", summary["offer_sales"] == 2)
    check("buy-it-now sales identified", summary["bin_sales"] == 2)
    check("share reported", summary["offer_share"] == 0.5, summary["offer_share"])
    check("offer median separate", summary["offer_median"] == 33.5, summary["offer_median"])
    check("bin median separate", summary["bin_median"] == 49.0, summary["bin_median"])
    check("headline median spans both", summary["price"] == 41.5, summary["price"])

    print("\n2. A card nobody negotiates on reports no offers")
    plain = sc.summarise([sale(10), sale(11), sale(12)], TITLE)
    check("no offer sales", plain["offer_sales"] == 0)
    check("share is zero", plain["offer_share"] == 0.0)
    check("no offer median invented", plain["offer_median"] is None)

    print("\n3. The policy decides which sales set the price")
    settings.SOLDCOMPS_OFFER_MODE = "exclude"
    excluded = sc.summarise(items, TITLE)
    check("offers dropped from the median", excluded["price"] == 49.0, excluded["price"])
    check("count reflects only buy-it-now", excluded["count"] == 2, excluded["count"])
    check("they are still reported", excluded["offer_sales"] == 2)

    settings.SOLDCOMPS_OFFER_MODE = "prefer"
    preferred = sc.summarise(items, TITLE)
    check("offers alone set the price", preferred["price"] == 33.5, preferred["price"])
    check("count reflects only offers", preferred["count"] == 2)

    settings.SOLDCOMPS_OFFER_MODE = "prefer"
    thin = sc.summarise([sale(50), sale(48), sale(35, offer=True)], TITLE)
    check("prefer falls back when offers are too few",
          thin["price"] == 48.0, thin["price"])

    settings.SOLDCOMPS_OFFER_MODE = "include"

    print("\n4. A thin result set cannot hide a different player")
    # Under four comps there are no frequencies to learn from, so the card's own name is
    # required instead. Without it a $999 sale of another player sets the price.
    mixed = [sale(50), sale(48),
             sale(999, offer=True, title="2025 topps chrome different player refractor")]
    loose = sc.summarise(mixed, TITLE)
    check("the near miss survives frequency analysis alone",
          loose["price"] == 50.0, loose["price"])

    guarded = sc.summarise(mixed, TITLE, ["Harden"])
    check("requiring the name drops it", guarded["offer_sales"] == 0, guarded["offer_sales"])
    check("price comes from the real comps", guarded["price"] == 49.0, guarded["price"])
    check("only the genuine sales counted", guarded["count"] == 2, guarded["count"])

    from services.price_sources import essential_name_terms
    check("surname chosen from a full name",
          essential_name_terms({"player_athlete": "James Harden"}) == ["Harden"])
    check("suffixes are not chosen",
          essential_name_terms({"player_athlete": "Marvin Harrison Jr"}) == ["Harrison"])
    check("character names work too",
          essential_name_terms({"character": "Nico Robin"}) == ["Robin"])
    check("a nameless card requires nothing", essential_name_terms({}) == [])

    surname_only = [sale(20, title="topps chrome harden refractor 2025"),
                    sale(22, title="2025 topps chrome harden refractor")]
    check("a comp titled with only the surname is kept",
          sc.summarise(surname_only, TITLE, ["Harden"])["count"] == 2)

    print("\n5. The signal reaches the pricing layer")
    sc.CACHE_PATH = str(Path(tempfile.mkdtemp()) / "cache.json")
    settings.SOLDCOMPS_API_KEY = "sc_test"

    class Response:
        status_code = 200
        def json(self):
            return {"items": items, "hasNextPage": False}

    sc.requests.get = lambda *a, **k: Response()

    quote = sources.soldcomps({"comp_query_override": TITLE})
    check("quote produced", quote is not None)
    check("detail names the offer sales",
          "2 via accepted offers" in quote["detail"], quote["detail"])
    check("offer block attached", quote["offers"]["offer_sales"] == 2, quote.get("offers"))
    check("both medians carried",
          quote["offers"]["offer_median"] == 33.5 and quote["offers"]["bin_median"] == 49.0,
          quote["offers"])

    print("\n6. Raw comps are cached, so policy changes need no new requests")
    calls = []
    sc.requests.get = lambda *a, **k: (calls.append(1), Response())[1]
    sc.CACHE_PATH = str(Path(tempfile.mkdtemp()) / "cache2.json")

    first = sc.fetch_sold(TITLE)
    check("first lookup fetched", len(calls) == 1)
    check("offer data present", first["offer_sales"] == 2)

    settings.SOLDCOMPS_OFFER_MODE = "exclude"
    again = sc.fetch_sold(TITLE)
    check("policy change applied from cache", again["price"] == 49.0, again["price"])
    check("no extra request spent", len(calls) == 1, len(calls))

    stored = json.load(open(sc.CACHE_PATH, encoding="utf-8"))
    entry = list(stored.values())[0]
    check("comps stored on disk", len(entry["items"]) == 4, len(entry.get("items", [])))
    check("best-offer flag stored",
          any(i.get("bestOfferAccepted") for i in entry["items"]))
    check("bulky fields not stored",
          all("itemId" not in i for i in entry["items"]))

    settings.SOLDCOMPS_OFFER_MODE = "include"

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
