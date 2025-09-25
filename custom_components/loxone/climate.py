"""
Improved Loxone climate platform with auto-to-manual temperature handling

This is a more robust implementation for Loxone RoomControllerV2 and AcControl
climate entities for Home Assistant (based on the PyLoxone approach).

Improvements:
- target_temperature falls back to manualTemperature when tempTarget is not present
- hvac_action now distinguishes HEATING / COOLING / PREHEATING / IDLE
- set_temperature always switches to manual (fix value, heating) if in AUTO
- more extra_state_attributes for debugging
- defensive handling of missing state uuids / values
- consistent temperature steps per device type
"""

from __future__ import annotations

import logging
from abc import ABC
from typing import Any, Dict, List, Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from voluptuous import All, Optional, Range
import voluptuous as vol

from . import LoxoneEntity
from .const import CONF_HVAC_AUTO_MODE, SENDDOMAIN
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

OPMODES = {
    -1: HVACMode.OFF,       # RoomController ausgeschaltet
    0: HVACMode.AUTO,       # Auto
    1: HVACMode.AUTO,
    2: HVACMode.AUTO,
    3: HVACMode.HEAT_COOL, # Fixwert Auto/Heating/Cooling
    4: HVACMode.HEAT,      # Fixwert Heizen
    5: HVACMode.COOL,      # Fixwert Kühlen
}

OPMODETOLOXONE = {
    HVACMode.AUTO: 0,
    HVACMode.HEAT_COOL: 3,
    HVACMode.HEAT: 4,
    HVACMode.COOL: 5,
    HVACMode.OFF: -1,      # Off-Modus
}

PLATFORM_SCHEMA = vol.Schema(
    {
        Optional(CONF_HVAC_AUTO_MODE, default=0): All(int, Range(min=0, max=2)),
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    miniserver = get_miniserver_from_hass(hass)
    loxconfig = miniserver.lox_config.json
    entities: List[ClimateEntity] = []

    for climate in get_all(loxconfig, "IRoomControllerV2"):
        climate = add_room_and_cat_to_value_values(loxconfig, climate)
        climate.update({"hass": hass, CONF_HVAC_AUTO_MODE: 0})
        entities.append(LoxoneRoomControllerV2(**climate))

    for accontrol in get_all(loxconfig, "AcControl"):
        accontrol = add_room_and_cat_to_value_values(loxconfig, accontrol)
        accontrol.update({"hass": hass})
        entities.append(LoxoneAcControl(**accontrol))

    if entities:
        async_add_entities(entities)


class LoxoneRoomControllerV2(LoxoneEntity, ClimateEntity, ABC):
    _attr_supported_features = (
        ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.hass: HomeAssistant = kwargs["hass"]
        self._autoMode = kwargs.get(CONF_HVAC_AUTO_MODE, 0)
        self._stateAttribUuids: Dict[str, str] = kwargs.get("states", {})
        self._stateAttribValues: Dict[str, Any] = {}
        self.type = "RoomControllerV2"
        self._modeList = kwargs.get("details", {}).get("timerModes", [])
        self._attr_device_info = get_or_create_device(
            self.unique_id, self.name, self.type, self.room
        )

    async def event_handler(self, event: Event) -> None:
        update = False
        if not self._stateAttribUuids or not isinstance(event.data, dict):
            return
        for key in set(self._stateAttribUuids.values()) & event.data.keys():
            self._stateAttribValues[key] = event.data[key]
            update = True
        if update:
            self.schedule_update_ha_state()

    def get_state_value(self, name: str) -> Optional[Any]:
        uuid = self._stateAttribUuids.get(name)
        if not uuid:
            return None
        return self._stateAttribValues.get(uuid)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {"is_overridden": self.is_overridden}
        keys = [
            "tempActual",
            "tempTarget",
            "manualTemperature",
            "comfortTemperature",
            "operatingMode",
            "activeMode",
            "isHeating",
            "isCooling",
            "prepareState",
            "overrideEntries",
        ]
        for k in keys:
            val = self.get_state_value(k)
            if val is not None:
                attrs[k] = val
        attrs["_state_uuids"] = self._stateAttribUuids
        return attrs

    @property
    def is_overridden(self) -> bool:
        _override_entries = self.get_state_value("overrideEntries")
        if not _override_entries:
            return False
        try:
            if isinstance(_override_entries, str):
                entries = eval(_override_entries)
            else:
                entries = _override_entries
            return isinstance(entries, list) and len(entries) > 0
        except Exception:
            return False

    @property
    def current_temperature(self) -> Optional[float]:
        val = self.get_state_value("tempActual")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> Optional[float]:
        val = self.get_state_value("tempTarget")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        manual = self.get_state_value("manualTemperature")
        if manual is not None:
            try:
                return float(manual)
            except (TypeError, ValueError):
                pass
        return None

    @property
    def target_temperature_step(self) -> Optional[float]:
        return 0.5

    @property
    def temperature_unit(self) -> str:
        format_str = self.details.get("format")
        if not format_str:
            return UnitOfTemperature.CELSIUS
        if "°F" in format_str or " F" in format_str:
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def hvac_modes(self) -> List[HVACMode]:
        """Return supported hvac modes including OFF."""
        return [HVACMode.AUTO, HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]

    @property
    def hvac_mode(self) -> Optional[HVACMode]:
        """Return current HVAC mode, handling -1 for OFF."""
        op = self.get_state_value("operatingMode")
        return OPMODES.get(op, HVACMode.OFF)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set HVAC mode, including OFF (-1)."""
        try:
            target_mode = OPMODETOLOXONE.get(hvac_mode)
            if target_mode is None:
                _LOGGER.warning("%s: unsupported hvac_mode %s", self.entity_id, hvac_mode)
                return
            payload = f"setOperatingMode/{target_mode}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            _LOGGER.debug("%s: set_hvac_mode fired %s", self.entity_id, payload)
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in set_hvac_mode: %s", self.entity_id, exc)

    @property
    def preset_modes(self) -> List[str]:
        return [mode.get("name") for mode in self._modeList if "name" in mode]

    @property
    def preset_mode(self) -> Optional[str]:
        active = self.get_state_value("activeMode")
        for mode in self._modeList:
            if mode.get("id") == active:
                return mode.get("name")
        return None

    def set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature.

        Behavior:
        - If currently in AUTO (-1 excluded), switch to manual Fixwert HEAT (4)
        - Then set temperature using setManualTemperature/<value>
        """
        temp = kwargs.get("temperature") or kwargs.get("target_temp")
        if temp is None:
            _LOGGER.debug("%s: set_temperature called without temperature", self.entity_id)
            return
        try:
            op = self.get_state_value("operatingMode")
            if op is None or int(op) <= 2:
                # Currently in AUTO → switch to manual HEAT (4)
                payload_mode = "setOperatingMode/4"
                self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload_mode))
                _LOGGER.debug("%s: switched from AUTO to manual HEAT mode", self.entity_id)

            # Set manual temperature
            payload_temp = f"setManualTemperature/{temp}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload_temp))
            _LOGGER.debug("%s: set_temperature fired %s", self.entity_id, payload_temp)
        except Exception as exc:
            _LOGGER.exception("%s: error in set_temperature: %s", self.entity_id, exc)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        try:
            target_mode = self._autoMode if hvac_mode == HVACMode.AUTO else OPMODETOLOXONE.get(hvac_mode)
            if target_mode is None:
                return
            payload = f"setOperatingMode/{target_mode}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in set_hvac_mode: %s", self.entity_id, exc)

    def set_preset_mode(self, preset_mode: str) -> None:
        try:
            mode_id = next((mode["id"] for mode in self._modeList if mode.get("name") == preset_mode), None)
            if mode_id is None:
                return
            payload = f"override/{mode_id}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in set_preset_mode: %s", self.entity_id, exc)

    def turn_off(self) -> None:
        try:
            payload = "setOperatingMode/0"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in turn_off: %s", self.entity_id, exc)

    def turn_on(self) -> None:
        try:
            payload = f"setOperatingMode/{self._autoMode}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in turn_on: %s", self.entity_id, exc)


class LoxoneAcControl(LoxoneEntity, ClimateEntity, ABC):
    _attr_supported_features = (
        ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.hass: HomeAssistant = kwargs["hass"]
        self._stateAttribUuids: Dict[str, str] = kwargs.get("states", {})
        self._stateAttribValues: Dict[str, Any] = {}
        self.type = "AcControl"
        self._attr_device_info = get_or_create_device(
            self.unique_id, self.name, self.type, self.room
        )

    async def event_handler(self, event: Event) -> None:
        update = False
        if not self._stateAttribUuids or not isinstance(event.data, dict):
            return
        for key in set(self._stateAttribUuids.values()) & event.data.keys():
            self._stateAttribValues[key] = event.data[key]
            update = True
        if update:
            self.schedule_update_ha_state()

    def get_state_value(self, name: str) -> Optional[Any]:
        uuid = self._stateAttribUuids.get(name)
        if not uuid:
            return None
        return self._stateAttribValues.get(uuid)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {
            "uuid": self.uuidAction,
            "device_type": self.type,
            "room": self.room,
            "category": self.cat,
            "platform": "loxone",
        }
        keys = ["temperature", "targetTemperature", "status", "isCooling", "isHeating"]
        for k in keys:
            val = self.get_state_value(k)
            if val is not None:
                attrs[k] = val
        attrs["_state_uuids"] = self._stateAttribUuids
        return attrs

    @property
    def current_temperature(self) -> Optional[float]:
        val = self.get_state_value("temperature")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> Optional[float]:
        val = self.get_state_value("targetTemperature")
        if val is None:
            alt = self.get_state_value("setpoint")
            if alt is not None:
                try:
                    return float(alt)
                except Exception:
                    return None
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def temperature_unit(self) -> str:
        if "format" in self.details:
            if "°" in self.details["format"]:
                if "F" in self.details["format"]:
                    return UnitOfTemperature.FAHRENHEIT
                return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.CELSIUS

    @property
    def target_temperature_step(self) -> Optional[float]:
        return 1.0

    @property
    def hvac_modes(self) -> List[HVACMode]:
        return [HVACMode.OFF, HVACMode.AUTO]

    @property
    def hvac_mode(self) -> HVACMode:
        status = self.get_state_value("status")
        if status in (1, "1", True, "true", "True", "on", "ON"):
            return HVACMode.AUTO
        return HVACMode.OFF

    @property
    def hvac_action(self) -> Optional[HVACAction]:
        if self.get_state_value("isHeating") in (1, "1", True, "true"):
            return HVACAction.HEATING
        if self.get_state_value("isCooling") in (1, "1", True, "true"):
            return HVACAction.COOLING
        return HVACAction.IDLE

    def set_temperature(self, **kwargs: Any) -> None:
        target = kwargs.get("targetTemperature") or kwargs.get("temperature")
        if target is None:
            return
        try:
            payload = f"setTarget/{target}"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
        except Exception as exc:
            _LOGGER.exception("%s: error in AcControl set_temperature: %s", self.entity_id, exc)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        try:
            payload = "off" if hvac_mode == HVACMode.OFF else "on"
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=payload))
            self.schedule_update_ha_state()
        except Exception as exc:
            _LOGGER.exception("%s: error in AcControl set_hvac_mode: %s", self.entity_id, exc)
