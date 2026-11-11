"""eBay item-specific validity rules, driven by real publish rejections.

eBay validates aspect values, and a bad one fails the whole listing at publish time.
Each check here corresponds to an error eBay actually returned. No API calls.

Run: python tools/test_aspects.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.card_aspects import build_card_aspects, match_title_year, numeric_year

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


SPORTS = {"card_category": "sports", "year": "2025-26", "brand": "Topps",
          "set": "Topps Chrome", "player_athlete": "James Harden", "sport": "Basketball",
          "league": "NBA", "team": "Los Angeles Clippers", "card_number": "SE-7"}
DISNEY = {"card_category": "other", "character": "Nemo", "franchise": "Pixar",
          "brand": "Topps", "set": "Topps Chrome Neon", "year": "2025-26", "card_number": "29"}
POKEMON = {"card_category": "pokemon", "game": "Pokemon TCG", "name": "Charizard ex",
           "set": "151", "card_number": "199/165", "year": "2023"}
ONE_PIECE = {"card_category": "one_piece", "game": "One Piece Card Game", "name": "Luffy",
             "set": "OP-01", "card_number": "OP01-001", "year": "2022-23"}


def main():
    print("\n1. Year Manufactured must be numeric (eBay error 25002)")
    cases = [("2025-26", "2025"), ("1999-00", "1999"), ("2023", "2023"),
             ("2025/26", "2025"), ("2026 Topps Chrome", "2026"),
             ("", ""), ("20th Anniversary", ""), (None, "")]
    for value, expected in cases:
        got = numeric_year(value)
        check("{!r} -> {!r}".format(value, expected), got == expected, got)

    print("\n2. Every card type sends a numeric Year Manufactured")
    for label, card in [("sports", SPORTS), ("non-sport", DISNEY),
                        ("pokemon", POKEMON), ("one piece", ONE_PIECE)]:
        aspects = build_card_aspects(card)
        year_values = aspects.get("Year Manufactured", [])
        ok = bool(year_values) and year_values[0].isdigit() and len(year_values[0]) == 4
        check("{} year is a plain 4-digit number".format(label), ok, year_values)

    print("\n3. Season keeps the readable range (sports only)")
    sports_aspects = build_card_aspects(SPORTS)
    check("Season keeps 2025-26", sports_aspects.get("Season") == ["2025-26"],
          sports_aspects.get("Season"))
    check("Year Manufactured trimmed to 2025",
          sports_aspects.get("Year Manufactured") == ["2025"],
          sports_aspects.get("Year Manufactured"))
    check("non-sport sends no Season", "Season" not in build_card_aspects(DISNEY))

    print("\n4. A card with no usable year simply omits the aspect")
    no_year = dict(DISNEY)
    no_year["year"] = "unknown"
    check("no empty Year Manufactured sent",
          "Year Manufactured" not in build_card_aspects(no_year),
          build_card_aspects(no_year).get("Year Manufactured"))

    print("\n5. Nothing else regressed on the sports card")
    check("Sport present", sports_aspects.get("Sport") == ["Basketball"])
    check("Player/Athlete present", sports_aspects.get("Player/Athlete") == ["James Harden"])
    check("Type present", sports_aspects.get("Type") == ["Sports Trading Card"])

    print("\n6. No value over 65 characters (eBay error 25002, 'is too long')")
    # The real rejection: "name" held the whole card description and became the athlete.
    lawson = {"card_category": "sports", "year": "2024", "brand": "Topps",
              "set": "Topps Chrome Formula 1", "sport": "Formula 1",
              "name": "Liam Lawson 2024 Topps Chrome Formula 1 Chrome Autographs Orange "
                      "X-Refractor /25 #CAC-LL",
              "player_athlete": "Liam Lawson", "card_number": "CAC-LL",
              "parallel": "Orange X-Refractor " * 5}
    aspects = build_card_aspects(lawson)
    check("athlete comes from player_athlete", aspects.get("Player/Athlete") == ["Liam Lawson"],
          aspects.get("Player/Athlete"))
    longest = max((v for values in aspects.values() for v in values), key=len)
    check("every value fits", len(longest) <= 65, (len(longest), longest))
    check("long values cut on a word boundary", not longest.endswith("X-Refrac"), longest)
    no_athlete = dict(lawson, player_athlete="")
    athlete = build_card_aspects(no_athlete).get("Player/Athlete", [""])[0]
    check("a description-sized name is still cut to fit", 0 < len(athlete) <= 65, athlete)

    print("\n7. A year fixed in the title reaches the item specifics")
    # The real case: detected 2024, seller retitled "2026 Topps Marvel ...", eBay got 2024.
    doom = {"card_category": "other", "character": "Doctor Doom", "brand": "Topps",
            "set": "Topps Marvel", "year": "2024", "card_number": "36"}
    fixed = match_title_year(doom, "2026 Topps Marvel Doctor Doom #36 Refractor")
    check("title year wins", fixed["year"] == "2026", fixed.get("year"))
    check("aspects follow", build_card_aspects(fixed).get("Year Manufactured") == ["2026"],
          build_card_aspects(fixed).get("Year Manufactured"))
    check("detected data left untouched", doom["year"] == "2024")
    season = match_title_year(dict(SPORTS), "2025-26 Topps Chrome James Harden #SE-7")
    check("matching season kept as is", season["year"] == "2025-26", season["year"])
    retitled = match_title_year(dict(SPORTS), "2024-25 Topps Chrome James Harden #SE-7")
    check("season retitled", build_card_aspects(retitled).get("Season") == ["2024-25"],
          build_card_aspects(retitled).get("Season"))
    check("title without a year changes nothing",
          match_title_year(doom, "Topps Marvel Doctor Doom #36") is doom)

    print("\n8. No symbols eBay bans (25019 'LP_SNB_CutsieCharacters')")
    from services.openai_service import build_seo_title
    from utils.text_utils import ebay_safe
    for raw, want in [("SR★★", "SR 2 Star"), ("SR★", "SR 1 Star"), ("UR ☆☆☆", "UR 3 Star"),
                      ("Pokémon™ Charizard®", "Pokémon Charizard"), ("½ Price ♥", "1/2 Price"),
                      ("Ｒｅ：ＺＥＲＯ", "Re:ZERO"), ("Gold – Prizm", "Gold - Prizm")]:
        check("{!r} -> {!r}".format(raw, want), ebay_safe(raw) == want, ebay_safe(raw))

    # The real rejection: a Union Arena SR★★.
    emilia = {"card_category": "union_arena", "game": "Union Arena", "name": "Emilia",
              "franchise": "Re:ZERO Starting Life in Another World", "rarity": "SR★★",
              "card_number": "UE04BT/RZR-1-070"}
    title = build_seo_title(emilia, {})
    check("title keeps the rarity, loses the stars", "SR 2 Star" in title and "★" not in title, title)
    values = [v for vals in build_card_aspects(emilia).values() for v in vals]
    check("no item specific carries a star", not any("★" in v for v in values),
          [v for v in values if "★" in v])

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
