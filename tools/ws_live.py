"""Watch a real state change flow through the live WebSocket.

    python tools/ws_live.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from aiohttp import ClientSession

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


api_mod = load("came_api", ROOT / "custom_components" / "came_thermo" / "api.py")

# Any thermostat keycode visible on the account; pass it as the first argument.
KEY = sys.argv[1] if len(sys.argv) > 1 else "0123456789ABCDEF"


class FakeCoordinator:
    def __init__(self, state: dict) -> None:
        self.data = {KEY: state}
        self.updates = 0

    def apply(self, payload: dict) -> None:
        state = self.data[KEY]
        changed = False
        for raw_key, value in payload.items():
            if "." in raw_key:
                section, _, leaf = raw_key.partition(".")
                target = state.get(section)
                if isinstance(target, dict) and target.get(leaf) != value:
                    target[leaf] = value
                    changed = True
                continue
            if state.get(raw_key) != value:
                state[raw_key] = value
                changed = True
        if changed:
            self.updates += 1
            print(f"  merged frame -> {payload} (updates={self.updates})")


async def main() -> int:
    import os

    username = os.environ.get("CAME_USERNAME")
    password = os.environ.get("CAME_PASSWORD")
    client_id = os.environ.get("CAME_CLIENT_ID")
    client_secret = os.environ.get("CAME_CLIENT_SECRET")
    if not username or not password or not client_id or not client_secret:
        print("Set CAME_USERNAME, CAME_PASSWORD, CAME_CLIENT_ID and CAME_CLIENT_SECRET.")
        return 1

    async with ClientSession() as session:
        auth = api_mod.CameThermoAuth(
            session, username, password, client_id=client_id, client_secret=client_secret
        )
        api = api_mod.CameThermoApi(session, auth)
        original = await api.async_get_device(KEY)
        coordinator = FakeCoordinator(original)
        start_temp = float(original["manual_temp"])
        start_mode = original["mode"]
        print(f"start: mode={start_mode} manual_temp={start_temp} temp_loc={original['temp_loc']}")

        token = await auth.async_get_token()
        async with session.ws_connect(
            api_mod.thermostat_ws_url(KEY),
            headers={"Sec-WebSocket-Protocol": f"bearer, {token}"},
            heartbeat=30,
        ) as ws:
            print("websocket connected")

            async def pump(seconds: float) -> None:
                try:
                    async with asyncio.timeout(seconds):
                        async for msg in ws:
                            if msg.type.name != "TEXT":
                                continue
                            coordinator.apply(json.loads(msg.data))
                except (TimeoutError, asyncio.TimeoutError):
                    pass

            reader = asyncio.create_task(pump(35))

            # Cause a real change so the device pushes a frame.
            bumped = round(start_temp + 0.5, 1)
            print(f"sending set_manual_temp={bumped}")
            await api.async_send_command(KEY, {"set_manual_temp": bumped})
            await asyncio.sleep(12)

            print(f"restoring set_manual_temp={start_temp}")
            await api.async_send_command(KEY, {"set_manual_temp": start_temp})
            await asyncio.sleep(12)

            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass

        print(f"merged updates: {coordinator.updates}")
        print(f"cache manual_temp={coordinator.data[KEY]['manual_temp']} (expected {start_temp})")

        final = await api.async_get_device(KEY)
        print(f"device mode={final['mode']} manual_temp={final['manual_temp']}")
        assert float(final["manual_temp"]) == start_temp, "restore failed"
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
