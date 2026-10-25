"""Configuration: environment settings and the voice-command catalogue.

One gateway process talks to **one** Home Assistant instance. Point it at a
second HA by running a second container with its own ``.env``.

The catalogue has two layers:

* ``targets`` map a Bulgarian noun phrase (``лампите в хола``) to an entity or
  action. The built-in on/off verbs are combined with those phrases, so a
  target automatically understands ``включи лампите в хола``,
  ``пусни лампите в хола``, ``загаси лампите в хола`` and so on.
* ``commands`` are explicit custom commands for everything that is not a
  simple on/off target: queries (temperature), intent scripts, unusual
  services, or phrases that should override a generated target phrase.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Built-in verb synonyms. ``verbs.on`` / ``verbs.off`` in the YAML add to
# these lists; they never replace them, so "включи" and "изключи" keep working.
DEFAULT_ON_VERBS: tuple[str, ...] = (
    "включи",
    "включвай",
    "включвам",
    "пусни",
    "пускай",
    "пускам",
    "светни",
    "светвай",
    "запали",
    "пали",
    "стартирай",
    "активирай",
)
DEFAULT_OFF_VERBS: tuple[str, ...] = (
    "изключи",
    "изключвай",
    "изключвам",
    "спри",
    "спирай",
    "спирам",
    "загаси",
    "загасяй",
    "загасвай",
    "изгаси",
    "угаси",
    "угасвай",
    "прекрати",
)

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_SPACES = re.compile(r"\s+")


def _restore_direction_keys(data: Any) -> Any:
    """Undo PyYAML 1.1's habit of reading unquoted ``on:``/``off:`` as booleans."""
    if not isinstance(data, Mapping):
        return data
    restored = dict(data)
    for key, name in ((True, "on"), (False, "off")):
        if key in restored and name not in restored:
            restored[name] = restored.pop(key)
    return restored


def _clean_words(words: Iterable[str]) -> list[str]:
    """Lowercase, trim and de-duplicate a list of verbs or phrases."""
    cleaned: list[str] = []
    for item in words:
        word = _SPACES.sub(" ", str(item).strip().lower()).strip()
        if word and word not in cleaned:
            cleaned.append(word)
    return cleaned


def _phrase_key(phrase: str) -> str:
    return " ".join(phrase.lower().split())


def _merge_words(*groups: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for word in group:
            if word not in merged:
                merged.append(word)
    return merged


def _domain_of(entity_id: str) -> str:
    return entity_id.partition(".")[0]


def _action_for_entity(entity_id: str, kind: Literal["on", "off"]) -> ActionConfig:
    """Infer the HA service for an entity id.

    Scenes only expose ``scene.turn_on``. That makes ``off: scene.all_off``
    work as "apply the all-off scene" rather than an invalid ``turn_off``.
    """
    domain = _domain_of(entity_id)
    service = "scene.turn_on" if domain == "scene" else f"{domain}.turn_{kind}"
    return ActionConfig(service=service, target={"entity_id": entity_id})


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``BGV_``), optionally from ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="BGV_", env_file=".env", extra="ignore"
    )

    config_dir: Path = Path("config")
    models_dir: Path = Path("models")

    # Which catalogue inside config_dir to load. commands.yaml is the
    # gitignored per-deployment file; commands-example.yaml is a tracked
    # starting point to copy and adapt.
    commands_file: str = "commands.yaml"

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
        return self.config_dir / self.commands_file


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


class VerbConfig(BaseModel):
    """Extra on/off verb synonyms appended to the built-in defaults."""

    on: list[str] = Field(default_factory=list)
    off: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _direction_keys(cls, data: Any) -> Any:
        return _restore_direction_keys(data)

    @field_validator("on", "off")
    @classmethod
    def _normalise(cls, words: list[str]) -> list[str]:
        return _clean_words(words)


class ActionConfig(BaseModel):
    """Execute a Home Assistant service directly (self-contained mapping)."""

    service: str
    target: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None


class QueryConfig(BaseModel):
    """Read an entity state and render a Bulgarian sentence."""

    entity_id: str
    template: str = "В момента {name} е {value} {unit}."
    round: int | None = 1
    unit: str | None = None
    unit_singular: str | None = None


class TargetConfig(BaseModel):
    """A Bulgarian phrase mapped to an on/off target.

    ``on``/``off`` can be:

    * a bare entity id — the service is inferred from its domain
      (``switch.hall`` → ``switch.turn_on`` / ``switch.turn_off``);
    * a full action mapping (``service``, ``target``, ``data``, ``response``);
    * omitted when that direction is not available (a scene usually has no
      "off"; map a separate all-off scene under ``off`` instead).

    A top-level ``entity`` is the shorthand for both directions. For a scene
    entity shorthand only the on direction is generated, because re-applying
    the same scene for "off" would be wrong.
    """

    phrases: list[str] = Field(min_length=1)
    hotwords: list[str] = Field(default_factory=list)
    entity: str | None = None
    on: ActionConfig | str | None = None
    off: ActionConfig | str | None = None

    @model_validator(mode="before")
    @classmethod
    def _direction_keys(cls, data: Any) -> Any:
        return _restore_direction_keys(data)

    @field_validator("phrases")
    @classmethod
    def _normalise_phrases(cls, phrases: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in phrases:
            phrase = _SPACES.sub(" ", str(item).strip()).strip()
            key = phrase.lower()
            if key and key not in seen:
                seen.add(key)
                cleaned.append(phrase)
        if not cleaned:
            raise ValueError("target phrases must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _validate(self) -> "TargetConfig":
        if self.entity is not None and not _ENTITY_ID.match(self.entity):
            raise ValueError(
                f"target entity must be a full entity_id, got {self.entity!r}"
            )
        for kind in ("on", "off"):
            value = getattr(self, kind)
            if isinstance(value, str) and not _ENTITY_ID.match(value):
                raise ValueError(
                    f"target {kind} must be an entity_id or an action mapping, "
                    f"got {value!r}"
                )
        if not (self.entity or self.on or self.off):
            raise ValueError("target needs entity, on or off")
        return self

    def action_for(self, kind: Literal["on", "off"]) -> ActionConfig | None:
        """Return the action for one direction, or None when it is not mapped."""
        spec = getattr(self, kind)
        if isinstance(spec, str):
            return _action_for_entity(spec, kind)
        if spec is not None:
            return spec
        if self.entity is None:
            return None
        if _domain_of(self.entity) == "scene" and kind == "off":
            return None
        return _action_for_entity(self.entity, kind)


class CommandConfig(BaseModel):
    """One explicit custom voice command.

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
    """The loaded catalogue: targets, custom commands and matcher settings."""

    language: str = "bg"
    match: MatchSettings = Field(default_factory=MatchSettings)
    verbs: VerbConfig = Field(default_factory=VerbConfig)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    commands: dict[str, CommandConfig] = Field(default_factory=dict)

    @field_validator("targets", mode="before")
    @classmethod
    def _accept_target_shorthand(cls, value: Any) -> Any:
        """Allow ``лампите: switch.hall`` and default the phrase to the key."""
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("targets must be a mapping of names to targets")

        expanded: dict[str, Any] = {}
        for raw_name, spec in value.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("target names must not be empty")
            if isinstance(spec, str):
                spec = {"phrases": [name], "entity": spec}
            elif isinstance(spec, Mapping):
                spec = dict(spec)
                spec.setdefault("phrases", [name])
            else:
                raise ValueError(
                    f"target {name!r} must be an entity_id or a mapping"
                )
            expanded[name] = spec
        return expanded

    @model_validator(mode="after")
    def _expand_targets(self) -> "CommandsFile":
        """Turn target names + default verbs into ordinary command entries."""
        # Explicit commands win over an identical generated phrase. This is
        # useful for a custom reply or a special action for one exact wording.
        custom_phrases: dict[str, str] = {}
        for intent, command in self.commands.items():
            for phrase in command.phrases:
                key = _phrase_key(phrase)
                if key in custom_phrases and custom_phrases[key] != intent:
                    raise ValueError(
                        f"phrase {phrase!r} is used by both "
                        f"{custom_phrases[key]} and {intent}"
                    )
                custom_phrases[key] = intent

        generated: dict[str, CommandConfig] = {}
        generated_phrases: dict[str, str] = {}
        on_verbs = _merge_words(DEFAULT_ON_VERBS, self.verbs.on)
        off_verbs = _merge_words(DEFAULT_OFF_VERBS, self.verbs.off)

        for name, target in self.targets.items():
            for kind, verbs in (("on", on_verbs), ("off", off_verbs)):
                action = target.action_for(kind)
                if action is None:
                    continue
                intent = f"{name}:{kind}"
                if intent in self.commands or intent in generated:
                    raise ValueError(
                        f"target {name!r} generates intent {intent!r}, "
                        "which already exists"
                    )

                phrases: list[str] = []
                for target_phrase in target.phrases:
                    for verb in verbs:
                        phrase = f"{verb} {target_phrase}".strip()
                        key = _phrase_key(phrase)
                        if key in custom_phrases:
                            continue
                        owner = generated_phrases.get(key)
                        if owner is not None and owner != intent:
                            raise ValueError(
                                f"phrase {phrase!r} is generated by both "
                                f"{owner} and {intent}"
                            )
                        if owner == intent:
                            continue
                        generated_phrases[key] = intent
                        phrases.append(phrase)

                if not phrases:
                    continue

                response = action.response
                if not response:
                    verb_label = "Включих" if kind == "on" else "Изключих"
                    response = f"{verb_label} {target.phrases[0]}."
                generated[intent] = CommandConfig(
                    speak=False,
                    phrases=phrases,
                    hotwords=target.hotwords,
                    action=action.model_copy(update={"response": response}),
                )

        if not self.commands and not generated:
            raise ValueError("configure at least one target or custom command")
        self.commands = {**self.commands, **generated}
        return self

    def extra_verbs(self) -> dict[str, tuple[str, ...]]:
        """Configured verb synonyms, for the matcher's opposite-verb guard."""
        return {"on": tuple(self.verbs.on), "off": tuple(self.verbs.off)}

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
