"""Render Bulgarian query answers from a Home Assistant state object."""

from __future__ import annotations

from typing import Any

from .config import QueryConfig


def format_value(raw: Any, digits: int | None) -> tuple[str, float | None]:
    """Return a display string (Bulgarian decimal comma) and the numeric value."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return str(raw), None

    displayed = number
    if digits is None:
        text = str(raw)
    elif digits <= 0:
        displayed = float(round(number))
        text = f"{displayed:.0f}"
    else:
        displayed = round(number, digits)
        text = f"{displayed:.{digits}f}"
    return text.replace(".", ","), displayed


def render_query(config: QueryConfig, state: dict[str, Any]) -> str:
    """Render ``config.template`` with ``{value}``, ``{unit}``, ``{name}``, ``{state}``."""
    attributes = state.get("attributes") or {}
    value, number = format_value(state.get("state"), config.round)

    unit = config.unit or attributes.get("unit_of_measurement") or ""
    if (
        config.unit_singular
        and number is not None
        and abs(number - 1) < 1e-9
    ):
        unit = config.unit_singular

    name = attributes.get("friendly_name") or config.entity_id
    try:
        return config.template.format(
            value=value, unit=unit, name=name, state=state.get("state")
        )
    except (KeyError, IndexError, ValueError):
        # A malformed template must not break the reply.
        parts = [f"{name}: {value}".rstrip()]
        if unit:
            parts.append(str(unit))
        return " ".join(parts) + "."
