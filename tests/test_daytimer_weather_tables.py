"""Binary DAYTIMER_STATES and WEATHER_STATES tables (Loxone communication spec).

Daytimer record:  uuid (16) + default value (double) + entry count (int32),
                  then per entry mode, from, to, needActivate (int32 each) + value (double).
Weather record:   uuid (16) + lastUpdate (uint32) + entry count (int32),
                  then per entry timestamp, weatherType, windDirection, solarRadiation,
                  relativeHumidity (int32 each) + temperature, perceivedTemperature, dewPoint,
                  precipitation, windSpeed, barometricPressure (double each).
All little endian. Both tables used to be ignored, so schedules and weather never arrived.
"""

from __future__ import annotations

import struct
import uuid

import pytest

from custom_components.loxone.pyloxone_api import connection
from custom_components.loxone.pyloxone_api.message import (
    DaytimerStatesTable,
    MessageType,
    WeatherStatesTable,
    parse_message,
)

UUID_A = uuid.UUID("1f13f86a-0303-a6d2-ffff-f00355b3ca63")
UUID_B = uuid.UUID("15ea0aa7-024c-3e04-ffff-b2c94f8ecfc0")


def _key(u: uuid.UUID) -> str:
    """Event key format of the integration: 8-4-4-16 hex."""
    head, tail = str(u).rsplit("-", 2)[0], "".join(str(u).rsplit("-", 2)[1:])
    return f"{head}-{tail}"


def _daytimer_record(u: uuid.UUID, default: float, entries: list[tuple[int, int, int, int, float]]) -> bytes:
    record = u.bytes_le + struct.pack("<di", default, len(entries))
    for mode, start, end, needs_activation, value in entries:
        record += struct.pack("<iiiid", mode, start, end, needs_activation, value)
    return record


def _weather_record(u: uuid.UUID, last_update: int, entries: list[tuple]) -> bytes:
    record = u.bytes_le + struct.pack("<Ii", last_update, len(entries))
    for entry in entries:
        record += struct.pack("<iiiiidddddd", *entry)
    return record


def test_daytimer_entries_are_parsed():
    body = _daytimer_record(UUID_A, 45.0, [(3, 360, 1320, 0, 60.0), (9, 480, 1380, 1, 55.5)])

    result = DaytimerStatesTable(body).as_dict()

    assert result == {
        _key(UUID_A): {
            "default_value": 45.0,
            "entries": [
                {"mode": 3, "from": 360, "to": 1320, "needs_activation": False, "value": 60.0},
                {"mode": 9, "from": 480, "to": 1380, "needs_activation": True, "value": 55.5},
            ],
        }
    }


def test_several_daytimers_in_one_table():
    body = _daytimer_record(UUID_A, 1.0, []) + _daytimer_record(UUID_B, 0.0, [(10, 0, 1440, 0, 1.0)])

    result = DaytimerStatesTable(body).as_dict()

    assert result[_key(UUID_A)] == {"default_value": 1.0, "entries": []}
    assert result[_key(UUID_B)]["entries"][0]["mode"] == 10


def test_truncated_daytimer_table_raises():
    body = _daytimer_record(UUID_A, 1.0, [(3, 0, 60, 0, 1.0)])[:-4]
    with pytest.raises(struct.error):
        DaytimerStatesTable(body).as_dict()


def test_weather_entries_are_parsed():
    entry = (536_000_000, 3, 270, 120, 81, 7.5, 5.25, 4.5, 0.2, 12.0, 1018.0)
    body = _weather_record(UUID_B, 536_000_100, [entry, (536_003_600, 10, 180, 0, 90, 6.0, 4.0, 4.4, 1.1, 8.0, 1017.0)])

    result = WeatherStatesTable(body).as_dict()[_key(UUID_B)]

    assert result["last_update"] == 536_000_100
    assert len(result["entries"]) == 2
    assert result["entries"][0] == {
        "timestamp": 536_000_000,
        "weather_type": 3,
        "wind_direction": 270,
        "solar_radiation": 120,
        "relative_humidity": 81,
        "temperature": 7.5,
        "perceived_temperature": 5.25,
        "dew_point": 4.5,
        "precipitation": 0.2,
        "wind_speed": 12.0,
        "barometric_pressure": 1018.0,
    }


def test_truncated_weather_table_raises():
    body = _weather_record(UUID_B, 1, [(1, 1, 1, 1, 1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])[:-8]
    with pytest.raises(struct.error):
        WeatherStatesTable(body).as_dict()


def test_tables_are_dispatched_by_type():
    body = _daytimer_record(UUID_A, 1.0, [])
    assert parse_message(body, MessageType.DAYTIMER_STATES).as_dict() == {
        _key(UUID_A): {"default_value": 1.0, "entries": []}
    }


def test_daytimer_and_weather_reach_the_integration_callback():
    assert MessageType.DAYTIMER_STATES in connection.CALLBACK_MESSAGE_TYPES
    assert MessageType.WEATHER_STATES in connection.CALLBACK_MESSAGE_TYPES
