from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from app.config import CommandsFile
from app.matcher import Matcher


def build(raw: dict) -> tuple[CommandsFile, Matcher]:
    commands = CommandsFile.model_validate(raw)
    return commands, Matcher(
        commands.commands,
        threshold=commands.match.threshold,
        margin=commands.match.margin,
        extra_verbs=commands.extra_verbs(),
    )


CATALOGUE = {
    "targets": {
        "лампите в хола": {
            "phrases": ["лампите в хола", "осветлението в хола"],
            "entity": "switch.hall_lamp",
        },
        "осветлението": {
            "phrases": ["осветлението", "всички лампи"],
            "on": "scene.all_lights_on",
            "off": "scene.all_lights_off",
        },
    }
}


@pytest.mark.parametrize(
    ("text", "intent", "service"),
    [
        ("включи лампите в хола", "лампите в хола:on", "switch.turn_on"),
        ("пусни лампите в хола", "лампите в хола:on", "switch.turn_on"),
        ("светни лампите в хола", "лампите в хола:on", "switch.turn_on"),
        ("изключи лампите в хола", "лампите в хола:off", "switch.turn_off"),
        ("спри лампите в хола", "лампите в хола:off", "switch.turn_off"),
        ("загаси лампите в хола", "лампите в хола:off", "switch.turn_off"),
        ("угаси лампите в хола", "лампите в хола:off", "switch.turn_off"),
    ],
)
def test_default_verbs_compose_with_entity_target(
    text: str, intent: str, service: str
) -> None:
    commands, matcher = build(CATALOGUE)
    match = matcher.match(text)
    assert match.ok
    assert match.intent == intent
    action = commands.commands[intent].action
    assert action is not None
    assert action.service == service
    assert action.target == {"entity_id": "switch.hall_lamp"}


def test_target_aliases_all_accept_default_verbs() -> None:
    _, matcher = build(CATALOGUE)
    assert matcher.match("изключи осветлението в хола").intent == "лампите в хола:off"
    assert matcher.match("пусни осветлението в хола").intent == "лампите в хола:on"


def test_on_and_off_scene_actions_stay_distinct() -> None:
    commands, matcher = build(CATALOGUE)

    on = matcher.match("включи всички лампи")
    off = matcher.match("загаси всички лампи")
    assert on.intent == "осветлението:on"
    assert off.intent == "осветлението:off"

    on_action = commands.commands[on.intent].action
    off_action = commands.commands[off.intent].action
    assert on_action is not None and off_action is not None
    assert on_action.service == "scene.turn_on"
    assert on_action.target == {"entity_id": "scene.all_lights_on"}
    # Scenes have no turn_off; the off scene is applied with scene.turn_on.
    assert off_action.service == "scene.turn_on"
    assert off_action.target == {"entity_id": "scene.all_lights_off"}


def test_generated_responses_name_the_target() -> None:
    commands, _ = build(CATALOGUE)
    assert (
        commands.commands["лампите в хола:on"].action.response
        == "Включих лампите в хола."
    )
    assert (
        commands.commands["лампите в хола:off"].action.response
        == "Изключих лампите в хола."
    )


def test_full_action_keeps_custom_response_and_data() -> None:
    commands, matcher = build(
        {
            "targets": {
                "лампите": {
                    "entity": "light.hall",
                    "on": {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.hall"},
                        "data": {"brightness": 200},
                        "response": "Светнах лампите.",
                    },
                }
            }
        }
    )
    match = matcher.match("пусни лампите")
    assert match.intent == "лампите:on"
    action = commands.commands[match.intent].action
    assert action is not None
    assert action.data == {"brightness": 200}
    assert action.response == "Светнах лампите."


def test_compact_shorthand_uses_the_key_as_the_phrase() -> None:
    commands, matcher = build({"targets": {"лампите в хола": "switch.hall"}})
    match = matcher.match("изключи лампите в хола")
    assert match.intent == "лампите в хола:off"
    assert commands.commands[match.intent].action.target == {
        "entity_id": "switch.hall"
    }


def test_scene_entity_shorthand_is_on_only() -> None:
    commands, matcher = build(
        {
            "targets": {
                "нощното осветление": {
                    "phrases": ["нощно осветление"],
                    "entity": "scene.night_light",
                }
            }
        }
    )
    assert "нощното осветление:on" in commands.commands
    assert "нощното осветление:off" not in commands.commands
    assert matcher.match("пусни нощно осветление").ok
    assert not matcher.match("изключи нощно осветление").ok


def test_extra_verbs_are_added_to_the_defaults() -> None:
    # PyYAML 1.1 reads unquoted on/off mapping keys as booleans; the loader
    # must restore them so this natural YAML works.
    raw = yaml.safe_load(
        """
        verbs:
          on: [активирай]
          off: [прекрати]
        targets:
          лампите:
            phrases: ["лампите"]
            entity: switch.hall
        """
    )
    commands, matcher = build(raw)

    assert matcher.match("активирай лампите").intent == "лампите:on"
    assert matcher.match("прекрати лампите").intent == "лампите:off"
    # Defaults still work alongside the extras.
    assert matcher.match("включи лампите").intent == "лампите:on"
    assert matcher.match("изключи лампите").intent == "лампите:off"
    assert commands.extra_verbs() == {"on": ("активирай",), "off": ("прекрати",)}


def test_custom_command_wins_over_an_identical_generated_phrase() -> None:
    commands, matcher = build(
        {
            "targets": {"лампите": {"entity": "switch.hall"}},
            "commands": {
                "HallLampOff": {
                    "phrases": ["изключи лампите"],
                    "action": {
                        "service": "light.turn_off",
                        "target": {"entity_id": "light.hall"},
                        "response": "Изключих лампите в хола.",
                    },
                }
            },
        }
    )
    assert "изключи лампите" not in commands.commands["лампите:off"].phrases
    match = matcher.match("изключи лампите")
    assert match.intent == "HallLampOff"
    assert commands.commands[match.intent].action.target == {
        "entity_id": "light.hall"
    }
    # The rest of the generated verb forms still reach the target.
    assert matcher.match("спри лампите").intent == "лампите:off"


def test_duplicate_generated_phrases_across_targets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="generated by both"):
        CommandsFile.model_validate(
            {
                "targets": {
                    "A": {"phrases": ["лампите"], "entity": "switch.a"},
                    "B": {"phrases": ["лампите"], "entity": "switch.b"},
                }
            }
        )


def test_target_needs_an_action() -> None:
    with pytest.raises(ValidationError, match="target needs entity, on or off"):
        CommandsFile.model_validate({"targets": {"лампите": {"phrases": ["лампите"]}}})


def test_empty_catalogue_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one target or custom command"):
        CommandsFile.model_validate({})


def test_invalid_entity_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="full entity_id"):
        CommandsFile.model_validate(
            {"targets": {"лампите": {"phrases": ["лампите"], "entity": "not-an-id"}}}
        )
