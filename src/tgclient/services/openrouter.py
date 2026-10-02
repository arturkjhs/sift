"""Minimal OpenRouter chat-completions client.

Every request is pinned to zero-data-retention endpoints (`provider.zdr`) and to providers that
don't collect data (`provider.data_collection = "deny"`). There is no way to turn this off.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
KEY_URL = "https://openrouter.ai/api/v1/key"
ZDR_ROUTING: dict[str, Any] = {"zdr": True, "data_collection": "deny"}


class OpenRouterError(Exception):
    pass


def mask_key(key: str) -> str:
    """For display: "sk-or-v1-…3f2a". Never show or log the whole key."""
    key = key.strip()
    if len(key) <= 12:
        return "\u2026" + key[-2:]
    return f"{key[:9]}\u2026{key[-4:]}"


class OpenRouter:
    def __init__(
        self,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "tgclient",
            },
        )

    async def complete(
        self, model: str, messages: list[dict[str, Any]], temperature: float = 0.2
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "provider": ZDR_ROUTING,
        }
        try:
            response = await self._http.post(API_URL, json=payload)
        except httpx.HTTPError as e:
            raise OpenRouterError(f"Network error: {e}") from e
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if response.status_code >= 400 or error:
            raise OpenRouterError(_error_text(response.status_code, error, model))
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise OpenRouterError("Unexpected response from OpenRouter") from e
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return (content or "").strip()

    async def transcribe(self, model: str, audio: bytes, audio_format: str, prompt: str) -> str:
        return await self.complete(model, [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(audio).decode("ascii"),
                    "format": audio_format,
                }},
            ],
        }], temperature=0.0)

    async def key_info(self) -> dict[str, Any]:
        """Check the key without spending anything: label, limits, usage. Raises on a bad key."""
        try:
            response = await self._http.get(KEY_URL)
        except httpx.HTTPError as e:
            raise OpenRouterError(f"Network error: {e}") from e
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or not isinstance(body, dict) or "data" not in body:
            error = body.get("error") if isinstance(body, dict) else None
            raise OpenRouterError(_error_text(response.status_code, error, ""))
        return body["data"] or {}

    async def aclose(self) -> None:
        await self._http.aclose()


def _error_text(status: int, error: Any, model: str) -> str:
    message = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "")
    elif error:
        message = str(error)
    if status == 401:
        return "OpenRouter rejected the API key"
    if status == 402:
        return "Not enough OpenRouter credits"
    if status == 404:
        # Usually: no endpoint of this model satisfies the zero-data-retention policy.
        return f"No zero-data-retention provider serves {model}" + (
            f" ({message})" if message else "")
    if status == 429:
        return "OpenRouter rate limit, try again later"
    return f"OpenRouter error {status}" + (f": {message}" if message else "")
