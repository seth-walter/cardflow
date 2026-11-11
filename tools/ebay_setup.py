"""One-time eBay account setup: business policy IDs and the inventory location.

Run after tools/ebay_consent.py has saved your tokens:

    python tools/ebay_setup.py

It lists the payment, return and shipping policies on your eBay account, asks which to
use, and writes their IDs to .env. Then it checks the inventory location CardFlow ships
from and creates it from your ZIP code if it does not exist. Nothing is listed or
changed on eBay apart from creating that location.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from config import settings
from services.ebay_auth import ebay_request
from services.ebay_inventory import create_inventory_location
from utils.env_utils import save_env_value

ACCOUNT_API = "https://api.ebay.com/sell/account/v1/"

# (.env key, API path, list key, id key, what it is for)
POLICIES = [
    ("EBAY_PAYMENT_POLICY_ID", "payment_policy", "paymentPolicies", "paymentPolicyId",
     "Payment policy"),
    ("EBAY_RETURN_POLICY_ID", "return_policy", "returnPolicies", "returnPolicyId",
     "Return policy"),
    ("EBAY_FULFILLMENT_POLICY_ID_UNDER_20", "fulfillment_policy", "fulfillmentPolicies",
     "fulfillmentPolicyId", "Shipping policy for cards $20 and under (envelope / PWE)"),
    ("EBAY_FULFILLMENT_POLICY_ID_OVER_20", "fulfillment_policy", "fulfillmentPolicies",
     "fulfillmentPolicyId", "Shipping policy for cards over $20 (tracked)"),
]


def fetch_policies(path: str, list_key: str) -> list:
    response = ebay_request(
        "GET", ACCOUNT_API + path,
        params={"marketplace_id": settings.EBAY_MARKETPLACE_ID}, timeout=30)
    return response.json().get(list_key, [])


def choose(label: str, policies: list, id_key: str, current: str) -> str:
    print("\n" + label + ":")
    for number, policy in enumerate(policies, start=1):
        marker = "  (current)" if policy[id_key] == current else ""
        print("  {}. {}{}".format(number, policy.get("name", "?"), marker))
    while True:
        raw = input("Pick 1-{} (Enter keeps the current one): ".format(len(policies))).strip()
        if not raw and current:
            return current
        if raw.isdigit() and 1 <= int(raw) <= len(policies):
            return policies[int(raw) - 1][id_key]
        print("  Type one of the numbers above.")


def setup_policies() -> None:
    cache = {}
    for env_key, path, list_key, id_key, label in POLICIES:
        if path not in cache:
            cache[path] = fetch_policies(path, list_key)
        policies = cache[path]
        if not policies:
            raise SystemExit(
                "\nNo {} found on your eBay account. Create one in Seller Hub > Account > "
                "Business policies (see the README), then run this again.".format(label.lower()))
        chosen = choose(label, policies, id_key, getattr(settings, env_key, ""))
        save_env_value(env_key, chosen)
        setattr(settings, env_key, chosen)


def setup_location() -> None:
    key = settings.EBAY_MERCHANT_LOCATION_KEY
    try:
        ebay_request("GET", "https://api.ebay.com/sell/inventory/v1/location/" + key,
                     timeout=30)
        print("\nInventory location '{}' already exists.".format(key))
        return
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise

    print("\nCardFlow needs an inventory location: the ZIP code your cards ship from.")
    postal = input("Your ZIP / postal code: ").strip()
    if not postal:
        raise SystemExit("No ZIP code given - run this again when you have it.")
    country = input("Country code (Enter for US): ").strip().upper() or "US"
    create_inventory_location(key, postal_code=postal, country=country)
    save_env_value("EBAY_MERCHANT_LOCATION_KEY", key)
    print("Created inventory location '{}'.".format(key))


def main():
    if not (settings.EBAY_CLIENT_ID and settings.EBAY_CLIENT_SECRET and settings.EBAY_REFRESH_TOKEN):
        raise SystemExit("eBay keys or tokens missing from .env. Fill in EBAY_CLIENT_ID, "
                         "EBAY_CLIENT_SECRET and EBAY_RUNAME, then run "
                         "python tools/ebay_consent.py first.")
    setup_policies()
    setup_location()

    missing = settings.missing_required()
    print("\nSaved to .env.")
    if missing:
        print("Still missing: " + ", ".join(missing))
    else:
        print("Setup complete. Start CardFlow with: python -m streamlit run CardFlow.py")


if __name__ == "__main__":
    main()
