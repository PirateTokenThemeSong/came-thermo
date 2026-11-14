"""Data coordinator for the CAME thermostat integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CameApiError, CameAuthError, CameThermoApi, CameThermoAuth, thermostat_ws_url
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, DOMAIN

_LOGGER = logging.getLogger(__name__)

WS_RECONNECT_MIN = 5
WS_RECONNECT_MAX = 300
WS_KEEPALIVE = 30


def device_signal(keycode: str) -> str:
    """Dispatcher signal fired when a thermostat pushes new state."""
    return f"{DOMAIN}_update_{keycode}"


class CameThermoCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll and stream the state of every thermostat on the account."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        username: str,
        password: str,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
            config_entry=entry,
        )
        self._session = async_get_clientsession(hass)
        self._auth = CameThermoAuth(
            self._session,
            username,
            password,
            client_id=entry.data.get(CONF_CLIENT_ID) or entry.options.get(CONF_CLIENT_ID, ""),
            client_secret=entry.data.get(CONF_CLIENT_SECRET)
            or entry.options.get(CONF_CLIENT_SECRET, ""),
        )
        self.api = CameThermoApi(self._session, self._auth)
        self._ws_tasks: dict[str, asyncio.Task] = {}
        self._ws_backoff: dict[str, int] = {}
        self._closing = False

    # ------------------------------------------------------------------ polling
    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            async with asyncio.timeout(30):
                data = await self._async_fetch_all()
        except CameAuthError as err:
            raise UpdateFailed(f"authentication failed: {err}") from err
        except (CameApiError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"error talking to CAME Connect: {err}") from err
        # Devices can go offline and come back; keep the listeners in sync.
        self._async_sync_streams()
        return data

    async def _async_fetch_all(self) -> dict[str, dict[str, Any]]:
        devices = await self.api.async_get_devices()
        states: dict[str, dict[str, Any]] = {}
        for device in devices:
            keycode = device["keycode"]
            try:
                state = await self.api.async_get_device(keycode)
            except CameApiError as err:
                _LOGGER.debug("Cannot read thermostat %s: %s", keycode, err)
                continue
            state.setdefault("keycode", keycode)
            state["_metadata"] = device
            states[keycode] = state
        return states

    async def async_refresh_device(self, keycode: str) -> None:
        """Refresh a single thermostat right after a command."""
        try:
            async with asyncio.timeout(20):
                state = await self.api.async_get_device(keycode)
        except (CameApiError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Refresh of %s failed: %s", keycode, err)
            return
        state.setdefault("keycode", keycode)
        state["_metadata"] = self.data.get(keycode, {}).get("_metadata", {})
        new_data = dict(self.data)
        new_data[keycode] = state
        self.async_set_updated_data(new_data)

    async def async_send_command(self, keycode: str, payload: dict[str, Any]) -> None:
        """Send a state patch and refresh the device afterwards."""
        await self.api.async_send_command(keycode, payload)
        # The cloud needs a moment before the new value shows up in a read.
        await asyncio.sleep(2)
        await self.async_refresh_device(keycode)

    # -------------------------------------------------------------- websockets
    @callback
    def async_start_streams(self) -> None:
        """Start one WebSocket listener per thermostat for live updates."""
        for keycode, state in self.data.items():
            if not state.get("on_line", 1):
                continue
            self._async_ensure_stream(keycode)

    @callback
    def _async_ensure_stream(self, keycode: str) -> None:
        """Start the listener for ``keycode`` unless one is already running."""
        if self._closing:
            return
        task = self._ws_tasks.get(keycode)
        if task is not None and not task.done():
            return
        self._ws_backoff.setdefault(keycode, WS_RECONNECT_MIN)
        self._ws_tasks[keycode] = self.hass.async_create_background_task(
            self._async_ws_loop(keycode),
            name=f"{DOMAIN}_ws_{keycode}",
        )

    @callback
    def _async_sync_streams(self) -> None:
        """Reconcile listeners after a poll (device may have come back online)."""
        for keycode, state in (self.data or {}).items():
            if state.get("on_line", 1):
                self._async_ensure_stream(keycode)
            else:
                task = self._ws_tasks.pop(keycode, None)
                if task is not None and not task.done():
                    task.cancel()

    async def async_stop_streams(self) -> None:
        self._closing = True
        tasks = [task for task in self._ws_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ws_tasks.clear()

    async def _async_ws_loop(self, keycode: str) -> None:
        """Keep a WebSocket open and merge every push into the coordinator data."""
        while not self._closing:
            try:
                token = await self._auth.async_get_token()
                # NOTE: aiohttp's ``protocols=`` sends a handshake the CAME server
                # rejects with HTTP 502. Setting the header manually works and the
                # server echoes the first token ("bearer") back.
                async with self._session.ws_connect(
                    thermostat_ws_url(keycode),
                    headers={"Sec-WebSocket-Protocol": f"bearer, {token}"},
                    heartbeat=WS_KEEPALIVE,
                    timeout=15,
                ) as ws:
                    _LOGGER.debug("WebSocket connected for %s", keycode)
                    self._ws_backoff[keycode] = WS_RECONNECT_MIN
                    await self._async_ws_pump(ws, keycode)
            except CameAuthError as err:
                _LOGGER.warning("WebSocket auth failed for %s: %s", keycode, err)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("WebSocket for %s dropped: %s", keycode, err)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the listener alive
                _LOGGER.exception("Unexpected WebSocket error for %s", keycode)

            if self._closing:
                return
            delay = self._ws_backoff.get(keycode, WS_RECONNECT_MIN)
            self._ws_backoff[keycode] = min(delay * 2, WS_RECONNECT_MAX)
            await asyncio.sleep(delay)

    async def _async_ws_pump(self, ws: aiohttp.ClientWebSocketResponse, keycode: str) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            self._async_apply_push(keycode, payload)

    @callback
    def _async_apply_push(self, keycode: str, payload: dict[str, Any]) -> None:
        """Merge a WebSocket frame into the cached state."""
        state = self.data.get(keycode)
        if not state:
            return

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
            state["updatedAt"] = dt_util.utcnow().isoformat()
            self.async_set_updated_data(dict(self.data))
            async_dispatcher_send(self.hass, device_signal(keycode))

    # ------------------------------------------------------------------ helpers
    def state_for(self, keycode: str) -> dict[str, Any]:
        return self.data.get(keycode, {})
