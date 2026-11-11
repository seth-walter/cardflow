"""Comp link tests: the right sites, the right query, correctly encoded. No network.

Run: python tools/test_comp_links.py
"""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.comp_links import build_comp_links

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def parts(url):
    parsed = urlparse(url)
    return parsed.netloc, parse_qs(parsed.query)


def main():
    print("\n1. Sports card routes to eBay sold + SportsCardsPro")
    sports = {
        "card_category": "sports", "year": "2025", "brand": "Topps Chrome",
        "player_athlete": "James Harden", "card_number": "SE-7", "sport": "Basketball",
    }
    links = dict((label, url) for label, url in build_comp_links(sports))
    check("three links offered", len(links) == 3, list(links))
    check("eBay sold link present", "eBay sold" in links)
    check("sports goes to SportsCardsPro", "SportsCardsPro" in links, list(links))
    check("not sent to PriceCharting", "PriceCharting" not in links)

    host, query = parts(links["eBay sold"])
    check("points at ebay.com", host == "www.ebay.com", host)
    check("restricted to sold", query.get("LH_Sold") == ["1"], query.get("LH_Sold"))
    check("restricted to completed", query.get("LH_Complete") == ["1"], query.get("LH_Complete"))
    check("query carries year/brand/player",
          all(term in query["_nkw"][0] for term in ["2025", "Topps Chrome", "James Harden"]),
          query["_nkw"][0])

    guide_host, guide_query = parts(links["SportsCardsPro"])
    check("guide host correct", guide_host == "www.sportscardspro.com", guide_host)
    check("guide gets same query", "James Harden" in guide_query["q"][0], guide_query["q"][0])

    print("\n2. Pokemon routes to PriceCharting instead")
    pokemon = {
        "card_category": "pokemon", "game": "Pokemon TCG", "name": "Charizard ex",
        "set": "151", "card_number": "199/165", "rarity": "Special Illustration Rare",
    }
    poke_links = dict((label, url) for label, url in build_comp_links(pokemon))
    check("TCG goes to PriceCharting", "PriceCharting" in poke_links, list(poke_links))
    check("not sent to SportsCardsPro", "SportsCardsPro" not in poke_links)
    check("still offers eBay sold", "eBay sold" in poke_links)

    print("\n3. Special characters are encoded, not broken")
    _, poke_query = parts(poke_links["eBay sold"])
    check("slash in card number survives", "199/165" in poke_query["_nkw"][0], poke_query["_nkw"][0])
    check("url has no raw spaces", " " not in poke_links["eBay sold"])
    check("ampersand-safe encoding",
          "&" not in poke_query["_nkw"][0] or "%26" in poke_links["eBay sold"])

    print("\n4. Graded cards search for the slab, not the raw card")
    graded = dict(sports)
    graded.update({"is_graded": True, "grader": "PSA", "grade": "10"})
    _, graded_query = parts(dict(build_comp_links(graded))["eBay sold"])
    check("grader in query", "PSA" in graded_query["_nkw"][0], graded_query["_nkw"][0])
    check("grade in query", "10" in graded_query["_nkw"][0])

    print("\n5. Unidentified card offers no misleading links")
    check("empty card data yields nothing", build_comp_links({}) == [])
    check("blank values yield nothing", build_comp_links({"name": "  ", "year": ""}) == [])

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
