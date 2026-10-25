from __future__ import annotations

from app.config import QueryConfig
from app.reply import format_value, render_query


def state(value: str, unit: str | None = "°C", name: str = "Outside Temperature Outside"):
    attributes = {"friendly_name": name}
    if unit:
        attributes["unit_of_measurement"] = unit
    return {"entity_id": "sensor.outside_temp", "state": value, "attributes": attributes}


def test_rounds_to_integer() -> None:
    assert format_value("10.7540130615234", 0) == ("11", 11.0)


def test_rounds_to_one_decimal_with_comma() -> None:
    assert format_value("10.754", 1) == ("10,8", 10.8)


def test_keeps_precision_when_round_is_none() -> None:
    # Still Bulgarian decimal comma, but no rounding.
    assert format_value("10.7540130615234", None)[0] == "10,7540130615234"


def test_non_numeric_state_is_returned_as_is() -> None:
    assert format_value("unavailable", 1) == ("unavailable", None)


def test_render_query_uses_unit_and_name() -> None:
    config = QueryConfig(
        entity_id="sensor.outside_temp",
        round=0,
        unit="градуса",
        template="В момента температурата навън е {value} {unit}.",
    )
    assert render_query(config, state("10.754")) == (
        "В момента температурата навън е 11 градуса."
    )


def test_singular_unit_for_one() -> None:
    config = QueryConfig(
        entity_id="sensor.outside_temp",
        round=0,
        unit="градуса",
        unit_singular="градус",
        template="{value} {unit}",
    )
    assert render_query(config, state("1.4")) == "1 градус"


def test_falls_back_when_template_is_malformed() -> None:
    config = QueryConfig(entity_id="sensor.outside_temp", template="{nope}")
    rendered = render_query(config, state("10.75"))
    assert "Outside Temperature Outside" in rendered
    assert "10,8" in rendered
    assert "°C" in rendered
