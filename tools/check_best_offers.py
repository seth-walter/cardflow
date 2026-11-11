"""Measure what Best Offer sales do to your comps, and which policy to run.

eBay now publishes the accepted amount on Best Offer sales. If that amount is what the
scraper reports, offer sales are the truest comps there are and should be counted. If
the asking price is reported instead, they inflate every median and should be excluded.

This compares the two groups across your own cards and says which it looks like.

    python tools/check_best_offers.py            # cached comps only, costs nothing
    python tools/check_best_offers.py --live 8   # also price 8 live listings
"""

import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import services.soldcomps as soldcomps


def cached_entries():
    """Every cached query that kept its raw comps."""
    try:
        with open(soldcomps.CACHE_PATH, "r", encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        return []

    return [(key, entry) for key, entry in cache.items() if entry.get("items")]


def fetch_live(sample):
    from core.reprice import card_data_from_listing
    from core.listing_builder import build_comp_query
    from services.ebay_listings import fetch_active_listings

    print("Loading live listings...")
    listings = fetch_active_listings(max_listings=sample, workers=6)

    for listing in listings:
        query = build_comp_query(card_data_from_listing(listing))
        if query.strip():
            soldcomps.fetch_sold(query)     # populates the cache with raw comps

    return len(listings)


def main():
    live = 0
    if "--live" in sys.argv:
        index = sys.argv.index("--live")
        live = int(sys.argv[index + 1]) if len(sys.argv) > index + 1 else 8

    if live:
        fetched = fetch_live(live)
        print("primed the cache from {} live listing(s)\n".format(fetched))

    entries = cached_entries()
    if not entries:
        print("No cached comps carry raw sold items yet.\n"
              "Run a pricing pass (or this tool with --live N) once the request quota "
              "resets, then run this again - it costs nothing after that.")
        return 0

    print("Comparing Best Offer sales against Buy It Now sales across {} cached "
          "card(s)\n".format(len(entries)))
    print("%-48s %5s %5s %10s %10s %7s" % (
        "CARD", "OFFR", "BIN", "OFFER MED", "BIN MED", "DIFF"))

    ratios, with_both, total_offers, total_bin = [], 0, 0, 0

    for key, entry in sorted(entries):
        keyword = key.split("|")[0]
        summary = soldcomps.summarise(entry["items"], keyword)
        if not summary:
            continue

        offers = summary.get("offer_sales") or 0
        bins = summary.get("bin_sales") or 0
        total_offers += offers
        total_bin += bins

        offer_median = summary.get("offer_median")
        bin_median = summary.get("bin_median")

        if offers and bins and offer_median and bin_median:
            ratio = offer_median / bin_median
            ratios.append(ratio)
            with_both += 1
            print("%-48s %5d %5d %10.2f %10.2f %6.0f%%" % (
                keyword[:48], offers, bins, offer_median, bin_median,
                (ratio - 1) * 100))
        elif offers or bins:
            print("%-48s %5d %5d %10s %10s %7s" % (
                keyword[:48], offers, bins,
                "-" if not offer_median else "{:.2f}".format(offer_median),
                "-" if not bin_median else "{:.2f}".format(bin_median), "-"))

    print("\n" + "=" * 92)
    print("Best Offer sales seen : {}".format(total_offers))
    print("Buy It Now sales seen : {}".format(total_bin))

    if total_offers + total_bin:
        print("Share sold via offers : {:.0f}%".format(
            100.0 * total_offers / (total_offers + total_bin)))

    if not ratios:
        print("\nNot enough cards have both kinds of sale to compare yet.")
        print("Current policy: SOLDCOMPS_OFFER_MODE={}".format(settings.SOLDCOMPS_OFFER_MODE))
        return 0

    middle = statistics.median(ratios)
    print("Cards with both kinds  : {}".format(with_both))
    print("Median offer/BIN ratio : {:.2f}".format(middle))

    print("\nVerdict:")
    if middle <= 0.92:
        print("   Offer sales come in clearly below Buy It Now sales, which means eBay is")
        print("   reporting the accepted amount. These are your most accurate comps.")
        print("   Keep SOLDCOMPS_OFFER_MODE=include (or try 'prefer').")
    elif middle >= 1.05:
        print("   Offer sales read HIGHER than Buy It Now sales. That is the signature of")
        print("   the asking price being reported instead of the accepted amount, which")
        print("   inflates every median. Set SOLDCOMPS_OFFER_MODE=exclude in .env.")
    else:
        print("   The two groups price about the same, so the policy barely matters.")
        print("   Leave SOLDCOMPS_OFFER_MODE=include.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
