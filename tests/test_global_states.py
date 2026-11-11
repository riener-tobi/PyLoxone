"""Miniserver-wide states: operating mode and internet connectivity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.loxone import binary_sensor as binary_sensor_module
from custom_components.loxone import sensor as sensor_module
from custom_components.loxone.daytimer import LoxoneOperatingModeSensor

STRUCTURE = {
    "msInfo": {"tempUnit": 0},
    "operatingModes": {"5": "Mittwoch", "10": "Heizperiode", "-6": "Abwesend"},
    "globalStates": {"operatingMode": "gs-opmode", "hasInternet": "gs-internet", "sunrise": "gs-sunrise"},
    "rooms": {},
    "cats": {},
    "controls": {},
}


def _feed(entity, data):
    entity.async_schedule_update_ha_state = lambda *_: None
    asyncio.run(entity.event_handler(SimpleNamespace(data=data)))


def _operating_mode(platform_entities, loxconfig=STRUCTURE):
    found = [e for e in platform_entities(sensor_module, loxconfig) if isinstance(e, LoxoneOperatingModeSensor)]
    return found[0] if found else None


@pytest.mark.parametrize(
    ("value", "expected"), [(5.0, "Mittwoch"), (-6.0, "Abwesend"), (99.0, "99"), ("Urlaub", "Urlaub")]
)
def test_operating_mode_is_named(platform_entities, value, expected):
    sensor = _operating_mode(platform_entities)
    assert sensor.unique_id == "gs-opmode"
    assert sensor._attr_name == "Loxone Operating Mode"

    _feed(sensor, {"gs-opmode": value})

    assert sensor.native_value == expected


def test_internet_connectivity(platform_entities):
    flags = {e.unique_id: e for e in platform_entities(binary_sensor_module, STRUCTURE)}
    internet = flags["gs-internet"]
    assert internet._attr_name == "Loxone Internet"
    assert internet.device_class == BinarySensorDeviceClass.CONNECTIVITY

    _feed(internet, {"gs-internet": 1.0})
    assert internet.is_on is True
    _feed(internet, {"gs-internet": 0.0})
    assert internet.is_on is False


def test_structure_without_global_states(platform_entities):
    loxconfig = {**STRUCTURE, "globalStates": {}}
    assert _operating_mode(platform_entities, loxconfig) is None
    assert "gs-internet" not in {e.unique_id for e in platform_entities(binary_sensor_module, loxconfig)}
