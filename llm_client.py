"""Helpers for using an LLM to summarise RSS items for posting."""

from __future__ import annotations

import logging
import textwrap
from typing import Any, Dict, Iterable, List, Optional

try:  # Optional dependency -- the rest of the project also treats it as optional
    import requests
except Exception:  # pragma: no cover - keep import errors from breaking the module
    requests = None  # type: ignore[assignment]

DEFAULT_MODEL = "gpt-3.5-turbo"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TONE = "Confident, constructive, and optimistic while staying respectful."
DEFAULT_TEMPERATURE = 0.6
DEFAULT_TIMEOUT = 15.0


def load_llm_settings(settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return OpenAI configuration extracted from ``settings``.

    The UI provides three user editable values: persona text, maximum character
    length and the API key.  Optional advanced values (model, base URL, etc.)
    may be passed through ``settings`` as well.  When the mandatory values are
    missing ``None`` is returned so callers can fall back to the legacy
    behaviour.
    """

    if not isinstance(settings, dict):
        return None

    persona_text = str(settings.get("rss_persona_text", "") or "").strip()
    api_key = str(settings.get("openai_api_key", "") or "").strip()

    max_length_raw = settings.get("rss_max_post_length")
    max_length: Optional[int]
    if isinstance(max_length_raw, (int, float)):
        max_length = int(max_length_raw)
    elif isinstance(max_length_raw, str):
        try:
            max_length = int(max_length_raw.strip())
        except ValueError:
            max_length = None
    else:
        max_length = None

    if not persona_text or not api_key:
        return None

    if max_length is None or max_length <= 0:
        max_length = 280

    base_url = str(settings.get("openai_base_url") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = str(settings.get("openai_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    tone = str(settings.get("rss_persona_tone") or "").strip()
    if not tone:
        tone = DEFAULT_TONE

    temperature_raw = settings.get("openai_temperature")
    try:
        temperature = float(temperature_raw)
    except (TypeError, ValueError):
        temperature = DEFAULT_TEMPERATURE

    timeout_raw = settings.get("openai_timeout")
    try:
        timeout = float(timeout_raw)
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return {
        "api_key": api_key,
        "persona_text": persona_text,
        "max_length": max_length,
        "model": model,
        "base_url": base_url,
        "tone": tone,
        "temperature": temperature,
        "timeout": timeout,
    }


def summarize_rss_item(rss_item: Dict[str, Any], persona_settings: Dict[str, Any]) -> str:
    """Call the OpenAI chat completion API and return generated post text."""

    if requests is None:
        raise RuntimeError("requests dependency is required for OpenAI API calls")

    api_key = persona_settings.get("api_key")
    if not api_key:
        raise ValueError("persona_settings missing 'api_key'")

    prompt = _build_prompt(rss_item, persona_settings)

    payload = {
        "model": persona_settings.get("model", DEFAULT_MODEL),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a social media editor who writes concise, opinionated "
                    "posts for X/Twitter based on article summaries."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(persona_settings.get("temperature", DEFAULT_TEMPERATURE)),
    }

    base_url = str(persona_settings.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    if not base_url:
        base_url = DEFAULT_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = float(persona_settings.get("timeout", DEFAULT_TIMEOUT))

    logging.debug("Calling OpenAI API at %s", url)
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    choices = data.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if content:
            return content.strip()

    raise RuntimeError("OpenAI response did not contain generated content")


def _build_prompt(rss_item: Dict[str, Any], persona_settings: Dict[str, Any]) -> str:
    """Construct the prompt passed to the model."""

    meta_fields: List[tuple[str, Iterable[str]]] = [
        ("Title", ("title",)),
        ("Summary", ("summary", "description")),
        ("Published", ("published", "updated")),
        ("Link", ("link",)),
        ("Source", ("source",)),
    ]

    lines: List[str] = []
    for label, keys in meta_fields:
        value = _first_non_empty(rss_item, keys)
        if value:
            lines.append(f"{label}: {value}")

    tags = rss_item.get("tags")
    if isinstance(tags, (list, tuple)) and tags:
        lines.append(f"Tags: {', '.join(str(t) for t in tags if str(t).strip())}")
    elif isinstance(tags, str) and tags.strip():
        lines.append(f"Tags: {tags.strip()}")

    if not lines:
        title = rss_item.get("title")
        if title:
            lines.append(f"Title: {title}")
        else:
            lines.append("(No metadata supplied beyond the raw item.)")

    persona_text = str(persona_settings.get("persona_text", "")).strip()
    if not persona_text:
        persona_text = "Use a pragmatic founder voice that adds concise, actionable opinion."

    max_length = int(persona_settings.get("max_length", 280))
    tone = persona_settings.get("tone", DEFAULT_TONE)

    guardrails = [
        f"Maximum length: {max_length} characters including spaces.",
        "Write a single paragraph suitable for an X/Twitter post.",
        "Adopt a first-person point of view and add a clear takeaway.",
        "Avoid hashtags, emoji, and trailing questions.",
        f"Tone: {tone}",
    ]

    prompt = textwrap.dedent(
        f"""
        Write a concise social media post summarizing and reacting to the article below for an X/Twitter audience.

        Persona guidance:
        {persona_text}

        Guardrails:
        {textwrap.indent('\n'.join(f'- {g}' for g in guardrails), ' ')}

        Article metadata:
        {textwrap.indent('\n'.join(lines), ' ')}

        Return only the post text with no surrounding quotes.
        """
    ).strip()

    return prompt


def _first_non_empty(data: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            value_str = str(value).strip()
            if value_str:
                return value_str
    return ""


__all__ = ["load_llm_settings", "summarize_rss_item"]
