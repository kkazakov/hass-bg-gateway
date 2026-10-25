from __future__ import annotations

import pytest

from app.config import CommandConfig
from app.matcher import Matcher, normalise


COMMANDS = {
    "HallLightOff": CommandConfig(
        phrases=[
            "изключи лампата в хола",
            "изгаси лампата в хола",
        ]
    ),
    "CorridorLightOn": CommandConfig(
        phrases=[
            "светни лампата в коридора",
            "включи лампата в коридора",
        ]
    ),
}


def make_matcher(**kwargs) -> Matcher:
    return Matcher(COMMANDS, threshold=88, margin=5, **kwargs)


def test_normalise_strips_punctuation_and_case() -> None:
    assert normalise("  Изключи, ЛАМПАТА в хола!  ") == "изключи лампата в хола"


def test_exact_phrase_matches() -> None:
    match = make_matcher().match("изключи лампата в хола")
    assert match.ok
    assert match.intent == "HallLightOff"
    assert match.score == pytest.approx(100.0)


def test_asr_slip_still_matches() -> None:
    # The model is known to hear "ломпата" for "лампата" (о/а confusion).
    match = make_matcher().match("изключи ломпата в хола")
    assert match.ok
    assert match.intent == "HallLightOff"
    assert 85 <= match.score < 100


def test_second_phrase_maps_to_same_intent() -> None:
    match = make_matcher().match("изгаси лампата в хола")
    assert match.ok
    assert match.intent == "HallLightOff"


def test_unrelated_text_is_rejected() -> None:
    match = make_matcher().match("пусни музика в кухнята")
    assert not match.ok
    assert match.reason is not None


def test_ambiguous_input_is_rejected() -> None:
    matcher = Matcher(
        {
            "A": CommandConfig(phrases=["светни лампата в коридора"]),
            "B": CommandConfig(phrases=["светни лампата в хола"]),
        },
        threshold=80,
        margin=5,
    )
    match = matcher.match("светни лампата")
    assert not match.ok
    assert "ambiguous" in (match.reason or "")


def test_empty_transcript_is_rejected() -> None:
    match = make_matcher().match("   ")
    assert not match.ok
    assert match.reason == "empty transcript"


def test_describe_groups_phrases_by_intent() -> None:
    described = {entry["intent"]: entry["phrases"] for entry in make_matcher().describe()}
    assert described["HallLightOff"] == [
        "изключи лампата в хола",
        "изгаси лампата в хола",
    ]
    assert described["CorridorLightOn"] == [
        "светни лампата в коридора",
        "включи лампата в коридора",
    ]
