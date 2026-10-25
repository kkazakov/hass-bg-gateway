"""Runtime object: everything the API needs, built once at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

import numpy as np

from .asr import AsrEngine
from .config import CommandConfig, CommandsFile, Settings, load_commands
from .ha import HaClient
from .matcher import Matcher
from .tts import AudioClip, TtsService

log = logging.getLogger(__name__)


class AsrLike(Protocol):
    def transcribe(self, samples: np.ndarray, *, chunk: int = 1600) -> str: ...


class MatcherLike(Protocol):
    def match(self, transcript: str): ...

    def describe(self) -> list[dict]: ...


class TtsLike(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def provider(self) -> str: ...

    @property
    def voice(self) -> str: ...

    @property
    def unavailable_reason(self) -> str | None: ...

    def synthesize(self, text: str) -> AudioClip | None: ...

    def close(self) -> None: ...


class HaLike(Protocol):
    async def handle_intent(self, intent: str) -> dict: ...

    async def call_service(
        self,
        service: str,
        target: dict | None = None,
        data: dict | None = None,
    ) -> dict: ...

    async def get_state(self, entity_id: str) -> dict: ...

    async def health(self) -> bool: ...

    async def aclose(self) -> None: ...


@dataclass
class Runtime:
    settings: Settings
    commands: CommandsFile
    matcher: MatcherLike
    asr: AsrLike
    tts: TtsLike
    ha: HaLike
    _closed: bool = field(default=False, repr=False)

    def command(self, intent: str) -> CommandConfig:
        return self.commands.commands[intent]

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.ha.aclose()
        self.tts.close()


async def build_runtime(settings: Settings) -> Runtime:
    """Load configuration and warm up models. Raises on unusable config."""
    token = settings.ha_token.strip()
    if not token:
        raise ValueError(
            "BGV_HA_TOKEN is empty; create a long-lived access token in "
            "Home Assistant and put it in .env"
        )
    if not settings.ha_url.strip():
        raise ValueError("BGV_HA_URL is empty")

    commands = load_commands(settings)
    matcher = Matcher(
        commands.commands,
        threshold=commands.match.threshold,
        margin=commands.match.margin,
    )

    ha = HaClient(
        settings.ha_url,
        token,
        verify_tls=settings.ha_verify_tls,
        timeout_s=settings.ha_timeout_s,
    )
    try:
        asr = AsrEngine(
            settings.models_dir,
            threads=settings.asr_threads,
            hotwords=commands.hotwords(),
            hotwords_score=settings.hotwords_score,
        )
        tts = TtsService(
            settings.models_dir,
            provider=settings.tts_provider,
            language=settings.tts_language,
            enabled=settings.tts_enabled,
            timeout_s=settings.tts_timeout_s,
        )
    except Exception:
        await ha.aclose()
        raise

    log.info(
        "runtime ready: commands=%d ha=%s tts=%s",
        len(commands.commands),
        settings.ha_url,
        f"{tts.provider}:{tts.voice}" if tts.ready else "off",
    )
    return Runtime(
        settings=settings,
        commands=commands,
        matcher=matcher,
        asr=asr,
        tts=tts,
        ha=ha,
    )


RuntimeBuilder = Callable[[Settings], Awaitable[Runtime]]
