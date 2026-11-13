"""Sensors from the panel's summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import CONF_FOLLOW_CAMERA
from .coordinator import GroundskeeperConfigEntry, options
from .entity import GroundskeeperEntity, GroundskeeperLiveEntity

PARALLEL_UPDATES = 0

Data = dict[str, Any]


def _ts(value: Any) -> datetime | None:
    return dt_util.utc_from_timestamp(value) if isinstance(value, (int, float)) else None


def _sub(data: Data, key: str) -> Data:
    v = data.get(key)
    return v if isinstance(v, dict) else {}


def start_check(data: Data) -> str | None:
    """'Ready' when a start would go out now, else the first reason the panel gives; None when it cannot say."""
    cs = data.get("can_start")
    if not isinstance(cs, dict):
        return None
    if cs.get("would_send"):
        return "Ready"
    why = cs.get("why") or []
    if why and why[0].get("title"):
        return why[0]["title"]
    return cs.get("reason") or "Can't start"


def start_check_attrs(data: Data) -> Data:
    cs = _sub(data, "can_start")
    return {
        "plan": cs.get("plan_name"),
        "reasons": [f"{w.get('title')}: {w.get('fix')}" if w.get("fix") else w.get("title") for w in cs.get("why") or []],
    }


@dataclass(frozen=True, kw_only=True)
class GroundskeeperSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Data], StateType | datetime]
    attrs_fn: Callable[[Data], Data] | None = None
    robot_value: bool = True


SENSORS: tuple[GroundskeeperSensorDescription, ...] = (
    GroundskeeperSensorDescription(
        key="state",
        translation_key="state",
        value_fn=lambda d: d.get("state"),
        robot_value=False,
    ),
    GroundskeeperSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("battery"),
    ),
    GroundskeeperSensorDescription(
        key="positioning",
        translation_key="positioning",
        value_fn=lambda d: _sub(d, "rtk").get("label"),
    ),
    GroundskeeperSensorDescription(
        key="satellites",
        translation_key="satellites",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _sub(d, "rtk").get("sats"),
    ),
    GroundskeeperSensorDescription(
        key="lawn",
        translation_key="lawn",
        value_fn=lambda d: _sub(d, "lawn").get("state"),
        attrs_fn=lambda d: {"dry_by": _ts(_sub(d, "lawn").get("dry_by"))},
        robot_value=False,
    ),
    GroundskeeperSensorDescription(
        key="head",
        translation_key="head",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("head"),
    ),
    GroundskeeperSensorDescription(
        key="run_distance",
        translation_key="run_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _sub(d, "run").get("distance_m"),
    ),
    GroundskeeperSensorDescription(
        key="last_run",
        translation_key="last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts(_sub(d, "last_run").get("ended")),
        attrs_fn=lambda d: {
            "result": _sub(d, "last_run").get("end_reason"),
            "started": _ts(_sub(d, "last_run").get("started")),
            "distance_m": _sub(d, "last_run").get("distance_m"),
            "rtk_fixed_pct": _sub(d, "last_run").get("rtk_fixed_pct"),
        },
        robot_value=False,
    ),
    GroundskeeperSensorDescription(
        key="next_window",
        translation_key="next_window",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts(_sub(d, "next_window").get("next")),
        attrs_fn=lambda d: {
            "window": _sub(d, "next_window").get("name"),
            "plan": _sub(d, "next_window").get("plan_name"),
            "active": _sub(d, "next_window").get("active"),
            "auto_start": _sub(d, "next_window").get("auto_start"),
        },
        robot_value=False,
    ),
    GroundskeeperSensorDescription(
        key="start_check",
        translation_key="start_check",
        value_fn=start_check,
        attrs_fn=start_check_attrs,
        robot_value=False,
    ),
    GroundskeeperSensorDescription(
        key="cloud",
        translation_key="cloud",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("cloud"),
        robot_value=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GroundskeeperConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GroundskeeperSensor(coordinator, description) for description in SENSORS)
    if coordinator.live and options(entry)[CONF_FOLLOW_CAMERA]:
        async_add_entities([FollowCameraSensor(coordinator)])


class GroundskeeperSensor(GroundskeeperEntity, SensorEntity):
    entity_description: GroundskeeperSensorDescription

    def __init__(self, coordinator, description: GroundskeeperSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self.robot_value = description.robot_value

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> Data | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class FollowCameraSensor(GroundskeeperLiveEntity, SensorEntity):
    """The Frigate camera whose coverage holds the robot, as the panel picks it (Live's follow-cam), by its Frigate
    name. `covered` is false when no camera's coverage holds the robot and this is the first yard camera instead."""

    _attr_translation_key = "follow_camera"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "follow_camera")

    @property
    def native_value(self) -> str | None:
        return (self.live_state.get("follow") or {}).get("camera")

    @property
    def extra_state_attributes(self) -> Data:
        return {"covered": bool((self.live_state.get("follow") or {}).get("covered"))}
