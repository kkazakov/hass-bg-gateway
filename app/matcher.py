"""Fuzzy phrase matching: ASR transcript -> configured intent.

The transcript is rarely perfect (`ломпата` for `лампата`, `светли` for
`светни`). Matching against the configured phrases with RapidFuzz is cheap
(~0.1 ms for a few dozen phrases) and absorbs those single-phoneme slips.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .config import CommandConfig

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, normalise Unicode."""
    text = unicodedata.normalize("NFC", text).lower().strip()
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class Advice:
    """A matched command."""

    intent: str
    phrase: str
    score: float
    runner_up: float


@dataclass(frozen=True)
class Match:
    """The outcome of matching a transcript against the command table."""

    intent: str | None
    phrase: str | None
    score: float
    runner_up: float
    transcript: str
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.intent is not None


class Matcher:
    def __init__(
        self,
        commands: dict[str, CommandConfig],
        *,
        threshold: float = 88.0,
        margin: float = 5.0,
    ) -> None:
        if not commands:
            raise ValueError("no commands configured")
        self._threshold = threshold
        self._margin = margin
        self._intents: list[str] = []
        self._choices: list[str] = []
        self._original: dict[str, str] = {}
        for intent, command in commands.items():
            for phrase in command.phrases:
                key = normalise(phrase)
                if not key:
                    continue
                self._intents.append(intent)
                self._choices.append(key)
                self._original[key] = phrase

    def match(self, transcript: str) -> Match:
        query = normalise(transcript)
        if not query:
            return Match(None, None, 0.0, 0.0, transcript, "empty transcript")

        results = process.extract(
            query, self._choices, scorer=fuzz.WRatio, limit=2
        )
        best_choice, best_score, best_index = results[0]
        second_score = results[1][1] if len(results) > 1 else 0.0

        if best_score < self._threshold:
            return Match(
                None, None, float(best_score), float(second_score), transcript,
                f"below threshold ({best_score:.1f} < {self._threshold})",
            )
        if second_score and (best_score - second_score) < self._margin:
            return Match(
                None, None, float(best_score), float(second_score), transcript,
                f"ambiguous (margin {best_score - second_score:.1f} < {self._margin})",
            )

        return Match(
            intent=self._intents[best_index],
            phrase=self._original[best_choice],
            score=float(best_score),
            runner_up=float(second_score),
            transcript=transcript,
        )

    @property
    def intents(self) -> list[str]:
        return sorted(set(self._intents))

    def describe(self) -> list[dict]:
        """Return the command table for ``GET /v1/commands``."""
        grouped: dict[str, list[str]] = {}
        for intent, choice in zip(self._intents, self._choices, strict=True):
            grouped.setdefault(intent, []).append(self._original[choice])
        return [
            {"intent": intent, "phrases": phrases}
            for intent, phrases in grouped.items()
        ]
