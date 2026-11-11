"""
Loxone weather service (``weatherServer`` in the structure file) as a weather entity.

Current conditions and the hourly forecast arrive in binary WEATHER_STATES tables
(see ``pyloxone_api.message.WeatherStatesTable``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfLength, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.util import dt as dt_util

from . import LoxoneEntity
from .miniserver import get_miniserver_from_hass

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

LOXONE_EPOCH = datetime(2009, 1, 1)  # noqa: DTZ001 - Loxone time counts local wall-clock seconds from here

# Loxone weatherType (structure file "weatherTypeTexts") -> HA condition. 8 and 9 are unused.
WEATHER_CONDITIONS: dict[int, str] = {
    1: "sunny", 2: "partlycloudy", 3: "partlycloudy", 4: "cloudy", 5: "cloudy",
    6: "fog", 7: "fog",
    10: "rainy", 11: "rainy", 12: "pouring", 13: "rainy",
    14: "snowy-rainy", 15: "snowy-rainy", 16: "rainy", 17: "pouring",
    18: "lightning-rainy", 19: "lightning-rainy",
    20: "snowy", 21: "snowy", 22: "snowy", 23: "snowy", 24: "snowy",
    25: "snowy-rainy", 26: "snowy-rainy", 27: "snowy-rainy", 28: "snowy-rainy", 29: "snowy-rainy",
}


def condition_for(weather_type: int, solar_radiation: int) -> str | None:
    """Return the HA condition; a clear sky without solar radiation is a clear night."""
    condition = WEATHER_CONDITIONS.get(weather_type)
    if condition == "sunny" and solar_radiation <= 0:
        return "clear-night"
    return condition


def loxone_time(seconds: int) -> datetime:
    """Convert Loxone time (seconds since 2009-01-01, local wall clock) to an aware datetime."""
    return (LOXONE_EPOCH + timedelta(seconds=seconds)).replace(tzinfo=dt_util.get_default_time_zone())


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the weather entity if the Miniserver has the weather service."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    states = loxconfig.get("weatherServer", {}).get("states", {})
    if not states.get("actual"):
        return
    fahrenheit = loxconfig.get("msInfo", {}).get("tempUnit") == 1
    async_add_entities([LoxoneWeather(states["actual"], states.get("forecast"), fahrenheit=fahrenheit)])


class LoxoneWeather(LoxoneEntity, WeatherEntity):
    """Current weather and hourly forecast of the Loxone weather service."""

    _attr_should_poll = False
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfLength.MILLIMETERS

    def __init__(self, actual_uuid: str, forecast_uuid: str | None, *, fahrenheit: bool) -> None:
        """Initialize for the weather service's actual / forecast states."""
        super().__init__(name="Loxone Weather", uuidAction=actual_uuid)
        self._forecast_uuid = forecast_uuid
        self._attr_native_temperature_unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
        self._current: dict[str, Any] | None = None
        self._forecast: list[dict[str, Any]] = []

    @property
    def available(self) -> bool:
        """
        Return False until the weather service sent current data, and while disconnected.

        Without an active Loxone weather service the Miniserver never sends weather tables.
        """
        return self._current is not None and self._connection_available()

    def _now(self, key: str) -> Any:
        return self._current[key] if self._current else None

    @property
    def condition(self) -> str | None:
        """Return the current condition."""
        if not self._current:
            return None
        return condition_for(self._current["weather_type"], self._current["solar_radiation"])

    @property
    def native_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._now("temperature")

    @property
    def native_apparent_temperature(self) -> float | None:
        """Return the perceived temperature."""
        return self._now("perceived_temperature")

    @property
    def native_dew_point(self) -> float | None:
        """Return the dew point."""
        return self._now("dew_point")

    @property
    def humidity(self) -> float | None:
        """Return the relative humidity."""
        return self._now("relative_humidity")

    @property
    def native_pressure(self) -> float | None:
        """Return the barometric pressure."""
        return self._now("barometric_pressure")

    @property
    def native_wind_speed(self) -> float | None:
        """Return the wind speed."""
        return self._now("wind_speed")

    @property
    def wind_bearing(self) -> float | None:
        """Return the wind direction in degrees."""
        return self._now("wind_direction")

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the hourly forecast."""
        return [
            Forecast(
                datetime=loxone_time(entry["timestamp"]).isoformat(),
                condition=condition_for(entry["weather_type"], entry["solar_radiation"]),
                native_temperature=entry["temperature"],
                native_apparent_temperature=entry["perceived_temperature"],
                native_dew_point=entry["dew_point"],
                humidity=entry["relative_humidity"],
                native_precipitation=entry["precipitation"],
                native_wind_speed=entry["wind_speed"],
                wind_bearing=entry["wind_direction"],
                native_pressure=entry["barometric_pressure"],
            )
            for entry in self._forecast
        ]

    async def event_handler(self, e: Event) -> None:
        """Apply current weather and forecast tables."""
        if self.uuidAction in e.data:
            entries = self._entries_of(e.data[self.uuidAction])
            if entries:
                self._current = entries[0]
                self.async_schedule_update_ha_state()
        if self._forecast_uuid and self._forecast_uuid in e.data:
            entries = self._entries_of(e.data[self._forecast_uuid])
            if entries is not None:
                self._forecast = entries
                await self.async_update_listeners(("hourly",))

    def _entries_of(self, table: Any) -> list[dict[str, Any]] | None:
        if isinstance(table, dict) and isinstance(table.get("entries"), list):
            return table["entries"]
        _LOGGER.warning("Unexpected weather data: %r", table)
        return None
