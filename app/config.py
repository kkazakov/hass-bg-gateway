"""Configuration: environment settings and the voice-command catalogue.

One gateway process talks to **one** Home Assistant instance. Point it at a
second HA by running a second container with its own ``.env``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``BGV_``), optionally from ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="BGV_", env_file=".env", extra="ignore"
    )

    config_dir: Path = Path("config")
    models_dir: Path = Path("models")

    # --- Home Assistant (exactly one per gateway) -------------------------
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    ha_verify_tls: bool = True
    ha_timeout_s: float = 10.0

    # --- API auth ----------------------------------------------------------
    # Empty string disables auth. When set, every /v1 endpoint requires the
    # X-API-Key header. Useful when the gateway is exposed through HTTPS.
    api_key: str = ""

    # --- ASR ---------------------------------------------------------------
    asr_threads: int = 4
    hotwords_score: float = 3.0
    lead_silence_s: float = 0.2
    tail_silence_s: float = 1.0

    # --- TTS ---------------------------------------------------------------
    tts_enabled: bool = True
    tts_provider: str = "gtts"  # gtts (female, cloud) | piper (male, local)
    tts_language: str = "bg"
    tts_timeout_s: float = 5.0
    request_timeout_s: float = 15.0

    log_level: str = "INFO"

    @property
    def commands_path(self) -> Path:
        return self.config_dir / "commands.yaml"


class MatchSettings(BaseModel):
    """Thresholds for fuzzy phrase matching (RapidFuzz WRatio, 0-100)."""

    threshold: float = 88.0
    margin: float = 5.0

    @model_validator(mode="after")
    def _validate(self) -> "MatchSettings":
        if not 0 <= self.threshold <= 100:
            raise ValueError("match.threshold must be between 0 and 100")
        if self.margin < 0:
            raise ValueError("match.margin must be >= 0")
        return self


class ActionConfig(BaseModel):
    """Execute a Home Assistant service directly (self-contained mapping)."""

    service: str
    target: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    response: str = "Готово."


class QueryConfig(BaseModel):
    """Read an entity state and render a Bulgarian sentence."""

    entity_id: str
    template: str = "В момента {name} е {value} {unit}."
    round: int | None = 1
    unit: str | None = None
    unit_singular: str | None = None


class CommandConfig(BaseModel):
    """One voice command.

    Mapping is either:
    * ``action`` — call an HA service directly,
    * ``query``  — read an entity and speak the rendered answer,
    * neither    — call an HA ``intent_script`` by the command name.
    """

    speak: bool = False
    phrases: list[str] = Field(min_length=1)
    hotwords: list[str] = Field(default_factory=list)
    action: ActionConfig | None = None
    query: QueryConfig | None = None

    @model_validator(mode="after")
    def _validate(self) -> "CommandConfig":
        if self.action and self.query:
            raise ValueError("a command cannot define both action and query")
        if self.query and not self.speak:
            raise ValueError("query commands must set speak: true")
        return self


class CommandsFile(BaseModel):
    language: str = "bg"
    match: MatchSettings = Field(default_factory=MatchSettings)
    commands: dict[str, CommandConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> "CommandsFile":
        seen: dict[str, str] = {}
        for intent, command in self.commands.items():
            for phrase in command.phrases:
                key = " ".join(phrase.lower().split())
                if key in seen and seen[key] != intent:
                    raise ValueError(
                        f"phrase {phrase!r} is used by both {seen[key]} and {intent}"
                    )
                seen[key] = intent
        return self

    def hotwords(self) -> list[str]:
        words: list[str] = []
        for command in self.commands.values():
            for word in command.hotwords:
                word = word.strip()
                if word and word not in words:
                    words.append(word)
        return words


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"configuration file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def load_commands(settings: Settings) -> CommandsFile:
    return CommandsFile.model_validate(_load_yaml(settings.commands_path))
