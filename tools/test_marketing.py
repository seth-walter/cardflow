"""Promoted Listings and the Promote page's actions. Every eBay call is stubbed.

Run: python tools/test_marketing.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["SESSION_STATE_PATH"] = os.path.join(tempfile.mkdtemp(), "session_cards.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from config import settings
from services import ebay_marketing as marketing

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = "x" if body is not None else ""

    def json(self):
        return self._body


calls = []


def fake_ebay(routes):
    def request(method, url, **kwargs):
        calls.append((method, url.replace(marketing.MARKETING_URL, ""), kwargs.get("json"),
                      kwargs.get("params")))
        for (want_method, fragment), reply in routes.items():
            if method == want_method and fragment in url:
                reply = reply(kwargs) if callable(reply) else reply
                if reply.status_code >= 400:
                    raise requests.HTTPError("{} error".format(reply.status_code), response=reply)
                return reply
        raise AssertionError("unexpected call {} {}".format(method, url))
    return request


def main():
    print("\n1. Scope gate and rate bounds")
    saved_scopes = settings.EBAY_SCOPES
    settings.EBAY_SCOPES = ["https://api.ebay.com/oauth/api_scope"]
    check("no marketing scope -> cannot promote", not marketing.has_marketing_scope())
    settings.EBAY_SCOPES = saved_scopes + [marketing.SCOPE]
    check("with the scope -> can promote", marketing.has_marketing_scope())
    settings.EBAY_SCOPES = saved_scopes
    check("rate below eBay's floor raised", marketing.clamp_rate(0.5) == 2.0)
    check("rate rounded to one decimal", marketing.clamp_rate(5.04) == 5.0)

    print("\n2. The CardFlow campaign is created once, id read from Location")
    marketing._campaign_id = None
    marketing.ebay_request = fake_ebay({
        ("GET", "get_campaign_by_name"): Reply(404, {"errors": [{"errorId": 35045}]}),
        ("POST", "/ad_campaign/C9/bulk_create"): lambda kw: Reply(207, {"responses": [
            {"listingId": "L1", "statusCode": 201, "adId": "A1"},
            {"listingId": "L2", "statusCode": 400, "errors": [
                {"errorId": 35036, "message": "The listing is already in this campaign."}]},
            {"listingId": "L3", "statusCode": 400, "errors": [
                {"errorId": 35048, "message": "Listing is not eligible for promotion."}]},
        ]}),
        ("POST", "/ad_campaign/C9/bulk_update_ads_bid"): Reply(207, {"responses": [
            {"listingId": "L2", "statusCode": 200}]}),
        ("POST", "/ad_campaign"): Reply(201, None,
                                        {"Location": marketing.MARKETING_URL + "/ad_campaign/C9"}),
    })
    calls.clear()
    outcome = marketing.promote_listings({"L1": 4, "L2": 6.5, "L3": 1})
    created = [c for c in calls if c[0] == "POST" and c[1] == "/ad_campaign"]
    check("campaign created", len(created) == 1, calls)
    body = created[0][2] if created else {}
    check("cost-per-sale, named for CardFlow",
          body.get("fundingStrategy", {}).get("fundingModel") == "COST_PER_SALE"
          and body.get("campaignName") == marketing.CAMPAIGN_NAME, body)
    check("new listing promoted", outcome["L1"] == {"ok": True, "rate": 4.0, "error": ""}, outcome["L1"])
    check("already-promoted listing re-rated instead of failing",
          outcome["L2"]["ok"] and outcome["L2"]["rate"] == 6.5, outcome["L2"])
    check("ineligible listing reported with eBay's reason",
          not outcome["L3"]["ok"] and "35048" in outcome["L3"]["error"], outcome["L3"])
    sent = [c[2] for c in calls if "bulk_create" in c[1]][0]["requests"]
    check("rates sent as eBay expects", {r["listingId"]: r["bidPercentage"] for r in sent}
          == {"L1": "4.0", "L2": "6.5", "L3": "2.0"}, sent)

    calls.clear()
    marketing.promote_listings({"L1": 3})
    check("campaign id cached, not created again",
          not [c for c in calls if c[1] == "/ad_campaign"], [c[1] for c in calls])

    print("\n3. Stopping and reading current ads")
    marketing._campaign_id = None
    marketing.ebay_request = fake_ebay({
        ("GET", "get_campaign_by_name"): Reply(200, {"campaignId": "C1", "campaignStatus": "RUNNING"}),
        ("POST", "bulk_delete_ads_by_listing_id"): Reply(200, {"responses": [
            {"listingId": "L1", "statusCode": 200}]}),
        ("GET", "/ad_campaign/C1/ad"): lambda kw: Reply(200, {
            "total": 2, "ads": [{"listingId": "L1", "bidPercentage": "4.0"},
                                {"listingId": "L2", "bidPercentage": "6.5"}]}),
    })
    check("existing campaign reused", marketing.campaign_id() == "C1")
    check("stop promoting", marketing.stop_promoting(["L1"])["L1"]["ok"])
    check("current ads read", marketing.current_ads() == {"L1": 4.0, "L2": 6.5})

    print("\n4. Promote page: promote and convert from the UI")
    from streamlit.testing.v1 import AppTest
    import core.reprice
    import services.ebay_auction
    import services.ebay_listings

    start = (datetime.utcnow() - timedelta(days=50)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    listings = [
        {"offer_id": "O1", "listing_id": "L1", "title": "Stale card", "price": 20.0,
         "watch_count": 0, "start_time": start, "listing_type": "FixedPriceItem", "card_data": {}},
        {"offer_id": "", "listing_id": "L2", "title": "Seller Hub card", "price": 15.0,
         "watch_count": 0, "start_time": start, "listing_type": "FixedPriceItem", "card_data": {}},
    ]
    promoted, converted = [], []
    services.ebay_listings.fetch_active_listings = lambda **kw: listings
    marketing.has_marketing_scope = lambda: True
    marketing.current_ads = lambda: {}
    marketing.promote_listings = lambda rates: promoted.append(rates) or {
        lid: {"ok": True, "rate": rate, "error": ""} for lid, rate in rates.items()}
    services.ebay_auction.convert_to_auction = lambda offer_id, price, duration="DAYS_7": (
        converted.append((offer_id, price, duration)) or {
            "listing_id": "NEW1", "start_price": price, "duration": duration, "steps": []})
    core.reprice.suggest_for_listings = lambda rows, **kw: rows

    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages" / "2_Promote.py"),
                             default_timeout=60)
    page.run()
    page.sidebar.button[0].click().run()
    check("page renders listings", not page.exception, [e.value for e in page.exception])
    promote_buttons = [b for b in page.button if b.label == "Promote"]
    check("promote button per listing", len(promote_buttons) == 2, [b.label for b in page.button])
    promote_buttons[0].click().run()
    check("promote sends the listing at its rate", promoted and "L1" in promoted[-1], promoted)
    check("result shown and kept", any("Promoted at" in str(s.value) for s in page.success),
          [s.value for s in page.success])

    confirm = [c for c in page.checkbox if "ends the live listing" in str(c.label)]
    check("convert offered only where CardFlow owns the offer", len(confirm) == 1, len(confirm))
    if confirm:
        confirm[0].check().run()
        [b for b in page.button if b.label == "Convert to auction"][0].click().run()
        check("convert called with the offer and opening bid",
              converted and converted[-1][0] == "O1", converted)
        check("converted listing reported", any("Now an auction" in str(s.value) for s in page.success),
              [s.value for s in page.success])
        # AppTest keeps the elements of the run that called st.rerun(), and cannot run
        # again while it holds the vanished widgets, so the state that hides them is checked.
        check("ended listing marked converted and its ad dropped",
              "O1" in page.session_state["promote_converted"]
              and "L1" not in page.session_state["promote_ads"],
              (page.session_state["promote_converted"], page.session_state["promote_ads"]))

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
