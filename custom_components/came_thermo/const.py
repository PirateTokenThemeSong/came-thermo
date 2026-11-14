"""Constants for the CAME thermostat (CAME Connect) integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "came_thermo"
MANUFACTURER = "CAME"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONF_KEYCODE = "keycode"
CONF_DEVICE_NAME = "device_name"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=2)
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600

# Thermostat ``mode`` values used by the cloud API.
MODE_OFF = "off"
MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODE_TIMED_MANUAL = "temp_man"
MODE_JOLLY = "jolly"
MODE_BOOST = "boost"
MODE_HOLIDAY = "holiday"

ALL_MODES = (
    MODE_OFF,
    MODE_MANUAL,
    MODE_AUTO,
    MODE_TIMED_MANUAL,
    MODE_JOLLY,
    MODE_BOOST,
    MODE_HOLIDAY,
)

# ``current_season`` values. ``winter``/``summer`` are heating/cooling seasons,
# ``antifreeze`` keeps only the frost protection active and ``off`` disables the
# thermostat entirely.
SEASON_WINTER = "winter"
SEASON_SUMMER = "summer"
SEASON_ANTIFREEZE = "antifreeze"
SEASON_OFF = "off"

ALL_SEASONS = (SEASON_WINTER, SEASON_SUMMER, SEASON_ANTIFREEZE, SEASON_OFF)

# Comfort / night / frost-free temperature levels, per season.
TEMP_LEVELS = ("T1", "T2", "T3")

MIN_TEMP = 3.0
MAX_TEMP = 35.0
TEMP_STEP = 0.1
