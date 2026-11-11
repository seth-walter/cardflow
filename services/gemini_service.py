"""Gemini as the vision model, called over REST (google-genai needs Python 3.9+).

Takes the same content parts the OpenAI calls are built from - {"type": "input_text"}
and {"type": "input_image", "image_url": "data:..."} - so the prompts, close-ups and
reference examples are shared and only the transport differs.
"""

import re
import threading
import time

import requests

from config import settings

URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
RETRIES = 3

# Running token totals since the app started, for the cost line in the UI and evals.
usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "free_calls": 0}
_usage_lock = threading.Lock()

# Set once the paid key returns 402 and GEMINI_FREE_API_KEY takes over; stays set until
# restart so every card in the batch does not first fail on the empty paid balance.
on_free_tier = False


def _part(item: dict) -> dict:
    if item.get("type") == "input_image":
        match = re.match(r"data:([^;]+);base64,(.*)", item["image_url"], re.DOTALL)
        if not match:
            raise ValueError("Gemini needs inline images (data: URLs)")
        part = {"inline_data": {"mime_type": match.group(1), "data": match.group(2)}}
        if item.get("detail") == "low":
            # Reference examples only need their border pattern: 267 tokens instead of
            # 1,093 per image (measured with countTokens).
            part["mediaResolution"] = {"level": "MEDIA_RESOLUTION_LOW"}
        return part
    return {"text": item.get("text", "")}


def _thinking(model: str) -> dict:
    """VISION_REASONING as Gemini thinking. Gemini 2.x takes a budget, 3.x a level."""
    effort = settings.VISION_REASONING
    if not effort:
        return {}
    if model.startswith("gemini-2"):
        budget = {"none": 0, "low": 1024, "medium": 4096, "high": 16384}.get(effort)
        return {} if budget is None else {"thinkingBudget": budget}
    level = {"none": "minimal", "low": "low", "medium": "medium", "high": "high"}.get(effort)
    return {"thinkingLevel": level} if level else {}


def generate(content: list, model: str) -> str:
    """The model's text reply to the content parts."""
    body = {
        "contents": [{"role": "user", "parts": [_part(item) for item in content]}],
        # Every prompt asks for bare JSON; this stops markdown fences around it.
        "generationConfig": {"responseMimeType": "application/json"},
    }
    global on_free_tier
    free = on_free_tier
    if not free:
        response = _post(body, model, settings.GEMINI_API_KEY)
    if not free and response.status_code == 402 and settings.GEMINI_FREE_API_KEY:
        # Paid prepayment balance is empty: carry on with the free-tier key.
        on_free_tier = free = True
    if free:
        model = settings.GEMINI_FREE_MODEL or model
        response = _post(body, model, settings.GEMINI_FREE_API_KEY)

    if response.status_code >= 400:
        raise RuntimeError("Gemini {} error: {}".format(response.status_code, _error_text(response)))

    data = response.json()
    meta = data.get("usageMetadata") or {}
    with _usage_lock:
        usage["calls"] += 1
        usage["free_calls"] += free
        usage["input_tokens"] += meta.get("promptTokenCount", 0)
        usage["output_tokens"] += (meta.get("candidatesTokenCount", 0)
                                   + meta.get("thoughtsTokenCount", 0))

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no reply")
        raise RuntimeError("Gemini returned nothing: {}".format(reason))
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _post(body: dict, model: str, key: str):
    """One request, retried through rate limits; the last response either way."""
    body = dict(body, generationConfig=dict(body["generationConfig"]))
    thinking = _thinking(model)
    if thinking:
        body["generationConfig"]["thinkingConfig"] = thinking

    for attempt in range(RETRIES + 1):
        response = requests.post(URL.format(model), json=body, timeout=120,
                                 headers={"x-goog-api-key": key})
        if response.status_code == 400 and thinking and "thinking" in response.text.lower():
            # A model that does not take this thinking setting: ask once without it.
            body["generationConfig"].pop("thinkingConfig", None)
            thinking = {}
            continue
        # 429 is the free tier's per-minute limit, 503 an overloaded model; both pass.
        if response.status_code in (429, 503) and attempt < RETRIES:
            time.sleep(_retry_delay(response, attempt))
            continue
        break
    return response


def _retry_delay(response, attempt: int) -> float:
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', response.text)
    return min(float(match.group(1)), 60.0) if match else 2.0 * (attempt + 1)


def _error_text(response) -> str:
    try:
        error = response.json().get("error") or {}
        return "{} {}".format(error.get("status", ""), error.get("message", "")).strip()
    except ValueError:
        return response.text[:300]
