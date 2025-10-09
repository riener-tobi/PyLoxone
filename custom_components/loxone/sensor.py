"""
Loxone Sensors

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging
import re
import json
from dataclasses import dataclass
from functools import cached_property

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (CONF_STATE_CLASS, PLATFORM_SCHEMA,
                                             SensorDeviceClass, SensorEntity,
                                             SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (CONF_DEVICE_CLASS, CONF_NAME,
                                 CONF_UNIT_OF_MEASUREMENT, CONF_VALUE_TEMPLATE,
                                 LIGHT_LUX, PERCENTAGE, STATE_UNKNOWN,
                                 UnitOfEnergy, UnitOfPower, UnitOfSpeed,
                                 UnitOfTemperature)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import CONF_ACTIONID, DOMAIN, SENDDOMAIN
from .helpers import (add_room_and_cat_to_value_values, get_all,
                      get_or_create_device)
from .miniserver import get_miniserver_from_hass

NEW_SENSOR = "sensors"

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Loxone Sensor"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACTIONID): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): cv.string,
        vol.Optional(CONF_STATE_CLASS): cv.string,
    }
)


@dataclass
class LoxoneRequiredKeysMixin:
    """Mixin for required keys."""

    loxone_format_string: str


@dataclass
class LoxoneEntityDescription(SensorEntityDescription, LoxoneRequiredKeysMixin):
    """Describes Loxone sensor entity."""


SENSOR_TYPES: tuple[LoxoneEntityDescription, ...] = (
    LoxoneEntityDescription(
        key="temperature",
        name="Temperature",
        suggested_display_precision=1,
        loxone_format_string=UnitOfTemperature.CELSIUS,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    LoxoneEntityDescription(
        key="temperature_fahrenheit",
        name="Temperature",
        suggested_display_precision=1,
        loxone_format_string=UnitOfTemperature.FAHRENHEIT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    LoxoneEntityDescription(
        key="windstrength",
        name="Wind Strength",
        suggested_display_precision=1,
        loxone_format_string=UnitOfSpeed.KILOMETERS_PER_HOUR,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.WIND_SPEED,
    ),
    LoxoneEntityDescription(
        key="kwh",
        name="Kilowatt per hour",
        suggested_display_precision=1,
        loxone_format_string=UnitOfEnergy.KILO_WATT_HOUR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    LoxoneEntityDescription(
        key="wh",
        name="Watt per hour",
        suggested_display_precision=1,
        loxone_format_string=UnitOfEnergy.WATT_HOUR,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    LoxoneEntityDescription(
        key="power",
        name="Watt",
        suggested_display_precision=1,
        loxone_format_string=UnitOfPower.WATT,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    LoxoneEntityDescription(
        key="light_level",
        name="Light Level",
        loxone_format_string=LIGHT_LUX,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ILLUMINANCE,
    ),
    LoxoneEntityDescription(
        key="humidity_or_battery",
        name="Humidity or Battery",
        suggested_display_precision=1,
        loxone_format_string=PERCENTAGE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

SENSOR_FORMATS = [desc.loxone_format_string for desc in SENSOR_TYPES]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Sensor from yaml"""
    value_template = config.get(CONF_VALUE_TEMPLATE)
    if value_template is not None:
        value_template.hass = hass

    # Devices from yaml
    if config:
        # Setup all Sensors in Yaml-File
        new_sensor = LoxoneCustomSensor(**config)
        async_add_devices([new_sensor], update_before_add=True)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    miniserver = get_miniserver_from_hass(hass)

    loxconfig = miniserver.lox_config.json
    entities = []
    if "softwareVersion" in loxconfig:
        entities.append(LoxoneVersionSensor(loxconfig["softwareVersion"]))

    for sensor in get_all(loxconfig, "InfoOnlyAnalog"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        sensor.update({"type": "analog"})
        entities.append(LoxoneSensor(**sensor))

    for sensor in get_all(loxconfig, "TextInput"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        entities.append(LoxoneTextSensor(**sensor))

    # Add TextStatus sensors
    for sensor in get_all(loxconfig, "TextState"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        entities.append(LoxoneTextStatusSensor(**sensor))

    @callback
    def async_add_sensors(_):
        async_add_entities(_, True)

    miniserver.listeners.append(
        async_dispatcher_connect(
            hass, miniserver.async_signal_new_device(NEW_SENSOR), async_add_sensors
        )
    )

    async_add_entities(entities, update_before_add=True)


class LoxoneCustomSensor(LoxoneEntity, SensorEntity):
    def __init__(self, **kwargs):
        self._attr_name = kwargs.pop("name", None)
        self._attr_state_class = kwargs.pop("state_class", None)
        self._attr_device_class = kwargs.pop("device_class", None)
        self._attr_native_unit_of_measurement = kwargs.pop("unit_of_measurement", None)
        self._attr_native_value = None  # Initialize state
        # Must be after the kwargs.pop functions!
        super().__init__(**kwargs)

    async def event_handler(self, e):
        if self.uuidAction in e.data:
            data = e.data[self.uuidAction]
            if isinstance(data, (list, dict)):
                data = str(data)
                if len(data) >= 255:
                    self._attr_native_value = data[:255]
                else:
                    self._attr_native_value = data
            else:
                self._attr_native_value = data

            self.async_schedule_update_ha_state()

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        if self._attr_native_unit_of_measurement in ["None", "none", "-"]:
            return None
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            "uuid": self.uuidAction,
            "platform": "loxone",
        }


class LoxoneVersionSensor(LoxoneEntity, SensorEntity):
    _attr_should_poll = False
    _attr_name = "Loxone Software Version"
    _attr_icon = "mdi:information-outline"
    _attr_unique_id = "loxone_software_version"

    def __init__(self, version_list, **kwargs):
        super().__init__(**kwargs)
        try:
            self._attr_native_value = ".".join([str(x) for x in version_list])
        except Exception:
            self._attr_native_value = STATE_UNKNOWN

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._attr_unique_id


class LoxoneTextSensor(LoxoneEntity, SensorEntity):
    """Representation of a Text Sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state = STATE_UNKNOWN

    async def event_handler(self, e):
        if self.states["text"] in e.data:
            self._state = str(e.data[self.states["text"]])
            self.async_schedule_update_ha_state()

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self.type

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def async_set_value(self, value):
        """Set new value."""
        self.hass.bus.async_fire(
            SENDDOMAIN, dict(uuid=self.uuidAction, value="{}".format(value))
        )
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            "uuid": self.uuidAction,
            "device_type": self.type,
            "platform": "loxone",
            "category": self.cat,
        }


class LoxoneSensor(LoxoneEntity, SensorEntity):
    """Representation of a Loxone Sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._format = self._get_format(self.details["format"])
        self._attr_should_poll = False
        self._attr_native_unit_of_measurement = self._clean_unit(self.details["format"])
        self._parent_id = kwargs.get("parent_id", None)

        if entity_description := self._get_entity_description():
            self.entity_description = entity_description

        else:
            precision = self._parse_digits_after_decimal(self.details["format"])
            if precision:
                self._attr_suggested_display_precision = precision

        _uuid = self.unique_id
        if self._parent_id:
            _uuid = self._parent_id

        self.type = "Sensor analog"
        self._attr_device_info = get_or_create_device(
            _uuid, self.name, self.type, self.room
        )

    def _parse_digits_after_decimal(self, format_string):
        """Parse digits after the decimal point from the format string."""
        pattern = r"\.(\d+)"
        match = re.search(pattern, format_string)
        if match:
            digits = int(match.group(1))
            return digits
        return None

    def _get_entity_description(self) -> SensorEntityDescription | None:
        """Return the sensor entity description."""
        if self._attr_native_unit_of_measurement in SENSOR_FORMATS:
            return SENSOR_TYPES[
                SENSOR_FORMATS.index(self._attr_native_unit_of_measurement)
            ]
        return None

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return self.state is not None

    def _get_lox_rounded_value(self, value):
        try:
            return float(self._format % float(value))
        except ValueError:
            return value

    async def event_handler(self, e):
        if self.uuidAction in e.data:
            self._attr_native_value = e.data[self.uuidAction]
            self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            "uuid": self.uuidAction,
            "device_type": self.type + "_sensor",
            "platform": "loxone",
            "category": self.cat,
        }


class LoxoneTextStatusSensor(LoxoneEntity, SensorEntity):
    """Representation of a Loxone TextStatus sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state = STATE_UNKNOWN
        self._icon = None
        self._color = None
        
        # Get state UUIDs from control configuration
        self._state_uuid_text = self.states["textAndIcon"]
        self._state_uuid_icon = self.states.get("iconAndColor")
        
        self._attr_should_poll = False
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = None

        # Device Info für bessere Integration
        self._attr_device_info = get_or_create_device(
            self.uuidAction, self.name, "TextStatus", self.room
        )

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.uuidAction

    @cached_property 
    def name(self):
        """Return the name of the sensor."""
        return self._attr_name

    async def event_handler(self, e):
        """Handle state updates for TextStatus sensor."""
        if not e.data:
            return
            
        update_required = False
        
        # Handle text state updates - direkter String
        if self._state_uuid_text in e.data:
            data = e.data[self._state_uuid_text]
            
            # Text ist ein direkter String, kein Dictionary
            if isinstance(data, str) and data != self._state:
                self._state = data
                update_required = True
        
        # Handle icon state updates - JSON String
        if self._state_uuid_icon and self._state_uuid_icon in e.data:
            data = e.data[self._state_uuid_icon]
            new_icon, new_color = self._parse_icon_state(data)
            
            if new_icon != self._icon or new_color != self._color:
                self._icon = new_icon
                self._color = new_color
                update_required = True
        
        if update_required:
            self.async_schedule_update_ha_state()

    def _parse_icon_state(self, data):
        """Parse the icon and color data from JSON string."""
        icon = "mdi:text"
        color = None

        if isinstance(data, str):
            try:
                # Entferne eventuelle doppelte Anführungszeichen
                clean_data = data.strip('"')
                parsed = json.loads(clean_data)
                
                if isinstance(parsed, dict):
                    # Extrahiere Icon und Color aus dem JSON
                    icon = parsed.get("icon", icon)
                    color = parsed.get("color")
                    
                    # Konvertiere Loxone Icons zu MDI Icons falls nötig
                    icon = self._convert_loxone_icon(icon)
                    
            except json.JSONDecodeError:
                # Fallback auf Standard-Icon bei Parse-Fehlern
                pass
        
        return icon, color

    def _convert_loxone_icon(self, loxone_icon):
        """Convert Loxone icon paths to MDI icons."""
        if not loxone_icon:
            return "mdi:text"
            
        # Entferne Pfad und behalte nur den Dateinamen ohne Extension
        icon_name = loxone_icon.split('/')[-1].replace('.svg', '').lower()
        
        # Mapping von Loxone Icons zu MDI Icons
        icon_mapping = {
            "radiator": "mdi:radiator",
            "flame": "mdi:fire",
            "snowflake": "mdi:snowflake",
            "thermometer": "mdi:thermometer",
            "water": "mdi:water",
            "fan": "mdi:fan",
            "power": "mdi:power-plug",
            "light": "mdi:lightbulb",
            "window": "mdi:window-open",
            "door": "mdi:door",
            "information": "mdi:information",
            "warning": "mdi:alert",
            "error": "mdi:alert-circle",
            "success": "mdi:check-circle",
            "settings": "mdi:cog",
        }
        
        return icon_mapping.get(icon_name, "mdi:text")

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return self._icon or "mdi:text"

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        attrs = {
            "uuid": self.uuidAction,
            "device_type": "TextStatus",
            "platform": "loxone",
            "category": self.cat,
            "room": self.room,
        }
        
        if self._color:
            attrs["color"] = self._color
            
        return attrs

    async def async_added_to_hass(self):
        """Run when entity is about to be added to hass."""
        await super().async_added_to_hass()
        
        # Initial state from miniserver
        await self._update_initial_state()

    async def _update_initial_state(self):
        """Update initial state from miniserver."""
        miniserver = get_miniserver_from_hass(self.hass)
        
        # Text state
        try:
            initial_text = miniserver.get_state_by_uuid(self._state_uuid_text)
            if initial_text is not None and isinstance(initial_text, str):
                self._state = initial_text
        except Exception:
            pass
            
        # Icon state
        if self._state_uuid_icon:
            try:
                initial_icon = miniserver.get_state_by_uuid(self._state_uuid_icon)
                if initial_icon is not None:
                    self._icon, self._color = self._parse_icon_state(initial_icon)
            except Exception:
                pass

        self.async_schedule_update_ha_state()