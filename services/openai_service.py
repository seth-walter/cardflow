import re

from openai import OpenAI

from config import settings
from services import parallel_refs
from services.card_aspects import is_esco_card
from utils.text_utils import dedupe_phrases, ebay_safe

_client = None


def _openai():
    """Built on first use: OpenAI() refuses an empty key, and a Gemini-only setup has none."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client

SCHEMA = """{
  "card_category": "sports | pokemon | one_piece | union_arena | other",
  "name": string,
  "card_name": string,
  "player_athlete": string,
  "character": string,
  "brand": string,
  "manufacturer": string,
  "set": string,
  "insert_set": string,
  "finish_observed": string,
  "parallel": string,
  "serial_number": string,
  "print_run": string,
  "promo_stamp": string,
  "rookie": boolean,
  "year": string,
  "season": string,
  "year_manufactured": string,
  "card_number": string,
  "variant": string,
  "features": string,
  "rarity": string,
  "finish": string,
  "game": string,
  "franchise": string,
  "tv_show": string,
  "card_type": string,
  "creature_monster_type": string,
  "speciality": string,
  "stage": string,
  "hp": string,
  "sport": string,
  "league": string,
  "team": string,
  "autographed": "Yes | No",
  "signed_by": string,
  "is_graded": boolean,
  "grader": string,
  "grade": string,
  "certification_number": string,
  "condition_estimate": string,
  "corners_condition": string,
  "edges_condition": string,
  "front_surface_condition": string,
  "back_surface_condition": string,
  "whitening_condition": string,
  "centering_condition": string,
  "visible_flaws": string,
  "language": string,
  "country_of_origin": string,
  "notes": string
}"""

GUIDANCE = """You are identifying a trading card for an eBay listing. A collector will search
for the EXACT version of this card, so the parallel, print run, insert and promo stamp
matter more than anything else. Take your time and inspect every corner of both images.

Images are labelled: the card front, enlarged close-ups of the front (use them for small
print, serials, codes and the finish pattern), the back, and then possibly labelled
reference examples of parallels. Scans may be rotated; read text in whatever orientation
it is in.

Things that are NOT part of the card's identity:
- The social media line on Topps backs ("X @TOPPS", an X logo, "CHROME" above it). The
  X there is the social network, never part of the set name.
- "NOT FOR SALE", "BANDAI MADE IN JAPAN", copyright and legal lines - as words in the
  set or title. The year printed in the copyright line is still the card's year.
- The sport name: never add "Basketball", "Football" etc. to the set.

Fields that decide the title:
- set: the product line exactly as collectors name it, including the brand, e.g.
  "Topps Chrome", "Topps Flagship" (the base paper Topps set), "Bowman", "Bowman Chrome",
  "Panini Prizm", "Donruss Optic", "Topps Chrome Formula 1", "Topps Disney Neon",
  "Topps Chrome UFC", "Topps Chrome WWE", "Topps Chrome UEFA Club Competitions",
  "Upper Deck Series 1", "Topps Series 2", "Bowman Draft", "Topps Finest".
  Read the back: the set name and copyright line are printed there.
- year: the year as the hobby names the product. Use the season form ("2025-26") for
  basketball and hockey products that print it that way on the back; otherwise one year.
  Take it from the card: a year in the set logo or on the back, else the copyright line
  ("© 2026 The Topps Company", "© 2026 MARVEL"). Never supply it from memory of when
  that set usually comes out - sets like Topps Marvel recur every year with the same
  name. If no year is printed anywhere, leave it empty.
- card_number: as printed, without "#", including any prefix ("RA-IS", "AC-20", "154").
- insert_set: the named insert or subset when the card is not a base card, e.g.
  "Activators", "Rookie Autographs", "Future Stars". Only an insert name printed on the
  card as a logo or title, normally with a lettered card-number prefix. Empty for base
  cards (plain-number cards), never a caption, slogan or design description.
- finish_observed: BEFORE naming the parallel, describe in a few words what the border
  and photo background actually show in the close-ups: "flat smooth sheen, no pattern",
  "checkerboard of small squares", "broad sweeping arcs of light", "tight parallel
  ripples", "inverted black-and-white photo", "gold mirror foil behind the player",
  "cream border", "black paisley border". Scanners flatten the rainbow, so judge the
  pattern and colour, not the shine.
- parallel: the exact parallel name, never a generic one when a specific one applies.
  It must agree with finish_observed. A Chrome card with no pattern and no shine is
  base (empty); flat sheen with no pattern is plain "Refractor".
  Chrome finishes: "X-Refractor" (checkerboard / crosshatch of small squares);
  "Ray Wave Refractor" (broad curved arcs or bands of light sweeping across the border
  and background, like light rays - not a checkerboard, not tight ripples);
  "Wave Refractor" (tight, evenly spaced parallel ripples); "Negative Refractor" (photo
  in inverted, film-negative black and white); "Prism Refractor" (triangular shards);
  "Speckle Refractor" (small flecks); "Mojo Refractor" (swirling circles); "Sonar
  Refractor" (concentric rings); "Lava Refractor" (blobs); "Pulsar Refractor" (dots).
  Plain "Refractor" is the default only when the finish is a smooth, patternless sheen.
  Patterns also come in numbered colours: a coloured border WITH Ray Wave arcs is
  "<Colour> Ray Wave Refractor" (e.g. "Blue Ray Wave Refractor" /150), with ripples
  "<Colour> Wave Refractor", with dots "<Colour> Pulsar Refractor", with a basketball
  pebble/seam texture "<Colour> Basketball Refractor". Name both the colour and pattern.
  Topps Chrome has no "Checkerboard" or "Geometric" base refractor: a crosshatched,
  gridded or diamond-checked shine is the "X-Refractor".
  Colour parallels use the colour plus pattern, e.g. "Magenta Refractor" (the
  pink-red Topps Chrome colour, numbered /399), "Blue Wave Refractor", "Gold Refractor",
  "Black Refractor", "Orange Refractor". A colour with ripples is "<Colour> Wave".
  Paper parallels: "Vintage Stock" (cream/off-white aged-looking border, numbered /99 on
  Topps flagship), "Golden Mirror" (gold reflective foil pattern in the photo background),
  "Rainbow Foil", "Gold", "Black",
  "Independence Day", "Clear". A colour border with a serial number is a numbered
  colour parallel; name the colour. Empty only for a true base card.
- serial_number: the stamped serial exactly as printed, e.g. "03/10", "112/399". It is
  small gold, silver or black foil digits, on the front (often along an edge, sideways,
  over the artwork) or on the back. Check every close-up. A missed serial is the costliest
  mistake. Never infer a serial from the colour alone; if digits are unclear, give your
  best reading of what is printed.
- Chrome vs paper and Update vs flagship: read the front logo and the back card-number
  prefix (USC = Topps Chrome Update, US = Topps Update paper, BCP = Bowman Chrome
  prospect, BP = Bowman paper prospect).
- print_run: the denominator with a slash, e.g. "/10", "/199"; "1/1" for one of ones.
- rookie: true when an RC logo or "Rookie" designation is printed on the card.
- autographed: "Yes" only for a real on-card or sticker autograph; real autos carry
  authentication text on the back. Printed facsimile signatures are "No" (Topps Disney
  FA-, DL-, TR-, QD- cards are facsimiles; real Disney autos are AA-).
- promo_stamp: any event or promo stamp printed on the art, as printed, e.g.
  "ROUND ONE PIECE", "Pre-Release", "Store Championship", "Winner", "Judge",
  "Prerelease", "Staff". Copy only the stamp's own words; "NOT FOR SALE" is not a
  stamp. Empty when there is none. Never treat a stamp as the set.

Category rules:
- Pokemon: card_category "pokemon", game "Pokemon TCG"; card_number as printed
  ("199/165"); rarity as printed or by symbol (Illustration Rare, Special Illustration
  Rare, Double Rare, Ultra Rare, Hyper Rare, Holo Rare, Reverse Holo); set the finish to
  "Reverse Holofoil" for reverse holos. Note set symbol and regulation mark in notes.
- One Piece: card_category "one_piece", game "One Piece Card Game", manufacturer
  "Bandai"; card_number is the code by the rarity box (e.g. "ST29-005", "OP09-068").
  Read every character of it from the close-ups, upside down if the card is: the set
  digit decides which card it is (EB01-007 and EB04-007 are different characters).
  rarity is the letter code in the box after the number (C, UC, R, SR, SEC, L, P, TR).
  Name exactly as printed (keep dots: "Monkey.D.Luffy").
  parallel uses only these words: "SP" when a small box reading SP is printed just
  before the card code (rarity stays the boxed letters after it, e.g. SR); "Manga" when
  the background is black-and-white manga panels with speech bubbles; "Alt Art" for any
  other alternate illustration or full-art version; empty for the regular print. Never
  "Special Card", "Special Rare" or "Parallel".
  A large event logo printed on the art (e.g. "ROUND ONE PIECE") is a promo_stamp; on a
  stamped promo leave parallel empty.
- Union Arena: card_category "union_arena", game "Union Arena", manufacturer "Bandai";
  card_number is the full code (e.g. "UE01BT/JJK-1-001"); rarity as printed including
  stars (C, U, R, SR, SR★, SR★★, UR); franchise is the anime series printed on the card.
- Sports (including Formula 1, racing, UFC, wrestling): card_category "sports";
  player_athlete is the athlete or driver; fill sport, league and team.
  Formula 1 is sport "Racing", league "Formula 1", team = constructor; set
  "Topps Chrome Formula 1" (Chrome F1 numbered ladder: Teal /299, Pink /250, Aqua /199,
  Blue /150, Green /99, /75, Gold /50, Orange /25, Black /10, Red /5, Superfractor 1/1;
  unnumbered: Refractor, RayWave, Lazer, Checker Flag, F1 Logofractor).
- Entertainment cards (Topps Disney, Disney Neon, Star Wars, Marvel, Garbage Pail Kids
  and similar): card_category "other", character = the character, franchise = the
  property. Leave sport, league, team and player_athlete empty.
- ESCO music cards (ESCO x LISA, ESCO x KATSEYE "WILD" and other ESCO Trading Cards
  artist sets): card_category "other", brand "ESCO", set "ESCO x <artist>" as printed
  (e.g. "ESCO x LISA"), character and franchise = the artist or group ("LISA",
  "KATSEYE"); for a group card name the member in card_name. card_number is the number
  after "No." ("No.37" -> "37"). insert_set is the subset printed on the card ("PRESS
  PLAY", "Alter Ego", "Holiday Season"). parallel is the foil colour plus "Foil" ("Pink
  Foil", "Gold Foil", "Rainbow Foil"); the lava-like Magma Foils are "<Colour> Magma
  Foil". Foils are serial numbered (/800 down to 1/1): read the serial. ESCO autographs
  are hand-signed, so a signed card is autographed "Yes", signed_by the artist.
- Graded slabs: is_graded true; grader, grade and certification_number from the label.

Condition: judge corners, edges, surfaces, whitening and centering from the images;
condition_estimate concise ("Near Mint", "Excellent", "Light wear", "Damaged"). Use
confident seller wording, never "appears", "seems", "likely". Use an empty string for
anything that cannot be determined. Never invent a parallel or serial that is not there.

Return ONLY valid JSON with this schema, no markdown:
"""


def _image(data_url: str) -> dict:
    # "high" lets the model read small foil serials and card codes at full scan size.
    return {"type": "input_image", "image_url": data_url, "detail": "high"}


def _text(text: str) -> dict:
    return {"type": "input_text", "text": text}


REFERENCE_INTRO = """REFERENCE EXAMPLES (not the card being identified): whole-card scans of
parallels this seller has listed before, each labelled with its exact parallel name. Compare
the border and background finish of the card above with these. When its pattern matches
an example (same arcs, checkerboard, ripples, flat sheen, inverted photo, border colour or
background foil), use that example's label as the parallel. Players and designs differ;
only the finish matters. Ignore an example whose finish does not match."""


def generate_card_data(front_data_url: str, back_data_url: str = "", model: str = "",
                       front_tiles=None, references=None, reference_exclude: str = "") -> str:
    """Identify a card from its scans. The back image is optional (single-sided scans).

    references: [(label, data_url)] parallel examples; None loads the learned library.
    """
    content = [_text(GUIDANCE + SCHEMA), _text("CARD FRONT:"), _image(front_data_url)]
    if front_tiles:
        content.append(_text("CARD FRONT CLOSE-UPS (top-left, top-right, bottom-left, "
                             "bottom-right):"))
        content += [_image(tile) for tile in front_tiles]
    if back_data_url:
        content += [_text("CARD BACK:"), _image(back_data_url)]

    if references is None:
        references = parallel_refs.reference_images(exclude_source=reference_exclude)
    if references:
        content.append(_text(REFERENCE_INTRO))
        for label, data_url in references:
            # Low detail is a flat-rate tile per image: plenty to see a border pattern.
            content += [_text("Example - " + label + ":"),
                        {"type": "input_image", "image_url": data_url, "detail": "low"}]

    return complete(content, model)


def complete(content: list, model: str = "") -> str:
    """Send content parts to the vision model; "gemini-*" models go to Gemini."""
    model = model or settings.VISION_MODEL
    if model.startswith("gemini"):
        from services import gemini_service
        return gemini_service.generate(content, model)

    request = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    if model.startswith(("gpt-5", "o3", "o4")) and settings.VISION_REASONING:
        request["reasoning"] = {"effort": settings.VISION_REASONING}

    response = _openai().responses.create(**request)
    return response.output_text.strip()


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"unknown", "none", "n/a", "na", "[base]", "base", "not visible",
                        "not shown", "false", "no"}:
        return ""
    return text


def _dedupe_parts(parts):
    """Clean junk values, then drop phrases already contained in a longer one."""
    return dedupe_phrases(_clean(part) for part in parts)


def _trim_title(title: str, max_len: int = 80) -> str:
    title = ebay_safe(str(title or "").replace('"', ''))
    if len(title) <= max_len:
        return title
    trimmed = title[:max_len].rstrip()
    # Avoid ending on dangling punctuation/partial hashtag where possible.
    return trimmed.rstrip(" -_/,.#")


def _combined(card_data: dict, keys) -> str:
    return " ".join(_clean(card_data.get(k)).lower() for k in keys)


def _is_pokemon(card_data: dict) -> bool:
    combined = _combined(card_data, ["card_category", "game", "brand", "manufacturer",
                                     "set", "notes", "card_type"])
    return "pokemon" in combined or "pokémon" in combined


def _is_union_arena(card_data: dict) -> bool:
    combined = _combined(card_data, ["card_category", "game", "set"])
    return "union_arena" in combined or "union arena" in combined


def _is_one_piece(card_data: dict) -> bool:
    if _is_union_arena(card_data):
        return False
    combined = _combined(card_data, ["card_category", "game", "brand", "manufacturer",
                                     "set", "notes", "card_type"])
    return "one piece" in combined or "onepiece" in combined or "one_piece" in combined


def _is_sports(card_data: dict) -> bool:
    category = _clean(card_data.get("card_category")).lower()
    if category == "sports":
        return True
    combined = _combined(card_data, ["sport", "league", "team", "brand", "manufacturer",
                                     "set", "player_athlete", "notes"])
    markers = {"basketball", "football", "baseball", "hockey", "soccer", "nba", "nfl", "mlb", "nhl", "ufc", "wwe"}
    return any(marker in combined for marker in markers)


STAMP_NOISE = re.compile(r"\b(not for sale|stamped|stamp|version|logo)\b", re.IGNORECASE)
NUMBERED_COLOUR = re.compile(
    r"^(magenta|pink|purple|aqua|teal|blue|green|yellow|gold|orange|black|red|white|"
    r"silver|bronze)( wave)? refractor$", re.IGNORECASE)
SPORT_WORDS = re.compile(r"\s*\b(basketball|football|baseball|hockey|soccer)\b", re.IGNORECASE)


def promo_label(card_data: dict) -> str:
    """The promo stamp as sellers write it. "ROUND ONE PIECE" -> "Round One Promo"."""
    stamp = " ".join(STAMP_NOISE.sub(" ", _clean(card_data.get("promo_stamp"))).split())
    if not stamp:
        return ""
    low = stamp.lower()
    if "round" in low:
        return "Round One Promo"
    words = " ".join(word.capitalize() if word.isupper() and len(word) > 2 else word
                     for word in stamp.split())
    return words if "promo" in low else words + " Promo"


def print_run(card_data: dict) -> str:
    """"/199" from either field; one-of-ones keep their "1/1"."""
    for key in ("print_run", "serial_number"):
        value = _clean(card_data.get(key)).replace(" ", "")
        match = re.search(r"(\d*)/(\d+)", value)
        if not match:
            continue
        numerator, denominator = match.group(1), match.group(2)
        if denominator == "1" and numerator in ("", "1", "01"):
            return "1/1"
        return "/" + denominator.lstrip("0")
    return ""


def _parallel(card_data: dict) -> str:
    return _clean(card_data.get("parallel") or card_data.get("validated_variant")
                  or card_data.get("variant") or card_data.get("parallel_variety"))


def _is_rookie(card_data: dict) -> bool:
    rookie = card_data.get("rookie")
    if isinstance(rookie, str):
        rookie = rookie.strip().lower() in {"true", "yes", "1"}
    return bool(rookie) or _clean(card_data.get("features")).lower() in {"rookie", "rookie card", "rc"}


def _grade(card_data: dict) -> str:
    grader = _clean(card_data.get("grader") or card_data.get("professional_grader"))
    grade = _clean(card_data.get("grade"))
    if card_data.get("is_graded") and grader and grade:
        return "{} {}".format(grader, grade)
    return ""


def _number(card_data: dict) -> str:
    number = _clean(card_data.get("card_number") or card_data.get("number")).lstrip("#")
    return "#" + number if number else ""


def build_seo_title(card_data: dict, price_data: dict) -> str:
    """Build a stable, category-aware eBay title and hard-cap it at 80 chars.

    Follows the shape of the titles the seller writes by hand: what the card is first,
    then the details that make it this exact version (insert, parallel, print run).
    """
    year = _clean(card_data.get("year") or card_data.get("season") or card_data.get("year_manufactured"))
    brand = _clean(card_data.get("brand") or card_data.get("manufacturer"))
    card_set = _clean(card_data.get("set") or card_data.get("card_set") or card_data.get("pokemon_set"))
    rarity = _clean(card_data.get("rarity"))
    finish = _clean(card_data.get("finish"))
    parallel = _parallel(card_data)
    promo = promo_label(card_data)
    run = print_run(card_data)
    grade = _grade(card_data)

    if _is_pokemon(card_data):
        name = _clean(card_data.get("name") or card_data.get("card_name") or card_data.get("character"))
        parts = _dedupe_parts([year, "Pokemon", card_set, name])
        parts.append(_number(card_data))
        for extra in [rarity, finish, parallel, promo]:
            if extra and extra.lower() not in {"holofoil", "normal"}:
                parts.append(extra)
        parts += [run, grade]
        return _trim_title(" ".join(_dedupe_parts(parts)))

    if _is_one_piece(card_data):
        name = _clean(card_data.get("name") or card_data.get("card_name") or card_data.get("character"))
        if promo:
            # Promos sell on the stamp, not the set the art was reprinted from, and the
            # stamp is the version - a parallel reading here is the model describing it.
            name = re.sub(r"\s*\([^)]*\)\s*$", "", name)   # "(Parallel)", "(Full Art)"
            parts = ["One Piece", promo, name, run, grade]
        else:
            parallel = re.sub(r"\b(special (card|rare)|sp card)\b", "SP", parallel,
                              flags=re.IGNORECASE)
            # The version is what the card sells on, so it must survive the 80-character
            # cut; a long set name ("Extra Booster: Memorial Collection") goes first.
            tail = [_number(card_data), rarity, parallel, run, grade]
            parts = ["One Piece", card_set, name] + tail
            if len(" ".join(_dedupe_parts(parts))) > 80:
                parts = ["One Piece", name] + tail
        return _trim_title(" ".join(_dedupe_parts(parts)))

    if _is_union_arena(card_data):
        name = _clean(card_data.get("name") or card_data.get("card_name") or card_data.get("character"))
        series = _clean(card_data.get("franchise") or card_data.get("tv_show"))
        code = _clean(card_data.get("card_number")).lstrip("#")
        parts = ["Union Arena", series, name, code, rarity, parallel, promo, run, grade]
        if len(ebay_safe(" ".join(_dedupe_parts(parts)))) > 80:
            # Series names run long ("Re:ZERO Starting Life in Another World") and the
            # rarity at the end is what sells it; the code ("UE04BT/RZR-1-070") goes first.
            parts.remove(code)
        return _trim_title(" ".join(_dedupe_parts(parts)))

    # Sports and entertainment cards share one shape.
    subject = _clean(card_data.get("player_athlete") or card_data.get("character")
                     or card_data.get("name") or card_data.get("card_name"))
    insert = _clean(card_data.get("insert_set"))
    # Sellers write "Auto", and buyers search for it; "Rookie Autographs" is the insert's
    # checklist name and costs half a title.
    if re.search(r"autograph", insert, re.IGNORECASE):
        insert = "Auto"
    # Base cards carry plain numbers; inserts carry a prefix (AC-20, MT-2, RA-IS). On a
    # plain number the "insert" is the model naming the design or a caption ("Horizons",
    # "Seven Moves", "Rookies"), which buyers never search.
    # ESCO numbers every card plainly ("No.37"), and its subsets ("PRESS PLAY", "Alter
    # Ego") are printed on the card, so they stay.
    elif (insert and not is_esco_card(card_data)
          and re.fullmatch(r"#?\d+", _clean(card_data.get("card_number")))):
        insert = ""
    card_set = SPORT_WORDS.sub("", card_set).strip()
    # A numbered colour is titled "Magenta /399", "Blue Wave /150": the colour and run
    # are what buyers search, and "Refractor" only costs characters.
    if run and NUMBERED_COLOUR.match(parallel):
        parallel = re.sub(r"\s+Refractor$", "", parallel, flags=re.IGNORECASE)
    parts = _dedupe_parts([year, brand, card_set, subject])
    parts.append(_number(card_data))
    parts += [insert, parallel, run]

    autographed = _clean(card_data.get("autographed")).lower() in {"yes", "true"}
    if autographed and "auto" not in " ".join([insert, parallel]).lower():
        parts.append("Auto")
    if promo:
        parts.append(promo)
    if _is_rookie(card_data):
        parts.append("RC")
    parts.append(grade)
    return _trim_title(" ".join(_dedupe_parts(parts)))


# Print runs the major manufacturers actually use. A serial whose denominator is not one
# of these is far more likely a misread (03/10 read as 03/18) than a real print run.
STANDARD_PRINT_RUNS = {
    1, 2, 3, 5, 7, 8, 10, 15, 20, 23, 24, 25, 30, 35, 40, 45, 49, 50, 60, 65, 70, 75, 80,
    85, 90, 95, 99, 100, 110, 112, 125, 135, 149, 150, 165, 175, 185, 188, 196, 199, 200,
    225, 249, 250, 275, 299, 300, 325, 349, 350, 375, 399, 400, 425, 450, 475, 499, 500,
    550, 599, 600, 650, 675, 699, 700, 750, 799, 800, 850, 899, 999, 1000, 1999, 2000,
    2500, 5000,
}


def title_warnings(card_data: dict) -> list:
    """Things in the title worth a human look before it publishes."""
    warnings = []
    run = print_run(card_data)
    if run.startswith("/"):
        try:
            denominator = int(run[1:])
        except ValueError:
            denominator = 0
        if denominator not in STANDARD_PRINT_RUNS:
            warnings.append("serial read as {} - not a standard print run, check the "
                            "card".format(_clean(card_data.get("serial_number")) or run))

    serial = _clean(card_data.get("serial_number"))
    match = re.search(r"(\d+)\s*/\s*(\d+)", serial)
    if match and int(match.group(1)) > int(match.group(2)):
        warnings.append("serial {} is impossible (number above the run)".format(serial))
    warnings += card_data.get("catalog_warnings") or []
    return warnings
