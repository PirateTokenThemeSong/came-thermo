"""Standalone API explorer for the CAME Connect thermostat cloud.

This is the scratch tool used to reverse engineer the API; it is not needed by
the Home Assistant integration, but it is handy to verify endpoints, dump the
raw state of a thermostat or print the live WebSocket frames.

Usage (PowerShell):

    $env:CAME_USERNAME = "you@example.com"
    $env:CAME_PASSWORD = "..."
    python tools/came_probe.py devices
    python tools/came_probe.py state  <keycode>
    python tools/came_probe.py send   <keycode> '{"set_mode": "manual"}'
    python tools/came_probe.py watch  <keycode>

The OAuth client credentials can be overridden with CAME_CLIENT_ID /
CAME_CLIENT_SECRET; by default the values used by the official web app are used.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from came_thermo.api import (  # noqa: E402
    CameThermoApi,
    CameThermoAuth,
    thermostat_ws_url,
)


def _client(session: ClientSession) -> CameThermoApi:
    username = os.environ.get("CAME_USERNAME")
    password = os.environ.get("CAME_PASSWORD")
    if not username or not password:
        raise SystemExit("Set CAME_USERNAME and CAME_PASSWORD first.")
    auth = CameThermoAuth(
        session,
        username,
        password,
        client_id=os.environ.get("CAME_CLIENT_ID", ""),
        client_secret=os.environ.get("CAME_CLIENT_SECRET", ""),
    )
    return CameThermoApi(session, auth)


async def cmd_devices() -> None:
    async with ClientSession() as session:
        api = _client(session)
        for device in await api.async_get_devices():
            print(
                f"{device['keycode']}  {device['name']:<24} "
                f"{device['model']:<10} device_id={device['device_id']}"
            )


async def cmd_state(keycode: str) -> None:
    async with ClientSession() as session:
        api = _client(session)
        print(json.dumps(await api.async_get_device(keycode), indent=2))


async def cmd_send(keycode: str, payload: str) -> None:
    async with ClientSession() as session:
        api = _client(session)
        print(await api.async_send_command(keycode, json.loads(payload)))
        await asyncio.sleep(5)
        state = await api.async_get_device(keycode)
        print(json.dumps(state, indent=2))


async def cmd_watch(keycode: str, seconds: int = 120) -> None:
    async with ClientSession() as session:
        api = _client(session)
        token = await api._auth.async_get_token()  # noqa: SLF001 - explorer tool
        async with session.ws_connect(
            thermostat_ws_url(keycode), protocols=("bearer", token), heartbeat=30
        ) as ws:
            print("connected; waiting for pushes")
            try:
                async with asyncio.timeout(seconds):
                    async for msg in ws:
                        print(msg.data)
            except (TimeoutError, asyncio.TimeoutError):
                print("done")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    command, *rest = argv[1:]
    if command == "devices":
        asyncio.run(cmd_devices())
    elif command == "state" and rest:
        asyncio.run(cmd_state(rest[0]))
    elif command == "send" and len(rest) >= 2:
        asyncio.run(cmd_send(rest[0], rest[1]))
    elif command == "watch" and rest:
        asyncio.run(cmd_watch(rest[0], int(rest[1]) if len(rest) > 1 else 120))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
