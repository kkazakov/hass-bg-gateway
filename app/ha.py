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

log = logging.getLogger(__name__)


class HaError(RuntimeError):
    """Raised when Home Assistant cannot be reached or rejects the intent."""


class HaClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        verify_tls: bool = True,
        timeout_s: float = 10.0,
    ) -> None:
        self.name = "homeassistant"
        self.url = url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self.url,
            headers={"Authorization": f"Bearer {token}"},
            verify=verify_tls,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
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

    async def call_service(
        self,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call ``domain.service`` (e.g. ``light.turn_off``) directly."""
        if "." not in service:
            raise HaError(f"invalid service {service!r}, expected domain.service")
        # The REST API expects target fields (entity_id/area_id/...) flattened
        # into the service data. A nested {"target": ...} is rejected with 400
        # on HA 2026.10.
        payload: dict[str, Any] = {}
        if target:
            payload.update(target)
        if data:
            payload.update(data)
        try:
            response = await self._client.post(
                f"/api/services/{service.replace('.', '/', 1)}", json=payload
            )
        except httpx.HTTPError as exc:
            raise HaError(f"{self.name}: request failed: {exc}") from exc

        if response.status_code == 401:
            raise HaError(f"{self.name}: invalid or expired token")
        if response.status_code >= 400:
            raise HaError(
                f"{self.name}: HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            changed = response.json()
        except ValueError:
            changed = []
        return {"changed": changed if isinstance(changed, list) else []}

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        """Return the current state object for ``entity_id``."""
        try:
            response = await self._client.get(f"/api/states/{entity_id}")
        except httpx.HTTPError as exc:
            raise HaError(f"{self.name}: request failed: {exc}") from exc

        if response.status_code == 404:
            raise HaError(f"{self.name}: entity {entity_id} not found")
        if response.status_code == 401:
            raise HaError(f"{self.name}: invalid or expired token")
        if response.status_code >= 400:
            raise HaError(
                f"{self.name}: HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/api/")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
