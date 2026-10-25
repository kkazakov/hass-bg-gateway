"""Fuzzy phrase matching: ASR transcript -> configured intent.

The transcript is rarely perfect (`ломпата` for `лампата`, `светли` for
`светни`). Matching against the configured phrases with RapidFuzz is cheap
(~0.1 ms for a few dozen phrases) and absorbs those single-phoneme slips.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .config import DEFAULT_OFF_VERBS, DEFAULT_ON_VERBS, CommandConfig

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Bulgarian verbs that mean opposite things. Fuzzy matching must never turn
# "включи" (on) into "изключи" (off) just because they differ by two letters.
_VERB_CLASSES: dict[str, frozenset[str]] = {
    "on": frozenset(DEFAULT_ON_VERBS),
    "off": frozenset(DEFAULT_OFF_VERBS),
    "open": frozenset({"отвори", "открий", "вдигни"}),
    "close": frozenset({"затвори", "закрий", "смъкни", "спусни", "свали"}),
    "raise": frozenset({"увеличи", "качи", "усили", "вдигни"}),
    "lower": frozenset({"намали", "сниши", "свали"}),
}


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
        extra_verbs: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        if not commands:
            raise ValueError("no commands configured")
        self._threshold = threshold
        self._margin = margin
        self._verb_classes: dict[str, set[str]] = {
            name: set(words) for name, words in _VERB_CLASSES.items()
        }
        for name, words in (extra_verbs or {}).items():
            self._verb_classes.setdefault(name, set()).update(
                word.strip().lower() for word in words if word.strip()
            )
        self._intents: list[str] = []
        self._choices: list[str] = []
        self._original: dict[str, str] = {}
        self._exact: dict[str, tuple[str, str]] = {}
        for intent, command in commands.items():
            for phrase in command.phrases:
                key = normalise(phrase)
                if not key:
                    continue
                self._intents.append(intent)
                self._choices.append(key)
                self._original[key] = phrase
                self._exact.setdefault(key, (intent, phrase))

    def match(self, transcript: str) -> Match:
        query = normalise(transcript)
        if not query:
            return Match(None, None, 0.0, 0.0, transcript, "empty transcript")

        # An exact configured phrase is unambiguous by definition; near-identical
        # variants ("навън" / "отвън") would otherwise trip the margin rule.
        exact = self._exact.get(query)
        if exact is not None:
            intent, phrase = exact
            return Match(intent, phrase, 100.0, 0.0, transcript)

        results = process.extract(
            query, self._choices, scorer=fuzz.WRatio, limit=2
        )
        best_choice, best_score, best_index = results[0]
        if len(results) > 1:
            _, second_score, second_index = results[1]
        else:
            second_score, second_index = 0.0, None

        if best_score < self._threshold:
            return Match(
                None, None, float(best_score), float(second_score), transcript,
                f"below threshold ({best_score:.1f} < {self._threshold})",
            )

        # Guard against opposite verbs ("включи" vs "изключи"): a wrong action
        # is worse than asking to repeat.
        query_classes = self._classes_of(query.split())
        phrase_classes = self._classes_of(best_choice.split())
        if query_classes and phrase_classes and not (query_classes & phrase_classes):
            return Match(
                None, None, float(best_score), float(second_score), transcript,
                "opposite action ("
                + ",".join(sorted(query_classes))
                + " vs "
                + ",".join(sorted(phrase_classes))
                + ")",
            )
        same_intent = (
            second_index is not None
            and self._intents[second_index] == self._intents[best_index]
        )
        if (
            second_score
            and (best_score - second_score) < self._margin
            and not same_intent
        ):
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

    def _classes_of(self, tokens: list[str]) -> set[str]:
        found: set[str] = set()
        for token in tokens:
            for name, words in self._verb_classes.items():
                if token in words:
                    found.add(name)
        return found

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
