"""
Loxone schedules (Daytimer, IRCV2Daytimer) and the Miniserver operating mode as read-only sensors.

The schedule entries arrive in binary DAYTIMER_STATES tables
(see ``pyloxone_api.message.DaytimerStatesTable``); value and mode are value states.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.util import dt as dt_util

from . import LoxoneEntity
from .helpers import clean_unit, control_device_info, get_all, get_or_create_device, with_room_and_cat

if TYPE_CHECKING:
    from homeassistant.core import Event
    from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

MINUTES_PER_DAY = 1440

# Value of a room controller schedule while the controller is switched off
# (observed live: exactly the rooms whose controller was off reported -1).
ROOM_SCHEDULE_OFF = -1


def minutes_since_midnight() -> int:
    """Return the current local time in minutes since midnight (the unit of schedule entries)."""
    now = dt_util.now()
    return now.hour * 60 + now.minute


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def schedule_for_mode(
    entries: list[dict], mode: int, now_minutes: int, name_of: Any = lambda value: value
) -> tuple[list[dict], str | None]:
    """
    Return the entries of ``mode`` as HH:MM ranges and the next start or end after ``now_minutes``.

    ``name_of`` turns an entry value into what is shown (e.g. a room controller's mode name).
    A change exactly at midnight belongs to the next day and is not reported.
    """
    today = sorted((e for e in entries if e["mode"] == mode), key=lambda e: e["from"])
    ranges = [{"from": _hhmm(e["from"]), "to": _hhmm(e["to"]), "value": name_of(e["value"])} for e in today]
    upcoming = sorted({m for e in today for m in (e["from"], e["to"]) if now_minutes < m < MINUTES_PER_DAY})
    return ranges, (_hhmm(upcoming[0]) if upcoming else None)


def _number_name(value: float | None, names: dict[int, str]) -> str | None:
    """Return the name for an integral ``value``, else the value as text (Loxone sends floats)."""
    if value is None:
        return None
    if float(value).is_integer():
        return names.get(int(value), str(int(value)))
    return str(value)


class LoxoneScheduleSensor(LoxoneEntity, SensorEntity):
    """Current value of a schedule, with today's entries and the next change as attributes."""

    def __init__(
        self,
        control: dict,
        name: str,
        mode_names: dict[int, str],
        value_names: dict[int, str] | None,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize; ``value_names`` maps entry values to names (room schedules), None for analog values."""
        super().__init__(**{**control, "name": name})
        self._attr_should_poll = False
        self._mode_names = mode_names
        self._value_names = value_names
        self._value: float | None = None
        self._mode: float | None = None
        self._default: float | None = None
        self._entries: list[dict] = []
        self._attr_device_info = device_info
        unit = clean_unit(control.get("details", {}).get("format", "")) if value_names is None else ""
        self._attr_native_unit_of_measurement = unit or None

    def _shown(self, value: float | None) -> Any:
        if self._value_names is None or value is None:
            return value
        return _number_name(value, self._value_names)

    @property
    def native_value(self) -> Any:
        """Return the scheduled value (a mode name for room schedules)."""
        return self._shown(self._value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return mode, default value, today's entries and the next change."""
        attrs: dict[str, Any] = {**self._attr_extra_state_attributes, "device_type": self.type}
        if self._mode is not None:
            attrs["mode"] = _number_name(self._mode, self._mode_names)
        if self._default is not None:
            attrs["default_value"] = self._shown(self._default)
        if self._mode is not None and self._entries:
            today, next_change = schedule_for_mode(
                self._entries, int(self._mode), minutes_since_midnight(), self._shown
            )
            attrs["entries_today"] = today
            attrs["next_change"] = next_change
        return attrs

    async def event_handler(self, e: Event) -> None:
        """Apply value, mode and schedule updates of this control."""
        changed = False
        for state_key, attr in (("value", "_value"), ("mode", "_mode")):
            uuid = self.states.get(state_key)
            if uuid and uuid in e.data:
                setattr(self, attr, e.data[uuid])
                changed = True
        uuid = self.states.get("entriesAndDefaultValue")
        if uuid and uuid in e.data:
            table = e.data[uuid]
            if isinstance(table, dict) and "entries" in table:
                self._default = table.get("default_value")
                self._entries = table["entries"]
                changed = True
            else:
                _LOGGER.warning("Unexpected schedule data for %s: %r", self.name, table)
        if changed:
            self.async_schedule_update_ha_state()


class LoxoneOperatingModeSensor(LoxoneEntity, SensorEntity):
    """The Miniserver's current operating mode (weekday, holiday, heating period, ...)."""

    _attr_should_poll = False
    _attr_icon = "mdi:calendar-star"

    def __init__(self, state_uuid: str, mode_names: dict[int, str]) -> None:
        """Initialize for the ``globalStates.operatingMode`` state."""
        super().__init__(name="Loxone Operating Mode", uuidAction=state_uuid)
        self._mode_names = mode_names

    async def event_handler(self, e: Event) -> None:
        """Name the mode number (or keep a text value as it is)."""
        if self.uuidAction in e.data:
            value = e.data[self.uuidAction]
            if isinstance(value, (int, float)):
                self._attr_native_value = _number_name(value, self._mode_names)
            else:
                self._attr_native_value = str(value)
            self.async_schedule_update_ha_state()


def operating_mode_sensors(loxconfig: dict) -> list[LoxoneOperatingModeSensor]:
    """Return the operating mode sensor, if the structure file has the global state."""
    uuid = loxconfig.get("globalStates", {}).get("operatingMode")
    if not uuid:
        return []
    return [LoxoneOperatingModeSensor(uuid, {int(k): v for k, v in loxconfig.get("operatingModes", {}).items()})]


def schedule_sensors(loxconfig: dict) -> list[LoxoneScheduleSensor]:
    """Return one sensor per Daytimer and per room controller schedule (IRCV2Daytimer)."""
    mode_names = {int(k): v for k, v in loxconfig.get("operatingModes", {}).items()}
    sensors: list[LoxoneScheduleSensor] = []
    for raw_timer in get_all(loxconfig, "Daytimer"):
        timer = with_room_and_cat(loxconfig, raw_timer)
        sensors.append(LoxoneScheduleSensor(timer, timer["name"], mode_names, None, control_device_info(timer)))
    for raw_irc in get_all(loxconfig, "IRoomControllerV2"):
        irc = with_room_and_cat(loxconfig, raw_irc)
        value_names = {ROOM_SCHEDULE_OFF: "Aus"}
        value_names.update({m["id"]: m["name"] for m in irc.get("details", {}).get("timerModes", [])})
        # Same device as the room controller's other entities (see the IRC sub-sensors in sensor.py).
        device_info = get_or_create_device(irc["uuidAction"], irc["name"], "RoomControllerV2", irc.get("room", ""))
        for sub in irc.get("subControls", {}).values():
            if sub.get("type") == "IRCV2Daytimer":
                control = {**sub, "room": irc.get("room", ""), "cat": irc.get("cat", "")}
                name = f"{irc['name']} Schedule"
                sensors.append(LoxoneScheduleSensor(control, name, mode_names, value_names, device_info))
    return sensors
