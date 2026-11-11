"""Auction conversion and starting-price logic. Every eBay call is stubbed.

Nothing here touches a live listing.

Run: python tools/test_auction.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.ebay_auction as auction

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def main():
    print("\n1. The opening bid follows how well the price is known")
    liquid = auction.recommend_start_price(
        {"estimated_price": 40.0, "count": 12, "basis": "sold", "needs_review": False}, 41.99)
    check("many sales opens low", liquid["start_price"] == 9.99, liquid)
    check("and says why", "12 recent sales" in liquid["reason"], liquid["reason"])

    middling = auction.recommend_start_price(
        {"estimated_price": 500.0, "count": 5, "basis": "sold", "needs_review": False}, 549.99)
    check("a few sales opens at half", middling["start_price"] == 249.99, middling)

    thin = auction.recommend_start_price(
        {"estimated_price": 200.0, "count": 2, "basis": "sold", "needs_review": True}, 199.99)
    check("thin comps open near market", thin["start_price"] == 159.99, thin)
    check("and are flagged as not confident", thin["confident"] is False)

    asks = auction.recommend_start_price(
        {"estimated_price": 25.0, "count": 9, "basis": "ask", "needs_review": False}, 24.99)
    check("asking prices open highest of all", asks["ratio"] == 0.90, asks)
    check("never below a dollar",
          auction.recommend_start_price({"estimated_price": 0.50, "basis": "ask"}, 0.50)
          ["start_price"] == 0.99)

    print("\n2. Converting ends the fixed price before creating the auction")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url.rsplit("/", 2)[-2:], kwargs.get("json")))

        class R:
            text = "{}"
            def json(self):
                if method == "GET":
                    return {"offerId": "o1", "sku": "card-1", "categoryId": "261328",
                            "listingDescription": "<p>keep me</p>",
                            "pricingSummary": {"price": {"value": "41.99"}}}
                if url.endswith("/publish"):
                    return {"listingId": "111222333"}
                if method == "POST" and url.endswith("/offer"):
                    return {"offerId": "auction-1"}
                return {}
        return R()

    auction.ebay_request = fake_request
    result = auction.convert_to_auction("o1", 10.0, duration="DAYS_7")

    methods = [c[0] for c in calls]
    urls = [c[1] for c in calls]
    check("read the original first", methods[0] == "GET", methods)
    check("withdrew before creating",
          any("withdraw" in u[-1] for u in urls)
          and [i for i, u in enumerate(urls) if "withdraw" in u[-1]][0]
              < [i for i, m in enumerate(methods) if m == "POST" and "withdraw" not in urls[i][-1]][-1],
          urls)
    check("auction offer created", result["auction_offer_id"] == "auction-1", result)
    check("auction published", result["listing_id"] == "111222333", result)
    check("original offer kept by default", result["original_deleted"] is False)
    check("no DELETE was sent", "DELETE" not in methods, methods)

    created = [c[2] for c in calls if c[2] and c[2].get("format") == "AUCTION"]
    check("format is AUCTION", len(created) == 1, len(created))
    payload = created[0]
    check("start price carried", payload["pricingSummary"]["auctionStartPrice"]["value"] == "10.00")
    check("duration carried", payload["listingDuration"] == "DAYS_7")
    check("same sku reused", payload["sku"] == "card-1")
    check("category preserved", payload["categoryId"] == "261328")
    check("description preserved", payload["listingDescription"] == "<p>keep me</p>")
    check("no fixed price sent", "price" not in payload["pricingSummary"])
    check("cheap start uses the under-20 shipping policy",
          "fulfillmentPolicyId" in payload["listingPolicies"])

    print("\n3. Delete-and-relist removes the original")
    calls[:] = []
    auction.convert_to_auction("o1", 25.0, delete_original=True)
    check("a DELETE was sent", "DELETE" in [c[0] for c in calls], [c[0] for c in calls])

    print("\n4. A failed auction is reported loudly, not silently")
    def failing_request(method, url, **kwargs):
        class R:
            text = "{}"
            def json(self):
                if method == "GET":
                    return {"offerId": "o1", "sku": "card-1", "categoryId": "261328"}
                if method == "POST" and url.endswith("/offer"):
                    return {"errors": [{"message": "category does not allow auctions"}]}
                return {}
        return R()

    auction.ebay_request = failing_request
    try:
        auction.convert_to_auction("o1", 10.0)
        check("raises when the auction cannot be created", False, "no exception")
    except RuntimeError as exc:
        check("raises when the auction cannot be created", True)
        check("warns the card is now unlisted", "not listed right now" in str(exc), str(exc)[:90])

    print("\n5. An offer without a sku or category is refused before anything is ended")
    def bare_request(method, url, **kwargs):
        class R:
            text = "{}"
            def json(self):
                return {"offerId": "o1"} if method == "GET" else {}
        return R()

    auction.ebay_request = bare_request
    try:
        auction.convert_to_auction("o1", 10.0)
        check("refuses an offer it cannot rebuild", False, "no exception")
    except RuntimeError as exc:
        check("refuses an offer it cannot rebuild", "no sku or category" in str(exc), str(exc))

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
