from functools import lru_cache
from typing import Any, Dict, List, Optional

from config import settings
from services.ebay_auth import ebay_request


CARD_CONDITION_DESCRIPTOR_ID = "40001"


@lru_cache(maxsize=128)
def get_item_condition_policies(category_id: str) -> Dict[str, Any]:
    """Fetch eBay item condition policies for a category.

    eBay condition descriptor values are category-specific, so the app should
    not hardcode values like 400011 unless eBay says that value is valid for
    the current category.
    """
    marketplace_id = getattr(settings, "EBAY_MARKETPLACE_ID", "EBAY_US") or "EBAY_US"

    response = ebay_request(
        "GET",
        f"https://api.ebay.com/sell/metadata/v1/marketplace/{marketplace_id}/get_item_condition_policies",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
        params={"filter": f"categoryIds:{{{category_id}}}"},
        timeout=30,
    )
    return response.json()


def _collect_condition_descriptors(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    descriptors: List[Dict[str, Any]] = []

    for policy in data.get("itemConditionPolicies", []) or []:
        # Some responses put descriptors at the policy level.
        for descriptor in policy.get("conditionDescriptors", []) or []:
            descriptors.append(descriptor)

        # Other responses nest descriptors under itemConditions.
        for condition in policy.get("itemConditions", []) or []:
            for descriptor in condition.get("conditionDescriptors", []) or []:
                descriptors.append(descriptor)

    return descriptors


@lru_cache(maxsize=128)
def get_card_condition_values(category_id: str) -> Dict[str, str]:
    """Return valid Card Condition descriptor values for this category.

    Example return:
        {"Near Mint or Better": "400010", "Excellent": "400011"}
    """
    try:
        data = get_item_condition_policies(str(category_id))
    except Exception as exc:
        print(f"Warning: unable to fetch eBay condition policies for category {category_id}: {exc}")
        return {}

    for descriptor in _collect_condition_descriptors(data):
        descriptor_id = str(
            descriptor.get("conditionDescriptorId")
            or descriptor.get("name")
            or ""
        )

        if descriptor_id != CARD_CONDITION_DESCRIPTOR_ID:
            continue

        values = descriptor.get("conditionDescriptorValues", []) or []
        result: Dict[str, str] = {}

        for value in values:
            value_name = (
                value.get("conditionDescriptorValueName")
                or value.get("name")
                or ""
            )
            value_id = (
                value.get("conditionDescriptorValueId")
                or value.get("value")
                or ""
            )

            if value_name and value_id:
                result[str(value_name)] = str(value_id)

        return result

    return {}


def choose_valid_card_condition_value(
    category_id: str,
    condition_estimate: str,
) -> Optional[str]:
    """Choose the closest valid eBay Card Condition descriptor value.

    This avoids failures like:
    "Condition descriptor value 400011 is not valid for condition descriptor 40001."
    """
    valid_values = get_card_condition_values(str(category_id))

    if not valid_values:
        return None

    normalized = {
        name.lower().strip(): value_id
        for name, value_id in valid_values.items()
        if name and value_id
    }

    text = (condition_estimate or "").lower().strip()

    if text in {"near mint", "nm", "near mint or better"}:
        desired_names = [
            "near mint or better",
            "near mint",
            "excellent or better",
            "excellent",
        ]
    elif text in {"excellent", "ex"}:
        desired_names = [
            "excellent",
            "excellent or better",
            "near mint or better",
            "near mint",
        ]
    elif text in {"very good", "vg"}:
        desired_names = [
            "very good",
            "very good or better",
            "excellent",
            "excellent or better",
            "near mint or better",
        ]
    elif text in {"poor", "damaged", "heavy wear"}:
        desired_names = [
            "poor",
            "poor or better",
            "very good",
            "very good or better",
        ]
    else:
        desired_names = [
            "near mint or better",
            "near mint",
            "excellent or better",
            "excellent",
        ]

    for desired_name in desired_names:
        if desired_name in normalized:
            return normalized[desired_name]

    # Last resort: never hardcode an invalid value. Use one value eBay returned.
    return next(iter(valid_values.values()), None)
