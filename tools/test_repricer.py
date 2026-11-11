"""Repricer tests with every eBay call stubbed. No network, no live listings touched.

Run: python tools/test_repricer.py
"""

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.reprice as reprice
import services.ebay_listings as listings_module
from core.listing_builder import build_comp_query
from core.reprice import card_data_from_listing, movers, suggest_for_listings
from utils.text_utils import clean_comp_query

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


SPORTS_LISTING = {
    "sku": "card-1", "offer_id": "offer-1", "price": 41.99, "status": "PUBLISHED",
    "title": "2025 Topps Chrome Tyrese Maxey Paradox SSP #PX",
    "aspects": {"Player/Athlete": ["Tyrese Maxey"], "Set": ["Topps Chrome"],
                "Year Manufactured": ["2025"], "Sport": ["Basketball"],
                "Card Number": ["PX"], "Type": ["Sports Trading Card"],
                "Manufacturer": ["Topps"], "Graded": ["No"]},
}

DISNEY_LISTING = {
    "sku": "card-2", "offer_id": "offer-2", "price": 12.00, "status": "PUBLISHED",
    "title": "2026 Topps Chrome Neon Nemo #29/50",
    "aspects": {"Character": ["Nemo"], "Franchise": ["Pixar"], "Set": ["Topps Chrome Neon"],
                "Year Manufactured": ["2026"], "Type": ["Non-Sport Trading Card"],
                "Manufacturer": ["Topps"], "Graded": ["No"]},
}

BARE_LISTING = {
    "sku": "card-3", "offer_id": "offer-3", "price": 5.00, "status": "PUBLISHED",
    "title": "Odd Vintage Lot Card 1994", "aspects": {},
}


def stub_price(price, basis="sold", needs_review=False, count=6):
    def _estimate(card_data):
        return {"source": "stub", "basis": basis, "estimated_price": price,
                "low": price * 0.8, "high": price * 1.2, "count": count,
                "confidence": "high" if basis == "sold" else "medium",
                "spread_ratio": 1.5, "needs_review": needs_review,
                "review_reasons": ["stubbed"] if needs_review else [],
                "detail": "stubbed comps", "url": "", "query_used": ""}
    reprice.get_price_estimate = _estimate


def main():
    print("\n1. Listing specifics become pricing input")
    card_data = card_data_from_listing(SPORTS_LISTING)
    check("player carried over", card_data.get("player_athlete") == "Tyrese Maxey")
    check("year carried over", card_data.get("year") == "2025")
    check("type maps to sports", card_data.get("card_category") == "sports")
    # The title carries the parallel ("Paradox SSP") while the aspects do not, and
    # pricing a parallel off base-card comps is how a $500 card was valued at $12.
    check("comp query is the listing title, cleaned of seller notes",
          build_comp_query(card_data) == clean_comp_query(SPORTS_LISTING["title"]),
          build_comp_query(card_data))
    check("the parallel survives cleaning",
          "paradox ssp" in build_comp_query(card_data), build_comp_query(card_data))
    check("aspects still drive routing", card_data.get("sport") == "Basketball")

    disney_data = card_data_from_listing(DISNEY_LISTING)
    check("non-sport type mapped", disney_data.get("card_category") == "other")
    check("character carried over", disney_data.get("character") == "Nemo")
    check("franchise carried over", disney_data.get("franchise") == "Pixar")

    print("\n2. A listing with no specifics falls back to its title")
    bare = card_data_from_listing(BARE_LISTING)
    check("title used as the query",
          build_comp_query(bare) == clean_comp_query(BARE_LISTING["title"]),
          build_comp_query(bare))

    print("\n3. Suggestions compare against the current price")
    stub_price(60.0)
    rows = suggest_for_listings([SPORTS_LISTING])
    row = rows[0]
    check("suggested price returned", row["suggested"] == 60.0)
    check("delta computed", row["delta"] == 18.01, row["delta"])
    check("percent computed", row["delta_pct"] == 42.9, row["delta_pct"])
    check("basis carried through", row["basis"] == "sold")
    check("current price preserved", row["price"] == 41.99)

    print("\n4. A pricing failure never breaks the batch")
    def _boom(card_data):
        raise RuntimeError("comp source down")
    reprice.get_price_estimate = _boom
    broken = suggest_for_listings([SPORTS_LISTING, DISNEY_LISTING])
    check("both rows returned", len(broken) == 2)
    check("error recorded", "comp source down" in broken[0]["error"])
    check("no suggestion invented", broken[0]["suggested"] is None)

    print("\n5. Only meaningful movers are shown")
    stub_price(60.0)
    big = suggest_for_listings([SPORTS_LISTING])
    stub_price(42.50)
    small = suggest_for_listings([SPORTS_LISTING])
    check("42% move passes a 15% filter", len(movers(big, 15)) == 1)
    check("1% move filtered out", len(movers(small, 15)) == 0, small[0]["delta_pct"])
    check("errors never appear as movers", len(movers(broken, 0)) == 0)

    print("\n6. Sold-only and review filters")
    stub_price(60.0, basis="ask")
    ask_rows = suggest_for_listings([SPORTS_LISTING])
    check("ask suggestion hidden when sold-only", len(movers(ask_rows, 15, sold_only=True)) == 0)
    check("ask suggestion shown otherwise", len(movers(ask_rows, 15)) == 1)
    stub_price(60.0, needs_review=True)
    flagged = suggest_for_listings([SPORTS_LISTING])
    check("review flag carried through", flagged[0]["needs_review"] is True)

    print("\n7. Repricing edits price only, and never invents an offer")
    captured = {}

    def fake_request(method, url, **kwargs):
        class R:
            text = "{}"
            def json(self):
                if method == "GET":
                    return {"offerId": "offer-1", "sku": "card-1", "status": "PUBLISHED",
                            "listing": {"listingId": "111"}, "categoryId": "261328",
                            "availableQuantity": 1, "listingDescription": "<p>keep me</p>",
                            "listingPolicies": {"paymentPolicyId": "p",
                                                "bestOfferTerms": {
                                                    "autoDeclinePrice": {"value": "30.00"}}},
                            "pricingSummary": {"price": {"value": "41.99", "currency": "USD"}}}
                captured["method"] = method
                captured["body"] = kwargs.get("json")
                return {}
        return R()

    listings_module.ebay_request = fake_request
    listings_module.update_offer_price("offer-1", 55.0, min_offer_price=44.0)

    body = captured.get("body", {})
    check("sent as PUT", captured.get("method") == "PUT", captured.get("method"))
    check("price updated", body.get("pricingSummary", {}).get("price", {}).get("value") == "55.00",
          body.get("pricingSummary"))
    check("description preserved", body.get("listingDescription") == "<p>keep me</p>")
    check("policies preserved", "paymentPolicyId" in body.get("listingPolicies", {}))
    check("quantity preserved", body.get("availableQuantity") == 1)
    # The caller passed an explicit floor of 44.00, which applies the current policy to
    # a listing created under the old one.
    check("explicit floor applied",
          body["listingPolicies"]["bestOfferTerms"]["autoDeclinePrice"]["value"] == "44.00",
          body["listingPolicies"]["bestOfferTerms"])
    check("read-only offerId stripped", "offerId" not in body)
    check("read-only listing block stripped", "listing" not in body)
    check("read-only status stripped", "status" not in body)


    print("\n8. The best-offer floor moves with the price")

    def offer_with(price, floor=None, auto_accept=None):
        terms = {"bestOfferEnabled": True}
        if floor is not None:
            terms["autoDeclinePrice"] = {"value": str(floor), "currency": "USD"}
        if auto_accept is not None:
            terms["autoAcceptPrice"] = {"value": str(auto_accept), "currency": "USD"}
        return {"offerId": "o1", "sku": "s1", "status": "PUBLISHED",
                "listing": {"listingId": "1"}, "categoryId": "261328",
                "availableQuantity": 1, "listingDescription": "<p>d</p>",
                "listingPolicies": {"paymentPolicyId": "p", "bestOfferTerms": terms},
                "pricingSummary": {"price": {"value": str(price), "currency": "USD"}}}

    def run_update(existing, new_price, min_offer=None, keep=False):
        sent = {}

        def fake(method, url, **kwargs):
            class R:
                text = "{}"
                def json(self):
                    if method == "GET":
                        return existing
                    sent["body"] = kwargs.get("json")
                    return {}
            return R()

        listings_module.ebay_request = fake
        listings_module.update_offer_price("o1", new_price, min_offer_price=min_offer,
                                           keep_existing_ratio=keep)
        return sent["body"]["listingPolicies"]["bestOfferTerms"]

    # With keep_existing_ratio, a 70% floor on $100 stays 70% when the card goes to $200.
    terms = run_update(offer_with(100.0, floor=70.0), 200.0, min_offer=160.0, keep=True)
    check("keep-ratio preserves the seller's own ratio",
          terms["autoDeclinePrice"]["value"] == "140.00", terms["autoDeclinePrice"])

    # By default the current policy is applied instead, which is how old floors migrate.
    terms = run_update(offer_with(100.0, floor=70.0), 200.0, min_offer=40.0)
    check("explicit floor wins by default",
          terms["autoDeclinePrice"]["value"] == "40.00", terms["autoDeclinePrice"])

    # Lowering the price must not leave the floor above it.
    terms = run_update(offer_with(100.0, floor=90.0), 50.0, min_offer=40.0)
    check("floor never lands above the new price",
          float(terms["autoDeclinePrice"]["value"]) < 50.0, terms["autoDeclinePrice"])

    # Auto-accept decides what sells without you seeing the offer: never touched.
    terms = run_update(offer_with(100.0, floor=80.0, auto_accept=95.0), 200.0, min_offer=160.0)
    check("auto-accept left exactly as the seller set it",
          terms["autoAcceptPrice"]["value"] == "95.0", terms["autoAcceptPrice"])

    # A listing with no floor should not gain one.
    body = None

    def fake_no_terms(method, url, **kwargs):
        class R:
            text = "{}"
            def json(self):
                if method == "GET":
                    return {"offerId": "o1", "sku": "s1", "status": "PUBLISHED",
                            "listing": {"listingId": "1"},
                            "listingPolicies": {"paymentPolicyId": "p"},
                            "pricingSummary": {"price": {"value": "10.00"}}}
                globals()["_sent_body"] = kwargs.get("json")
                return {}
        return R()

    listings_module.ebay_request = fake_no_terms
    listings_module.update_offer_price("o1", 20.0, min_offer_price=16.0)
    policies = globals()["_sent_body"]["listingPolicies"]
    check("no best-offer terms invented", "bestOfferTerms" not in policies, policies)
    check("price still updated",
          globals()["_sent_body"]["pricingSummary"]["price"]["value"] == "20.00")

    print("\n9. The 20% offer floor policy")
    from config import settings as cfg
    from services.ebay_offers import suggested_min_offer

    check("20% of the price", suggested_min_offer(100.0) == 20.0, suggested_min_offer(100.0))
    check("scales with the card", suggested_min_offer(549.99) == 110.0,
          suggested_min_offer(549.99))
    check("never below eBay's dollar minimum", suggested_min_offer(2.99) == 0.99,
          suggested_min_offer(2.99))
    check("never at or above the asking price", suggested_min_offer(1.00) < 1.00,
          suggested_min_offer(1.00))
    check("ratio is configurable", cfg.MIN_OFFER_RATIO == 0.20, cfg.MIN_OFFER_RATIO)

    print("\n10. Repricing moves an old 80% floor onto the new policy")
    updated = run_update(offer_with(100.0, floor=80.0), 200.0,
                         min_offer=suggested_min_offer(200.0))
    check("old 80% floor replaced with 20%",
          updated["autoDeclinePrice"]["value"] == "40.00", updated["autoDeclinePrice"])

    def run_update_keep(existing, new_price, min_offer):
        sent = {}

        def fake(method, url, **kwargs):
            class R:
                text = "{}"
                def json(self):
                    if method == "GET":
                        return existing
                    sent["body"] = kwargs.get("json")
                    return {}
            return R()

        listings_module.ebay_request = fake
        listings_module.update_offer_price("o1", new_price, min_offer_price=min_offer,
                                           keep_existing_ratio=True)
        return sent["body"]["listingPolicies"]["bestOfferTerms"]

    kept = run_update_keep(offer_with(100.0, floor=80.0), 200.0, suggested_min_offer(200.0))
    check("keep-ratio option still preserves 80%",
          kept["autoDeclinePrice"]["value"] == "160.00", kept["autoDeclinePrice"])

    accepted = run_update(offer_with(100.0, floor=80.0, auto_accept=95.0), 200.0,
                          min_offer=suggested_min_offer(200.0))
    check("auto-accept still untouched under the new policy",
          accepted["autoAcceptPrice"]["value"] == "95.0", accepted["autoAcceptPrice"])

    print("\n11. Recently repriced listings are left alone")
    import core.reprice_history as history
    history.HISTORY_PATH = str(Path(tempfile.mkdtemp()) / "history.json")

    stub_price(60.0)
    rows = suggest_for_listings([SPORTS_LISTING])
    check("a never-repriced listing has no age", rows[0]["repriced_days_ago"] is None,
          rows[0]["repriced_days_ago"])
    check("and it shows up as a mover", len(movers(rows, 15, cooldown_days=14)) == 1)

    history.record(SPORTS_LISTING["offer_id"], 41.99, title="t")
    rows = suggest_for_listings([SPORTS_LISTING])
    check("age recorded after repricing", rows[0]["repriced_days_ago"] is not None,
          rows[0]["repriced_days_ago"])
    check("hidden while inside the cooldown",
          len(movers(rows, 15, cooldown_days=14)) == 0, movers(rows, 15, cooldown_days=14))
    check("still visible with no cooldown", len(movers(rows, 15, cooldown_days=0)) == 1)
    check("history records what it was set to",
          history.lookup(SPORTS_LISTING["offer_id"])["price"] == 41.99)
    check("and what it was before",
          history.lookup(SPORTS_LISTING["offer_id"])["previous_price"] is None)
    check("clearing history restores it",
          history.forget_all() == 1 and history.count() == 0)

    print("\n12. The page never writes to a widget key after drawing it")
    # Streamlit raises on that, which is how the publish flow used to fail after a
    # successful publish. A static check is enough to stop it coming back.
    page_lines = io.open(Path(__file__).resolve().parents[1] / "pages" / "1_Repricer.py",
                         encoding="utf-8").read().splitlines()

    # Writing a widget key BEFORE the widget is drawn is fine and is how bulk-select
    # works. Writing it AFTER is what raises, so only ordering matters.
    # The key expression changed when legacy listings (no offer id) were supported.
    for prefix, widget_marker in (("pick_", 'key="pick_" + key'),
                                  ("new_", 'key="new_" + key')):
        drawn_at = next((i for i, line in enumerate(page_lines) if widget_marker in line), None)
        check("widget " + prefix + " is drawn somewhere", drawn_at is not None, drawn_at)

        offenders = [
            (i + 1, line.strip()) for i, line in enumerate(page_lines)
            if drawn_at is not None and i > drawn_at
            and ("st.session_state[" + chr(34) + prefix) in line
            and "=" in line.split("]")[-1]
        ]
        check("nothing assigns to " + prefix + " keys after the widget is drawn",
              not offenders, offenders[:2])

    print("\n13. The load limit counts live listings, not inventory items")
    # Realistic mix: most accounts hold unpublished and sold-out items too.
    inventory = []
    for i in range(250):
        inventory.append({"sku": "sku%03d" % i,
                          "product": {"title": "Card %d" % i, "aspects": {},
                                      "imageUrls": ["u"]}})

    def offer_for(sku):
        index = int(sku.replace("sku", ""))
        if index % 3 == 0:           # every third item was never published
            return None
        return {"offerId": "offer" + sku,
                "status": "PUBLISHED" if index % 3 == 1 else "UNPUBLISHED",
                "categoryId": "261328",
                "pricingSummary": {"price": {"value": "10.00", "currency": "USD"}}}

    def fake_page(method, url, **kwargs):
        params = kwargs.get("params") or {}
        offset = params.get("offset", 0)
        limit = params.get("limit", 100)

        class R:
            text = "{}"
            def json(self):
                return {"inventoryItems": inventory[offset:offset + limit]}
        return R()

    listings_module.ebay_request = fake_page
    listings_module.fetch_offer_for_sku = offer_for

    progress = []
    got = listings_module.fetch_active_listings(
        max_listings=20, workers=2, include_legacy=False,
        on_progress=lambda found, scanned: progress.append((found, scanned)))

    check("returns exactly the number of listings asked for", len(got) == 20, len(got))
    check("every one is published",
          all(row["status"] == "PUBLISHED" for row in got),
          [row["status"] for row in got[:3]])
    check("more items were scanned than listings returned",
          progress[-1][1] > 20, progress[-1])
    check("progress reports found and scanned separately",
          progress[-1] == (20, progress[-1][1]), progress[-1])

    fewer = listings_module.fetch_active_listings(max_listings=500, workers=2,
                                                 include_legacy=False)
    check("asking for more than exist returns what exists",
          0 < len(fewer) < 500, len(fewer))
    check("and it walked the whole inventory",
          len(fewer) == len([i for i in range(250) if i % 3 == 1]), len(fewer))

    print("\n14. Listings created outside the app are included")
    # The Inventory API cannot see Seller Hub listings; Trading can. Both are stubbed.
    trading_rows = [
        {"sku": "card-1", "offer_id": "", "listing_id": "111", "status": "PUBLISHED",
         "title": "2025 Topps Chrome Harden", "aspects": {}, "image": "",
         "category_id": "", "price": 41.99, "currency": "USD", "quantity": "1",
         "listing_type": "FixedPriceItem", "source": "trading"},
        {"sku": "", "offer_id": "", "listing_id": "222", "status": "PUBLISHED",
         "title": "NeeDoh Cloud Pleaser Squishy", "aspects": {}, "image": "",
         "category_id": "", "price": 18.99, "currency": "USD", "quantity": "1",
         "listing_type": "FixedPriceItem", "source": "trading"},
    ]

    listings_module.fetch_all_active_listings = lambda: [dict(r) for r in trading_rows]
    listings_module.fetch_item_category = lambda item_id: "180933"
    listings_module.fetch_offer_for_sku = lambda sku: (
        {"offerId": "offer-1", "status": "PUBLISHED", "categoryId": "261328",
         "pricingSummary": {"price": {"value": "41.99"}}} if sku == "card-1" else None)

    merged = listings_module.fetch_active_listings(workers=2)
    check("both listings returned", len(merged) == 2, len(merged))

    by_id = {row["listing_id"]: row for row in merged}
    check("app listing gained its offer id", by_id["111"]["offer_id"] == "offer-1", by_id["111"])
    check("app listing marked as inventory-managed", by_id["111"]["source"] == "inventory")
    check("app listing has its card category", by_id["111"]["category_id"] == "261328")

    check("outside listing has no offer id", by_id["222"]["offer_id"] == "")
    check("outside listing stays marked as trading", by_id["222"]["source"] == "trading")
    check("its category was looked up separately", by_id["222"]["category_id"] == "180933",
          by_id["222"]["category_id"])

    print("\n15. Non-card listings are separable from card listings")
    CARDS = {"261328", "183050", "183454"}
    cards_only = [row for row in merged if row["category_id"] in CARDS]
    check("the squishy is not a card", len(cards_only) == 1, [r["title"] for r in cards_only])
    check("the card survives the filter", cards_only[0]["listing_id"] == "111")

    print("\n16. A card handled elsewhere can be silenced without touching eBay")
    import core.reprice_history as hist
    hist.HISTORY_PATH = str(Path(tempfile.mkdtemp()) / "skip.json")

    stub_price(60.0)
    listing_rows = suggest_for_listings([SPORTS_LISTING])
    check("it shows as a mover first", len(movers(listing_rows, 15, cooldown_days=14)) == 1)

    # What the "Leave it" button does: record the CURRENT price, change nothing on eBay.
    hist.record(SPORTS_LISTING["offer_id"], SPORTS_LISTING["price"],
                title=SPORTS_LISTING["title"], previous_price=SPORTS_LISTING["price"])

    listing_rows = suggest_for_listings([SPORTS_LISTING])
    check("now hidden by the cooldown",
          len(movers(listing_rows, 15, cooldown_days=14)) == 0)
    check("the recorded price is the one it already had",
          hist.lookup(SPORTS_LISTING["offer_id"])["price"] == 41.99,
          hist.lookup(SPORTS_LISTING["offer_id"]))
    check("still visible if the cooldown is turned off",
          len(movers(listing_rows, 15, cooldown_days=0)) == 1)

    print("\n17. The skip button never writes a widget key")
    page_lines = io.open(Path(__file__).resolve().parents[1] / "pages" / "1_Repricer.py",
                         encoding="utf-8").read().splitlines()
    skip_line = next((i for i, line in enumerate(page_lines) if 'key="skip_" + key' in line), None)
    check("skip button exists", skip_line is not None, skip_line)
    after = [line.strip() for line in page_lines[skip_line:skip_line + 6]]
    check("it records history rather than ticking a box",
          any("record_reprice" in line for line in after)
          and not any("st.session_state[" in line for line in after), after)
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
