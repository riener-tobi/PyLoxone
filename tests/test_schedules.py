"""Daytimer (e.g. a boiler schedule) and the room controllers' IRCV2Daytimer as sensors."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.loxone import sensor as sensor_module
from custom_components.loxone.daytimer import LoxoneScheduleSensor, schedule_for_mode

OPERATING_MODES = {"3": "Montag", "5": "Mittwoch", "10": "Heizperiode"}

STRUCTURE = {
    "msInfo": {"tempUnit": 0},
    "operatingModes": OPERATING_MODES,
    "rooms": {
        "room-heiz": {"uuid": "room-heiz", "name": "Heizraum"},
        "room-wz": {"uuid": "room-wz", "name": "Wohnzimmer"},
    },
    "cats": {},
    "controls": {
        "timer-uuid": {
            "name": "Boiler Schaltuhr",
            "type": "Daytimer",
            "uuidAction": "timer-uuid",
            "room": "room-heiz",
            "cat": "",
            "details": {"analog": True, "format": "%.1f°C"},
            "states": {
                "mode": "timer-mode",
                "value": "timer-value",
                "entriesAndDefaultValue": "timer-entries",
                "override": "timer-override",
            },
        },
        "irc-uuid": {
            "name": "Wohnzimmer",
            "type": "IRoomControllerV2",
            "uuidAction": "irc-uuid",
            "room": "room-wz",
            "cat": "",
            "details": {
                "timerModes": [
                    {"name": "Komfort-Temperatur", "id": 1},
                    {"name": "Eco-Temperatur", "id": 0},
                    {"name": "Gebäudeschutz", "id": 2},
                ],
                "format": "%.1f°",
            },
            "states": {"tempActual": "irc-temp"},
            "subControls": {
                "irc-timer": {
                    "name": "Heating and Cooling",
                    "type": "IRCV2Daytimer",
                    "uuidAction": "irc-timer",
                    "details": {"analog": True, "format": "%.1f°"},
                    "states": {"mode": "irc-mode", "value": "irc-value", "entriesAndDefaultValue": "irc-entries"},
                }
            },
        },
    },
}

ENTRIES = [
    {"mode": 5, "from": 360, "to": 480, "needs_activation": False, "value": 60.0},
    {"mode": 5, "from": 1020, "to": 1320, "needs_activation": False, "value": 55.0},
    {"mode": 3, "from": 300, "to": 1380, "needs_activation": False, "value": 50.0},
]


def _schedules(platform_entities) -> dict:
    return {e.unique_id: e for e in platform_entities(sensor_module, STRUCTURE) if isinstance(e, LoxoneScheduleSensor)}


def _feed(entity, data: dict) -> None:
    entity.async_schedule_update_ha_state = lambda *_: None
    asyncio.run(entity.event_handler(SimpleNamespace(data=data)))


def test_schedule_for_the_active_mode_and_next_change():
    today, next_change = schedule_for_mode(ENTRIES, mode=5, now_minutes=500)

    assert today == [{"from": "06:00", "to": "08:00", "value": 60.0}, {"from": "17:00", "to": "22:00", "value": 55.0}]
    assert next_change == "17:00"


def test_no_next_change_after_the_last_entry():
    assert schedule_for_mode(ENTRIES, mode=5, now_minutes=1330)[1] is None
    assert schedule_for_mode(ENTRIES, mode=7, now_minutes=0) == ([], None)


def test_entry_until_midnight_is_shown_as_24_00():
    today, _ = schedule_for_mode([{"mode": 5, "from": 0, "to": 1440, "needs_activation": False, "value": 1.0}], 5, 0)
    assert today == [{"from": "00:00", "to": "24:00", "value": 1.0}]


def test_daytimer_and_room_schedules_are_created(platform_entities):
    schedules = _schedules(platform_entities)

    assert schedules["timer-uuid"]._attr_name == "Boiler Schaltuhr"
    assert schedules["timer-uuid"].native_unit_of_measurement == "°C"
    assert schedules["irc-timer"]._attr_name == "Wohnzimmer Schedule"
    # The room schedule reports the scheduled mode, not a temperature.
    assert schedules["irc-timer"].native_unit_of_measurement is None


def test_daytimer_value_mode_and_entries(platform_entities, monkeypatch):
    timer = _schedules(platform_entities)["timer-uuid"]
    monkeypatch.setattr("custom_components.loxone.daytimer.minutes_since_midnight", lambda: 500)

    _feed(timer, {"timer-value": 60.0, "timer-mode": 5.0, "timer-entries": {"default_value": 45.0, "entries": ENTRIES}})

    assert timer.native_value == 60.0
    attrs = timer.extra_state_attributes
    assert attrs["mode"] == "Mittwoch"
    assert attrs["default_value"] == 45.0
    assert attrs["entries_today"] == [
        {"from": "06:00", "to": "08:00", "value": 60.0},
        {"from": "17:00", "to": "22:00", "value": 55.0},
    ]
    assert attrs["next_change"] == "17:00"


def test_room_schedule_names_the_scheduled_mode(platform_entities, monkeypatch):
    schedule = _schedules(platform_entities)["irc-timer"]
    monkeypatch.setattr("custom_components.loxone.daytimer.minutes_since_midnight", lambda: 400)

    _feed(
        schedule,
        {
            "irc-value": 1.0,
            "irc-mode": 10.0,
            "irc-entries": {
                "default_value": 0.0,
                "entries": [{"mode": 10, "from": 360, "to": 1320, "needs_activation": False, "value": 1.0}],
            },
        },
    )

    assert schedule.native_value == "Komfort-Temperatur"
    attrs = schedule.extra_state_attributes
    assert attrs["mode"] == "Heizperiode"
    assert attrs["default_value"] == "Eco-Temperatur"
    assert attrs["entries_today"] == [{"from": "06:00", "to": "22:00", "value": "Komfort-Temperatur"}]
    assert attrs["next_change"] == "22:00"


def test_unknown_mode_numbers_stay_numbers(platform_entities):
    schedule = _schedules(platform_entities)["irc-timer"]
    _feed(schedule, {"irc-value": 7.0, "irc-mode": 42.0})

    assert schedule.native_value == "7"
    assert schedule.extra_state_attributes["mode"] == "42"


def test_switched_off_room_controller(platform_entities):
    """Loxone reports -1 while the room controller is switched off (seen live on four rooms)."""
    schedule = _schedules(platform_entities)["irc-timer"]
    _feed(schedule, {"irc-value": -1.0})

    assert schedule.native_value == "Aus"
