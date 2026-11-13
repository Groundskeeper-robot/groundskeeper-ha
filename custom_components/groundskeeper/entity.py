"""The base entity: one device for the robot, as the panel sees it."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GroundskeeperCoordinator, robot_online


class GroundskeeperEntity(CoordinatorEntity[GroundskeeperCoordinator]):
    """An entity fed by the panel's summary."""

    _attr_has_entity_name = True
    # True for values that come from the robot itself: they show unavailable once the robot stops reporting, so a
    # stale battery or state never reads as current.
    robot_value = True

    def __init__(self, coordinator: GroundskeeperCoordinator, key: str) -> None:
        super().__init__(coordinator)
        rid = coordinator.config_entry.unique_id
        self._attr_unique_id = f"{rid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, rid)},
            name="Groundskeeper",
            manufacturer="Yarbo",
            model="Yarbo robot",
            sw_version=coordinator.data.get("version"),
            configuration_url=coordinator.client.url,
        )

    @property
    def available(self) -> bool:
        return super().available and (not self.robot_value or robot_online(self.coordinator.data))


class GroundskeeperLiveEntity(GroundskeeperEntity):
    """An entity fed by the panel's live feed as well: it updates on each event named in `live_events`."""

    robot_value = False
    live_events: tuple[str, ...] = ("state", "connection", "stale")

    @property
    def live(self):
        return self.coordinator.live

    @property
    def live_state(self) -> dict[str, Any]:
        return (self.live.state if self.live else None) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.live and self.live.fresh)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.live:
            self.async_on_remove(async_dispatcher_connect(self.hass, self.live.signal, self._on_live))

    @callback
    def _on_live(self, event: str, data: Any) -> None:
        if event in self.live_events:
            self.async_write_ha_state()
