"""Binary sensors for the CAME thermostat integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .coordinator import CameThermoCoordinator, device_signal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CameThermoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for keycode in coordinator.data:
        entities.append(CameHeatingActive(coordinator, keycode))
        entities.append(CameOnline(coordinator, keycode))
    async_add_entities(entities)


class CameThermoBinarySensor(BinarySensorEntity):
    """Base class for the thermostat binary sensors."""

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
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._state)

    @property
    def is_on(self) -> bool | None:
        raise NotImplementedError


class CameHeatingActive(CameThermoBinarySensor):
    """Whether the thermostat relay is currently demanding heat or cool."""

    _attr_translation_key = "relay_active"
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_relay"

    @property
    def is_on(self) -> bool:
        return bool(self._state.get("relay_status"))


class CameOnline(CameThermoBinarySensor):
    """Cloud connectivity of the thermostat."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_online"

    @property
    def is_on(self) -> bool:
        return bool(self._state.get("on_line"))
