"""Support for Loxone TextStatus sensors."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .helpers import get_controller, get_dict_value_by_key_path
from .sensor import LoxoneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Loxone TextStatus sensors."""
    controller = get_controller(hass)
    entities = []

    # Find all TextState controls in the Loxone configuration
    for control in controller.loxconfig["controls"].values():
        if control["type"] == "TextState":
            entities.append(LoxoneTextStatus(control, controller))

    async_add_entities(entities)
    _LOGGER.info("Added %d Loxone TextStatus entities", len(entities))


class LoxoneTextStatus(LoxoneEntity, SensorEntity):
    """Representation of a Loxone TextStatus sensor."""

    def __init__(self, control, controller):
        """Initialize the TextStatus sensor."""
        super().__init__(control, controller)
        
        # Get state UUIDs from control configuration
        self._state_uuid_text = control["states"]["textAndIcon"]
        self._state_uuid_icon = control["states"].get("iconAndColor")
        
        # Initialize state attributes
        self._state = None
        self._icon = None
        self._color = None
        
        # Register state updates
        self.controller.register_listener(
            self._state_uuid_text, 
            self.async_update_text_callback
        )
        
        if self._state_uuid_icon:
            self.controller.register_listener(
                self._state_uuid_icon,
                self.async_update_icon_callback
            )

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
        """Return additional state attributes."""
        attrs = super().extra_state_attributes
        if self._color:
            attrs["color"] = self._color
        return attrs

    async def async_update_text_callback(self, event):
        """Handle text state updates."""
        if event is None:
            return
            
        new_state = self.parse_text_state(event)
        if new_state != self._state:
            self._state = new_state
            self.async_write_ha_state()

    async def async_update_icon_callback(self, event):
        """Handle icon state updates."""
        if event is None:
            return
            
        new_icon, new_color = self.parse_icon_state(event)
        if new_icon != self._icon or new_color != self._color:
            self._icon = new_icon
            self._color = new_color
            self.async_write_ha_state()

    def parse_text_state(self, data):
        """Parse the text state data."""
        if isinstance(data, dict):
            return data.get("text", "Unknown")
        elif isinstance(data, str):
            return data
        else:
            return str(data) if data is not None else "Unknown"

    def parse_icon_state(self, data):
        """Parse the icon and color data."""
        icon = "mdi:text"
        color = None
        
        if isinstance(data, dict):
            icon = data.get("icon", icon)
            color = data.get("color")
        elif isinstance(data, str) and ";" in data:
            # Format: "icon;color"
            parts = data.split(";")
            icon = parts[0] if parts[0] else icon
            color = parts[1] if len(parts) > 1 else color
            
        return icon, color

    async def async_added_to_hass(self):
        """Run when entity is about to be added to hass."""
        await super().async_added_to_hass()
        
        # Get initial state
        initial_text = self.controller.get_state_by_uuid(self._state_uuid_text)
        if initial_text is not None:
            self._state = self.parse_text_state(initial_text)
            
        if self._state_uuid_icon:
            initial_icon = self.controller.get_state_by_uuid(self._state_uuid_icon)
            if initial_icon is not None:
                self._icon, self._color = self.parse_icon_state(initial_icon)