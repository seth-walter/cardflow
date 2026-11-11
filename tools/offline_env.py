"""Placeholder settings for the offline UI tests.

CardFlow stops on a setup screen when required .env values are missing, which is right
for a real launch but would stop the tests on a fresh clone. These placeholders go in
before config.settings loads, and python-dotenv never overrides a variable that is
already set, so the tests never touch your real keys. Import this first.
"""

import os

PLACEHOLDERS = [
    "OPENAI_API_KEY", "GEMINI_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
    "EBAY_REFRESH_TOKEN", "EBAY_PAYMENT_POLICY_ID", "EBAY_RETURN_POLICY_ID",
    "EBAY_FULFILLMENT_POLICY_ID_UNDER_20", "EBAY_FULFILLMENT_POLICY_ID_OVER_20",
    "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_UPLOAD_PRESET",
]

for key in PLACEHOLDERS:
    os.environ.setdefault(key, "offline-test-" + key.lower())
