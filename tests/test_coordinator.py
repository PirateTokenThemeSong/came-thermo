"""Tests for the WebSocket push handling of the coordinator."""
from __future__ import annotations



from homeassistant.core import HomeAssistant

from custom_components.came_thermo.coordinator import CameThermoCoordinator, device_signal

KEYCODE = "0123456789ABCDEF"


def make_coordinator(hass: HomeAssistant, web_socket) -> CameThermoCoordinator:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="came_thermo", data={}, options={})
    entry.add_to_hass(hass)
    coordinator = CameThermoCoordinator(
        hass, entry, username="u", password="p", scan_interval=None
    )
    coordinator.data = {
        KEYCODE: {
            "keycode": KEYCODE,
            "temp_loc": 20.0,
            "mode": "auto",
            "bb": {"enable": False, "temp": 19},
        }
    }
    coordinator._closing = False
    coordinator._auth = web_socket
    return coordinator


async def test_push_updates_flat_value(hass: HomeAssistant) -> None:
    coordinator = make_coordinator(hass, None)
    signals: list[str] = []
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    async_dispatcher_connect(hass, device_signal(KEYCODE), lambda: signals.append("push"))

    coordinator._async_apply_push(KEYCODE, {"temp_loc": 21.4})
    await hass.async_block_till_done()

    assert coordinator.data[KEYCODE]["temp_loc"] == 21.4
    assert signals == ["push"]


async def test_push_updates_nested_value(hass: HomeAssistant) -> None:
    coordinator = make_coordinator(hass, None)
    coordinator._async_apply_push(KEYCODE, {"bb.enable": True, "bb.temp": 18.5})
    assert coordinator.data[KEYCODE]["bb"] == {"enable": True, "temp": 18.5}


async def test_push_with_no_change_does_not_notify(hass: HomeAssistant) -> None:
    coordinator = make_coordinator(hass, None)
    calls: list[str] = []
    coordinator.async_set_updated_data = lambda data: calls.append("updated")
    coordinator._async_apply_push(KEYCODE, {"temp_loc": 20.0})
    assert calls == []


async def test_push_for_unknown_device_is_ignored(hass: HomeAssistant) -> None:
    coordinator = make_coordinator(hass, None)
    coordinator._async_apply_push("UNKNOWN", {"temp_loc": 1.0})
    assert "UNKNOWN" not in coordinator.data


async def test_ws_pump_parses_json_frames(hass: HomeAssistant) -> None:
    coordinator = make_coordinator(hass, None)

    class FakeWs:
        def __init__(self, messages):
            self._messages = list(messages)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._messages:
                raise StopAsyncIteration
            return self._messages.pop(0)

    class Msg:
        def __init__(self, kind, data):
            self.type = kind
            self.data = data

    import aiohttp

    await coordinator._async_ws_pump(
        FakeWs(
            [
                Msg(aiohttp.WSMsgType.TEXT, '{"temp_loc": 22.2}'),
                Msg(aiohttp.WSMsgType.TEXT, "not json"),
                Msg(aiohttp.WSMsgType.TEXT, "[1, 2, 3]"),
                Msg(aiohttp.WSMsgType.PING, ""),
                Msg(aiohttp.WSMsgType.TEXT, '{"mode": "manual"}'),
            ]
        ),
        KEYCODE,
    )
    assert coordinator.data[KEYCODE]["temp_loc"] == 22.2
    assert coordinator.data[KEYCODE]["mode"] == "manual"
