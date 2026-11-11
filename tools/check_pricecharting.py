"""Decide whether a PriceCharting token is worth keeping, in about a minute.

Prices a sample of your real live listings through the paid provider and reports what
fraction it can actually match, how its numbers compare to your current prices, and
whether the grade-field mapping looks right. If coverage is poor, cancel the same day.

Run after putting PRICECHARTING_TOKEN in .env:

    python tools/check_pricecharting.py          # samples 25 listings
    python tools/check_pricecharting.py 60
"""

import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core.listing_builder import build_comp_query
from core.reprice import card_data_from_listing
from services.ebay_listings import fetch_active_listings
from services.price_sources import _pricecharting_field, pricecharting


def main():
    if not settings.PRICECHARTING_TOKEN:
        raise SystemExit(
            "No PRICECHARTING_TOKEN in .env. Add the 40-character token from the "
            "Subscription page (API/Download button) and run this again."
        )

    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 25

    print("Loading your live listings...")
    listings = fetch_active_listings(max_listings=sample_size, workers=6)

    if not listings:
        raise SystemExit("No published listings found to test against.")

    print("Checking {} listing(s) against the paid provider...\n".format(len(listings)))
    print("%-44s %9s %9s %7s  %s" % ("YOUR LISTING", "YOURS", "PROVIDER", "DIFF", "MATCHED AS"))

    matched, ratios, suspicious = 0, [], []

    for listing in listings:
        card_data = card_data_from_listing(listing)
        quote = pricecharting(card_data)
        yours = listing["price"]

        if not quote:
            print("%-44s %9.2f %9s %7s  -" % (listing["title"][:44], yours, "-", "-"))
            continue

        matched += 1
        theirs = quote["price"]
        ratios.append(theirs / yours if yours else 1.0)

        print("%-44s %9.2f %9.2f %6.0f%%  %s" % (
            listing["title"][:44], yours, theirs,
            (theirs / yours - 1) * 100 if yours else 0,
            quote["detail"][:36]))

        # A match whose product name shares nothing with the card is a bad match.
        key_terms = [t.lower() for t in build_comp_query(card_data).split() if len(t) > 3]
        detail = quote["detail"].lower()
        if key_terms and not any(term in detail for term in key_terms):
            suspicious.append((listing["title"], quote["detail"]))

    coverage = 100.0 * matched / len(listings)

    print("\n" + "=" * 78)
    print("Coverage:   %d of %d listings matched (%.0f%%)" % (matched, len(listings), coverage))

    if ratios:
        print("Their price vs yours: median %.2fx (%.0f%% %s)" % (
            median(ratios), abs(median(ratios) - 1) * 100,
            "higher" if median(ratios) > 1 else "lower"))

    if suspicious:
        print("\n%d match(es) look wrong - the matched product shares no key term:" % len(suspicious))
        for title, detail in suspicious[:5]:
            print("   %s\n      matched as: %s" % (title[:60], detail[:60]))

    print("\nGraded field mapping in use:")
    for grade in ["10", "9", "8", "raw"]:
        card = {"is_graded": grade != "raw", "grade": grade}
        print("   %-4s -> %s" % (grade, _pricecharting_field(card)))

    print("\nVerdict:")
    if coverage >= 70 and len(suspicious) <= len(listings) * 0.1:
        print("   Good coverage and clean matches. Worth keeping.")
    elif coverage >= 40:
        print("   Partial coverage. Useful, but plenty of cards will still fall back")
        print("   to asking prices. Judge whether that is worth the monthly fee.")
    else:
        print("   Poor coverage for your inventory. Cancel before the next billing date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
