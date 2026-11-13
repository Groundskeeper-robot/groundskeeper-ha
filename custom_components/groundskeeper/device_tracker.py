"""The robot on Home Assistant's map, from the panel's live feed. Off unless switched on in the options: a device
tracker's position goes into Home Assistant's history, which then holds where the robot (and so the home) is."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_TRACKER
from .coordinator import GroundskeeperConfigEntry, options
from .entity import GroundskeeperLiveEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: GroundskeeperConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.live and options(entry)[CONF_DEVICE_TRACKER]:
        async_add_entities([GroundskeeperTracker(coordinator)])


class GroundskeeperTracker(GroundskeeperLiveEntity, TrackerEntity):
    _attr_translation_key = "robot"
    _attr_source_type = SourceType.GPS
    # The state event (every 5 s) carries the position, and `position` takes a newer pose if one came: writing on
    # every pose (about one a second while mowing) would fill the recorder for little gain.
    live_events = ("state", "connection")

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "tracker")

    @property
    def available(self) -> bool:
        return super().available and self.live.position is not None

    @property
    def latitude(self) -> float | None:
        return (self.live.position or {}).get("lat")

    @property
    def longitude(self) -> float | None:
        return (self.live.position or {}).get("lon")
