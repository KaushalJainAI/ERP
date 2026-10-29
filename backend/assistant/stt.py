"""Speech-to-text dispatch for voice input with domain vocabulary biasing."""

import logging
import os
from typing import Optional

from django.apps import apps
from django.conf import settings

logger = logging.getLogger(__name__)

DOMAIN_VOCABULARY = (
    "ERP, Sales Invoice, Purchase Invoice, Payment Entry, bill, challan, "
    "haldi turmeric, mirchi chilli, dhaniya coriander, jeera cumin, garam masala, "
    "kilo, kg, gram, bori, bag, katta, packet, quintal, "
    "rate, price list, cash, bank, payment received, advance."
)

LANG_MAP = {
    "en": "en",
    "hi": "hi",
    "hinglish": "hi",
    "gu": "gu",
    "mr": "mr",
    "pa": "pa",
    "ta": "ta",
    "te": "te",
    "kn": "kn",
    "bn": "bn",
    "ml": "ml",
    "ur": "ur",
}


def build_biased_prompt(company=None) -> str:
    """Build vocabulary prompt biased with the company's top item and party names."""
    prompt_parts = [DOMAIN_VOCABULARY]
    try:
        Item = apps.get_model("stock.Item")
        top_items = list(Item.objects.filter(is_active=True).values_list("name", flat=True)[:15])
        if top_items:
            prompt_parts.append(", ".join(top_items))

        Customer = apps.get_model("selling.Customer")
        top_customers = list(Customer.objects.filter(is_active=True).values_list("name", flat=True)[:10])
        if top_customers:
            prompt_parts.append(", ".join(top_customers))
    except Exception as exc:
        logger.debug("Could not build dynamic vocabulary: %s", exc)

    return ". ".join(prompt_parts)


def transcribe_audio(audio_file, language: str = "en", company=None) -> str:
    """Transcribe audio bytes or file-like object using Voxtral / Whisper / OpenRouter.

    Degrades gracefully if STT service is unreachable or not configured.
    """
    api_key = os.getenv("STT_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("No STT API key configured.")
        return ""

    provider = os.getenv("STT_PROVIDER", "openrouter").lower()
    lang = LANG_MAP.get(language, "hi")
    prompt = build_biased_prompt(company)

    # In local/testing mode or when simulated:
    if hasattr(audio_file, "read"):
        content = audio_file.read()
    else:
        content = audio_file

    if not content:
        return ""

    try:
        # Standard Whisper/Voxtral endpoint via OpenRouter or OpenAI compatible STT
        endpoint = os.getenv("STT_ENDPOINT", "https://openrouter.ai/api/v1/audio/transcriptions")
        import requests

        files = {"file": ("audio.wav", content, "audio/wav")}
        data = {
            "model": os.getenv("STT_MODEL", "mistralai/voxtral-mini"),
            "language": lang,
            "prompt": prompt,
        }
        headers = {"Authorization": f"Bearer {api_key}"}

        resp = requests.post(endpoint, files=files, data=data, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("text", "")
        logger.warning("STT endpoint returned status %d: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("STT request failed: %s", exc)

    return ""
