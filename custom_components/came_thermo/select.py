"""Select entities exposing the raw operating mode and season of a CAME thermostat."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_MODES, ALL_SEASONS, DOMAIN, MANUFACTURER
from .coordinator import CameThermoCoordinator, device_signal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CameThermoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for keycode in coordinator.data:
        entities.append(CameModeSelect(coordinator, keycode))
        entities.append(CameSeasonSelect(coordinator, keycode))
    async_add_entities(entities)


class CameThermoSelect(SelectEntity):
    """Base class for the raw device selects."""

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


class CameModeSelect(CameThermoSelect):
    """Full operating mode: off, manual, auto, temp_man, jolly, boost, holiday."""

    _attr_translation_key = "operating_mode"
    _attr_options = list(ALL_MODES)
    _attr_icon = "mdi:thermostat"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_mode_select"

    @property
    def current_option(self) -> str | None:
        value = self._state.get("mode")
        return value if value in ALL_MODES else None

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.async_send_command(self._keycode, {"set_mode": option})
        self.async_write_ha_state()


class CameSeasonSelect(CameThermoSelect):
    """Season switch: winter, summer, antifreeze or off."""

    _attr_translation_key = "season_select"
    _attr_options = list(ALL_SEASONS)
    _attr_icon = "mdi:calendar-range"

    def __init__(self, coordinator: CameThermoCoordinator, keycode: str) -> None:
        super().__init__(coordinator, keycode)
        self._attr_unique_id = f"{DOMAIN}_{keycode}_season_select"

    @property
    def current_option(self) -> str | None:
        value = self._state.get("current_season")
        return value if value in ALL_SEASONS else None

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.async_send_command(self._keycode, {"current_season": option})
        self.async_write_ha_state()
