"""Validate a SoldComps key against your real cards, spending only a few requests.

The free plan allows 100 requests a month, so this defaults to 8 - enough to see whether
your cards get matched and whether the sold prices look sane next to your own.

    python tools/check_soldcomps.py        # 8 of your live listings
    python tools/check_soldcomps.py 15
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core.listing_builder import build_comp_query
from core.reprice import card_data_from_listing
from services.ebay_listings import fetch_active_listings
from services.soldcomps import cache_stats, fetch_sold


def main():
    if not settings.SOLDCOMPS_API_KEY:
        raise SystemExit(
            "No SOLDCOMPS_API_KEY in .env.\n"
            "Sign up free at https://sold-comps.com (100 requests/month, no card), "
            "then put the sc_... key in .env as SOLDCOMPS_API_KEY."
        )

    sample = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    print("Loading your live listings...")
    listings = fetch_active_listings(max_listings=sample, workers=6)
    if not listings:
        raise SystemExit("No published listings found to test with.")

    print("Checking {} card(s) against real eBay sold data "
          "(uses up to {} of your monthly requests)\n".format(len(listings), len(listings)))
    print("%-40s %8s %8s %6s %5s  %s" % (
        "CARD", "YOURS", "SOLD MED", "DIFF", "COMPS", "RANGE"))

    matched, cheaper, dearer = 0, 0, 0

    for listing in listings:
        card_data = card_data_from_listing(listing)
        query = build_comp_query(card_data)
        summary = fetch_sold(query)
        yours = listing["price"]

        if not summary:
            print("%-40s %8.2f %8s %6s %5s  no sold comps found" % (
                listing["title"][:40], yours, "-", "-", "-"))
            continue

        matched += 1
        diff = (summary["price"] / yours - 1) * 100 if yours else 0
        if diff < 0:
            cheaper += 1
        else:
            dearer += 1

        print("%-40s %8.2f %8.2f %5.0f%% %5d  $%.2f-$%.2f" % (
            listing["title"][:40], yours, summary["price"], diff,
            summary["count"], summary["floor"], summary["ceiling"]))

    print("\n" + "=" * 78)
    print("Matched %d of %d card(s) (%.0f%%)" % (
        matched, len(listings), 100.0 * matched / len(listings)))

    if matched:
        print("Your price was above the sold median on %d, below on %d." % (cheaper, dearer))

    stats = cache_stats()
    print("Cache holds %d quer(y/ies), %d still fresh - repeats cost no quota."
          % (stats["entries"], stats["fresh"]))

    print("\nVerdict:")
    if matched >= len(listings) * 0.7:
        print("   Strong coverage on your inventory. This is the sold-comp source to use.")
        print("   $9/month covers 2,000 lookups if the free 100 runs short.")
    elif matched:
        print("   Partial coverage. Check the misses above: usually the comp query is too")
        print("   specific (serial numbers, long parallel names) rather than a data gap.")
    else:
        print("   Nothing matched. Check the key, then try a simpler query by hand at")
        print("   https://sold-comps.com before spending more requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
