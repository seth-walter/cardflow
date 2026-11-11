"""Stale-listing and promotion suggestions. No network.

Run: python tools/test_promote.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.promote import (
    ACTION_PRICE, ACTION_PROMOTE, ACTION_RETHINK, ACTION_WAIT,
    days_listed, stale_listings, suggest_promotion,
)

failures = []
NOW = datetime(2026, 8, 31)


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def listing(days_old, watchers=0, price=20.0, title="card", offer_id="o1"):
    return {
        "offer_id": offer_id, "listing_id": "1", "title": title, "price": price,
        "watch_count": watchers, "image": "", "card_data": {},
        "start_time": (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


def main():
    print("\n1. Listing age is read from eBay's start time")
    check("120 days", days_listed(listing(120), NOW) == 120, days_listed(listing(120), NOW))
    check("same day is zero", days_listed(listing(0), NOW) == 0)
    check("missing start time gives nothing", days_listed({"start_time": ""}, NOW) is None)
    check("garbage start time gives nothing",
          days_listed({"start_time": "not a date"}, NOW) is None)

    print("\n2. Fresh listings are left alone")
    fresh = suggest_promotion(listing(14, watchers=0), None, NOW)
    check("no action suggested", fresh["action"] == ACTION_WAIT, fresh)
    check("no ad rate", fresh["ad_rate"] == 0.0)
    check("and it says why", "give it time" in fresh["reason"], fresh["reason"])

    print("\n3. The ad rate climbs with how long it has sat")
    ladder = [(45, 3, 2.0), (45, 0, 3.0), (75, 1, 5.0), (120, 0, 7.0)]
    for age, watchers, expected in ladder:
        result = suggest_promotion(listing(age, watchers), None, NOW)
        check("{} days, {} watcher(s) -> {:.0f}%".format(age, watchers, expected),
              result["ad_rate"] == expected, result["ad_rate"])

    watched = suggest_promotion(listing(45, 3), None, NOW)
    unwatched = suggest_promotion(listing(45, 0), None, NOW)
    check("watched listings need a smaller nudge",
          watched["ad_rate"] < unwatched["ad_rate"],
          (watched["ad_rate"], unwatched["ad_rate"]))
    check("the reason names the watchers", "3 watcher" in watched["reason"], watched["reason"])

    print("\n4. Very old listings are not sold by advertising alone")
    ancient = suggest_promotion(listing(120, 0), None, NOW)
    check("flagged as needing a rethink", ancient["action"] == ACTION_RETHINK, ancient)
    check("suggests auction or price cut", "auction" in ancient["reason"], ancient["reason"])

    print("\n5. An overpriced card is repriced, not promoted")
    comps = {"basis": "sold", "estimated_price": 20.0, "needs_review": False}
    overpriced = suggest_promotion(listing(120, 0, price=50.0), comps, NOW)
    check("promotion withheld", overpriced["action"] == ACTION_PRICE, overpriced)
    check("no ad spend suggested", overpriced["ad_rate"] == 0.0)
    check("marked overpriced", overpriced["overpriced"] is True)
    check("reason shows both numbers",
          "$50.00" in overpriced["reason"] and "$20.00" in overpriced["reason"],
          overpriced["reason"])

    fair = suggest_promotion(listing(120, 0, price=21.0), comps, NOW)
    check("a fairly priced old card is still promoted",
          fair["action"] == ACTION_RETHINK, fair["action"])

    print("\n6. Thin comps never block a promotion")
    unsure = {"basis": "sold", "estimated_price": 20.0, "needs_review": True}
    result = suggest_promotion(listing(75, 0, price=50.0), unsure, NOW)
    check("uncertain comps are not used to withhold advice",
          result["action"] == ACTION_PROMOTE, result)
    ask_based = {"basis": "ask", "estimated_price": 20.0, "needs_review": False}
    check("asking prices are not used either",
          suggest_promotion(listing(75, 0, price=50.0), ask_based, NOW)["action"] == ACTION_PROMOTE)

    print("\n7. The list is filtered and ordered by how long things have sat")
    rows = [listing(10, offer_id="a"), listing(200, offer_id="b"),
            listing(60, offer_id="c"), listing(35, offer_id="d")]
    stale = stale_listings(rows, min_days=30, now=NOW)
    check("fresh listing excluded", len(stale) == 3, [r["offer_id"] for r in stale])
    check("oldest first", [r["offer_id"] for r in stale] == ["b", "c", "d"],
          [r["offer_id"] for r in stale])
    check("each row carries its suggestion", all("action" in r for r in stale))
    check("original fields preserved", stale[0]["title"] == "card")

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
