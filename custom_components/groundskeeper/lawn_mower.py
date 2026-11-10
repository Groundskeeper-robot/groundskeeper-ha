"""The robot as a lawn mower entity. Read-only in this version: it offers no actions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GroundskeeperConfigEntry
from .entity import GroundskeeperEntity

PARALLEL_UPDATES = 0

# The panel's activity (app/runs.py ACTIVITY_LABELS) → Home Assistant's. "moving" is the wheels turning with no plan
# (the phone app driving, say): never shown as mowing. A charging break mid-plan is docked; the run sensor stays on.
ACTIVITY = {
    "mowing": LawnMowerActivity.MOWING,
    "transit": LawnMowerActivity.MOWING,
    "planning": LawnMowerActivity.MOWING,
    "paused": LawnMowerActivity.PAUSED,
    "returning": LawnMowerActivity.RETURNING,
    "docking": LawnMowerActivity.RETURNING,
    "docked": LawnMowerActivity.DOCKED,
    "charging_break": LawnMowerActivity.DOCKED,
    "idle": LawnMowerActivity.IDLE,
    "moving": LawnMowerActivity.IDLE,
}


def activity_from(data: dict[str, Any]) -> LawnMowerActivity | None:
    """Home Assistant's activity for the panel's summary; None when the panel cannot say yet."""
    if data.get("state") == "error" or data.get("error_code") or data.get("stalled"):
        return LawnMowerActivity.ERROR
    return ACTIVITY.get(data.get("activity") or "")


async def async_setup_entry(
    hass: HomeAssistant, entry: GroundskeeperConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([GroundskeeperMower(entry.runtime_data, "mower")])


class GroundskeeperMower(GroundskeeperEntity, LawnMowerEntity):
    """What the robot is doing, as the panel sees it."""

    _attr_name = None
    _attr_supported_features = LawnMowerEntityFeature(0)

    @property
    def activity(self) -> LawnMowerActivity | None:
        return activity_from(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        held = d.get("held") or {}
        return {
            "panel_state": d.get("state"),
            "head": d.get("head"),
            "pause_reason": d.get("pause_reason"),
            "held": held.get("title"),
            "plan_error": d.get("plan_error"),
            "error_code": d.get("error_code") or None,
            "stalled_s": d.get("stalled_s") or None,
        }
