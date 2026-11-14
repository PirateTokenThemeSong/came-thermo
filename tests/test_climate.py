"""Tests for the pure helpers of the CAME thermostat integration."""
from __future__ import annotations

import base64
import hashlib

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from came_thermo.climate import (  # noqa: E402
    MODE_TO_HVAC,
    SEASON_TO_HVAC,
    CameThermostat,
)
from came_thermo.const import (  # noqa: E402
    ALL_MODES,
    ALL_SEASONS,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_OFF,
    SEASON_ANTIFREEZE,
    SEASON_OFF,
    SEASON_SUMMER,
    SEASON_WINTER,
)


class FakeCoordinator:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.last_update_success = True
        self.sent: list[dict] = []

    def state_for(self, keycode: str) -> dict:
        return self._state

    @property
    def data(self) -> dict:
        return {"k": self._state}

    async def async_send_command(self, keycode: str, payload: dict) -> None:
        self.sent.append(payload)


def make_entity(state: dict) -> CameThermostat:
    entity = CameThermostat.__new__(CameThermostat)
    entity._coordinator = FakeCoordinator(state)
    entity._keycode = "k"
    # ``async_write_ha_state`` needs a running hass; the tests only care about
    # the payloads being sent.
    entity.async_write_ha_state = lambda *args, **kwargs: None
    return entity


BASE_STATE = {
    "keycode": "k",
    "temp_loc": 20.8,
    "manual_temp": 20.0,
    "set_point_temp": 21.5,
    "mode": "auto",
    "current_season": "winter",
    "relay_status": 1,
    "antifreeze_temp": 3,
    "offset_temp": 0,
    "on_line": 1,
    "algo": {"t_diff": 0.2, "type": "diff"},
    "winter": {"T1": 20, "T2": 21, "T3": 22},
    "summer": {"T1": 24, "T2": 26, "T3": 28},
}


def state(**overrides) -> dict:
    data = dict(BASE_STATE)
    data.update(overrides)
    return data


def test_mode_tables_cover_all_values() -> None:
    assert set(MODE_TO_HVAC) == set(ALL_MODES)
    assert set(SEASON_TO_HVAC) == set(ALL_SEASONS)


def test_current_temperature_uses_temp_loc() -> None:
    assert make_entity(state()).current_temperature == 20.8


@pytest.mark.parametrize(
    ("overrides", "expected_mode"),
    [
        ({"current_season": SEASON_WINTER, "mode": MODE_AUTO}, "auto"),
        ({"current_season": SEASON_WINTER, "mode": MODE_MANUAL}, "heat"),
        ({"current_season": SEASON_SUMMER, "mode": MODE_MANUAL}, "cool"),
        ({"current_season": SEASON_WINTER, "mode": MODE_OFF}, "off"),
        ({"current_season": SEASON_OFF, "mode": MODE_AUTO}, "off"),
        ({"current_season": SEASON_ANTIFREEZE, "mode": MODE_AUTO}, "off"),
    ],
)
def test_hvac_mode_mapping(overrides, expected_mode) -> None:
    assert make_entity(state(**overrides)).hvac_mode == expected_mode


@pytest.mark.parametrize(
    ("overrides", "expected_action"),
    [
        ({"mode": MODE_OFF}, "off"),
        ({"current_season": SEASON_WINTER, "relay_status": 1}, "heating"),
        ({"current_season": SEASON_SUMMER, "relay_status": 1}, "cooling"),
        ({"current_season": SEASON_WINTER, "relay_status": 0}, "idle"),
    ],
)
def test_hvac_action(overrides, expected_action) -> None:
    assert make_entity(state(**overrides)).hvac_action == expected_action


def test_target_temperature_prefers_manual_in_manual_mode() -> None:
    assert make_entity(state(mode=MODE_MANUAL)).target_temperature == 20.0
    assert make_entity(state(mode=MODE_AUTO)).target_temperature == 21.5


def test_target_temperature_masks_frost_value() -> None:
    entity = make_entity(state(mode=MODE_AUTO, set_point_temp=3, manual_temp=19.5))
    assert entity.target_temperature == 19.5


def test_season_levels_follow_active_season() -> None:
    winter = make_entity(state(mode=MODE_AUTO, current_season=SEASON_WINTER))
    assert winter._season_cfg() == {"T1": 20, "T2": 21, "T3": 22}
    summer = make_entity(state(mode=MODE_AUTO, current_season=SEASON_SUMMER))
    assert summer._season_cfg() == {"T1": 24, "T2": 26, "T3": 28}


def test_extra_attributes_expose_season_levels() -> None:
    attrs = make_entity(state()).extra_state_attributes
    assert attrs["season"] == "winter"
    assert attrs["t1_temperature"] == 20.0
    assert attrs["t3_temperature"] == 22.0
    assert attrs["differential"] == 0.2


@pytest.mark.asyncio
async def test_set_temperature_in_auto_switches_to_manual() -> None:
    entity = make_entity(state(mode=MODE_AUTO))
    await entity.async_set_temperature(temperature=22.5)
    assert entity._coordinator.sent == [
        {"set_manual_temp": 22.5},
        {"set_mode": MODE_MANUAL},
    ]


@pytest.mark.asyncio
async def test_set_temperature_in_manual_only_sends_setpoint() -> None:
    entity = make_entity(state(mode=MODE_MANUAL))
    await entity.async_set_temperature(temperature=21.0)
    assert entity._coordinator.sent == [{"set_manual_temp": 21.0}]


@pytest.mark.asyncio
async def test_auto_band_write_updates_levels_and_keeps_order() -> None:
    entity = make_entity(state(mode=MODE_AUTO, current_season=SEASON_WINTER))
    await entity.async_set_temperature(target_temp_low=22.5, target_temp_high=23.0)
    cfg = entity._coordinator.sent[0]["winter"]
    assert cfg["T1"] == 22.5
    assert cfg["T3"] == 23.0
    # T2 is pushed into the new band so the firmware does not reject the ordering.
    assert cfg["T1"] <= cfg["T2"] <= cfg["T3"]


@pytest.mark.asyncio
async def test_hvac_off_only_sends_mode() -> None:
    entity = make_entity(state())
    await entity.async_set_hvac_mode("off")
    assert entity._coordinator.sent == [{"set_mode": MODE_OFF}]


@pytest.mark.asyncio
async def test_cool_switches_season_and_uses_manual() -> None:
    entity = make_entity(state(current_season=SEASON_WINTER, mode=MODE_AUTO))
    await entity.async_set_hvac_mode("cool")
    assert entity._coordinator.sent == [
        {"current_season": SEASON_SUMMER},
        {"set_mode": MODE_MANUAL},
    ]


@pytest.mark.asyncio
async def test_heat_switches_back_to_winter() -> None:
    entity = make_entity(state(current_season=SEASON_SUMMER, mode=MODE_AUTO))
    await entity.async_set_hvac_mode("heat")
    assert entity._coordinator.sent == [
        {"current_season": SEASON_WINTER},
        {"set_mode": MODE_MANUAL},
    ]


@pytest.mark.asyncio
async def test_auto_keeps_season_and_sets_auto_mode() -> None:
    entity = make_entity(state(current_season=SEASON_WINTER, mode=MODE_MANUAL))
    await entity.async_set_hvac_mode("auto")
    assert entity._coordinator.sent == [{"set_mode": MODE_AUTO}]


@pytest.mark.asyncio
async def test_hvac_mode_wakes_device_from_season_off() -> None:
    entity = make_entity(state(current_season=SEASON_OFF, mode=MODE_OFF))
    await entity.async_set_hvac_mode("heat")
    assert entity._coordinator.sent == [
        {"current_season": SEASON_WINTER},
        {"set_mode": MODE_MANUAL},
    ]


def test_pkce_challenge_is_s256_of_verifier() -> None:

    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert expected == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_ws_url_uses_keycode_twice() -> None:
    from came_thermo.api import thermostat_ws_url

    assert (
        thermostat_ws_url("ABC")
        == "wss://thermo.cameconnect.net:8443/WebSocket/ABC/ABC"
    )
