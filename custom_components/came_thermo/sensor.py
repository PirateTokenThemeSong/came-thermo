"""Sensors for the CAME thermostat integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN, MANUFACTURER
from .coordinator import CameThermoCoordinator, device_signal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CameThermoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for keycode in coordinator.data:
        entities.append(CameDeviceModeSensor(coordinator, keycode))
        entities.append(CameSeasonSensor(coordinator, keycode))
        entities.append(CameSetpointSensor(coordinator, keycode))
        entities.append(CameAntifreezeSensor(coordinator, keycode))
        entities.append(CameLevelSensor(coordinator, keycode, "T1", "frost_free_temperature"))
        entities.append(CameLevelSensor(coordinator, keycode, "T2", "comfort_temperature"))
        entities.append(CameLevelSensor(coordinator, keycode, "T3", "economy_temperature"))
    async_add_entities(entities)


class CameThermoSensor(SensorEntity):
    """Base class for the thermostat sensors."""

    _attr_has_entity_name = True

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
    def _season_cfg(self) -> dict[str, Any]:
        key = "summer" if self._state.get("current_season") == "summer" else "winter"
        cfg = self._state.get(key)
        return cfg if isinstance(cfg, dict) else {}

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._state)

    @property
    def native_value(self) -> StateType:
        raise NotImplementedError


class CameDeviceModeSensor(CameThermoSensor):
    """Raw operating mode reported by the cloud API."""

    _attr_translation_key = "device_mode"
    _attr_icon = "mdi:thermostat"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_device_mode"

    @property
    def native_value(self) -> StateType:
        return self._state.get("mode")


class CameSeasonSensor(CameThermoSensor):
    """Active season: winter (heating), summer (cooling), antifreeze or off."""

    _attr_translation_key = "season"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["winter", "summer", "antifreeze", "off"]
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_season"

    @property
    def native_value(self) -> StateType:
        return self._state.get("current_season")


class CameSetpointSensor(CameThermoSensor):
    """Setpoint currently in force on the device."""

    _attr_translation_key = "active_setpoint"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_setpoint"

    @property
    def native_value(self) -> StateType:
        value = self._state.get("set_point_temp")
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        # The cloud reports the frost-protection temperature when nothing is demanded.
        return value if value > 3 else None


class CameAntifreezeSensor(CameThermoSensor):
    """Frost protection temperature."""

    _attr_translation_key = "antifreeze_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:snowflake-thermometer"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_antifreeze"

    @property
    def native_value(self) -> StateType:
        return self._state.get("antifreeze_temp")


class CameLevelSensor(CameThermoSensor):
    """Seasonal temperature level (T1/T2/T3)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: CameThermoCoordinator,
        keycode: str,
        level: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator, keycode)
        self._level = level
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{keycode}_{level.lower()}"

    @property
    def native_value(self) -> StateType:
        return self._season_cfg.get(self._level)
