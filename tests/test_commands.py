from __future__ import annotations

from app.config import Settings, load_commands
from app.matcher import Matcher


def test_both_command_catalogues_load() -> None:
    for name in ("commands.yaml", "commands-example.yaml"):
        commands = load_commands(Settings(_env_file=None, commands_file=name))
        assert commands.commands


def test_starter_catalogue_matches_its_phrases() -> None:
    commands = load_commands(Settings(_env_file=None, commands_file="commands.yaml"))
    matcher = Matcher(
        commands.commands,
        threshold=commands.match.threshold,
        margin=commands.match.margin,
    )
    assert matcher.match("изключи лампата").intent == "LightOff"
    assert matcher.match("каква е температурата навън").intent == "OutsideTemperature"
