"""Read-only values of EFM, Heatmixer and SolarPumpController as HA entities.

Each value becomes its own entity (unique id = state uuid) grouped under one
device per control, like the Meter sub-sensors.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.loxone import binary_sensor as binary_sensor_module
from custom_components.loxone import sensor as sensor_module
from custom_components.loxone.const import DOMAIN

STRUCTURE = {
    "msInfo": {"tempUnit": 0},
    "rooms": {"room-heizraum": {"uuid": "room-heizraum", "name": "Heizraum"}},
    "cats": {"cat-energie": {"uuid": "cat-energie", "name": "Energie"}},
    "controls": {
        "efm-uuid": {
            "name": "Energiefluss",
            "type": "EFM",
            "uuidAction": "efm-uuid",
            "room": "room-heizraum",
            "cat": "cat-energie",
            "details": {"actualFormat": "%.3fkW", "totalFormat": "%.1fkWh", "storageFormat": "%.0f%%"},
            "states": {
                "Ppwr": "efm-ppwr",
                "selfConsumption": "efm-self",
                "storageChargeFromProduction": "efm-storage-charge",
                "totalSelfConsumption": "efm-total-self",
                "totalStorageChargeFromProduction": "efm-total-storage-charge",
            },
            "subControls": {
                "haus-uuid": {
                    "name": "Haus",
                    "type": "Meter",
                    "uuidAction": "haus-uuid",
                    "room": "room-heizraum",
                    "cat": "cat-energie",
                    "details": {"type": "bidirectional", "actualFormat": "%.3fkW", "totalFormat": "%.1fkWh"},
                    "states": {"actual": "haus-actual", "total": "haus-total", "totalNeg": "haus-total-neg"},
                }
            },
        },
        "mixer-uuid": {
            "name": "HK EG Mischer",
            "type": "Heatmixer",
            "uuidAction": "mixer-uuid",
            "room": "room-heizraum",
            "cat": "",
            "details": {"jLockable": True},
            "states": {"jLocked": "mixer-locked", "tempActual": "mixer-actual", "tempTarget": "mixer-target"},
        },
        "solar-uuid": {
            "name": "Solarregelung",
            "type": "SolarPumpController",
            "uuidAction": "solar-uuid",
            "room": "room-heizraum",
            "cat": "",
            "details": {"jLockable": True, "buffers": [{"name": "Warmwasserspeicher"}]},
            "states": {
                "bufferTemp0": "solar-temp0",
                "bufferState0": "solar-state0",
                "bufferTemp1": "solar-temp1",
                "bufferState1": "solar-state1",
                "collectorTemp": "solar-collector",
                "heatOverload": "solar-overload",
                "logicOverride": "solar-override",
            },
        },
    },
}


def _by_unique_id(platform_entities, module, loxconfig=None) -> dict:
    return {e.unique_id: e for e in platform_entities(module, loxconfig or STRUCTURE)}


def test_efm_inner_meter_becomes_meter_sensors(platform_entities):
    sensors = _by_unique_id(platform_entities, sensor_module)

    assert sensors["haus-actual"]._attr_name == "Haus Actual"
    assert sensors["haus-total"]._attr_name == "Haus Total"
    assert sensors["haus-total-neg"]._attr_name == "Haus Total Neg"
    assert sensors["haus-actual"].device_class == SensorDeviceClass.POWER


def test_efm_flow_values(platform_entities):
    sensors = _by_unique_id(platform_entities, sensor_module)

    expected = {
        "efm-self": ("Energiefluss Self Consumption", "kW", SensorDeviceClass.POWER),
        "efm-storage-charge": ("Energiefluss Storage Charge From Production", "kW", SensorDeviceClass.POWER),
        "efm-total-self": ("Energiefluss Total Self Consumption", "kWh", SensorDeviceClass.ENERGY),
        "efm-total-storage-charge": (
            "Energiefluss Total Storage Charge From Production",
            "kWh",
            SensorDeviceClass.ENERGY,
        ),
    }
    for uid, (name, unit, device_class) in expected.items():
        sensor = sensors[uid]
        assert (sensor._attr_name, sensor.native_unit_of_measurement, sensor.device_class) == (name, unit, device_class)
    # Grid/production/storage power duplicate the Meter controls and are not created twice.
    assert "efm-ppwr" not in sensors


def test_heatmixer_actual_and_target_temperature(platform_entities):
    sensors = _by_unique_id(platform_entities, sensor_module)

    actual, target = sensors["mixer-actual"], sensors["mixer-target"]
    assert actual._attr_name == "HK EG Mischer Actual"
    assert target._attr_name == "HK EG Mischer Target"
    assert actual.native_unit_of_measurement == "°C"
    assert actual.device_class == SensorDeviceClass.TEMPERATURE
    assert "mixer-locked" not in sensors


def test_temperatures_follow_the_miniserver_unit(platform_entities):
    loxconfig = copy.deepcopy(STRUCTURE)
    loxconfig["msInfo"]["tempUnit"] = 1

    sensors = _by_unique_id(platform_entities, sensor_module, loxconfig)

    assert sensors["mixer-actual"].native_unit_of_measurement == "°F"
    assert sensors["solar-collector"].native_unit_of_measurement == "°F"


def test_solar_collector_and_buffers(platform_entities):
    sensors = _by_unique_id(platform_entities, sensor_module)

    assert sensors["solar-collector"]._attr_name == "Solarregelung Collector"
    assert sensors["solar-temp0"]._attr_name == "Solarregelung Warmwasserspeicher Temperature"
    assert sensors["solar-state0"]._attr_name == "Solarregelung Warmwasserspeicher State"
    # The structure names only the first buffer; the second gets a numbered name.
    assert sensors["solar-temp1"]._attr_name == "Solarregelung Buffer 2 Temperature"
    assert sensors["solar-state1"]._attr_name == "Solarregelung Buffer 2 State"
    assert sensors["solar-temp0"].device_class == SensorDeviceClass.TEMPERATURE
    # Unitless like every LoxoneSensor without a unit in its format ("" rather than None).
    assert not sensors["solar-state0"].native_unit_of_measurement


def test_values_of_one_control_share_its_device(platform_entities):
    sensors = _by_unique_id(platform_entities, sensor_module)

    for uid in ("mixer-actual", "mixer-target"):
        info = sensors[uid].device_info
        assert info["identifiers"] == {(DOMAIN, "mixer-uuid")}
        assert info["model"] == "Heatmixer"
        assert info["name"] == "HK EG Mischer"
        assert info["suggested_area"] == "Heizraum"


def test_value_follows_its_state_event(platform_entities):
    sensor = _by_unique_id(platform_entities, sensor_module)["mixer-actual"]
    sensor.async_schedule_update_ha_state = lambda *_: None

    asyncio.run(sensor.event_handler(SimpleNamespace(data={"mixer-actual": 41.5, "mixer-target": 45.0})))

    assert sensor.native_value == 41.5


def test_missing_states_create_no_entities(platform_entities):
    loxconfig = copy.deepcopy(STRUCTURE)
    loxconfig["controls"]["mixer-uuid"]["states"] = {}
    loxconfig["controls"]["efm-uuid"]["states"] = {}

    sensors = _by_unique_id(platform_entities, sensor_module, loxconfig)

    assert not any(uid.startswith(("mixer-", "efm-")) for uid in sensors)


def test_solar_flags_are_binary_sensors(platform_entities):
    flags = _by_unique_id(platform_entities, binary_sensor_module)

    overload, override = flags["solar-overload"], flags["solar-override"]
    assert overload._attr_name == "Solarregelung Heat Overload"
    assert overload.device_class == BinarySensorDeviceClass.PROBLEM
    assert override._attr_name == "Solarregelung Logic Override"
    assert overload.device_info["identifiers"] == {(DOMAIN, "solar-uuid")}


@pytest.mark.parametrize(("value", "expected"), [(1.0, True), (0.0, False)])
def test_solar_flag_follows_its_state(platform_entities, value, expected):
    overload = _by_unique_id(platform_entities, binary_sensor_module)["solar-overload"]
    overload.async_schedule_update_ha_state = lambda *_: None

    asyncio.run(overload.event_handler(SimpleNamespace(data={"solar-overload": value})))

    assert overload.is_on is expected
