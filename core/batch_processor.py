import uuid
from typing import Any, Dict, Optional

from core.image_pairing import uploaded_file_to_data_url
from core.listing_builder import build_comp_query, build_description, is_tcg_card
from services.card_aspects import is_non_sport_card
from core.scanned_image import ScannedImage
from services.ebay_auction import choose_listing_format
from services.ebay_inventory import create_or_replace_inventory_item
from services.ebay_offers import create_draft_offer, suggested_min_offer
from services.ebay_taxonomy import get_category_suggestions
from services.image_service import upload_card_images
from services.openai_service import build_seo_title, generate_card_data, title_warnings
from services.onepiece_lookup import enrich_one_piece_card_data
from services.pokemon_lookup import enrich_pokemon_card_data
from services.parallel_catalog import apply_catalog
from services.serial_check import recheck_serial
from services.sports_lookup import enrich_sports_card_data
from services.pricing_service import get_price_estimate
from utils.file_utils import clean_value
from utils.json_utils import try_parse_json

# Verified against eBay's taxonomy API (get_category_suggestions).
TCG_CATEGORY = ("183454", "CCG Individual Cards")
NON_SPORT_CATEGORY = ("183050", "Trading Card Singles")   # Collectibles > Non-Sport
SPORTS_CATEGORY = ("261328", "Trading Card Singles")      # Sports Mem > Sports Cards


def preferred_category(card_data):
    """The trading-card category this card belongs in, decided from the card itself."""
    if is_tcg_card(card_data):
        return TCG_CATEGORY
    if is_non_sport_card(card_data):
        return NON_SPORT_CATEGORY
    return SPORTS_CATEGORY


# Kept as the old name so any caller expecting a fallback still works.
fallback_category = preferred_category


def resolve_category(card_data, suggestions=None):
    """Pick the listing category, trusting eBay's suggestion only when it agrees.

    eBay's category suggester is keyword-driven and unreliable for card queries: it has
    answered "Other Disney Pins & Patches" and even "Washer & Dryer Parts" for Topps
    Disney cards, and routinely offers the sports category for non-sport cards, which
    then fails to publish because it demands a Sport aspect. The card's own type decides
    the category; a suggestion is honoured only when it matches that decision.
    """
    preferred_id, preferred_name = preferred_category(card_data)

    for suggestion in suggestions or []:
        category = suggestion.get("category", {}) or {}
        if category.get("categoryId") == preferred_id:
            return preferred_id, category.get("categoryName") or preferred_name

    return preferred_id, preferred_name


def ensure_card_category(card_data, category_id, category_name):
    """Publish-time guard for cards whose category was chosen before validation existed."""
    preferred_id, preferred_name = preferred_category(card_data)

    if category_id == preferred_id:
        return category_id, category_name or preferred_name

    return preferred_id, preferred_name


def _vision_data_url(image) -> str:
    """Scanned images provide a downscaled URL; Streamlit uploads fall back to raw bytes."""
    if hasattr(image, "vision_data_url"):
        return image.vision_data_url()
    return uploaded_file_to_data_url(image)


def reupload_rotated_images(front_path, back_path, front_rotation: int, back_rotation: int):
    """Re-upload a card's scans turned by the rotations chosen in the UI.

    Always starts from the original scan, so applying it twice is harmless.
    """
    front = ScannedImage(front_path)
    back = ScannedImage(back_path) if back_path else None

    return upload_card_images(
        front, back, front_rotation=front_rotation, back_rotation=back_rotation
    )


def process_scanned_card(front_file, back_file=None) -> Dict[str, Any]:
    """Identify one card, price it, and create its eBay draft offer.

    Runs on a background worker thread, so it must never touch Streamlit state.
    `back_file` is optional to support single-sided scans.
    """
    front_data_url = _vision_data_url(front_file)
    back_data_url = _vision_data_url(back_file) if back_file else ""

    # Enlarged close-ups of the front: serials and card codes are too small to read at
    # whole-card scale. Uploads without a scan on disk go without them.
    front_tiles = front_file.vision_tiles() if hasattr(front_file, "vision_tiles") else None

    raw_card_json = generate_card_data(front_data_url, back_data_url, front_tiles=front_tiles)
    card_data = try_parse_json(raw_card_json)

    if not card_data:
        raise ValueError("Could not parse card JSON for {}".format(getattr(front_file, "name", "scan")))

    # Validate and enrich card data before title/pricing/category creation.
    # Pokemon uses PokemonTCG lookup when confident; sports uses eBay active-listing
    # consensus only when multiple matching listings agree.
    card_data = enrich_pokemon_card_data(card_data)
    card_data = enrich_one_piece_card_data(card_data, front_data_url)
    card_data = enrich_sports_card_data(card_data)
    # The serial's print run against the product checklist: fills a parallel only one
    # parallel could be, and flags names the run contradicts.
    card_data = apply_catalog(card_data)
    # Doubtful serials (missing on a numbered parallel, or off the checklist) get a
    # second, zoomed-in read. Most cards skip this.
    card_data = recheck_serial(card_data, front_file, back_file)

    # Title first: it names the parallel, the print run and the grade, which is what a
    # sold-comp search has to match. Pricing off a looser query prices the wrong card.
    title = build_seo_title(card_data, {})
    card_data["comp_query_override"] = title

    price_data = get_price_estimate(card_data)
    suggested_price = float(price_data["estimated_price"])

    description = build_description(card_data, price_data)

    category_suggestions = get_category_suggestions(build_comp_query(card_data))

    category_id, category_name = resolve_category(card_data, category_suggestions)

    sku = "card-{}".format(uuid.uuid4().hex[:12])

    # Scans upload exactly as they came off the scanner. Rotation is a human call:
    # vision models judge card orientation too unreliably to trust with listing photos,
    # so the UI offers per-image rotate buttons instead.
    image_urls = upload_card_images(front_file, back_file)

    create_or_replace_inventory_item(
        sku=sku,
        title=title,
        description=description,
        category_id=category_id,
        sport=clean_value(card_data.get("sport")),
        league=clean_value(card_data.get("league")),
        team=clean_value(card_data.get("team")),
        condition_estimate=clean_value(card_data.get("condition_estimate")),
        is_graded=bool(card_data.get("is_graded", False)),
        image_urls=image_urls,
        price_value=suggested_price,
        card_data=card_data,
        category_name=category_name,
    )

    listing_plan = choose_listing_format(price_data, suggested_price)
    auction = listing_plan["format"] == "AUCTION"
    best_offer_enabled = not auction
    min_offer_price = suggested_min_offer(suggested_price)

    offer_result = create_draft_offer(
        sku=sku,
        category_id=category_id,
        price_value=suggested_price,
        best_offer_enabled=best_offer_enabled,
        min_offer_price=min_offer_price,
        listing_format=listing_plan["format"],
        auction_start_price=listing_plan.get("start_price"),
        auction_duration=listing_plan.get("duration", ""),
    )

    return {
        "sku": sku,
        "offer_id": offer_result.get("offerId"),
        "title": title,
        "description": description,
        "price": suggested_price,
        "price_source": price_data.get("source", "unknown"),
        "price_basis": price_data.get("basis", "ask"),
        "price_confidence": price_data.get("confidence", "low"),
        "needs_review": bool(price_data.get("needs_review", False)),
        "review_reasons": price_data.get("review_reasons", []),
        "title_warnings": title_warnings(card_data),
        "price_detail": price_data.get("detail", ""),
        "best_offer_enabled": best_offer_enabled,
        "min_offer_price": min_offer_price,
        "listing_format": listing_plan["format"],
        "offer_format": listing_plan["format"],     # what the eBay draft was created as
        "auction_start_price": listing_plan.get("start_price"),
        "auction_reason": listing_plan.get("reason", ""),
        "category_id": category_id,
        "category_name": category_name,
        "card_name": clean_value(card_data.get("name"), "Unknown"),
        "set": clean_value(card_data.get("set"), ""),
        "year": clean_value(card_data.get("year"), ""),
        "sport": clean_value(card_data.get("sport"), ""),
        "league": clean_value(card_data.get("league"), ""),
        "team": clean_value(card_data.get("team"), ""),
        "condition_estimate": clean_value(card_data.get("condition_estimate"), ""),
        "is_graded": bool(card_data.get("is_graded", False)),
        "card_data": card_data,
        "price_data": price_data,
        "image_urls": image_urls,
    }


# Backwards-compatible alias for the old upload-driven flow.
def process_single_card(front_file, back_file: Optional[Any] = None) -> Dict[str, Any]:
    return process_scanned_card(front_file, back_file)
