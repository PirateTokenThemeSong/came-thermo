"""Integration-level tests: config flow, coordinator and entity creation.

The cloud is stubbed by ``FakeApi``; the tests exercise the Home Assistant glue
(setup, entity registration, service calls, teardown).
"""
from __future__ import annotations

import copy

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.came_thermo import api as came_api
from custom_components.came_thermo.const import CONF_KEYCODE, DOMAIN

KEYCODE = "0123456789ABCDEF"

DEVICE = {
    "keycode": KEYCODE,
    "name": "Bagno",
    "model": "TH/550",
    "device_id": 74619,
    "group": "TRIX HOME",
    "raw": {},
}

STATE = {
    "keycode": KEYCODE,
    "Description": "Bagno",
    "temp_loc": 20.8,
    "manual_temp": 20.0,
    "set_point_temp": 21.5,
    "mode": "auto",
    "current_season": "winter",
    "relay_status": 0,
    "antifreeze_temp": 3,
    "offset_temp": 0,
    "on_line": 1,
    "SW_vers": "1.03",
    "HW_vers": "1.00",
    "algo": {"t_diff": 0.2, "type": "diff"},
    "winter": {"T1": 20, "T2": 21, "T3": 22},
    "summer": {"T1": 24, "T2": 26, "T3": 28},
}


class FakeAuth:
    """Stand-in for ``CameThermoAuth`` that never touches the network."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def async_get_token(self, force: bool = False) -> str:
        return "fake-token"


class FakeApi:
    """Stand-in for ``CameThermoApi`` that mutates an in-memory state."""

    def __init__(self, *args, **kwargs) -> None:
        self.state = copy.deepcopy(STATE)
        self.sent: list[dict] = []

    async def async_get_devices(self):
        return [DEVICE]

    async def async_get_device(self, keycode: str):
        return copy.deepcopy(self.state)

    async def async_send_command(self, keycode: str, payload: dict):
        self.sent.append(dict(payload))
        for key, value in payload.items():
            if key == "set_manual_temp":
                self.state["manual_temp"] = value
            elif key == "set_mode":
                self.state["mode"] = value
            elif key == "current_season":
                self.state["current_season"] = value
            elif key in ("antifreeze_temp", "offset_temp"):
                self.state[key] = value
            elif key in ("winter", "summer"):
                self.state[key] = {**self.state[key], **value}
            elif key == "set_algo":
                self.state["algo"] = value
        return {"ok": True}


@pytest.fixture
def fake_api(monkeypatch):
    holder: dict[str, FakeApi] = {}

    def factory(*args, **kwargs):
        api = FakeApi()
        holder["api"] = api
        return api

    # ``config_flow`` and ``coordinator`` import the classes directly, so the
    # names have to be patched where they are used, not only in ``api``.
    import custom_components.came_thermo.config_flow as config_flow
    import custom_components.came_thermo.coordinator as coordinator

    for module in (config_flow, coordinator):
        monkeypatch.setattr(module, "CameThermoApi", factory)
        monkeypatch.setattr(module, "CameThermoAuth", FakeAuth)
    monkeypatch.setattr(came_api, "CameThermoApi", factory)
    monkeypatch.setattr(came_api, "CameThermoAuth", FakeAuth)
    return holder


@pytest.fixture
async def setup_integration(hass: HomeAssistant, fake_api) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bagno",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            CONF_KEYCODE: KEYCODE,
        },
        unique_id=KEYCODE,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_KEYCODE] == KEYCODE
    assert result["data"][CONF_USERNAME] == "user@example.com"
    assert result["title"] == "Bagno"


async def test_config_flow_unique_id(hass: HomeAssistant, fake_api) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "u", CONF_PASSWORD: "p", CONF_KEYCODE: KEYCODE},
        unique_id=KEYCODE,
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "u", CONF_PASSWORD: "p", "client_id": "test-client-id", "client_secret": "test-client-secret"},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_invalid_auth(hass: HomeAssistant, monkeypatch) -> None:
    class BadAuth:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def async_get_token(self, force: bool = False) -> str:
            raise came_api.CameAuthError("nope")

    import custom_components.came_thermo.config_flow as config_flow

    monkeypatch.setattr(config_flow, "CameThermoAuth", BadAuth)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "u", CONF_PASSWORD: "wrong", "client_id": "test-client-id", "client_secret": "test-client-secret"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_entities_are_created(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(entity_registry, setup_integration.entry_id)
    by_domain: dict[str, list[str]] = {}
    for entry in entities:
        by_domain.setdefault(entry.domain, []).append(entry.entity_id)

    assert len(by_domain["climate"]) == 1
    assert any(e.endswith("_relay_active") for e in by_domain["binary_sensor"])
    assert any(e.endswith("_online") for e in by_domain["binary_sensor"])
    assert len(by_domain["number"]) == 10
    assert len(by_domain["select"]) == 2
    assert len(by_domain["sensor"]) == 7

    state = hass.states.get(by_domain["climate"][0])
    assert state is not None
    assert state.state == "auto"
    assert state.attributes["current_temperature"] == 20.8
    assert state.attributes["target_temp_step"] == 0.1


async def test_climate_service_calls_reach_the_api(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_api
) -> None:
    api = fake_api["api"]
    climate = next(
        state.entity_id
        for state in hass.states.async_all()
        if state.domain == "climate"
    )

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate, "temperature": 22.5},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert {"set_manual_temp": 22.5} in api.sent

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "off"}, blocking=True
    )
    await hass.async_block_till_done()
    assert {"set_mode": "off"} in api.sent
    assert hass.states.get(climate).state == "off"


async def test_number_and_select_services(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_api
) -> None:
    api = fake_api["api"]
    numbers = [s.entity_id for s in hass.states.async_all() if s.domain == "number"]
    antifreeze = next(e for e in numbers if e.endswith("_antifreeze_setpoint"))
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": antifreeze, "value": 4.5},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert {"antifreeze_temp": 4.5} in api.sent
    assert hass.states.get(antifreeze).state == "4.5"

    season = next(
        s.entity_id
        for s in hass.states.async_all()
        if s.domain == "select" and s.entity_id.endswith("_season")
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": season, "option": "summer"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert {"current_season": "summer"} in api.sent


async def test_unload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.entry_id not in hass.data[DOMAIN]


DEBUG_DUMP = True
