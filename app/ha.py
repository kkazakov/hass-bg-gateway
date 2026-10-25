"""Home Assistant client.

The gateway calls intents **by name** (``/api/intent/handle``) rather than
sending free text to the conversation agent. That is deliberate:

* the conversation response does not include the matched intent name, so the
  gateway could not tell an action from a query (and therefore whether to
  speak the reply);
* matching happens in the gateway against ``commands.yaml``, which is the
  surface you tune when ASR mishears a phrase;
* the device mapping stays in Home Assistant's ``intent_script``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import InstanceConfig

log = logging.getLogger(__name__)


class HaError(RuntimeError):
    """Raised when Home Assistant cannot be reached or rejects the intent."""


class HaClient:
    def __init__(self, name: str, config: InstanceConfig, token: str) -> None:
        self.name = name
        self._config = config
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            verify=config.verify_tls,
            timeout=httpx.Timeout(config.timeout_s, connect=5.0),
        )

    async def handle_intent(self, intent: str) -> dict[str, Any]:
        """Run an ``intent_script`` entry and return its speech text."""
        try:
            response = await self._client.post(
                "/api/intent/handle",
                json={"name": intent, "language": "bg"},
            )
        except httpx.HTTPError as exc:
            raise HaError(f"{self.name}: request failed: {exc}") from exc

        if response.status_code == 401:
            raise HaError(f"{self.name}: invalid or expired token")
        if response.status_code >= 400:
            raise HaError(
                f"{self.name}: HTTP {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        speech = payload.get("speech") or {}
        text = (speech.get("plain") or speech.get("ssml") or "").strip()
        if not text:
            log.warning("%s: intent %s returned no speech text", self.name, intent)
        return {
            "text": text,
            "response_type": payload.get("response_type"),
            "data": payload.get("data"),
        }

    async def health(self) -> bool:
        try:
            response = await self._client.get("/api/")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
