"""Loxone weather service as a Home Assistant weather entity."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.weather import WeatherEntityFeature
from homeassistant.util import dt as dt_util

from custom_components.loxone import weather as weather_module
from custom_components.loxone.const import LOXONE_PLATFORMS, Platform
from custom_components.loxone.weather import LoxoneWeather, condition_for, loxone_time

STRUCTURE = {
    "msInfo": {"tempUnit": 0},
    "rooms": {},
    "cats": {},
    "controls": {},
    "weatherServer": {"states": {"actual": "w-actual", "forecast": "w-forecast"}},
}


def _entry(timestamp, weather_type=3, temperature=7.5, solar_radiation=120):
    return {
        "timestamp": timestamp,
        "weather_type": weather_type,
        "wind_direction": 270,
        "solar_radiation": solar_radiation,
        "relative_humidity": 81,
        "temperature": temperature,
        "perceived_temperature": 5.0,
        "dew_point": 4.5,
        "precipitation": 0.2,
        "wind_speed": 12.0,
        "barometric_pressure": 1018.0,
    }


def _weather(platform_entities) -> LoxoneWeather:
    (entity,) = platform_entities(weather_module, STRUCTURE)
    entity.async_schedule_update_ha_state = lambda *_: None
    entity.async_update_listeners = lambda *_: asyncio.sleep(0)
    return entity


def _feed(entity, data):
    asyncio.run(entity.event_handler(SimpleNamespace(data=data)))


def test_weather_platform_is_forwarded():
    assert Platform.WEATHER in LOXONE_PLATFORMS


def test_no_weather_service_no_entity(platform_entities):
    assert platform_entities(weather_module, {**STRUCTURE, "weatherServer": {}}) == []


def test_current_conditions(platform_entities):
    weather = _weather(platform_entities)
    assert weather.unique_id == "w-actual"
    assert weather.condition is None  # nothing received yet

    _feed(weather, {"w-actual": {"last_update": 1, "entries": [_entry(536_000_000)]}})

    assert weather.condition == "partlycloudy"
    assert weather.native_temperature == 7.5
    assert weather.native_apparent_temperature == 5.0
    assert weather.native_dew_point == 4.5
    assert weather.humidity == 81
    assert weather.native_pressure == 1018.0
    assert weather.native_wind_speed == 12.0
    assert weather.wind_bearing == 270
    assert weather.native_temperature_unit == "°C"


def test_hourly_forecast(platform_entities):
    weather = _weather(platform_entities)
    assert weather.supported_features & WeatherEntityFeature.FORECAST_HOURLY

    _feed(weather, {"w-forecast": {"last_update": 1, "entries": [_entry(536_000_000), _entry(536_003_600, 11, 6.0)]}})
    forecast = asyncio.run(weather.async_forecast_hourly())

    assert [f["condition"] for f in forecast] == ["partlycloudy", "rainy"]
    assert forecast[1]["native_temperature"] == 6.0
    assert forecast[1]["datetime"] == loxone_time(536_003_600).isoformat()


@pytest.mark.parametrize(
    ("weather_type", "solar_radiation", "expected"),
    [
        (1, 300, "sunny"),
        (1, 0, "clear-night"),
        (5, 0, "cloudy"),
        (6, 0, "fog"),
        (12, 0, "pouring"),
        (18, 0, "lightning-rainy"),
        (21, 0, "snowy"),
        (26, 0, "snowy-rainy"),
        (8, 0, None),
        (99, 0, None),
    ],
)
def test_condition_mapping(weather_type, solar_radiation, expected):
    assert condition_for(weather_type, solar_radiation) == expected


def test_loxone_time_counts_from_2009_local_time():
    start = loxone_time(0)
    assert (start.year, start.month, start.day, start.hour) == (2009, 1, 1, 0)
    assert start.tzinfo is not None
    assert loxone_time(3600) - start == timedelta(hours=1)
    assert isinstance(loxone_time(0), datetime)
    assert start.utcoffset() == dt_util.get_default_time_zone().utcoffset(start.replace(tzinfo=None))


def test_unavailable_until_the_weather_service_sends_data(platform_entities):
    """Without an active Loxone weather service the Miniserver never sends weather tables."""
    weather = _weather(platform_entities)
    assert weather.available is False

    _feed(weather, {"w-actual": {"last_update": 1, "entries": [_entry(536_000_000)]}})

    assert weather.available is True
