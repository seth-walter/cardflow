from typing import Any, Dict, List, Optional

from services.ebay_auth import ebay_request
from services.card_aspects import build_card_aspects


TRACKED_THRESHOLD = 20.0


TRADING_CARD_CATEGORY_IDS = {"183050", "183454", "261328"}


def is_trading_card_category(category_id: str) -> bool:
    return str(category_id).strip() in TRADING_CARD_CATEGORY_IDS


def get_ungraded_condition_descriptor_value(
    category_id: str,
    condition_estimate: str,
) -> str:
    """
    Return valid eBay condition descriptor IDs for raw/ungraded cards.

    Important:
    Sports Trading Card Singles (261328) effectively only supports
    Near Mint or Better for raw cards.
    """

    category_id = str(category_id).strip()
    text = (condition_estimate or "").lower().strip()

    # Sports cards
    if category_id == "261328":
        return "400010"  # Near Mint or Better

    # TCG / non-sports mappings
    if any(x in text for x in ["lightly played", "lp"]):
        return "400015"

    if any(x in text for x in ["moderately played", "mp"]):
        return "400016"

    if any(x in text for x in ["heavily played", "hp", "damaged", "poor"]):
        return "400013"

    if any(x in text for x in ["near mint", "nm", "mint", "excellent"]):
        return "400010"

    # Safe default
    return "400010"


def get_grader_descriptor_value(grader: str) -> Optional[str]:
    text = (grader or "").lower().strip()

    if not text:
        return None

    # Order matters: BCCG/BVG before generic Beckett/BGS.
    if "bccg" in text:
        return "275011"
    if "bvg" in text:
        return "275012"
    if "bgs" in text or "beckett" in text:
        return "275013"
    if "psa" in text or "professional sports authenticator" in text:
        return "275010"
    if "csg" in text or "certified sports guaranty" in text:
        return "275014"
    if "cgc" in text or "certified guaranty" in text:
        return "275015"
    if "sgc" in text or "sportscard guaranty" in text:
        return "275016"
    if "ksa" in text:
        return "275017"
    if "gma" in text:
        return "275018"
    if "hga" in text:
        return "275019"
    if "isa" in text:
        return "2750110"
    if "pca" in text:
        return "2750111"
    if "gsg" in text:
        return "2750112"
    if "pgs" in text:
        return "2750113"
    if "mnt" in text:
        return "2750114"
    if "tag" in text or "technical authentication" in text:
        return "2750115"
    if "rare" in text or "rare edition" in text:
        return "2750116"
    if "rcg" in text or "revolution card" in text:
        return "2750117"
    if "pcg" in text or "premier card" in text:
        return "2750118"
    if "ace" in text:
        return "2750119"
    if "cga" in text or "card grading australia" in text:
        return "2750120"
    if text == "tcg" or "trading card grading" in text:
        return "2750121"
    if "ark" in text:
        return "2750122"

    # Keep graded status rather than silently creating an ungraded listing.
    # eBay supports Other for all three trading-card categories.
    return "2750123"


def get_grade_descriptor_value(grade: str) -> Optional[str]:
    import re

    text = (grade or "").lower().strip()

    if not text:
        return None

    if "authentic altered" in text:
        return "2750220"
    if "authentic" in text:
        return "2750219"

    grade_map = {
        "10": "275020",
        "9.5": "275021",
        "9": "275022",
        "8.5": "275023",
        "8": "275024",
        "7.5": "275025",
        "7": "275026",
        "6.5": "275027",
        "6": "275028",
        "5.5": "275029",
        "5": "2750210",
        "4.5": "2750211",
        "4": "2750212",
        "3.5": "2750213",
        "3": "2750214",
        "2.5": "2750215",
        "2": "2750216",
        "1.5": "2750217",
        "1": "2750218",
    }

    # Prefer decimal grades before whole numbers.
    match = re.search(r"(?<!\d)(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)(?!\d)", text)
    if match:
        return grade_map.get(match.group(1))

    return None


def build_condition_fields(
    category_id: str,
    is_graded: bool,
    grader: str = "",
    grade: str = "",
    certification_number: str = "",
    condition_estimate: str = "",
) -> Dict[str, Any]:
    if is_graded and is_trading_card_category(category_id):
        grader_value = get_grader_descriptor_value(grader)
        grade_value = get_grade_descriptor_value(grade)

        if not grader_value:
            raise ValueError(f"Graded card detected, but grader could not be mapped: {grader!r}")
        if not grade_value:
            raise ValueError(f"Graded card detected, but grade could not be mapped: {grade!r}")

        descriptors = [
            {
                "name": "27501",  # Professional Grader
                "values": [grader_value],
            },
            {
                "name": "27502",  # Grade
                "values": [grade_value],
            },
        ]

        cert = (certification_number or "").strip()
        if cert:
            descriptors.append({
                "name": "27503",  # Certification Number
                "additionalInfo": cert[:30],
            })

        return {
            "condition": "LIKE_NEW",
            "conditionDescriptors": descriptors,
        }

    if is_trading_card_category(category_id):
        return {
            "condition": "USED_VERY_GOOD",
            "conditionDescriptors": [
                {
                    "name": "40001",  # Card Condition
                    "values": [
                        get_ungraded_condition_descriptor_value(
                            category_id,
                            condition_estimate,
                        )
                    ],
                }
            ],
        }

    return {
        "condition": "USED_VERY_GOOD",
        "conditionDescriptors": [],
    }

def get_package_details(price_value: Optional[float]) -> Dict[str, Any]:
    if price_value is None:
        price_value = 0

    if float(price_value) > TRACKED_THRESHOLD:
        # Tracked shipping
        return {
            "dimensions": {
                "length": 6,
                "width": 4,
                "height": 1,
                "unit": "INCH",
            },
            "weight": {
                "value": 8,
                "unit": "OUNCE",
            },
        }
    else:
        # PWE (plain white envelope)
        return {
            "dimensions": {
                "length": 6,
                "width": 4,
                "height": 0.2,
                "unit": "INCH",
            },
            "weight": {
                "value": 3,
                "unit": "OUNCE",
            },
        }


def build_inventory_payload(
    title: str,
    description: str,
    category_id: str,
    sport: str = "",
    league: str = "",
    team: str = "",
    condition_estimate: str = "",
    is_graded: bool = False,
    image_urls: Optional[List[str]] = None,
    price_value: Optional[float] = None,
    card_data: Optional[Dict[str, Any]] = None,
    category_name: str = "",
) -> Dict[str, Any]:

    enriched_card_data = dict(card_data or {})

    # Backward compatibility
    if sport:
        enriched_card_data.setdefault("sport", sport)
    if league:
        enriched_card_data.setdefault("league", league)
    if team:
        enriched_card_data.setdefault("team", team)
    if condition_estimate:
        enriched_card_data.setdefault("condition_estimate", condition_estimate)

    enriched_card_data.setdefault("is_graded", is_graded)

    aspects = build_card_aspects(
        enriched_card_data,
        category_name=category_name,
    )

    category_text = f"{category_id} {category_name}".lower()
    combined_text = " ".join(str(v).lower() for v in enriched_card_data.values())

    if "game" not in {k.lower() for k in aspects.keys()}:
        if (
            "one piece" in combined_text
            or "onepiece" in combined_text
            or "bandai" in combined_text
            or "one piece" in category_text
        ):
            aspects["Game"] = ["One Piece Card Game"]
        elif (
            "pokemon" in combined_text
            or "pokémon" in combined_text
            or "pokemon" in category_text
            or "pokémon" in category_text
        ):
            aspects["Game"] = ["Pokémon TCG"]

    grader = enriched_card_data.get("grader") or enriched_card_data.get("professional_grader") or ""
    grade = enriched_card_data.get("grade") or ""
    certification_number = (
        enriched_card_data.get("certification_number")
        or enriched_card_data.get("cert_number")
        or ""
    )

    condition_fields = build_condition_fields(
        category_id=category_id,
        is_graded=bool(enriched_card_data.get("is_graded", False)),
        grader=str(grader),
        grade=str(grade),
        certification_number=str(certification_number),
        condition_estimate=condition_estimate,
    )

    payload: Dict[str, Any] = {
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1
            }
        },
        "condition": condition_fields["condition"],
        "packageWeightAndSize": get_package_details(price_value),
        "product": {
            "title": title,
            "description": description,
        },
        "conditionDescriptors": condition_fields["conditionDescriptors"],
    }

    if image_urls:
        payload["product"]["imageUrls"] = image_urls

    if aspects:
        payload["product"]["aspects"] = aspects

    return payload


def create_or_replace_inventory_item(
    sku: str,
    title: str,
    description: str,
    category_id: str,
    sport: str = "",
    league: str = "",
    team: str = "",
    condition_estimate: str = "",
    is_graded: bool = False,
    image_urls: Optional[List[str]] = None,
    price_value: Optional[float] = None,
    card_data: Optional[Dict[str, Any]] = None,
    category_name: str = "",
) -> None:
    headers = {
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }

    payload = build_inventory_payload(
        title=title,
        description=description,
        category_id=category_id,
        sport=sport,
        league=league,
        team=team,
        condition_estimate=condition_estimate,
        is_graded=is_graded,
        image_urls=image_urls,
        price_value=price_value,
        card_data=card_data,
        category_name=category_name,
    )

    ebay_request(
        "PUT",
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers=headers,
        json=payload,
        timeout=30,
    )


def get_inventory_location(location_key: str) -> Dict[str, Any]:
    response = ebay_request(
        "GET",
        f"https://api.ebay.com/sell/inventory/v1/location/{location_key}",
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    return response.json()


def create_inventory_location(
    location_key: str,
    postal_code: str = "20190",
    country: str = "US",
) -> Dict[str, Any]:
    payload = {
        "location": {
            "address": {
                "postalCode": postal_code,
                "country": country,
            }
        },
        "name": location_key,
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
    }

    response = ebay_request(
        "POST",
        f"https://api.ebay.com/sell/inventory/v1/location/{location_key}",
        headers={
            "Content-Type": "application/json",
            "Content-Language": "en-US",
        },
        json=payload,
        timeout=30,
    )

    return response.json() if response.text else {}