"""Config flow for the CAME thermostat integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CameApiError, CameAuthError, CameThermoApi, CameThermoAuth
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DEVICE_NAME,
    CONF_KEYCODE,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate(
    hass,
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
    keycode: str | None,
) -> list[dict[str, Any]]:
    """Log in and return the thermostats on the account (or raise)."""
    session = async_get_clientsession(hass)
    auth = CameThermoAuth(
        session,
        username,
        password,
        client_id=client_id,
        client_secret=client_secret,
    )
    # Explicit login so a bad password is reported as such.
    await auth.async_get_token(force=True)
    api = CameThermoApi(session, auth)
    devices = await api.async_get_devices()
    if keycode:
        devices = [d for d in devices if d["keycode"] == keycode]
    return devices


class CameThermoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup of a CAME thermostat."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._client_id: str = ""
        self._client_secret: str = ""
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            client_id = (user_input.get(CONF_CLIENT_ID) or "").strip()
            client_secret = (user_input.get(CONF_CLIENT_SECRET) or "").strip()

            if not client_id or not client_secret:
                errors["base"] = "missing_client"
            else:
                try:
                    devices = await _async_validate(
                        self.hass, username, password, client_id, client_secret, None
                    )
                except CameAuthError as err:
                    _LOGGER.warning("CAME Connect login failed: %s", err)
                    errors["base"] = "invalid_auth"
                except (CameApiError, aiohttp.ClientError, TimeoutError):
                    _LOGGER.exception("Error contacting CAME Connect")
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error during CAME Connect setup")
                    errors["base"] = "unknown"
                else:
                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        self._username = username
                        self._password = password
                        self._client_id = client_id
                        self._client_secret = client_secret
                        self._devices = devices
                        return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    ),
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            keycode = user_input[CONF_KEYCODE]
            device = next(d for d in self._devices if d["keycode"] == keycode)
            await self.async_set_unique_id(keycode)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=device.get("name") or keycode,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_CLIENT_ID: self._client_id,
                    CONF_CLIENT_SECRET: self._client_secret,
                    CONF_KEYCODE: keycode,
                    CONF_DEVICE_NAME: device.get("name") or keycode,
                },
            )

        if len(self._devices) == 1:
            # Single thermostat: skip the extra step, but keep the flow explicit.
            device = self._devices[0]
            await self.async_set_unique_id(device["keycode"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=device.get("name") or device["keycode"],
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_CLIENT_ID: self._client_id,
                    CONF_CLIENT_SECRET: self._client_secret,
                    CONF_KEYCODE: device["keycode"],
                    CONF_DEVICE_NAME: device.get("name") or device["keycode"],
                },
            )

        options = {
            device["keycode"]: f"{device.get('name') or device['keycode']} ({device.get('model') or 'CAME'})"
            for device in self._devices
        }
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KEYCODE): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": value, "label": label}
                                for value, label in options.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CameThermoOptionsFlow()


class CameThermoOptionsFlow(OptionsFlow):
    """Options: poll interval and account credentials."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        data = self.config_entry.data

        if user_input is not None:
            username = user_input.get(CONF_USERNAME) or data[CONF_USERNAME]
            password = user_input.get(CONF_PASSWORD) or data[CONF_PASSWORD]
            client_id = (user_input.get(CONF_CLIENT_ID) or data.get(CONF_CLIENT_ID, "")).strip()
            client_secret = (user_input.get(CONF_CLIENT_SECRET) or data.get(CONF_CLIENT_SECRET, "")).strip()

            new_data = dict(data)
            changed = (
                username != data[CONF_USERNAME]
                or password != data[CONF_PASSWORD]
                or client_id != data.get(CONF_CLIENT_ID)
                or client_secret != data.get(CONF_CLIENT_SECRET)
            )

            if not client_id or not client_secret:
                errors["base"] = "missing_client"
            elif changed:
                # Credentials changed: verify them before saving anything.
                try:
                    await _async_validate(
                        self.hass,
                        username,
                        password,
                        client_id,
                        client_secret,
                        data[CONF_KEYCODE],
                    )
                except CameAuthError:
                    errors["base"] = "invalid_auth"
                except (CameApiError, aiohttp.ClientError, TimeoutError):
                    errors["base"] = "cannot_connect"
                else:
                    new_data.update(
                        {
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_CLIENT_ID: client_id,
                            CONF_CLIENT_SECRET: client_secret,
                        }
                    )
                    self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)

            if not errors:
                return self.async_create_entry(
                    title="", data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]}
                )

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current.get(CONF_SCAN_INTERVAL, 120),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=30,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                    vol.Optional(CONF_USERNAME, default=data.get(CONF_USERNAME, "")): str,
                    vol.Optional(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    ),
                    vol.Optional(
                        CONF_CLIENT_ID, default=data.get(CONF_CLIENT_ID, "")
                    ): str,
                    vol.Optional(
                        CONF_CLIENT_SECRET, default=data.get(CONF_CLIENT_SECRET, "")
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
        )
