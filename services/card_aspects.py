import re
from typing import Any, Dict, List, Optional

from utils.text_utils import ebay_safe


IGNORE_VALUES = {
    "",
    "unknown",
    "none",
    "n/a",
    "na",
    "[base]",
    "base",
    "not visible",
    "not shown",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


FOUR_DIGIT_YEAR = re.compile(r"(?:19|20)[0-9]{2}")


def numeric_year(value: Any) -> str:
    """The plain year eBay's Year Manufactured aspect demands.

    Card data carries seasons like "2025-26", which read correctly in a title and in the
    Season aspect but are rejected by Year Manufactured ("enter a numeric value").
    """
    match = FOUR_DIGIT_YEAR.search(clean_text(value))
    return match.group(0) if match else ""


def is_useful(value: Any) -> bool:
    text = clean_text(value)
    return text.lower() not in IGNORE_VALUES


def match_title_year(card_data: Dict[str, Any], title: str) -> Dict[str, Any]:
    """Card data with its year taken from the title, when the title starts with one.

    The title is what the seller checked and corrected; the detected year is the model's
    guess. Without this a fixed title ("2026 ...") still published "Year Manufactured:
    2024" from the original detection.
    """
    match = re.match(r"\s*((\d{4})(?:-\d{2})?)\b", title or "")
    if not match:
        return card_data
    current = clean_text(card_data.get("year") or card_data.get("season")
                         or card_data.get("year_manufactured"))
    if current.startswith(match.group(2)):
        return card_data
    fixed = dict(card_data)
    fixed["year"] = match.group(1)
    for key in ("season", "year_manufactured"):
        fixed.pop(key, None)
    return fixed


# eBay rejects the whole listing (error 25002) when any item-specific value is longer.
MAX_ASPECT_LENGTH = 65


def fit_aspect(text: str) -> str:
    # eBay's symbol ban covers item specifics too ("Rarity: SR★★" fails the listing).
    text = ebay_safe(text)
    if len(text) <= MAX_ASPECT_LENGTH:
        return text
    cut = text[:MAX_ASPECT_LENGTH + 1].rsplit(" ", 1)[0]
    return (cut if cut else text[:MAX_ASPECT_LENGTH]).rstrip(" ,-/")


def add_aspect(aspects: Dict[str, List[str]], key: str, value: Optional[Any]) -> None:
    if is_useful(value):
        aspects[key] = [fit_aspect(clean_text(value))]


def add_multi_aspect(aspects: Dict[str, List[str]], key: str, values: List[Any]) -> None:
    cleaned = []

    for value in values:
        text = fit_aspect(clean_text(value))
        if text and text.lower() not in IGNORE_VALUES and text not in cleaned:
            cleaned.append(text)

    if cleaned:
        aspects[key] = cleaned


def get_value(card_data: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = clean_text(card_data.get(key))
        if value and value.lower() not in IGNORE_VALUES:
            return value
    return ""


def normalize_yes_no(value: Any, default: str = "No") -> str:
    text = clean_text(value).lower()

    if text in {"yes", "y", "true", "signed", "autographed"}:
        return "Yes"

    if text in {"no", "n", "false", "unsigned", ""}:
        return default

    return default


def is_pokemon_card(card_data: Dict[str, Any], category_name: str = "") -> bool:
    combined = " ".join(
        clean_text(card_data.get(k))
        for k in ["brand", "set", "sport", "league", "game", "type", "notes", "card_type"]
    ).lower()

    combined += " " + category_name.lower()

    return "pokemon" in combined or "pokémon" in combined


def is_one_piece_card(card_data: Dict[str, Any], category_name: str = "") -> bool:
    combined = " ".join(
        clean_text(card_data.get(k))
        for k in ["brand", "manufacturer", "set", "game", "type", "notes", "card_type"]
    ).lower()

    combined += " " + category_name.lower()

    return (
        "one piece" in combined
        or "onepiece" in combined
        or "bandai" in combined
    )


# Entertainment properties that turn up on Topps/Panini non-sport releases. The value is
# the wording eBay expects in the Franchise aspect.
NON_SPORT_FRANCHISES = [
    ("garbage pail kids", "Garbage Pail Kids"),
    ("wacky packages", "Wacky Packages"),
    ("mars attacks", "Mars Attacks"),
    ("star wars", "Star Wars"),
    ("lord of the rings", "The Lord of the Rings"),
    ("harry potter", "Harry Potter"),
    ("stranger things", "Stranger Things"),
    ("walking dead", "The Walking Dead"),
    ("jurassic", "Jurassic Park"),
    ("indiana jones", "Indiana Jones"),
    ("james bond", "James Bond"),
    ("transformers", "Transformers"),
    ("ninja turtles", "Teenage Mutant Ninja Turtles"),
    ("power rangers", "Power Rangers"),
    ("hello kitty", "Hello Kitty"),
    ("sanrio", "Sanrio"),
    ("my little pony", "My Little Pony"),
    ("godzilla", "Godzilla"),
    ("x-files", "The X-Files"),
    ("spider-man", "Marvel"),
    ("avengers", "Marvel"),
    ("marvel", "Marvel"),
    ("batman", "DC Comics"),
    ("superman", "DC Comics"),
    ("dc comics", "DC Comics"),
    ("pixar", "Pixar"),
    ("disney", "Disney"),
    ("star trek", "Star Trek"),
    ("fortnite", "Fortnite"),
    ("minecraft", "Minecraft"),
]

SPORTS_SIGNAL_MARKERS = {
    "basketball", "football", "baseball", "hockey", "soccer", "golf", "tennis",
    "nba", "wnba", "nfl", "mlb", "nhl", "mls", "ufc", "wwe", "wrestling",
    "nascar", "formula 1", "boxing", "olympic",
}

NON_SPORT_CATEGORIES = {"other", "non_sport", "non-sport", "nonsport", "entertainment"}

ESCO = re.compile(r"\besco\b", re.IGNORECASE)


def is_esco_card(card_data: Dict[str, Any]) -> bool:
    """ESCO Trading Cards' music sets (ESCO x LISA, ESCO x KATSEYE)."""
    return bool(ESCO.search(" ".join(
        clean_text(card_data.get(key)) for key in ["brand", "manufacturer", "set"])))


def detect_franchise(card_data: Dict[str, Any], category_name: str = "") -> str:
    """The entertainment property a card belongs to, or "" when it is not one."""
    explicit = clean_text(card_data.get("franchise"))
    if is_useful(explicit):
        return explicit

    # An ESCO card's property is the artist on it.
    if is_esco_card(card_data):
        artist = get_value(card_data, "character", "name")
        if artist:
            return artist

    haystack = " ".join(
        clean_text(card_data.get(key))
        for key in ["set", "brand", "manufacturer", "game", "notes", "card_type",
                    "name", "card_name", "character", "card_category"]
    ).lower()
    haystack += " " + category_name.lower()

    for marker, label in NON_SPORT_FRANCHISES:
        if marker in haystack:
            return label

    return ""


def has_sports_signal(card_data: Dict[str, Any], category_name: str = "") -> bool:
    """True when the card carries real sports identity, not just a player-shaped field."""
    if clean_text(card_data.get("card_category")).lower() == "sports":
        return True

    for key in ["sport", "league", "team"]:
        if is_useful(card_data.get(key)):
            return True

    haystack = " ".join(
        clean_text(card_data.get(key))
        for key in ["sport", "league", "team", "set", "brand", "manufacturer", "notes"]
    ).lower()
    haystack += " " + category_name.lower()

    return any(marker in haystack for marker in SPORTS_SIGNAL_MARKERS)


def is_non_sport_card(card_data: Dict[str, Any], category_name: str = "") -> bool:
    """Entertainment cards (Disney, Star Wars, Marvel...) that are neither TCG nor sport.

    Deliberately conservative: anything showing sports identity, and anything we cannot
    place, keeps the long-standing sports handling rather than being rerouted.
    """
    if is_pokemon_card(card_data, category_name) or is_one_piece_card(card_data, category_name):
        return False

    if has_sports_signal(card_data, category_name):
        return False

    if detect_franchise(card_data, category_name):
        return True

    category = clean_text(card_data.get("card_category")).lower()
    return category in NON_SPORT_CATEGORIES and is_useful(card_data.get("character"))


LEAGUE_TO_SPORT = [
    ("nba", "Basketball"), ("wnba", "Basketball"), ("ncaab", "Basketball"),
    ("nfl", "Football"), ("ncaaf", "Football"), ("cfl", "Football"),
    ("mlb", "Baseball"), ("milb", "Baseball"),
    ("nhl", "Hockey"), ("khl", "Hockey"),
    ("mls", "Soccer"), ("fifa", "Soccer"), ("uefa", "Soccer"), ("premier league", "Soccer"),
    ("ufc", "Mixed Martial Arts"), ("wwe", "Wrestling"), ("aew", "Wrestling"),
    ("nascar", "Racing"), ("formula 1", "Racing"), ("f1", "Racing"),
    ("pga", "Golf"), ("atp", "Tennis"), ("wta", "Tennis"),
    ("basketball", "Basketball"), ("football", "Football"), ("baseball", "Baseball"),
    ("hockey", "Hockey"), ("soccer", "Soccer"), ("golf", "Golf"), ("tennis", "Tennis"),
    ("boxing", "Boxing"), ("wrestling", "Wrestling"), ("racing", "Racing"),
]


def derive_sport(card_data: Dict[str, Any]) -> str:
    """Best-effort Sport value. eBay rejects a sports listing that has none."""
    explicit = get_value(card_data, "sport")
    if explicit:
        return explicit

    haystack = " ".join(
        clean_text(card_data.get(key))
        for key in ["league", "team", "set", "brand", "notes", "card_type", "card_category"]
    ).lower()

    for marker, sport in LEAGUE_TO_SPORT:
        if marker in haystack:
            return sport

    return ""


def infer_sports_features(variant: str, features: str) -> List[str]:
    values = []

    if is_useful(features):
        values.append(features)

    variant_lower = variant.lower()

    if is_useful(variant):
        if any(x in variant_lower for x in ["insert", "case hit", "downtown", "kaboom", "color blast"]):
            values.append("Insert")
        elif any(x in variant_lower for x in ["silver", "prizm", "refractor", "parallel", "holo", "laser"]):
            values.append("Parallel")
        else:
            values.append(variant)

    return values


def infer_tcg_features(variant: str, features: str, finish: str) -> List[str]:
    values = []

    if is_useful(features):
        values.append(features)

    if is_useful(variant):
        values.append(variant)

    if is_useful(finish):
        values.append(finish)

    return values


def build_card_aspects(card_data: Dict[str, Any], category_name: str = "") -> Dict[str, List[str]]:
    aspects: Dict[str, List[str]] = {}

    card_category = clean_text(card_data.get("card_category")).lower()
    game_value = clean_text(card_data.get("game")).lower()

    pokemon = (
        is_pokemon_card(card_data, category_name)
        or card_category == "pokemon"
        or "pokemon" in game_value
        or "pokémon" in game_value
    )

    union_arena = (
        card_category == "union_arena"
        or "union arena" in game_value
    )

    one_piece = not union_arena and (
        is_one_piece_card(card_data, category_name)
        or card_category == "one_piece"
        or "one piece" in game_value
        or "onepiece" in game_value
    )

    name = get_value(card_data, "name", "player_athlete", "card_name", "character")
    brand = get_value(card_data, "brand", "manufacturer")
    card_set = get_value(card_data, "set", "card_set", "pokemon_set")
    year = get_value(card_data, "year", "season", "year_manufactured")
    card_number = get_value(card_data, "card_number", "number")
    variant = get_value(card_data, "parallel", "variant", "parallel_variety", "variety")
    serial_number = get_value(card_data, "serial_number")
    # "/399" from either field: the identifier often reports only the serial "112/399".
    run_match = re.search(r"/\s*(\d+)", get_value(card_data, "print_run") or serial_number)
    print_run = run_match.group(1).lstrip("0") if run_match else ""
    rookie = card_data.get("rookie") in (True, "true", "True", "yes", "Yes")
    features = get_value(card_data, "features")
    rarity = get_value(card_data, "rarity")
    finish = get_value(card_data, "finish")

    team = get_value(card_data, "team")
    league = get_value(card_data, "league")
    sport = get_value(card_data, "sport")

    is_graded = bool(card_data.get("is_graded", False))
    grade = get_value(card_data, "grade")
    grader = get_value(card_data, "grader", "professional_grader")
    cert_number = get_value(card_data, "certification_number", "cert_number")

    condition = get_value(card_data, "condition_estimate", "card_condition")
    autographed = normalize_yes_no(card_data.get("autographed"), default="No")
    signed_by = get_value(card_data, "signed_by")
    language = get_value(card_data, "language") or "English"
    country = get_value(card_data, "country_of_origin") or "United States"

    if pokemon:
        character = get_value(card_data, "character") or name

        add_aspect(aspects, "Game", get_value(card_data, "game") or "Pokémon TCG")
        add_aspect(aspects, "Type", "Collectible Card Game")
        add_aspect(aspects, "Card Name", name)
        add_aspect(aspects, "Character", character)
        add_aspect(aspects, "Set", card_set)
        add_aspect(aspects, "Card Number", card_number)
        add_aspect(aspects, "Manufacturer", brand or "The Pokémon Company")
        add_aspect(aspects, "Year Manufactured", numeric_year(year))
        add_aspect(aspects, "Finish", finish)
        add_aspect(aspects, "Rarity", rarity)
        add_aspect(aspects, "Card Type", get_value(card_data, "card_type"))
        add_aspect(aspects, "Creature/Monster Type", get_value(card_data, "creature_monster_type"))
        add_aspect(aspects, "Speciality", get_value(card_data, "speciality"))
        add_aspect(aspects, "Stage", get_value(card_data, "stage"))
        add_aspect(aspects, "HP", get_value(card_data, "hp"))
        add_aspect(aspects, "Language", language)
        add_aspect(aspects, "Country of Origin", country)
        add_aspect(aspects, "Autographed", autographed)
        add_aspect(aspects, "Game", get_value(card_data, "game") or "Pokémon TCG")

        if autographed == "Yes":
            add_aspect(aspects, "Signed By", signed_by)

        add_multi_aspect(aspects, "Features", infer_tcg_features(variant, features, finish))

    elif union_arena:
        character = get_value(card_data, "character") or name

        add_aspect(aspects, "Game", "Union Arena")
        add_aspect(aspects, "Type", "Collectible Card Game")
        add_aspect(aspects, "Card Name", name)
        add_aspect(aspects, "Character", character)
        add_aspect(aspects, "Franchise", get_value(card_data, "franchise"))
        add_aspect(aspects, "Set", card_set)
        add_aspect(aspects, "Card Number", card_number)
        add_aspect(aspects, "Manufacturer", brand or "Bandai")
        add_aspect(aspects, "Year Manufactured", numeric_year(year))
        add_aspect(aspects, "Rarity", rarity)
        add_aspect(aspects, "Finish", finish)
        add_aspect(aspects, "Language", language)
        add_aspect(aspects, "Country of Origin", get_value(card_data, "country_of_origin") or "Japan")
        add_aspect(aspects, "Autographed", autographed)
        add_multi_aspect(aspects, "Features", infer_tcg_features(variant, features, finish))

    elif one_piece:
        character = get_value(card_data, "character") or name

        add_aspect(aspects, "Game", get_value(card_data, "game") or "One Piece Card Game")
        add_aspect(aspects, "Type", "Collectible Card Game")
        add_aspect(aspects, "Card Name", name)
        add_aspect(aspects, "Character", character)
        add_aspect(aspects, "Set", card_set)
        add_aspect(aspects, "Card Number", card_number)
        add_aspect(aspects, "Manufacturer", brand or "Bandai")
        add_aspect(aspects, "Year Manufactured", numeric_year(year))
        add_aspect(aspects, "Rarity", rarity)
        add_aspect(aspects, "Finish", finish)
        add_aspect(aspects, "Card Type", get_value(card_data, "card_type"))
        add_aspect(aspects, "Language", language)
        add_aspect(aspects, "Country of Origin", country)
        add_aspect(aspects, "Autographed", autographed)
        add_aspect(aspects, "Game", get_value(card_data, "game") or "One Piece Card Game")

        if autographed == "Yes":
            add_aspect(aspects, "Signed By", signed_by)

        add_multi_aspect(aspects, "Features", infer_tcg_features(variant, features, finish))

    elif is_non_sport_card(card_data, category_name):
        # Entertainment cards are filtered by Character and Franchise on eBay, so the
        # sports wording ("Player/Athlete", "Sports Trading Card") actively hides them.
        character = get_value(card_data, "character", "name", "card_name")
        franchise = detect_franchise(card_data, category_name)

        add_aspect(aspects, "Type", "Non-Sport Trading Card")
        add_aspect(aspects, "Character", character)
        add_aspect(aspects, "Franchise", franchise)
        add_aspect(aspects, "TV Show/Movie", get_value(card_data, "tv_show", "movie", "series"))
        add_aspect(aspects, "Card Name", get_value(card_data, "card_name") or character)
        add_aspect(aspects, "Set", card_set)
        add_aspect(aspects, "Card Number", card_number)
        add_aspect(aspects, "Manufacturer", brand)
        add_aspect(aspects, "Year Manufactured", numeric_year(year))
        add_aspect(aspects, "Parallel/Variety", variant)
        add_aspect(aspects, "Finish", finish)
        add_aspect(aspects, "Rarity", rarity)
        non_sport_features = infer_tcg_features(variant, features, finish)
        if print_run or serial_number:
            non_sport_features.append("Serial Numbered")
        add_multi_aspect(aspects, "Features", non_sport_features)
        add_aspect(aspects, "Print Run", print_run)
        add_aspect(aspects, "Language", language)
        add_aspect(aspects, "Country of Origin", country)
        add_aspect(aspects, "Autographed", autographed)
        add_aspect(
            aspects,
            "Original/Licensed Reprint",
            get_value(card_data, "original_licensed_reprint") or "Original",
        )

        if autographed == "Yes":
            add_aspect(aspects, "Signed By", signed_by)

    else:
        # eBay rejects the sports category without a Sport value, so fall back to
        # deriving it from the league or team when the identifier left it blank.
        add_aspect(aspects, "Sport", sport or derive_sport(card_data))
        # The athlete field, not "name": the identifier has filled "name" with a whole
        # card description ("Liam Lawson 2024 Topps Chrome ... #CAC-LL").
        add_aspect(aspects, "Player/Athlete",
                   get_value(card_data, "player_athlete") or name)
        add_aspect(aspects, "Season", year)
        add_aspect(aspects, "Year Manufactured", numeric_year(year))
        add_aspect(aspects, "Manufacturer", brand)
        add_aspect(aspects, "Parallel/Variety", variant)
        sports_features = infer_sports_features(variant, features)
        if rookie and "Rookie" not in sports_features:
            sports_features.append("Rookie")
        if print_run or serial_number:
            sports_features.append("Serial Numbered")
        add_multi_aspect(aspects, "Features", sports_features)
        add_aspect(aspects, "Print Run", print_run)
        add_aspect(aspects, "Set", card_set)
        add_aspect(aspects, "Team", team)
        add_aspect(aspects, "League", league)
        add_aspect(aspects, "Autographed", autographed)

        if autographed == "Yes":
            add_aspect(aspects, "Signed By", signed_by)

        add_aspect(aspects, "Card Name", get_value(card_data, "card_name") or name)
        add_aspect(aspects, "Card Number", card_number)
        add_aspect(aspects, "Type", "Sports Trading Card")
        add_aspect(aspects, "Country of Origin", country)
        add_aspect(aspects, "Language", language)
        add_aspect(
            aspects,
            "Original/Licensed Reprint",
            get_value(card_data, "original_licensed_reprint") or "Original",
        )

    add_aspect(aspects, "Graded", "Yes" if is_graded else "No")

    if is_graded:
        add_aspect(aspects, "Professional Grader", grader)
        add_aspect(aspects, "Grade", grade)
        add_aspect(aspects, "Certification Number", cert_number)

    if "Game" not in aspects:
        game = get_value(card_data, "game")

        if game:
            add_aspect(aspects, "Game", game)
        elif card_category == "one_piece":
            add_aspect(aspects, "Game", "One Piece Card Game")
        elif card_category == "pokemon":
            add_aspect(aspects, "Game", "Pokémon TCG")

    if is_graded:
        add_aspect(aspects, "Grade", grade)
        add_aspect(aspects, "Professional Grader", grader)
        add_aspect(aspects, "Certification Number", cert_number)
    else:
        add_aspect(aspects, "Card Condition", condition)

    return aspects