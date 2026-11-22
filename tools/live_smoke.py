"""Exercise the real API + coordinator plumbing against the live cloud.

This bypasses Home Assistant's test harness socket blocking and drives the same
objects the integration uses at runtime:

    $env:CAME_USERNAME="you@example.com"; $env:CAME_PASSWORD="..."
    python tools/live_smoke.py [keycode]
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

from aiohttp import ClientSession


def _load_api():
    """Import ``api.py`` directly, bypassing the Home Assistant package glue."""
    path = Path(__file__).resolve().parents[1] / "custom_components" / "came_thermo" / "api.py"
    spec = importlib.util.spec_from_file_location("came_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


_api = _load_api()
CameThermoApi = _api.CameThermoApi
CameThermoAuth = _api.CameThermoAuth
thermostat_ws_url = _api.thermostat_ws_url


async def main() -> int:
    username = os.environ.get("CAME_USERNAME")
    password = os.environ.get("CAME_PASSWORD")
    client_id = os.environ.get("CAME_CLIENT_ID")
    client_secret = os.environ.get("CAME_CLIENT_SECRET")
    if not username or not password:
        print("Set CAME_USERNAME and CAME_PASSWORD.")
        return 1
    if not client_id or not client_secret:
        print(
            "Set CAME_CLIENT_ID and CAME_CLIENT_SECRET too "
            "(see docs/getting-started.md)."
        )
        return 1

    async with ClientSession() as session:
        auth = CameThermoAuth(
            session, username, password, client_id=client_id, client_secret=client_secret
        )
        api = CameThermoApi(session, auth)

        token = await auth.async_get_token()
        print(f"login ok, token length {len(token)}")

        devices = await api.async_get_devices()
        print(f"devices: {[(d['keycode'], d['name'], d['model']) for d in devices]}")
        if not devices:
            return 1

        keycode = sys.argv[1] if len(sys.argv) > 1 else devices[0]["keycode"]
        state = await api.async_get_device(keycode)
        interesting = (
            "mode set_mode current_season manual_temp temp_loc set_point_temp "
            "relay_status on_line antifreeze_temp offset_temp algo"
        ).split()
        print(json.dumps({k: state.get(k) for k in interesting}, indent=1))

        # Read one WebSocket frame to prove the live channel works.
        token = await auth.async_get_token()
        async with session.ws_connect(
            thermostat_ws_url(keycode), protocols=("bearer", token), heartbeat=30
        ) as ws:
            print("websocket connected, protocol:", ws.protocol)
            try:
                async with asyncio.timeout(20):
                    async for msg in ws:
                        print("push frame:", msg.data)
                        break
            except (TimeoutError, asyncio.TimeoutError):
                print("no push within 20s (device only pushes on change)")

        # A round-trip write: set the manual setpoint to its current value.
        current = float(state["manual_temp"])
        await api.async_send_command(keycode, {"set_manual_temp": current})
        await asyncio.sleep(4)
        after = await api.async_get_device(keycode)
        print("setpoint write ok:", after["manual_temp"] == current, after["manual_temp"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
