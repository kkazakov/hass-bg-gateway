from __future__ import annotations

from app.config import CommandsFile, Settings, load_commands
from app.matcher import Matcher


def load_example() -> tuple[CommandsFile, Matcher]:
    commands = load_commands(
        Settings(_env_file=None, commands_file="commands-example.yaml")
    )
    matcher = Matcher(
        commands.commands,
        threshold=commands.match.threshold,
        margin=commands.match.margin,
        extra_verbs=commands.extra_verbs(),
    )
    return commands, matcher


def test_example_catalogue_loads() -> None:
    commands, _ = load_example()
    assert commands.commands
    assert commands.targets


def test_example_catalogue_expands_scenes_and_queries() -> None:
    commands, matcher = load_example()

    on = matcher.match("включи лампата в хола")
    assert on.intent == "лампата в хола:on"
    assert commands.commands[on.intent].action.service == "light.turn_on"

    off = matcher.match("загаси лампите в хола")
    assert off.ok
    assert commands.commands[off.intent].action.service == "light.turn_off"

    scene_on = matcher.match("пусни всички светлини")
    assert scene_on.intent == "всички светлини:on"
    assert commands.commands[scene_on.intent].action.target == {
        "entity_id": "scene.all_lights_on"
    }

    scene_off = matcher.match("изключи всички лампи")
    assert scene_off.intent == "всички светлини:off"
    assert commands.commands[scene_off.intent].action.target == {
        "entity_id": "scene.all_lights_off"
    }

    assert matcher.match("нощно осветление").intent == "NightLight"
    assert matcher.match("каква е температурата в хола").intent == "HallTemperature"
