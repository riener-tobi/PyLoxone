"""Ventilation measurements are sensors / binary sensors, not fan entities.

They used to be built as LoxoneSensor / LoxoneDigitalSensor but added through the
fan platform, so Home Assistant registered them as ``fan.*`` entities.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.loxone import binary_sensor as binary_sensor_module
from custom_components.loxone import fan as fan_module
from custom_components.loxone import sensor as sensor_module
from custom_components.loxone.const import DOMAIN
from custom_components.loxone.fan import LoxoneVentilation

STRUCTURE = {
    "msInfo": {"tempUnit": 0},
    "rooms": {"room-bad": {"uuid": "room-bad", "name": "Bad OG"}},
    "cats": {},
    "controls": {
        "vent-uuid": {
            "name": "Raumlüfter",
            "type": "Ventilation",
            "uuidAction": "vent-uuid",
            "room": "room-bad",
            "cat": "",
            "details": {
                "hasPresence": True,
                "hasIndoorHumidity": True,
                "hasAirQuality": True,
                "modes": [],
                "timerProfiles": [],
            },
            "states": {
                "speed": "vent-speed",
                "mode": "vent-mode",
                "presence": "vent-presence",
                "humidityIndoor": "vent-humidity",
                "airQualityIndoor": "vent-air",
                "temperatureOutdoor": "vent-outdoor",
            },
        }
    },
}


class _FakeRegistry:
    def __init__(self, entries: dict[tuple[str, str], str]) -> None:
        self.entries = entries  # (domain, unique_id) -> entity_id
        self.removed: list[str] = []

    def async_get_entity_id(self, domain, platform, unique_id):
        assert platform == DOMAIN
        return self.entries.get((domain, unique_id))

    def async_remove(self, entity_id):
        self.removed.append(entity_id)


def test_fan_platform_only_creates_the_ventilation(platform_entities, monkeypatch):
    monkeypatch.setattr(fan_module.er, "async_get", lambda hass: _FakeRegistry({}))
    entities = platform_entities(fan_module, STRUCTURE)
    assert [type(e) for e in entities] == [LoxoneVentilation]


def test_measurements_are_sensors(platform_entities):
    sensors = {e.unique_id: e for e in platform_entities(sensor_module, STRUCTURE)}

    humidity, air, outdoor = sensors["vent-humidity"], sensors["vent-air"], sensors["vent-outdoor"]
    assert (humidity._attr_name, humidity.native_unit_of_measurement, humidity.device_class) == (
        "Raumlüfter Humidity",
        "%",
        SensorDeviceClass.HUMIDITY,
    )
    assert (air._attr_name, air.native_unit_of_measurement, air.device_class) == (
        "Raumlüfter Air Quality",
        "ppm",
        SensorDeviceClass.CO2,
    )
    # The state is the outdoor temperature; the old name "Temperature" suggested the room.
    assert (outdoor._attr_name, outdoor.native_unit_of_measurement, outdoor.device_class) == (
        "Raumlüfter Outdoor Temperature",
        "°C",
        SensorDeviceClass.TEMPERATURE,
    )
    for sensor in (humidity, air, outdoor):
        assert sensor.device_info["identifiers"] == {(DOMAIN, "vent-uuid")}
        assert sensor.device_info["name"] == "Raumlüfter"


def test_presence_is_a_binary_sensor(platform_entities):
    flags = {e.unique_id: e for e in platform_entities(binary_sensor_module, STRUCTURE)}

    presence = flags["vent-presence"]
    assert presence._attr_name == "Raumlüfter Presence"
    assert presence.device_class == BinarySensorDeviceClass.PRESENCE


def test_flags_in_details_switch_values_off(platform_entities):
    loxconfig = {**STRUCTURE, "controls": {"vent-uuid": {**STRUCTURE["controls"]["vent-uuid"]}}}
    loxconfig["controls"]["vent-uuid"]["details"] = {
        **STRUCTURE["controls"]["vent-uuid"]["details"],
        "hasPresence": False,
        "hasAirQuality": False,
    }

    sensors = {e.unique_id for e in platform_entities(sensor_module, loxconfig)}
    flags = {e.unique_id for e in platform_entities(binary_sensor_module, loxconfig)}

    assert "vent-air" not in sensors
    assert {"vent-humidity", "vent-outdoor"} <= sensors
    assert "vent-presence" not in flags


def test_misplaced_fan_entities_are_removed_from_the_registry(monkeypatch, caplog):
    registry = _FakeRegistry(
        {
            ("fan", "vent-humidity"): "fan.raumlufter_humidity",
            ("fan", "vent-outdoor"): "fan.raumlufter_temperature",
            ("fan", "vent-uuid"): "fan.raumlufter",  # the real fan stays
        }
    )
    monkeypatch.setattr(fan_module.er, "async_get", lambda hass: registry)

    fan_module.remove_misplaced_ventilation_entities(SimpleNamespace(), STRUCTURE)

    assert sorted(registry.removed) == ["fan.raumlufter_humidity", "fan.raumlufter_temperature"]
    assert "fan.raumlufter_humidity" in caplog.text


def test_room_mapping_leaves_the_shared_structure_untouched():
    """Sensor, binary sensor and fan platform all map the same Ventilation control."""
    from custom_components.loxone.helpers import with_room_and_cat

    control = STRUCTURE["controls"]["vent-uuid"]
    first = with_room_and_cat(STRUCTURE, control)
    second = with_room_and_cat(STRUCTURE, control)

    assert first["room"] == second["room"] == "Bad OG"
    assert control["room"] == "room-bad"
