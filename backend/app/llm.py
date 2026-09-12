"""Single LLM entry point for every agent.

Backed by Gemini on Google Vertex AI. Every call has a deterministic
fallback: if credentials are missing or the call fails, the caller's
rule-based fallback is returned instead, so the customer-care pipeline
degrades rather than breaking.
"""
import json
import os
import re

from . import config

_client = None
_types = None
_init_error: str | None = None


def _build_client():
    if config.GOOGLE_APPLICATION_CREDENTIALS:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config.GOOGLE_APPLICATION_CREDENTIALS
    if not config.VERTEX_PROJECT_ID:
        return None, None, "VERTEX_PROJECT_ID not set"

    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=config.VERTEX_PROJECT_ID, location=config.VERTEX_LOCATION)
    return client, types, None


try:
    _client, _types, _init_error = _build_client()
except Exception as exc:
    _client, _types, _init_error = None, None, str(exc)

if _init_error:
    print(f"[llm] running in rule-based fallback mode: {_init_error}")


def is_live() -> bool:
    return _client is not None


def provider_info() -> dict:
    return {
        "provider": "vertex-gemini",
        "model": config.GEMINI_MODEL,
        "location": config.VERTEX_LOCATION,
        "live": is_live(),
        "error": _init_error,
    }


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM output: {text!r}")
    return json.loads(match.group(0))


def _call(system_prompt: str, user_prompt: str, max_tokens: int, json_mode: bool) -> str:
    cfg = _types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        temperature=0.3,
        # Customer-care replies are short and latency-sensitive; thinking adds
        # seconds per message without improving these outputs.
        thinking_config=_types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    resp = _client.models.generate_content(
        model=config.GEMINI_MODEL, contents=user_prompt, config=cfg
    )
    return (resp.text or "").strip()


def complete_json(system_prompt: str, user_prompt: str, fallback: dict) -> dict:
    if not _client:
        return fallback
    try:
        return _extract_json(_call(system_prompt, user_prompt, 500, json_mode=True))
    except Exception as exc:
        print(f"[llm] complete_json failed, using fallback: {exc}")
        return fallback


def complete_text(system_prompt: str, user_prompt: str, fallback: str) -> str:
    if not _client:
        return fallback
    try:
        text = _call(system_prompt, user_prompt, 400, json_mode=False)
        return text or fallback
    except Exception as exc:
        print(f"[llm] complete_text failed, using fallback: {exc}")
        return fallback
