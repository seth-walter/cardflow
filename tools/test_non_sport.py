"""Non-sport / entertainment card tests (Topps Disney, Star Wars, Marvel...).

The main risk in this feature is a sports or TCG card being rerouted, so roughly half
of these checks assert that existing routing is unchanged. No API calls.

Run: python tools/test_non_sport.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.batch_processor import ensure_card_category, fallback_category, resolve_category
from core.listing_builder import build_comp_query, is_tcg_card
from services.card_aspects import (
    build_card_aspects, derive_sport, detect_franchise, is_non_sport_card,
)
from services.comp_links import build_comp_links
from services.openai_service import build_seo_title
from services.pokemon_lookup import is_probably_pokemon
from services.sports_lookup import is_sports_card

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


DISNEY = {
    "card_category": "other", "name": "Mickey Mouse", "card_name": "Mickey Mouse",
    "character": "Mickey Mouse", "brand": "Topps", "manufacturer": "Topps",
    "set": "Disney 100", "year": "2023", "card_number": "1", "variant": "Silver",
    "finish": "Foil", "sport": "", "league": "", "team": "", "player_athlete": "",
    "condition_estimate": "Near Mint", "is_graded": False,
}

STAR_WARS = {
    "card_category": "other", "name": "Darth Vader", "character": "Darth Vader",
    "brand": "Topps", "set": "Star Wars Chrome Black", "year": "2022",
    "card_number": "12", "sport": "", "league": "", "team": "",
}

SPORTS = {
    "card_category": "sports", "year": "2025", "brand": "Topps", "set": "Topps Chrome",
    "player_athlete": "James Harden", "card_number": "SE-7", "sport": "Basketball",
    "league": "NBA", "team": "Los Angeles Clippers",
}

POKEMON = {
    "card_category": "pokemon", "game": "Pokemon TCG", "name": "Charizard ex",
    "set": "151", "card_number": "199/165", "rarity": "Special Illustration Rare",
    "year": "2023",
}


def main():
    print("\n1. Detection")
    check("Disney recognised", is_non_sport_card(DISNEY) is True)
    check("Disney franchise named", detect_franchise(DISNEY) == "Disney", detect_franchise(DISNEY))
    check("Star Wars recognised", is_non_sport_card(STAR_WARS) is True)
    check("Star Wars franchise named", detect_franchise(STAR_WARS) == "Star Wars",
          detect_franchise(STAR_WARS))
    check("sports card NOT rerouted", is_non_sport_card(SPORTS) is False)
    check("pokemon NOT rerouted", is_non_sport_card(POKEMON) is False)
    check("empty card is not non-sport", is_non_sport_card({}) is False)
    check("unknown card falls back to sports handling",
          is_non_sport_card({"name": "Someone", "brand": "Topps"}) is False)

    print("\n2. Sports identity always wins over a franchise word")
    # A real risk: a football player on a team whose name matches a franchise word.
    tricky = {"card_category": "sports", "player_athlete": "Chris Godwin",
              "sport": "Football", "league": "NFL", "team": "Tampa Bay Buccaneers",
              "brand": "Panini", "set": "Marvel Team-Ups"}
    check("sports signal beats franchise text", is_non_sport_card(tricky) is False,
          detect_franchise(tricky))

    print("\n3. eBay item specifics")
    aspects = build_card_aspects(DISNEY)
    check("Type is non-sport", aspects.get("Type") == ["Non-Sport Trading Card"],
          aspects.get("Type"))
    check("Character populated", aspects.get("Character") == ["Mickey Mouse"],
          aspects.get("Character"))
    check("Franchise populated", aspects.get("Franchise") == ["Disney"], aspects.get("Franchise"))
    check("no Player/Athlete aspect", "Player/Athlete" not in aspects, list(aspects))
    check("no Sport aspect", "Sport" not in aspects, list(aspects))
    check("set kept", aspects.get("Set") == ["Disney 100"])
    check("card number kept", aspects.get("Card Number") == ["1"])
    check("manufacturer kept", aspects.get("Manufacturer") == ["Topps"])
    check("parallel kept", aspects.get("Parallel/Variety") == ["Silver"])
    check("condition kept", aspects.get("Card Condition") == ["Near Mint"])
    check("graded flag kept", aspects.get("Graded") == ["No"])

    print("\n4. Sports and Pokemon aspects unchanged")
    sports_aspects = build_card_aspects(SPORTS)
    check("sports still Sports Trading Card",
          sports_aspects.get("Type") == ["Sports Trading Card"], sports_aspects.get("Type"))
    check("sports still Player/Athlete",
          sports_aspects.get("Player/Athlete") == ["James Harden"],
          sports_aspects.get("Player/Athlete"))
    poke_aspects = build_card_aspects(POKEMON)
    check("pokemon still a CCG", poke_aspects.get("Type") == ["Collectible Card Game"],
          poke_aspects.get("Type"))
    check("pokemon keeps Character", "Character" in poke_aspects)

    print("\n5. Category fallback (used when eBay's suggestion is empty)")
    check("non-sport -> 183050", fallback_category(DISNEY)[0] == "183050",
          fallback_category(DISNEY))
    check("sports -> 261328", fallback_category(SPORTS)[0] == "261328", fallback_category(SPORTS))
    check("pokemon -> 183454", fallback_category(POKEMON)[0] == "183454",
          fallback_category(POKEMON))

    print("\n6. Comp links point at a guide that carries the card")
    disney_links = dict(build_comp_links(DISNEY))
    check("Disney goes to PriceCharting", "PriceCharting" in disney_links, list(disney_links))
    check("Disney not sent to SportsCardsPro", "SportsCardsPro" not in disney_links)
    check("Disney still gets eBay sold", "eBay sold" in disney_links)
    sports_links = dict(build_comp_links(SPORTS))
    check("sports still goes to SportsCardsPro", "SportsCardsPro" in sports_links,
          list(sports_links))

    print("\n7. Title and comp query")
    title = build_seo_title(DISNEY, {})
    print("    " + title)
    check("title reads correctly", title == "2023 Topps Disney 100 Mickey Mouse #1 Silver", title)
    check("title within 80 chars", len(title) <= 80, len(title))
    query = build_comp_query(DISNEY)
    check("comp query has set and character",
          "Disney 100" in query and "Mickey Mouse" in query, query)

    print("\n8. Enrichment paths stay clear of non-sport cards")
    check("pokemon lookup skips Disney", is_probably_pokemon(DISNEY) is False)
    check("sports lookup skips Disney", is_sports_card(DISNEY) is False)
    check("is_tcg_card skips Disney", is_tcg_card(DISNEY) is False)


    print("\n9. Category validation (the four real publish failures)")
    nemo = {"card_category": "other", "character": "Nemo", "franchise": "Pixar",
            "brand": "Topps", "set": "Topps Chrome Neon", "year": "2026"}
    mcqueen = {"card_category": "other", "character": "Lightning McQueen",
               "franchise": "Pixar", "brand": "Topps", "set": "Topps Chrome", "year": "2026"}

    junk = [{"category": {"categoryId": "143", "categoryName": "Other Disney Pins & Patches"}}]
    check("Disney Pins suggestion rejected", resolve_category(nemo, junk)[0] == "183050",
          resolve_category(nemo, junk))

    washer = [{"category": {"categoryId": "99697", "categoryName": "Washer & Dryer Parts"}}]
    check("Washer & Dryer Parts rejected", resolve_category(mcqueen, washer)[0] == "183050",
          resolve_category(mcqueen, washer))

    sports_sugg = [{"category": {"categoryId": "261328", "categoryName": "Trading Card Singles"}}]
    check("sports category rejected for a Disney card",
          resolve_category(nemo, sports_sugg)[0] == "183050", resolve_category(nemo, sports_sugg))

    good = [{"category": {"categoryId": "183050", "categoryName": "Trading Card Singles"}}]
    check("matching suggestion accepted", resolve_category(nemo, good)[0] == "183050")
    check("no suggestions still resolves", resolve_category(nemo, [])[0] == "183050")
    check("sports card keeps sports category",
          resolve_category(SPORTS, sports_sugg)[0] == "261328")
    check("pokemon keeps CCG category",
          resolve_category(POKEMON, [{"category": {"categoryId": "183454",
                                                   "categoryName": "CCG"}}])[0] == "183454")

    print("\n10. Publish-time guard repairs cards already stored with a bad category")
    check("stored 143 corrected",
          ensure_card_category(nemo, "143", "Other Disney Pins")[0] == "183050")
    check("stored 99697 corrected",
          ensure_card_category(mcqueen, "99697", "Washer & Dryer Parts")[0] == "183050")
    check("stored 261328 corrected for Disney",
          ensure_card_category(nemo, "261328", "Trading Card Singles")[0] == "183050")
    check("correct category left alone",
          ensure_card_category(SPORTS, "261328", "Trading Card Singles")
          == ("261328", "Trading Card Singles"))

    print("\n11. Sports listings always carry a Sport aspect")
    check("derived from league", derive_sport({"league": "NBA"}) == "Basketball")
    check("derived from a set name", derive_sport({"set": "Panini Prizm Football"}) == "Football")
    check("derived from sport-bearing team text",
          derive_sport({"team": "Alabama Football"}) == "Football")
    # Documented limit: a bare team nickname carries no sport keyword, so it yields "".
    check("bare team nickname yields nothing",
          derive_sport({"team": "Tampa Bay Buccaneers"}) == "",
          derive_sport({"team": "Tampa Bay Buccaneers"}))
    check("explicit sport wins", derive_sport({"sport": "Baseball", "league": "NBA"}) == "Baseball")
    no_sport = {"card_category": "sports", "league": "NFL", "player_athlete": "Some Player",
                "brand": "Panini", "set": "Prizm"}
    check("missing Sport is filled in",
          build_card_aspects(no_sport).get("Sport") == ["Football"],
          build_card_aspects(no_sport).get("Sport"))
    check("non-sport card still has no Sport aspect", "Sport" not in build_card_aspects(DISNEY))

    print("\n12. ESCO music cards (ESCO x LISA)")
    from services.openai_service import title_warnings
    lisa = {"card_category": "other", "brand": "ESCO", "set": "ESCO x LISA",
            "character": "LISA", "year": "2026", "card_number": "37",
            "insert_set": "PRESS PLAY", "parallel": "Pink Foil", "serial_number": "412/800"}
    check("ESCO recognised as non-sport", is_non_sport_card(lisa) is True)
    check("routed to non-sport category", fallback_category(lisa)[0] == "183050",
          fallback_category(lisa))
    title = build_seo_title(lisa, {})
    print("    " + title)
    check("title keeps subset, foil and run",
          title == "2026 ESCO x LISA #37 PRESS PLAY Pink Foil /800", title)
    check("/800 is a real print run", not title_warnings(lisa), title_warnings(lisa))
    lisa_aspects = build_card_aspects(lisa)
    check("artist is the franchise", lisa_aspects.get("Franchise") == ["LISA"],
          lisa_aspects.get("Franchise"))
    check("serial numbered feature", "Serial Numbered" in lisa_aspects.get("Features", []),
          lisa_aspects.get("Features"))
    check("print run sent", lisa_aspects.get("Print Run") == ["800"], lisa_aspects.get("Print Run"))
    check("no Sport aspect", "Sport" not in lisa_aspects)
    no_franchise = dict(lisa, character="", franchise="", name="LISA")
    check("found by brand even without a character", is_non_sport_card(no_franchise) is True)
    auto = dict(lisa, insert_set="Alter Ego", parallel="Gold Foil", serial_number="",
                autographed="Yes", signed_by="LISA", card_number="12")
    check("auto title", build_seo_title(auto, {}) == "2026 ESCO x LISA #12 Alter Ego Gold Foil Auto",
          build_seo_title(auto, {}))
    check("'fresco' is not ESCO", is_non_sport_card({"brand": "Fresco", "name": "X"}) is False)
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
