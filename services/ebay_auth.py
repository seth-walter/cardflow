import base64
import time

import requests

from config import settings
from utils.env_utils import save_env_value


def refresh_ebay_user_token() -> str:
    auth = base64.b64encode(
        f"{settings.EBAY_CLIENT_ID}:{settings.EBAY_CLIENT_SECRET}".encode()
    ).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth}",
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": settings.EBAY_REFRESH_TOKEN,
        "scope": " ".join(settings.EBAY_SCOPES),
    }

    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data,
        timeout=30,
    )
    response.raise_for_status()

    new_access_token = response.json()["access_token"]
    settings.EBAY_USER_TOKEN = new_access_token
    save_env_value("EBAY_USER_TOKEN", new_access_token)
    return new_access_token


GET_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5


def ebay_request(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers = dict(headers)
    headers["Authorization"] = f"Bearer {settings.EBAY_USER_TOKEN}"

    response = requests.request(method, url, headers=headers, **kwargs)

    if response.status_code == 401:
        refresh_ebay_user_token()
        headers["Authorization"] = f"Bearer {settings.EBAY_USER_TOKEN}"
        response = requests.request(method, url, headers=headers, **kwargs)

    # eBay returns intermittent 500s on reads that succeed moments later. Only GET is
    # retried: repeating a POST could publish, withdraw or delete something twice.
    if method.upper() == "GET":
        attempts = 0
        while response.status_code >= 500 and attempts < GET_RETRIES:
            attempts += 1
            time.sleep(RETRY_BACKOFF_SECONDS * attempts)
            response = requests.request(method, url, headers=headers, **kwargs)

    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        # A bare "400 Bad Request" hides why; eBay says why in the body.
        raise requests.HTTPError("{} - {}".format(error, ebay_error_text(response)),
                                 response=response) from None
    return response


def ebay_error_text(response: requests.Response) -> str:
    """eBay's own explanation of a failed call, e.g. "25002: Offer entity already exists"."""
    try:
        errors = response.json().get("errors") or []
    except ValueError:
        return response.text[:300]
    parts = []
    for error in errors:
        text = error.get("longMessage") or error.get("message") or ""
        params = ", ".join("{}={}".format(p.get("name"), p.get("value"))
                           for p in error.get("parameters") or [] if p.get("value"))
        parts.append("{}: {}{}".format(error.get("errorId", ""), text,
                                       " ({})".format(params) if params else ""))
    return "; ".join(parts) or response.text[:300]


def get_ebay_app_token() -> str:
    auth = base64.b64encode(
        f"{settings.EBAY_CLIENT_ID}:{settings.EBAY_CLIENT_SECRET}".encode()
    ).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth}",
    }

    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]
