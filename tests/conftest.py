"""Shared pytest fixtures for the CAME thermostat integration tests."""
from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

# The Home Assistant test plugin blocks ``socket.socket`` to catch accidental
# network calls. On Windows its event-loop policy needs a socket pair even for
# tests that never reach the network (everything here is stubbed), so the guard
# is neutralised for this test suite only. Real API behaviour is covered by
# ``tools/live_smoke.py``.
import pytest_socket  # noqa: E402

pytest_socket.disable_socket = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
async def plain_aiohttp_session(monkeypatch):
    """Keep Home Assistant's aiodns session out of the way.

    HA's shared session uses ``AsyncResolver``, which refuses to run on the
    Windows proactor loop. Nothing in these tests reaches the network (the cloud
    and the WebSocket are stubbed), so ``async_get_clientsession`` only has to
    return something inert.
    """
    import custom_components.came_thermo.config_flow as config_flow
    import custom_components.came_thermo.coordinator as coordinator

    class InertSession:
        async def ws_connect(self, *args, **kwargs):  # pragma: no cover - never used
            raise AssertionError("network access in a unit test")

        async def close(self) -> None:
            return None

    for module in (config_flow, coordinator):
        monkeypatch.setattr(module, "async_get_clientsession", lambda hass: InertSession())

    # The WebSocket listener is exercised by ``test_ws_push_merging``; keeping it
    # out of the HA glue tests avoids background tasks outliving the test.
    monkeypatch.setattr(
        coordinator.CameThermoCoordinator, "async_start_streams", lambda self: None
    )
    monkeypatch.setattr(
        coordinator.CameThermoCoordinator, "async_stop_streams", _async_noop
    )
    yield


async def _async_noop(*args, **kwargs) -> None:
    return None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration in every test."""
    yield
