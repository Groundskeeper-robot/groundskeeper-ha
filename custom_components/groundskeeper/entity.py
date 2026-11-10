"""The base entity: one device for the robot, as the panel sees it."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
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
