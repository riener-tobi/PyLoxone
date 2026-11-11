"""TextState ("Statusbaustein") as a read-only HA sensor."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from custom_components.loxone.sensor import DEFAULT_TEXT_STATE_ICON, LoxoneTextStateSensor, parse_icon_and_color

TEXT_UUID = "text-state-uuid"
ICON_UUID = "icon-state-uuid"


def _sensor(states: dict | None = None) -> LoxoneTextStateSensor:
    entity = LoxoneTextStateSensor(
        name="Gastherme",
        uuidAction="textstate-uuid",
        room="Heizraum",
        cat="Heizung",
        type="TextState",
        states=states if states is not None else {"textAndIcon": TEXT_UUID, "iconAndColor": ICON_UUID},
        details={},
    )
    entity.async_schedule_update_ha_state = lambda *_: None
    return entity


def _event(entity, data):
    asyncio.run(entity.event_handler(SimpleNamespace(data=data)))


def test_unique_id_is_the_control_uuid():
    # Same as the fork: existing registry entries keep their entity ids.
    assert _sensor().unique_id == "textstate-uuid"


def test_text_comes_from_text_and_icon_state():
    entity = _sensor()
    assert entity.native_value is None

    _event(entity, {TEXT_UUID: "Gastherme Bereit"})
    assert entity.native_value == "Gastherme Bereit"

    # Unrelated uuids, including the control uuid itself, change nothing.
    _event(entity, {"textstate-uuid": "ignored", "other": "x"})
    assert entity.native_value == "Gastherme Bereit"


def test_icon_and_color_are_parsed():
    entity = _sensor()
    _event(entity, {ICON_UUID: json.dumps({"icon": "IconsFilled/radiator.svg", "color": "#EDEDED"})})

    assert entity.icon == "mdi:radiator"
    assert entity.extra_state_attributes["color"] == "#EDEDED"
    assert entity.extra_state_attributes["device_type"] == "TextState"
    assert entity.extra_state_attributes["room"] == "Heizraum"


def test_text_and_icon_in_one_event():
    entity = _sensor()
    _event(entity, {TEXT_UUID: "Puffer warm", ICON_UUID: {"icon": "IconsFilled/flame-2.svg", "color": "#8DFF70"}})

    assert entity.native_value == "Puffer warm"
    assert entity.icon == "mdi:fire"


def test_control_without_icon_state():
    entity = _sensor(states={"textAndIcon": TEXT_UUID})
    _event(entity, {TEXT_UUID: "Nur Text"})

    assert entity.native_value == "Nur Text"
    assert entity.icon == DEFAULT_TEXT_STATE_ICON
    assert "color" not in entity.extra_state_attributes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (json.dumps({"icon": "IconsFilled/snowflake.svg", "color": "#73E0FF"}), ("mdi:snowflake", "#73E0FF")),
        # Double-encoded JSON string as some Miniserver versions send it.
        (json.dumps(json.dumps({"icon": "IconsFilled/water-drops.svg"})), ("mdi:water", None)),
        ({"icon": "IconsFilled/unknown-thing.svg", "color": "#000000"}, (DEFAULT_TEXT_STATE_ICON, "#000000")),
        (json.dumps({"color": "#FFFFFF"}), (DEFAULT_TEXT_STATE_ICON, "#FFFFFF")),
    ],
)
def test_parse_icon_and_color(raw, expected):
    assert parse_icon_and_color(raw, "Test") == expected


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "42", None])
def test_invalid_icon_payload_is_logged_and_falls_back(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_icon_and_color(raw, "Gastherme") == (DEFAULT_TEXT_STATE_ICON, None)

    assert any("Gastherme" in r.getMessage() for r in caplog.records)
