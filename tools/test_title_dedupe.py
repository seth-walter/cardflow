"""Title/query dedupe tests, built from the real duplicated titles seen on live listings.

The risk in this fix is over-stripping, so most of these checks are about what must
survive. No API calls.

Run: python tools/test_title_dedupe.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.listing_builder import build_comp_query
from services.openai_service import build_seo_title
from utils.text_utils import dedupe_phrases

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def main():
    print("\n1. The two real broken listings")
    castle = {
        "card_category": "sports", "year": "2025", "brand": "Topps",
        "set": "2025-26 Topps Chrome Updates Basketball",
        "player_athlete": "Stephon Castle", "card_number": "HM-SC", "sport": "Basketball",
    }
    harden = {
        "card_category": "sports", "year": "2025", "brand": "Topps", "set": "Topps Chrome",
        "player_athlete": "James Harden", "card_number": "SE-7", "sport": "Basketball",
    }

    castle_title = build_seo_title(castle, {})
    harden_title = build_seo_title(harden, {})
    print("    " + castle_title)
    print("    " + harden_title)

    check("Castle: no repeated Topps", castle_title.lower().count("topps") == 1, castle_title)
    check("Castle: no repeated 2025", castle_title.count("2025") == 1, castle_title)
    check("Castle: keeps the specific set", "2025-26 Topps Chrome Updates" in castle_title)
    check("Castle: keeps player", "Stephon Castle" in castle_title)
    check("Castle: keeps card number", "#HM-SC" in castle_title)
    check("Harden: no repeated Topps", harden_title.lower().count("topps") == 1, harden_title)
    check("Harden: reads correctly",
          harden_title == "2025 Topps Chrome James Harden #SE-7", harden_title)

    print("\n2. The same fix reaches the comp query that prices the card")
    castle_query = build_comp_query(castle)
    harden_query = build_comp_query(harden)
    print("    " + castle_query)
    print("    " + harden_query)
    check("Castle query deduped", castle_query.lower().count("topps") == 1, castle_query)
    check("Harden query deduped", harden_query.lower().count("topps") == 1, harden_query)
    check("query keeps the player", "James Harden" in harden_query)
    check("query keeps the number", "SE-7" in harden_query)

    print("\n3. The description's headline is deduped the same way")
    from core.listing_builder import build_description
    castle_desc = build_description(castle, {})
    harden_desc = build_description(harden, {})
    headline = harden_desc.split("</p>")[0].replace("<p>", "")
    print("    " + headline)
    check("Castle description: no repeated Topps",
          castle_desc.split("</p>")[0].lower().count("topps") == 1,
          castle_desc.split("</p>")[0])
    check("Harden description: no repeated Topps", headline.lower().count("topps") == 1, headline)
    check("description keeps the player", "James Harden" in headline)
    check("description keeps the number", "#SE-7" in headline)

    print("\n4. Distinct phrases that merely share a word must survive")
    check("Panini Prizm + Silver Prizm",
          dedupe_phrases(["Panini Prizm", "Silver Prizm"]) == ["Panini Prizm", "Silver Prizm"],
          dedupe_phrases(["Panini Prizm", "Silver Prizm"]))
    check("Chrome + Chrome Update Series keeps the longer",
          dedupe_phrases(["Chrome", "Chrome Update Series"]) == ["Chrome Update Series"])
    check("two different players kept",
          len(dedupe_phrases(["Michael Jordan", "Scottie Pippen"])) == 2)
    check("set and player sharing a word both kept",
          dedupe_phrases(["Bowman Chrome", "Chrome Refractor"]) == ["Bowman Chrome", "Chrome Refractor"],
          dedupe_phrases(["Bowman Chrome", "Chrome Refractor"]))

    print("\n4. Numbers are compared as tokens, not substrings")
    check("year 2012 does not swallow card number 12",
          dedupe_phrases(["2012", "12"]) == ["2012", "12"], dedupe_phrases(["2012", "12"]))
    check("number 10 survives beside 100", dedupe_phrases(["100", "10"]) == ["100", "10"])
    check("year inside a longer year range is dropped",
          dedupe_phrases(["2025", "2025-26 Topps Chrome"]) == ["2025-26 Topps Chrome"])

    print("\n5. Repeats differing only by case or punctuation collapse to one")
    check("case-insensitive repeat", dedupe_phrases(["Topps", "TOPPS"]) == ["Topps"])
    check("punctuation variant kept once",
          dedupe_phrases(["SE-7", "SE 7"]) == ["SE-7"], dedupe_phrases(["SE-7", "SE 7"]))
    check("blank values dropped", dedupe_phrases(["", None, "  ", "Topps"]) == ["Topps"])
    check("empty input is safe", dedupe_phrases([]) == [])

    print("\n6. Graded and Pokemon titles still build correctly")
    graded = dict(harden)
    graded.update({"is_graded": True, "grader": "PSA", "grade": "10"})
    graded_title = build_seo_title(graded, {})
    check("grade appended", graded_title.endswith("PSA 10"), graded_title)
    check("still deduped", graded_title.lower().count("topps") == 1, graded_title)

    pokemon = {
        "card_category": "pokemon", "game": "Pokemon TCG", "name": "Charizard ex",
        "set": "Pokemon 151", "card_number": "199/165", "year": "2023",
        "rarity": "Special Illustration Rare",
    }
    poke_title = build_seo_title(pokemon, {})
    print("    " + poke_title)
    check("Pokemon not repeated", poke_title.lower().count("pokemon") == 1, poke_title)
    check("Pokemon keeps the name", "Charizard ex" in poke_title)

    print("\n7. eBay's 80 character cap is still enforced")
    monster = {
        "card_category": "sports", "year": "2025",
        "brand": "Topps Chrome Update Series Sapphire Selections Superfractor",
        "set": "Topps Chrome Update Series Sapphire Selections Superfractor Refractor Parallel",
        "player_athlete": "Shai Gilgeous-Alexander", "card_number": "SGA-1",
        "variant": "Gold Refractor Numbered", "sport": "Basketball",
    }
    long_title = build_seo_title(monster, {})
    check("trimmed to 80", len(long_title) <= 80, len(long_title))

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
