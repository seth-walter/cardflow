"""One-time re-consent to add eBay scopes the app's refresh token does not carry yet.

The original token only has inventory and account scopes. This adds:
  sell.fulfillment.readonly - read your own orders, the only free source of real sold
                              comps (then run tools/backfill_sales.py)
  sell.marketing            - promote listings from the Promote page

Run: python tools/ebay_consent.py
"""

import base64
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from config import settings
from utils.env_utils import save_env_value

SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
]

AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def basic_auth() -> str:
    pair = "{}:{}".format(settings.EBAY_CLIENT_ID, settings.EBAY_CLIENT_SECRET)
    return base64.b64encode(pair.encode()).decode()


def consent_url() -> str:
    runame = os.getenv("EBAY_RUNAME", "") or getattr(settings, "EBAY_RUNAME", "")
    return "{}?{}".format(AUTH_URL, urlencode({
        "client_id": settings.EBAY_CLIENT_ID,
        "redirect_uri": runame,
        "response_type": "code",
        "scope": " ".join(SCOPES),
    }))


def exchange(code: str) -> dict:
    runame = os.getenv("EBAY_RUNAME", "")
    response = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + basic_auth(),
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": runame,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise SystemExit("eBay rejected the code:\n" + response.text)
    return response.json()


def main():
    runame = os.getenv("EBAY_RUNAME", "")
    if not runame:
        raise SystemExit("EBAY_RUNAME is missing from .env - it is the redirect name "
                         "shown on your eBay developer keyset.")

    url = consent_url()
    print("\n1. Open this URL and approve the requested access:\n")
    print(url)
    print("\n   (attempting to open it in your browser too)")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("\n2. eBay will redirect to your accepted URL. Copy the FULL address bar "
          "contents, or just the value of the ?code= parameter.\n")
    raw = input("Paste it here: ").strip()

    if not raw:
        raise SystemExit("Nothing pasted.")

    code = raw
    if "code=" in raw:
        code = raw.split("code=", 1)[1].split("&", 1)[0]

    # eBay's redirect URL-encodes the code; the token call needs it decoded.
    code = unquote(code)

    tokens = exchange(code)
    access = tokens.get("access_token", "")
    refresh = tokens.get("refresh_token", "")

    if not refresh:
        raise SystemExit("No refresh token returned:\n" + str(tokens))

    save_env_value("EBAY_USER_TOKEN", access)
    save_env_value("EBAY_REFRESH_TOKEN", refresh)
    # Token refreshes must ask for exactly these from now on.
    save_env_value("EBAY_SCOPES", " ".join(SCOPES))

    print("\nSaved new tokens to .env.")
    print("Scopes granted:")
    for scope in SCOPES:
        print("   " + scope)
    print("\nRestart CardFlow to pick them up. To pull in your own sales as comps, run: "
          "python tools/backfill_sales.py")


if __name__ == "__main__":
    main()
