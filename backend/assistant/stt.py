"""Speech-to-text dispatch for voice input.

Uses Mistral Voxtral through OpenRouter, the same setup as the NGU assistant. It
takes 16 kHz mono WAV from the browser. OpenRouter documents `prompt` as ignored on
most providers, so vocabulary biasing is best-effort: Hindi/Hinglish accuracy
comes from the model, not from the prompt.
"""

import logging
import os

from django.apps import apps

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mistralai/voxtral-mini-transcribe"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"

DOMAIN_VOCABULARY = (
    "ERP, Sales Invoice, Purchase Invoice, Payment Entry, bill, challan, "
    "haldi turmeric, mirchi chilli, dhaniya coriander, jeera cumin, garam masala, "
    "kilo, kg, gram, bori, bag, katta, packet, quintal, "
    "rate, price list, cash, bank, payment received, advance."
)

# UI language codes the transcription API accepts. Anything else is omitted, which
# makes it auto-detect (it rejects the literal string "auto").
STT_LANGUAGES = {"en", "hi", "gu", "mr", "pa", "ta", "te", "kn", "bn", "ml", "ur"}


class TranscriptionUnavailable(Exception):
    """The transcription service could not handle the request."""


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
    """Transcribe audio bytes or a file-like object.

    Returns the text ("" when nothing was said). Raises TranscriptionUnavailable
    when the service cannot be used, so the UI can tell that apart from silence.
    """
    api_key = os.getenv("STT_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise TranscriptionUnavailable("No STT API key configured.")

    content = audio_file.read() if hasattr(audio_file, "read") else audio_file
    if not content:
        return ""

    data = {
        "model": os.getenv("STT_MODEL") or DEFAULT_MODEL,
        "temperature": "0.0",
        "response_format": "json",
        "prompt": build_biased_prompt(company),
    }
    lang = (language or "").split("-")[0].lower()
    if lang in STT_LANGUAGES:
        data["language"] = lang

    import requests

    try:
        resp = requests.post(
            os.getenv("STT_ENDPOINT") or DEFAULT_ENDPOINT,
            files={"file": ("audio.wav", content, "audio/wav")},
            data=data,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=int(os.getenv("STT_TIMEOUT", "30")),
        )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()
    except (requests.RequestException, ValueError) as exc:
        raise TranscriptionUnavailable(str(exc)) from exc
