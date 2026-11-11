"""One Piece identification and the title/description sync. Network is stubbed.

Built from the cards that came out wrong in a real scanning session.

Run: python tools/test_onepiece.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.listing_builder import apply_title_to_description, build_comp_query
import services.onepiece_lookup as op
from services.onepiece_lookup import (
    enrich_one_piece_card_data, is_probably_one_piece, parse_card_code,
    strip_card_number_suffix,
)
from services.openai_service import build_seo_title

failures = []

CATALOGUE = {
    "PRB-02": [{"card_set_id": "PRB02-006", "card_name": "Roronoa Zoro - PRB02-006",
                "set_name": "Premium Booster -The Best- Vol. 2", "rarity": "R",
                "card_type": "Character", "market_price": 4.77}],
    "OP-16": [{"card_set_id": "OP16-095", "card_name": "Monkey.D.Luffy (095)",
               "set_name": "The Time of Battle", "rarity": "R",
               "card_type": "Character", "market_price": 0.21}],
    "ST-29": [{"card_set_id": "ST29-008", "card_name": "Nami (Full Art)",
               "set_name": "Starter Deck 29: Egghead", "rarity": "C",
               "card_type": "Character", "market_price": 24.29}],
}


def version(code, name, rarity, price, image_id, set_name="", set_id=""):
    return {"card_set_id": code, "card_name": name, "rarity": rarity, "market_price": price,
            "card_image_id": image_id, "set_name": set_name, "set_id": set_id,
            "card_image": "https://example/" + image_id + ".jpg", "card_type": "Character"}


# Every printing of a code, as /sets/card/<code>/ returns them: the four cards from the
# 2026-11-11 session that were drafted as $0.99 auctions.
VERSIONS = {
    "OP16-063": [version("OP16-063", "Kuzan", "R", 0.13, "OP16-063", "The Time of Battle", "OP-16"),
                 version("OP16-063", "Kuzan (Alternate Art)", "R", 3.06, "OP16-063_p1",
                         "The Time of Battle", "OP-16"),
                 version("OP16-063", "Kuzan (Manga)", "R", 1073.39, "OP16-063_p2",
                         "The Time of Battle", "OP-16")],
    "OP13-042": [version("OP13-042", "Edward.Newgate", "SR", 0.7, "OP13-042",
                         "Carrying On His Will", "OP-13"),
                 version("OP13-042", "Edward.Newgate (Alternate Art)", "SR", 12.34,
                         "OP13-042_p1", "Carrying On His Will", "OP-13"),
                 version("OP13-042", "Edward.Newgate (SP)", "SR", 105.14, "OP13-042_p2",
                         "Adventure on Kami's Island", "OP15-EB04")],
    "EB01-007": [version("EB01-007", "Yamato", "C", 0.19, "EB01-007",
                         "Extra Booster: Memorial Collection", "EB-01")],
    "EB04-007": [version("EB04-007", "Roronoa Zoro (EB04-007)", "SR", 16.32, "EB04-007"),
                 version("EB04-007", "Roronoa Zoro (EB04-007) (SP)", "SR", 364.56,
                         "EB04-007_p2", "Lands of Wa", "OP-17")],
}
for cards in CATALOGUE.values():
    for card in cards:
        VERSIONS.setdefault(card["card_set_id"], []).append(card)


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def main():
    op.CACHE_PATH = str(Path(tempfile.mkdtemp()) / "op_cache.json")
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        key = url.rstrip("/").rsplit("/", 1)[-1]

        class R:
            status_code = 200
            def json(self):
                if "/sets/card/" in url:
                    return VERSIONS.get(key, [])
                return CATALOGUE.get(key, [])
        return R()

    op.requests.get = fake_get

    print("\n1. The card code is found wherever the model put it")
    check("split across set and number",
          parse_card_code({"set": "OP09", "card_number": "068"})["card_set_id"] == "OP09-068")
    check("already joined",
          parse_card_code({"card_number": "OP16-095"})["card_set_id"] == "OP16-095")
    check("starter decks", parse_card_code({"card_number": "ST29-008"})["set_id"] == "ST-29")
    check("premium boosters", parse_card_code({"card_number": "PRB02-006"})["set_id"] == "PRB-02")
    check("extra boosters", parse_card_code({"card_number": "EB03-054"})["set_id"] == "EB-03")
    check("padded to the catalogue's shape",
          parse_card_code({"set": "OP9", "card_number": "68"})["card_set_id"] == "OP09-068")
    check("no code at all", parse_card_code({"name": "Luffy"}) is None)

    print("\n2. Catalogue bookkeeping is stripped from names")
    check("trailing code removed", strip_card_number_suffix("Roronoa Zoro - PRB02-006") == "Roronoa Zoro")
    check("reprint number removed", strip_card_number_suffix("Monkey.D.Luffy (095)") == "Monkey.D.Luffy")
    check("descriptive parentheses kept",
          strip_card_number_suffix("Nami (Full Art)") == "Nami (Full Art)")

    print("\n3. The real failures from the scanning session")
    zoro = enrich_one_piece_card_data({
        "card_category": "one_piece", "game": "One Piece Card Game", "name": "Roronoa Zoro",
        "brand": "Round One Piece", "manufacturer": "Bandai", "set": "PRB02",
        "card_number": "006", "rarity": "R"})
    check("junk brand replaced", zoro["brand"] == "Bandai", zoro["brand"])
    check("set code becomes the set name",
          zoro["set"] == "Premium Booster -The Best- Vol. 2", zoro["set"])
    check("card number is the full code", zoro["card_number"] == "PRB02-006")
    check("name loses the repeated code", zoro["name"] == "Roronoa Zoro", zoro["name"])
    check("market price captured", zoro["onepiece_market_price"] == 4.77)

    title = build_seo_title(zoro, {})
    print("    " + title)
    check("no 'Round' in the title", "Round" not in title, title)
    check("set name in the title", "Premium Booster" in title)
    check("full code in the title", "PRB02-006" in title)
    check("title within eBay's limit", len(title) <= 80, len(title))

    luffy = enrich_one_piece_card_data({
        "card_category": "one_piece", "game": "One Piece Card Game", "name": "Monkey.D. Luffy",
        "brand": "One Piece", "set": "OP16", "card_number": "095", "rarity": "R"})
    check("name normalised to the catalogue", luffy["name"] == "Monkey.D.Luffy", luffy["name"])
    check("set resolved", luffy["set"] == "The Time of Battle")

    print("\n4. Comp queries improve as a result")
    query = build_comp_query(luffy)
    check("query carries the set name", "Time of Battle" in query, query)
    check("query carries the full code", "OP16-095" in query, query)

    print("\n5. Card versions are fetched once and cached")

    def fresh_cache():
        op.CACHE_PATH = str(Path(tempfile.mkdtemp()) / "op_cache.json")
        calls[:] = []

    fresh_cache()
    for _ in range(3):
        enrich_one_piece_card_data({"game": "One Piece Card Game", "card_number": "OP16-095"})
    check("three lookups cost one fetch", len(calls) == 1, calls)
    check("every printing of the code is asked for", calls and "/sets/card/OP16-095/" in calls[0],
          calls)

    fresh_cache()
    enrich_one_piece_card_data({"game": "One Piece Card Game", "card_number": "OP16-095"})
    enrich_one_piece_card_data({"game": "One Piece Card Game", "card_number": "ST29-008"})
    check("different codes fetched separately", len(calls) == 2, calls)

    print("\n6. A lookup miss never makes a listing worse")
    unknown = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Some Card", "set": "OP99",
        "card_number": "001", "brand": "Round One Piece"})
    check("code still normalised", unknown["card_number"] == "OP99-001", unknown["card_number"])
    check("brand still corrected", unknown["brand"] == "Bandai")
    check("name left alone", unknown["name"] == "Some Card")

    untouched = {"card_category": "sports", "player_athlete": "James Harden",
                 "brand": "Topps", "card_number": "SE-7"}
    check("non One Piece cards are ignored",
          enrich_one_piece_card_data(untouched) == untouched)
    check("detector says no", is_probably_one_piece(untouched) is False)

    print("\n7. Editing a title rewrites only the description's first line")
    description = ("<p>Old headline #12</p>\n"
                   "<p><strong>Condition:</strong> Near Mint</p>\n"
                   "<ul><li><strong>Corners:</strong> Sharp</li></ul>\n"
                   "<p>Shipped securely.</p>")
    updated = apply_title_to_description(description, "New headline #12 Refractor")
    check("headline replaced", "New headline #12 Refractor" in updated)
    check("old headline gone", "Old headline" not in updated)
    check("condition kept", "Near Mint" in updated)
    check("breakdown kept", "Corners" in updated)
    check("closing lines kept", "Shipped securely" in updated)
    check("an empty description becomes the title",
          apply_title_to_description("", "Title") == "<p>Title</p>")
    check("a blank title changes nothing",
          apply_title_to_description(description, "   ") == description)
    check("running twice is stable",
          apply_title_to_description(updated, "New headline #12 Refractor") == updated)

    print("\n8. The printing is chosen by eye, and priced as that printing")
    from services.ebay_auction import choose_listing_format
    picked = {}

    def fake_pick(front_data_url, versions):
        picked["labels"] = [op.version_label(v["card_name"]) or "Base" for v in versions]
        index = picked["labels"].index(picked["answer"]) if picked["answer"] in picked["labels"] else None
        return {"index": index, "confident": picked.get("confident", True)}

    op.pick_version = fake_pick
    fresh_cache()

    picked.update(answer="Manga", confident=True)
    kuzan = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Kuzan", "card_number": "OP16-063",
        "rarity": "R", "parallel": "Parallel"}, "data:image/jpeg;base64,x")
    check("all three printings offered", picked["labels"] == ["Base", "Alt Art", "Manga"],
          picked["labels"])
    check("Manga, not 'Parallel'", kuzan["parallel"] == "Manga", kuzan["parallel"])
    check("priced as the Manga, not the $0.13 base", kuzan["onepiece_market_price"] == 1073.39,
          kuzan.get("onepiece_market_price"))
    title = build_seo_title(kuzan, {})
    print("    " + title)
    check("title says Manga", title.endswith("#OP16-063 R Manga"), title)

    picked.update(answer="SP", confident=True)
    newgate = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Edward.Newgate", "card_number": "OP13-042",
        "rarity": "SR", "parallel": "Special Rare"}, "data:image/jpeg;base64,x")
    title = build_seo_title(newgate, {})
    print("    " + title)
    check("SP, rarity kept once", "#OP13-042 SR SP" in title and "Special" not in title, title)
    check("SP priced as the SP", newgate["onepiece_market_price"] == 105.14)

    print("\n9. A misread set digit does not swap in another character")
    picked.update(answer="SP", confident=True)
    zoro = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Roronoa Zoro", "card_number": "EB01-007",
        "rarity": "SR", "parallel": "SP"}, "data:image/jpeg;base64,x")
    check("EB01 corrected to EB04", zoro["card_number"] == "EB04-007", zoro["card_number"])
    check("still Zoro, never Yamato", zoro["name"] == "Roronoa Zoro", zoro["name"])
    check("the SP printing", zoro["parallel"] == "SP" and zoro["onepiece_market_price"] == 364.56,
          (zoro["parallel"], zoro.get("onepiece_market_price")))

    nobody = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Boa Hancock", "card_number": "EB01-007"})
    check("no catalogue card with that name: reading kept", nobody["name"] == "Boa Hancock")
    check("and flagged", nobody.get("identity_review"), nobody.get("identity_review"))

    print("\n10. An unsure printing is flagged, unpriced, and never a cheap auction")
    picked.update(answer="Manga", confident=False)
    unsure = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Kuzan", "card_number": "OP16-063"},
        "data:image/jpeg;base64,x")
    check("no catalogue price on a guess", "onepiece_market_price" not in unsure)
    check("flagged for review", unsure.get("identity_review"), unsure.get("identity_review"))

    picked.update(answer="Base", confident=True)
    clash = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Edward.Newgate", "card_number": "OP13-042",
        "parallel": "SP"}, "data:image/jpeg;base64,x")
    check("images say base, card says SP: flagged", clash.get("identity_review")
          and "onepiece_market_price" not in clash, clash.get("identity_review"))

    no_image = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Edward.Newgate", "card_number": "OP13-042",
        "parallel": "Special Card"})
    check("no scan to compare: the SP hint picks the printing", no_image["parallel"] == "SP")
    check("but it is checked, not trusted", no_image.get("identity_review")
          and "onepiece_market_price" not in no_image)

    missing = enrich_one_piece_card_data({
        "game": "One Piece Card Game", "name": "Yamato", "card_number": "EB01-007",
        "parallel": "Manga"})
    check("catalogue lacks the Manga: not priced as the base",
          "onepiece_market_price" not in missing and missing.get("identity_review"),
          missing.get("identity_review"))
    check("keeps the hobby word", missing["parallel"] == "Manga", missing["parallel"])

    plan = choose_listing_format({"estimated_price": 0.99, "needs_review": True}, 0.99)
    check("a flagged price is never defaulted to auction", plan["format"] == "FIXED_PRICE", plan)

    print("\n11. The version survives the 80-character cut")
    long_set = build_seo_title({
        "card_category": "one_piece", "game": "One Piece Card Game", "name": "Monkey.D.Luffy",
        "set": "Extra Booster: Memorial Collection Super Long Name", "card_number": "EB01-061",
        "rarity": "SEC", "parallel": "Alt Art"}, {})
    print("    " + long_set)
    check("set name dropped before the version", long_set.endswith("SEC Alt Art")
          and len(long_set) <= 80, long_set)

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
