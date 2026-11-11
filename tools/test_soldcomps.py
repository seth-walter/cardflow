"""SoldComps provider tests. Every HTTP call is stubbed - no key or quota needed.

Run: python tools/test_soldcomps.py
"""

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import services.soldcomps as sc
import services.price_sources as sources

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def sold_item(price, ended="2026-08-01", title="card"):
    return {"listingType": "sold", "soldPrice": str(price), "endedAt": ended,
            "title": title, "url": "u"}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def main():
    # Isolate the cache so a real one is never touched.
    sc.CACHE_PATH = str(Path(tempfile.mkdtemp()) / "cache.json")
    settings.SOLDCOMPS_API_KEY = "sc_test_key"

    print("\n1. Summarising real sold listings")
    items = [sold_item(p) for p in [10, 12, 14, 16, 18, 100]]
    summary = sc.summarise(items)
    # median of [10,12,14,16,18,100] averages the middle pair: (14+16)/2
    check("median used as the price", summary["price"] == 15.0, summary["price"])
    check("median ignores the 100 outlier", summary["price"] < 20)
    check("counts every sale", summary["count"] == 6, summary["count"])
    check("range uses the middle half, not the outlier",
          summary["high"] < 100, summary["high"])
    check("true ceiling still recorded", summary["ceiling"] == 100.0)
    check("latest sale date kept", summary["latest_sale"] == "2026-08-01")

    print("\n2. Junk and empty data are rejected, never guessed")
    check("no items -> nothing", sc.summarise([]) is None)
    check("unparseable prices -> nothing",
          sc.summarise([{"listingType": "sold", "soldPrice": "n/a"}]) is None)
    check("zero prices ignored",
          sc.summarise([sold_item(0), sold_item(0)]) is None)
    check("active listings excluded",
          sc.summarise([{"listingType": "active", "soldPrice": "50"}] * 3) is None)

    print("\n3. Currency formatting from the API is handled")
    check("dollar signs and commas parsed",
          sc.summarise([{"listingType": "sold", "soldPrice": "$1,250.00"}] * 3)["price"] == 1250.0)

    print("\n4. The API call asks for completed sales in a date window")
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        title = params["keyword"]
        return FakeResponse({"items": [sold_item(p, title=title) for p in [20, 22, 24]]})

    sc.requests.get = fake_get
    result = sc.fetch_sold("2025 Topps Chrome James Harden SE-7")

    check("hits the documented endpoint", captured["url"].endswith("/v1/scrape"), captured["url"])
    check("bearer auth sent",
          captured["headers"]["Authorization"] == "Bearer sc_test_key",
          captured["headers"])
    check("asks for sold only", captured["params"]["sold"] == "true")
    check("date window applied", "soldAfter" in captured["params"], captured["params"])
    check("sorted by most recent", captured["params"]["sortOrder"] == "endedRecently")
    check("summary returned", result["count"] == 3 and result["price"] == 22.0, result)

    print("\n5. Results are cached so the request quota is not burned twice")
    calls = {"n": 0}

    def counting_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        title = params["keyword"]
        return FakeResponse({"items": [sold_item(p, title=title) for p in [30, 31, 32]]})

    sc.requests.get = counting_get
    sc.fetch_sold("cached query example")
    sc.fetch_sold("cached query example")
    sc.fetch_sold("cached query example")
    check("three lookups cost one request", calls["n"] == 1, calls["n"])
    check("cache reports the entry", sc.cache_stats()["fresh"] >= 1, sc.cache_stats())

    print("\n6. Quota and errors fail quiet, so pricing falls through")
    sc.requests.get = lambda *a, **k: FakeResponse({"code": "quota_exceeded"}, status=429)
    check("429 returns nothing", sc.fetch_sold("brand new query one") is None)
    sc.requests.get = lambda *a, **k: FakeResponse({}, status=500)
    check("server error returns nothing", sc.fetch_sold("brand new query two") is None)

    def boom(*a, **k):
        raise RuntimeError("network down")
    sc.requests.get = boom
    check("network failure returns nothing", sc.fetch_sold("brand new query three") is None)

    print("\n7. Wired into pricing ahead of asking prices")
    check("in the sold source list", sources.soldcomps in sources.SOLD_SOURCES)
    order = [f.__name__ for f in sources.SOLD_SOURCES]
    check("free TCGplayer tried before paid quota",
          order.index("tcgplayer_market") < order.index("soldcomps"), order)
    check("tried before asking prices", "soldcomps" in order)

    settings.SOLDCOMPS_API_KEY = ""
    check("no key means the source is skipped",
          sources.soldcomps({"year": "2025", "brand": "Topps", "player_athlete": "X"}) is None)


    print("\n8. Extra pages are fetched only when a card is short of comps")
    from config import settings as cfg
    cfg.SOLDCOMPS_API_KEY = "sc_test_key"   # section 7 deliberately cleared it
    calls = {"pages": []}

    def paging_get(url, headers=None, params=None, timeout=None):
        calls["pages"].append(params["page"])
        return FakeResponse({"items": [sold_item(50, title=params["keyword"])] * 3, "hasNextPage": True})

    sc.requests.get = paging_get
    cfg.SOLDCOMPS_MAX_PAGES = 3
    cfg.SOLDCOMPS_ENOUGH_COMPS = 8
    sc.fetch_sold("paging query alpha")
    check("kept paging while comps were thin", calls["pages"] == [1, 2, 3], calls["pages"])

    calls["pages"] = []

    def rich_get(url, headers=None, params=None, timeout=None):
        calls["pages"].append(params["page"])
        return FakeResponse({"items": [sold_item(50, title=params["keyword"])] * 20, "hasNextPage": True})

    sc.requests.get = rich_get
    sc.fetch_sold("paging query beta")
    check("stopped after one page once comps were plentiful",
          calls["pages"] == [1], calls["pages"])

    calls["pages"] = []

    def last_page_get(url, headers=None, params=None, timeout=None):
        calls["pages"].append(params["page"])
        return FakeResponse({"items": [sold_item(50, title=params["keyword"])] * 2, "hasNextPage": False})

    sc.requests.get = last_page_get
    sc.fetch_sold("paging query gamma")
    check("respects hasNextPage=false", calls["pages"] == [1], calls["pages"])

    cfg.SOLDCOMPS_MAX_PAGES = 1
    calls["pages"] = []
    sc.requests.get = paging_get
    sc.fetch_sold("paging query delta")
    check("default of one page costs one request", calls["pages"] == [1], calls["pages"])

    source = io.open("services/soldcomps.py", encoding="utf-8").read()
    check("no eBay cookie header is ever sent", "X-eBay-Cookies" not in source)

    print("\n9. Relevance filtering, from the real Ray Allen listing")
    # Captured from live sold data. Without filtering this card priced at $12.99.
    ray_items = [
        {"listingType": "sold", "soldPrice": "485",
         "title": "RAY ALLEN 2025-26 TOPPS CHROME UPDATE MOMENT IN TIME GOLD GEOMETRIC /50"},
        {"listingType": "sold", "soldPrice": "99.99",
         "title": "2025-26 Topps Chrome Update Ray Allen Moment in Time True Blue Refractor"},
        {"listingType": "sold", "soldPrice": "217.50",
         "title": "2025-26 Topps Chrome Update Ray Allen Moment in Time Orange /25 Heat"},
        {"listingType": "sold", "soldPrice": "590",
         "title": "2025 Topps Chrome Update Ray Allen, Moment in Time, True Gold /50, card"},
        {"listingType": "sold", "soldPrice": "435",
         "title": "2025-26 Topps Chrome Update Ray Allen True Gold /50, Moments In Time"},
        {"listingType": "sold", "soldPrice": "500",
         "title": "2025-26 Topps Chrome Update Ray Allen Moment in Time #MT-21 True Gold /50"},
    ]
    ray_query = "2025-26 Topps Chrome Update Ray Allen True Gold /50 Moments In Time"

    required = sc.distinguishing_tokens(sc.tokens(ray_query), ray_items)
    check("parallel words identified as distinguishing",
          set(["true", "gold"]).issubset(set(required)), required)
    check("the player name is not required (every result has it)",
          "allen" not in required, required)

    filtered = sc.summarise(ray_items, ray_query)
    check("only the True Gold sales counted", filtered["count"] == 3, filtered["count"])
    check("median is the real one", filtered["price"] == 500.0, filtered["price"])
    check("Gold Geometric excluded", filtered["ceiling"] == 590.0, filtered["ceiling"])
    check("True Blue excluded", filtered["floor"] == 435.0, filtered["floor"])
    check("reports how many were considered", filtered["considered"] == 6, filtered["considered"])

    unfiltered = sc.summarise(ray_items)
    check("without a keyword nothing is filtered", unfiltered["count"] == 6, unfiltered["count"])
    check("filtering changes the answer materially",
          unfiltered["price"] != filtered["price"], (unfiltered["price"], filtered["price"]))

    print("\n10. A card with no true comps returns nothing rather than a near miss")
    # Real case: "Image Variation Sparkle SSP" had one matching sale in 40 results.
    lebron_items = [
        {"listingType": "sold", "soldPrice": "260",
         "title": "2025 Topps Chrome LeBron James #1 Image Variation Refractor"},
        {"listingType": "sold", "soldPrice": "240",
         "title": "2025 Topps Chrome LeBron James Image Variation SP"},
        {"listingType": "sold", "soldPrice": "5589",
         "title": "2025 Topps Chrome LeBron James #1 Superfractor 1/1"},
        {"listingType": "sold", "soldPrice": "159",
         "title": "2025 Topps Chrome LeBron James #1 Base Refractor"},
    ]
    lebron_query = "2025 Topps Chrome LeBron James #1 Image Variation Sparkle SSP"
    check("no sparkle sales means no comp",
          sc.summarise(lebron_items, lebron_query) is None,
          sc.summarise(lebron_items, lebron_query))

    print("\n11. Ordinary cards are not over-filtered")
    common = [{"listingType": "sold", "soldPrice": str(p),
               "title": "2025 Topps Chrome Will Howard #321 Refractor Parallel"}
              for p in (2.50, 2.99, 3.25, 2.75)]
    result = sc.summarise(common, "2025 Topps Chrome Will Howard 321 Refractor")
    check("matching listings all kept", result["count"] == 4, result)
    check("median sensible", 2.5 <= result["price"] <= 3.25, result["price"])
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
