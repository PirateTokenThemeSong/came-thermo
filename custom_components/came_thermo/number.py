"""Number entities for CAME thermostat settings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MAX_TEMP, MIN_TEMP, TEMP_STEP
from .coordinator import CameThermoCoordinator, device_signal
from .helpers import as_float, normalize_levels


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CameThermoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for keycode in coordinator.data:
        entities.append(CameManualTemperature(coordinator, keycode))
        entities.append(CameAntifreezeTemperature(coordinator, keycode))
        entities.append(CameTemperatureOffset(coordinator, keycode))
        entities.append(CameDifferential(coordinator, keycode))
        entities.append(CameSeasonLevel(coordinator, keycode, "T1", "winter_frost_free"))
        entities.append(CameSeasonLevel(coordinator, keycode, "T2", "winter_comfort"))
        entities.append(CameSeasonLevel(coordinator, keycode, "T3", "winter_economy"))
        entities.append(CameSeasonLevel(coordinator, keycode, "T1", "summer_frost_free", season="summer"))
        entities.append(CameSeasonLevel(coordinator, keycode, "T2", "summer_comfort", season="summer"))
        entities.append(CameSeasonLevel(coordinator, keycode, "T3", "summer_economy", season="summer"))
    async_add_entities(entities)


class CameThermoNumber(NumberEntity):
    """Base class for thermostat number settings."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_step = TEMP_STEP
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        self._coordinator = coordinator
        self._keycode = keycode
        metadata = coordinator.state_for(keycode).get("_metadata", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, keycode)},
            "manufacturer": MANUFACTURER,
            "model": metadata.get("model") or "CAME thermostat",
            "name": metadata.get("name") or keycode,
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

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._state)

    @property
    def native_value(self) -> float | None:
        raise NotImplementedError

    async def async_set_native_value(self, value: float) -> None:
        raise NotImplementedError

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._coordinator.async_send_command(self._keycode, payload)
        self.async_write_ha_state()


class CameManualTemperature(CameThermoNumber):
    """Manual setpoint used in manual / timed-manual modes."""

    _attr_translation_key = "manual_setpoint"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_manual_temp"

    @property
    def native_value(self) -> float | None:
        return as_float(self._state.get("manual_temp"))

    async def async_set_native_value(self, value: float) -> None:
        await self._send({"set_manual_temp": round(float(value), 1)})


class CameAntifreezeTemperature(CameThermoNumber):
    """Frost protection temperature."""

    _attr_translation_key = "antifreeze_setpoint"
    _attr_icon = "mdi:snowflake-thermometer"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_antifreeze_temp"

    @property
    def native_value(self) -> float | None:
        return as_float(self._state.get("antifreeze_temp"))

    async def async_set_native_value(self, value: float) -> None:
        await self._send({"antifreeze_temp": round(float(value), 1)})


class CameTemperatureOffset(CameThermoNumber):
    """Measured-temperature calibration offset."""

    _attr_translation_key = "temperature_offset"
    _attr_native_min_value = -5
    _attr_native_max_value = 5
    _attr_icon = "mdi:thermometer-plus"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_offset"

    @property
    def native_value(self) -> float | None:
        return as_float(self._state.get("offset_temp"))

    async def async_set_native_value(self, value: float) -> None:
        await self._send({"offset_temp": round(float(value), 1)})


class CameDifferential(CameThermoNumber):
    """Hysteresis (differential) used by the on/off algorithm."""

    _attr_translation_key = "differential"
    _attr_device_class = None
    _attr_native_unit_of_measurement = None
    _attr_native_step = 0.1
    _attr_native_min_value = 0.1
    _attr_native_max_value = 3
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_differential"

    @property
    def native_value(self) -> float | None:
        return as_float((self._state.get("algo") or {}).get("t_diff"))

    async def async_set_native_value(self, value: float) -> None:
        await self._send({"set_algo": {"t_diff": round(float(value), 1), "type": "diff"}})


class CameSeasonLevel(CameThermoNumber):
    """One of the three temperature levels of a season (winter or summer)."""

    def __init__(
        self,
        coordinator: CameThermoCoordinator,
        keycode: str,
        level: str,
        translation_key: str,
        season: str = "winter",
    ) -> None:
        super().__init__(coordinator, keycode)
        self._level = level
        self._season = season
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{keycode}_{season}_{level.lower()}"

    @property
    def _season_cfg(self) -> dict[str, Any]:
        cfg = self._state.get(self._season)
        return cfg if isinstance(cfg, dict) else {}

    @property
    def native_value(self) -> float | None:
        return as_float(self._season_cfg.get(self._level))

    async def async_set_native_value(self, value: float) -> None:
        cfg = dict(self._season_cfg)
        cfg[self._level] = round(float(value), 1)
        await self._send({self._season: normalize_levels(cfg)})
