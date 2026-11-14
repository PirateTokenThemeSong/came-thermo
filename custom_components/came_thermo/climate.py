"""Climate entity for a CAME thermostat."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MANUFACTURER,
    MAX_TEMP,
    MIN_TEMP,
    MODE_AUTO,
    MODE_BOOST,
    MODE_HOLIDAY,
    MODE_JOLLY,
    MODE_MANUAL,
    MODE_OFF,
    MODE_TIMED_MANUAL,
    SEASON_ANTIFREEZE,
    SEASON_OFF,
    SEASON_SUMMER,
    SEASON_WINTER,
    TEMP_STEP,
)
from .coordinator import CameThermoCoordinator, device_signal
from .helpers import as_float, normalize_levels

_LOGGER = logging.getLogger(__name__)

# Cloud mode -> Home Assistant HVAC mode.
MODE_TO_HVAC: dict[str, HVACMode] = {
    MODE_OFF: HVACMode.OFF,
    MODE_MANUAL: HVACMode.HEAT,
    MODE_TIMED_MANUAL: HVACMode.HEAT,
    MODE_AUTO: HVACMode.AUTO,
    MODE_JOLLY: HVACMode.HEAT,
    MODE_BOOST: HVACMode.HEAT,
    MODE_HOLIDAY: HVACMode.OFF,
}

# The device is a single-relay thermostat: cooling only exists as a "summer"
# season on the same relay, so both seasons map onto HA HEAT/COOL.
SEASON_TO_HVAC: dict[str, HVACMode] = {
    SEASON_WINTER: HVACMode.HEAT,
    SEASON_SUMMER: HVACMode.COOL,
    SEASON_ANTIFREEZE: HVACMode.OFF,
    SEASON_OFF: HVACMode.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CameThermoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CameThermostat(coordinator, keycode) for keycode in coordinator.data
    )


class CameThermostat(ClimateEntity):
    """A CAME TH-series thermostat exposed through CAME Connect."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        self._coordinator = coordinator
        self._keycode = keycode
        self._attr_unique_id = f"{DOMAIN}_{keycode}"
        metadata = coordinator.state_for(keycode).get("_metadata", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, keycode)},
            "manufacturer": MANUFACTURER,
            "model": metadata.get("model") or "CAME thermostat",
            "name": metadata.get("name") or keycode,
            "sw_version": coordinator.state_for(keycode).get("SW_vers"),
            "hw_version": coordinator.state_for(keycode).get("HW_vers"),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, device_signal(self._keycode), self._handle_push
            )
        )

    @callback
    def _handle_push(self) -> None:
        self.async_write_ha_state()

    @property
    def _state(self) -> dict[str, Any]:
        return self._coordinator.state_for(self._keycode)

    # ------------------------------------------------------------------ state
    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._state)

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self._state
        season = state.get("current_season")
        mode = state.get("mode")

        # Season off/antifreeze overrides whatever mode the device reports.
        if season in (SEASON_OFF, SEASON_ANTIFREEZE):
            return HVACMode.OFF
        if mode == MODE_OFF:
            return HVACMode.OFF
        if mode == MODE_AUTO:
            return HVACMode.AUTO
        if mode in MODE_TO_HVAC:
            return SEASON_TO_HVAC.get(season, HVACMode.HEAT)
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        state = self._state
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if state.get("relay_status") in (1, "1", True):
            return HVACAction.COOLING if self._is_summer else HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
        if self._has_summer:
            modes.insert(1, HVACMode.COOL)
        return modes

    @property
    def _is_summer(self) -> bool:
        return self._state.get("current_season") == SEASON_SUMMER

    @property
    def _has_summer(self) -> bool:
        return isinstance(self._state.get("summer"), dict)

    @property
    def current_temperature(self) -> float | None:
        state = self._state
        # ``temp_loc`` is the room temperature measured by the thermostat and is
        # pushed live over the WebSocket.
        value = as_float(state.get("temp_loc"))
        if value is None:
            value = as_float(state.get("temperature"))
        return value

    @property
    def target_temperature(self) -> float | None:
        state = self._state
        mode = state.get("mode")
        if mode == MODE_MANUAL or mode == MODE_TIMED_MANUAL:
            return as_float(state.get("manual_temp"))
        # In auto the cloud reports the active schedule setpoint here.
        setpoint = as_float(state.get("set_point_temp"))
        if setpoint is not None and setpoint > MIN_TEMP:
            return setpoint
        return as_float(state.get("manual_temp"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        season_cfg = self._season_cfg()
        return {
            "keycode": self._keycode,
            "device_mode": state.get("mode"),
            "season": state.get("current_season"),
            "relay_active": bool(state.get("relay_status")),
            "online": bool(state.get("on_line")),
            "antifreeze_temperature": as_float(state.get("antifreeze_temp")),
            "temperature_offset": as_float(state.get("offset_temp")),
            "t1_temperature": as_float(season_cfg.get("T1")),
            "t2_temperature": as_float(season_cfg.get("T2")),
            "t3_temperature": as_float(season_cfg.get("T3")),
            "differential": (state.get("algo") or {}).get("t_diff"),
            "firmware": state.get("SW_vers"),
            "hardware": state.get("HW_vers"),
            "last_cloud_update": state.get("updatedAt"),
        }

    def _season_cfg(self) -> dict[str, Any]:
        key = "summer" if self._is_summer else "winter"
        cfg = self._state.get(key)
        return cfg if isinstance(cfg, dict) else {}

    # ----------------------------------------------------------------- control
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        season = self._state.get("current_season")

        if hvac_mode == HVACMode.OFF:
            await self._send({"set_mode": MODE_OFF})
            return

        wanted_season = SEASON_SUMMER if hvac_mode == HVACMode.COOL else SEASON_WINTER

        # Make sure the requested season is active before switching the mode.
        if season != wanted_season:
            await self._send({"current_season": wanted_season})

        # AUTO follows the seasonal schedule, HEAT/COOL drive the manual setpoint.
        await self._send({"set_mode": MODE_AUTO if hvac_mode == HVACMode.AUTO else MODE_MANUAL})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode is not None and hvac_mode != self.hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)

        temperature = kwargs.get(ATTR_TEMPERATURE)
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        # Resolve the season *after* a possible mode switch.
        season = SEASON_SUMMER if self._is_summer else SEASON_WINTER

        if low is not None or high is not None:
            # Schedule band: adjust the seasonal levels instead of the setpoint.
            cfg = dict(self._season_cfg())
            if low is not None:
                cfg["T1"] = round(float(low), 1)
            if high is not None:
                cfg["T3"] = round(float(high), 1)
            cfg = normalize_levels(cfg)
            await self._send({season: cfg})
            return

        if temperature is None:
            return

        await self._send({"set_manual_temp": round(float(temperature), 1)})
        # In auto the season schedule drives the setpoint, so switch to manual to
        # make the requested temperature take effect.
        if self._state.get("mode") in (MODE_AUTO, MODE_JOLLY, MODE_BOOST, MODE_HOLIDAY):
            await self._send({"set_mode": MODE_MANUAL})

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _send(self, payload: dict[str, Any]) -> None:
        _LOGGER.debug("Sending %s to %s", payload, self._keycode)
        await self._coordinator.async_send_command(self._keycode, payload)
        self.async_write_ha_state()
