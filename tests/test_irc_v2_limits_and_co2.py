"""IRoomControllerV2: temperature limits from the protection temperatures, CO2 text attribute."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.climate import DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP

from custom_components.loxone.climate import ActiveMode, ActiveState, LoxoneRoomControllerV2, OperatingMode


def _irc(**state_values) -> LoxoneRoomControllerV2:
    rc = LoxoneRoomControllerV2.__new__(LoxoneRoomControllerV2)
    rc.hass = SimpleNamespace(bus=SimpleNamespace(fire=lambda *_: None))
    rc.uuidAction = "rc-uuid"
    rc.details = {"format": "%.1f°"}
    rc.operating_mode = OperatingMode.AUTO_HEAT
    rc.active_state = ActiveState(mode=ActiveMode.COMFORT, value=0)
    rc._range_possible = False
    rc._demand = None
    rc._attr_extra_state_attributes = {}
    state_values.setdefault("overrideReason", 0)
    rc._states = {name: f"uuid-{name}" for name in state_values}
    rc._state_attr_values = {f"uuid-{name}": v for name, v in state_values.items()}
    return rc


def test_limits_follow_frost_and_heat_protection():
    rc = _irc(frostProtectTemperature=5.0, heatProtectTemperature=32.0)
    assert rc.min_temp == 5.0
    assert rc.max_temp == 32.0


@pytest.mark.parametrize(
    "states",
    [
        {},  # controller without the protection states
        {"frostProtectTemperature": None, "heatProtectTemperature": 32.0},
        {"frostProtectTemperature": "kaputt", "heatProtectTemperature": 32.0},
        {"frostProtectTemperature": 30.0, "heatProtectTemperature": 30.0},  # no usable range
    ],
)
def test_limits_fall_back_to_ha_defaults(states):
    rc = _irc(**states)
    assert rc.min_temp == DEFAULT_MIN_TEMP
    assert rc.max_temp == DEFAULT_MAX_TEMP


def test_co2_with_unit_accompanies_a_valid_co2_value():
    # Kept for dashboards built against the fork, which show this attribute.
    attrs = _irc(co2=413.2).extra_state_attributes
    assert attrs["co2"] == 413.2
    assert attrs["co2_with_unit"] == "413.2 ppm"


def test_no_co2_attributes_without_sensor():
    # Controllers without a CO2 input report 0.
    attrs = _irc(co2=0).extra_state_attributes
    assert "co2" not in attrs
    assert "co2_with_unit" not in attrs
