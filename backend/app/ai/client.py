"""Thin wrapper around the Anthropic SDK: client lifecycle, refusal fallbacks and error mapping.

* The API key is read from the backend environment only (``ANTHROPIC_API_KEY``); it never reaches
  the browser.
* For models that support it, the server-side ``fallbacks: "default"`` option is sent, so a request
  declined by a safety classifier is retried on a suitable fallback model inside the same call.
* SDK errors are mapped to the platform's error types with actionable messages.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.core.config import get_settings
from app.core.errors import AppError, RateLimited, ServiceUnavailable

logger = logging.getLogger(__name__)

SERVER_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5",
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.AsyncAnthropic | None = None


class AIUnavailable(ServiceUnavailable):
    code = "ai_unavailable"


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is not None:
        return _client
    key = get_settings().anthropic_api_key
    if not key:
        raise AIUnavailable("AI features need ANTHROPIC_API_KEY to be set on the backend server.")
    _client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=120.0)
    return _client


def ai_configured() -> bool:
    return _client is not None or bool(get_settings().anthropic_api_key)


def set_client(client: Any) -> None:
    """Test hook: inject a fake client."""
    global _client
    _client = client


async def create_message(**kwargs: Any) -> Any:
    model = kwargs["model"]
    if model in SERVER_FALLBACK_MODELS:
        kwargs.setdefault("betas", [FALLBACK_BETA])
        kwargs.setdefault("fallbacks", "default")
    client = get_client()
    try:
        return await client.beta.messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("The Claude API rejected the configured API key.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIUnavailable("The Claude API key lacks permission for this model or feature.") from exc
    except anthropic.NotFoundError as exc:
        raise AppError(f"Model '{model}' was not found. Choose another model in AI settings.") from exc
    except anthropic.RateLimitError as exc:
        raise RateLimited("Claude API rate limit reached - please retry shortly.") from exc
    except anthropic.BadRequestError as exc:
        logger.warning("Claude request rejected: %s (request id %s)", exc.message, getattr(exc, "request_id", None))
        raise AppError(f"The Claude API rejected the request: {exc.message}") from exc
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        raise ServiceUnavailable("Could not reach the Claude API - please retry.") from exc
    except anthropic.APIStatusError as exc:
        logger.warning("Claude API error %s", exc.status_code)
        raise ServiceUnavailable(f"The Claude API returned an error ({exc.status_code}).") from exc


def block_type(block: Any) -> str:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", "")


def text_of(content: list[Any]) -> str:
    parts = []
    for b in content:
        if block_type(b) == "text":
            parts.append(b["text"] if isinstance(b, dict) else b.text)
    return "\n".join(p for p in parts if p).strip()


def echo_content(content: list[Any]) -> list[Any]:
    """Assistant content to send back in the tool loop, unchanged except for the documented fallback rule:
    blocks of a declined partial that precede the last ``fallback`` block (thinking, tool_use) are omitted,
    while the ``fallback`` block itself stays where it was."""
    idx = max((i for i, b in enumerate(content) if block_type(b) == "fallback"), default=-1)
    if idx < 0:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(content) if i >= idx or block_type(b) not in drop]


def usage_dict(resp: Any) -> dict[str, Any]:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: getattr(u, k, None) for k in keys if getattr(u, k, None) is not None}
