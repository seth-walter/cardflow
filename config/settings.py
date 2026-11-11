import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# openai | gemini. Picks the default models below; a model set explicitly in .env wins.
# Gemini usage is paid from the Google AI Pro plan's monthly Cloud credit.
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "openai").strip().lower()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# Optional key from a Google project with billing off. Used only once the paid key runs
# out of credit (402): rate-limited, and Google may train on free-tier requests.
GEMINI_FREE_API_KEY = os.getenv("GEMINI_FREE_API_KEY", "")
# The free tier may not offer GEMINI_MODEL; blank means the same model.
GEMINI_FREE_MODEL = os.getenv("GEMINI_FREE_MODEL", "")

# Card identification. The small model missed parallels, print runs and promo stamps on
# most cards (measured with tools/eval_titles.py against hand-corrected live titles).
# gpt-5.4-mini scored as well as gpt-5.5 on the eval set at a fraction of the cost.
VISION_MODEL = os.getenv("VISION_MODEL", GEMINI_MODEL if VISION_PROVIDER == "gemini"
                         else "gpt-5.4-mini")
# none | low | medium | high. Applies to the serial check too. Low is the cost choice:
# medium beat it only slightly on paired eval runs (token F1 0.906/0.902 vs 0.893/0.875
# on the Topps cards) for several times the reasoning tokens. "none" was never
# measured and reading foil serials needs some reasoning.
VISION_REASONING = os.getenv("VISION_REASONING", "low").strip().lower()
# Reads the digits of a located serial stamp (services/serial_check.py). One small call,
# only on cards whose serial is doubtful. gpt-5.5 was the only model to read a sideways
# 112/399 exactly; mini and gpt-5.4 read 11/399.
SERIAL_READ_MODEL = os.getenv("SERIAL_READ_MODEL", GEMINI_MODEL if VISION_PROVIDER == "gemini"
                              else "gpt-5.5")
# Which sports cards get the zoomed serial check: "all" (every card with no serial
# read: up to two extra calls per card) or "doubtful" (only cards with warnings or a
# parallel but no serial). "all" scored no better on the eval set (13 vs 14 exact).
SERIAL_CHECK = os.getenv("SERIAL_CHECK", "doubtful").strip().lower()

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_USER_TOKEN = os.getenv("EBAY_USER_TOKEN", "")
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN", "")
# What the refresh token was granted. A refresh asking for anything more fails outright
# (invalid_scope), so tools/ebay_consent.py rewrites this when it grants new scopes.
EBAY_SCOPES = (os.getenv("EBAY_SCOPES") or " ".join(
    "https://api.ebay.com/oauth/api_scope" + suffix
    for suffix in ("", "/sell.inventory", "/sell.inventory.readonly", "/sell.account.readonly")
)).split()

EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
EBAY_MERCHANT_LOCATION_KEY = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "home_us")
EBAY_PAYMENT_POLICY_ID = os.getenv("EBAY_PAYMENT_POLICY_ID", "")
EBAY_RETURN_POLICY_ID = os.getenv("EBAY_RETURN_POLICY_ID", "")
EBAY_FULFILLMENT_POLICY_ID_UNDER_20 = os.getenv("EBAY_FULFILLMENT_POLICY_ID_UNDER_20", "")
EBAY_FULFILLMENT_POLICY_ID_OVER_20 = os.getenv("EBAY_FULFILLMENT_POLICY_ID_OVER_20", "")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET", "")

# Live scanning (fi-7160 -> PaperStream Capture -> watched folder)
SCAN_INBOX_DIR = os.getenv("SCAN_INBOX_DIR", r"D:\Scans\CardScans")
MAX_CARDS_PER_SESSION = int(os.getenv("MAX_CARDS_PER_SESSION", "150"))
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "3"))
SCAN_POLL_SECONDS = float(os.getenv("SCAN_POLL_SECONDS", "1.0"))
# The card list survives restarts here. "Clear all cards" in the sidebar empties it.
SESSION_STATE_PATH = os.getenv("SESSION_STATE_PATH", str(Path(__file__).resolve().parents[1]
                                                          / "data" / "session_cards.json"))

# --- pricing -----------------------------------------------------------------
# Paid sold-comp provider (PriceCharting / SportsCardsPro share one token).
PRICECHARTING_TOKEN = os.getenv("PRICECHARTING_TOKEN", "")

# Active listings are asking prices and run high. Set this from your own realised
# sales (tools/calibrate_prices.py) once you have the data; 1.0 means "no correction".
ASK_TO_SOLD_FACTOR = float(os.getenv("ASK_TO_SOLD_FACTOR", "1.0"))

# A card is flagged NEEDS PRICE REVIEW below this many comps, or above this spread.
MIN_COMPS_FOR_CONFIDENCE = int(os.getenv("MIN_COMPS_FOR_CONFIDENCE", "3"))
MAX_SPREAD_RATIO = float(os.getenv("MAX_SPREAD_RATIO", "2.5"))

# SoldComps (sold-comps.com): real eBay completed sales via a third-party API.
SOLDCOMPS_API_KEY = os.getenv("SOLDCOMPS_API_KEY", "")
SOLDCOMPS_DAYS = int(os.getenv("SOLDCOMPS_DAYS", "90"))
SOLDCOMPS_CACHE_DAYS = int(os.getenv("SOLDCOMPS_CACHE_DAYS", "7"))
SOLDCOMPS_MIN_COMPS = int(os.getenv("SOLDCOMPS_MIN_COMPS", "2"))
# Extra pages are fetched only when a card comes back with few comps. Each page costs
# one request from the monthly quota, so this stays at 1 unless you raise it.
SOLDCOMPS_MAX_PAGES = int(os.getenv("SOLDCOMPS_MAX_PAGES", "1"))
SOLDCOMPS_ENOUGH_COMPS = int(os.getenv("SOLDCOMPS_ENOUGH_COMPS", "8"))
# eBay pads a thin search with "results matching fewer words", so a query for a True
# Gold /50 comes back full of base cards. Items must share this fraction of the query's
# words to count as a comp. Measured on real listings: 0.8 separates parallels cleanly.
SOLDCOMPS_MIN_RELEVANCE = float(os.getenv("SOLDCOMPS_MIN_RELEVANCE", "0.8"))

# Best Offer floor: offers below this share of the asking price are auto-declined.
# 0.20 lets almost anything through, which is the point - a low offer is still a sale.
MIN_OFFER_RATIO = float(os.getenv("MIN_OFFER_RATIO", "0.20"))
# eBay will not accept a floor under a dollar.
MIN_OFFER_ABSOLUTE = float(os.getenv("MIN_OFFER_ABSOLUTE", "0.99"))

# eBay now shows the accepted amount on Best Offer sales, which makes them the truest
# record of what a buyer actually paid. "include" counts them alongside Buy It Now
# sales, "exclude" ignores them, "prefer" uses them alone when there are enough.
SOLDCOMPS_OFFER_MODE = os.getenv("SOLDCOMPS_OFFER_MODE", "include").strip().lower()

# --- auctions ----------------------------------------------------------------
# New drafts priced under this go up as auctions instead of Buy It Now. 0 turns it off.
AUCTION_UNDER = float(os.getenv("AUCTION_UNDER", "50"))
AUCTION_DURATION = os.getenv("AUCTION_DURATION", "DAYS_7")
# The lowest opening bid you will accept, whatever the comps say. Set it to what a
# card costs you to list, sell and ship so no auction can lose money.
AUCTION_MIN_START = float(os.getenv("AUCTION_MIN_START", "0.99"))
# eBay's trading-card final value fee, used to show what an opening bid nets.
EBAY_FEE_RATE = float(os.getenv("EBAY_FEE_RATE", "0.1325"))
EBAY_FEE_FIXED = float(os.getenv("EBAY_FEE_FIXED", "0.40"))

# --- optional lookups ----------------------------------------------------------
# pokemontcg.io works without a key, at a lower rate limit.
POKEMONTCG_API_KEY = os.getenv("POKEMONTCG_API_KEY", "")


def missing_required() -> list:
    """The .env values CardFlow cannot run without, for the setup message on launch."""
    gemini = VISION_MODEL.startswith("gemini")
    required = {
        "GEMINI_API_KEY" if gemini else "OPENAI_API_KEY": GEMINI_API_KEY if gemini else OPENAI_API_KEY,
        "EBAY_CLIENT_ID": EBAY_CLIENT_ID,
        "EBAY_CLIENT_SECRET": EBAY_CLIENT_SECRET,
        "EBAY_REFRESH_TOKEN": EBAY_REFRESH_TOKEN,
        "EBAY_PAYMENT_POLICY_ID": EBAY_PAYMENT_POLICY_ID,
        "EBAY_RETURN_POLICY_ID": EBAY_RETURN_POLICY_ID,
        "EBAY_FULFILLMENT_POLICY_ID_UNDER_20": EBAY_FULFILLMENT_POLICY_ID_UNDER_20,
        "EBAY_FULFILLMENT_POLICY_ID_OVER_20": EBAY_FULFILLMENT_POLICY_ID_OVER_20,
        "CLOUDINARY_CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "CLOUDINARY_UPLOAD_PRESET": CLOUDINARY_UPLOAD_PRESET,
    }
    return [key for key, value in required.items() if not value]
