from typing import Any, Dict

from utils.file_utils import clean_value
from utils.text_utils import clean_comp_query, dedupe_phrases


IGNORE_VARIANTS = {"base", "[base]", "unknown", "none", "n/a", ""}


def is_tcg_card(card_data: Dict[str, Any]) -> bool:
    combined = " ".join(
        clean_value(card_data.get(k))
        for k in ["card_category", "game", "brand", "manufacturer", "set", "notes"]
    ).lower()

    return (
        "pokemon" in combined
        or "pokémon" in combined
        or "one piece" in combined
        or "one_piece" in combined
        or "bandai" in combined
        or "union arena" in combined
        or "union_arena" in combined
    )


def build_comp_query(card_data: Dict[str, Any]) -> str:
    """Search text for pricing comps.

    Deduped the same way titles are: a query reading "Topps Topps Chrome" matches far
    fewer real listings, which quietly degrades the price estimate.
    """
    # Repricing existing listings works from a listing title rather than scanned fields.
    override = clean_value(card_data.get("comp_query_override"))
    if override:
        # Titles carry seller notes ("READ DESC", "free shipping") that no completed
        # sale contains, and the comp filter would treat them as required words.
        return clean_comp_query(override) or override

    parts = []

    if is_tcg_card(card_data):
        for key in ["name", "card_name", "set", "card_number", "rarity"]:
            parts.append(clean_value(card_data.get(key)))

        return " ".join(dedupe_phrases(parts)).strip()

    for key in ["year", "brand", "set", "name", "player_athlete", "card_number"]:
        parts.append(clean_value(card_data.get(key)))

    variant = clean_value(card_data.get("variant") or card_data.get("parallel_variety"))
    if variant and variant.lower() not in IGNORE_VARIANTS:
        parts.append(variant)

    if card_data.get("is_graded"):
        grader = clean_value(card_data.get("grader"))
        grade = clean_value(card_data.get("grade"))

        if grader:
            parts.append(grader)
        if grade:
            parts.append(grade)

    return " ".join(dedupe_phrases(parts)).strip()



def apply_title_to_description(description_html: str, new_title: str) -> str:
    """Rewrite a description's opening line to match an edited title.

    Only the headline paragraph is replaced. Condition notes, the breakdown and anything
    typed by hand are left exactly as they are.
    """
    title = " ".join(str(new_title or "").split())
    if not title:
        return description_html

    html = str(description_html or "").strip()
    if not html:
        return "<p>{}</p>".format(title)

    if html.startswith("<p>") and "</p>" in html:
        return "<p>{}</p>".format(title) + html[html.index("</p>") + 4:]

    return "<p>{}</p>\n".format(title) + html


def _condition_detail(card_data: Dict[str, Any], *keys: str) -> str:
    """Return the first concrete condition-detail value OpenAI extracted."""
    for key in keys:
        value = clean_value(card_data.get(key))
        if value:
            return value
    return ""


def _sanitize_condition_text(text: str) -> str:
    """Remove weak/uncertain AI wording from seller-facing condition text."""
    replacements = {
        "appears to be ": "",
        "appears ": "",
        "seems to be ": "",
        "seems ": "",
        "looks like ": "",
        "possibly ": "",
        "likely ": "",
    }
    output = clean_value(text)
    lowered = output.lower()
    for old, new in replacements.items():
        if old in lowered:
            # simple case-insensitive-ish cleanup while preserving rest of text
            output = output.replace(old, new).replace(old.capitalize(), new.capitalize())
            lowered = output.lower()
    return output.strip()


def build_description(card_data: Dict[str, Any], price_data: Dict[str, Any] = None) -> str:
    name = clean_value(
        card_data.get("name")
        or card_data.get("card_name")
        or card_data.get("player_athlete")
        or card_data.get("character"),
        "Trading Card",
    )

    year = clean_value(card_data.get("year") or card_data.get("year_manufactured"))
    brand = clean_value(card_data.get("brand") or card_data.get("manufacturer"))
    card_set = clean_value(card_data.get("set"))
    card_number = clean_value(card_data.get("card_number"))
    variant = clean_value(card_data.get("variant") or card_data.get("parallel_variety"))
    grade = clean_value(card_data.get("grade"))
    grader = clean_value(card_data.get("grader"))
    condition = _sanitize_condition_text(
        clean_value(card_data.get("condition_estimate"), "Condition shown in photos")
    )

    parts = [year, brand, card_set, name]

    if card_number:
        parts.append(f"#{card_number}")

    if variant and variant.lower() not in IGNORE_VARIANTS:
        parts.append(variant)

    if card_data.get("is_graded") and grader and grade:
        parts.append(f"{grader} {grade}")

    # Same dedupe as the title: brand "Topps" arrives beside set "Topps Chrome".
    main_line = " ".join(dedupe_phrases(parts)).strip()

    html = [f"<p>{main_line}</p>"]

    if card_data.get("is_graded") and grader and grade:
        html.append(f"<p><strong>Grade:</strong> {grader} {grade}</p>")

        cert_number = clean_value(card_data.get("cert_number") or card_data.get("certification_number"))
        if cert_number:
            html.append(f"<p><strong>Certification Number:</strong> {cert_number}</p>")
    else:
        html.append(f"<p><strong>Condition:</strong> {condition}</p>")

        condition_rows = [
            ("Corners", _condition_detail(card_data, "corners_condition", "corners")),
            ("Edges", _condition_detail(card_data, "edges_condition", "edges")),
            ("Front surface", _condition_detail(card_data, "front_surface_condition", "front_surface")),
            ("Back surface", _condition_detail(card_data, "back_surface_condition", "back_surface")),
            ("Whitening", _condition_detail(card_data, "whitening_condition", "whitening")),
            ("Centering", _condition_detail(card_data, "centering_condition", "centering")),
            ("Visible flaws", _condition_detail(card_data, "visible_flaws", "flaws")),
        ]

        condition_rows = [
            (label, _sanitize_condition_text(value))
            for label, value in condition_rows
            if clean_value(value)
        ]

        if condition_rows:
            html.append("<p><strong>Condition breakdown:</strong></p>")
            html.append("<ul>")
            for label, value in condition_rows:
                html.append(f"<li><strong>{label}:</strong> {value}</li>")
            html.append(f"<li><strong>Overall assessment:</strong> {condition}</li>")
            html.append("</ul>")

    notes = _sanitize_condition_text(clean_value(card_data.get("notes")))
    if notes:
        html.append(f"<p>{notes}</p>")

    html.append("<p>Please review photos carefully for exact condition.</p>")
    html.append("<p>Exact card shown.</p>")
    html.append("<p>Shipped securely.</p>")

    return "\n".join(html)