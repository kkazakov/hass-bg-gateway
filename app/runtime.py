"""Runtime object: everything the API needs, built once at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

import numpy as np

from .asr import AsrEngine
from .config import (
    CommandConfig,
    CommandsFile,
    InstancesFile,
    Settings,
    load_commands,
    load_instances,
)
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
    instances: InstancesFile
    matcher: MatcherLike
    asr: AsrLike
    tts: TtsLike
    ha: dict[str, HaLike]
    default_instance: str
    _closed: bool = field(default=False, repr=False)

    def command(self, intent: str) -> CommandConfig:
        return self.commands.commands[intent]

    def client(self, instance: str | None) -> tuple[str, HaLike]:
        name = self.instances.resolve_name(instance)
        return name, self.ha[name]

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in self.ha.values():
            await client.aclose()
        self.tts.close()


async def build_runtime(settings: Settings) -> Runtime:
    """Load configuration and warm up models. Raises on unusable config."""
    commands = load_commands(settings)
    instances = load_instances(settings)

    matcher = Matcher(
        commands.commands,
        threshold=commands.match.threshold,
        margin=commands.match.margin,
    )
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

    clients: dict[str, HaClient] = {}
    try:
        for name, instance in instances.instances.items():
            clients[name] = HaClient(name, instance, instance.resolve_token())
    except Exception:
        for client in clients.values():
            await client.aclose()
        raise

    default = instances.resolve_name(None)
    log.info(
        "runtime ready: commands=%d instances=%s default=%s tts=%s",
        len(commands.commands),
        ",".join(clients),
        default,
        f"{tts.provider}:{tts.voice}" if tts.ready else "off",
    )
    return Runtime(
        settings=settings,
        commands=commands,
        instances=instances,
        matcher=matcher,
        asr=asr,
        tts=tts,
        ha=clients,
        default_instance=default,
    )


RuntimeBuilder = Callable[[Settings], Awaitable[Runtime]]
