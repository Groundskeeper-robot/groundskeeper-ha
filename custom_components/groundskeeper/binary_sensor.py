"""Binary sensors from the panel's summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GroundskeeperConfigEntry, robot_online
from .entity import GroundskeeperEntity

PARALLEL_UPDATES = 0

Data = dict[str, Any]


def _can_start(d: Data) -> bool | None:
    cs = d.get("can_start")
    return bool(cs.get("would_send")) if isinstance(cs, dict) else None


@dataclass(frozen=True, kw_only=True)
class GroundskeeperBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Data], bool | None]
    robot_value: bool = False


BINARY_SENSORS: tuple[GroundskeeperBinaryDescription, ...] = (
    GroundskeeperBinaryDescription(
        key="running",
        translation_key="running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: bool(d.get("running")),
        robot_value=True,
    ),
    GroundskeeperBinaryDescription(
        key="robot_online",
        translation_key="robot_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=robot_online,
    ),
    GroundskeeperBinaryDescription(
        key="needs_attention",
        translation_key="needs_attention",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: bool(d.get("stalled")),
    ),
    GroundskeeperBinaryDescription(
        key="lost_contact",
        translation_key="lost_contact",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: bool(d.get("lost_contact")),
    ),
    GroundskeeperBinaryDescription(
        key="rtk_wait",
        translation_key="rtk_wait",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("rtk_wait")),
    ),
    GroundskeeperBinaryDescription(
        key="can_start",
        translation_key="can_start",
        value_fn=_can_start,
    ),
    GroundskeeperBinaryDescription(
        key="local_link",
        translation_key="local_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("local_link")),
    ),
    GroundskeeperBinaryDescription(
        key="control",
        translation_key="control",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("control_enabled")),
    ),
    GroundskeeperBinaryDescription(
        key="pending_confirm",
        translation_key="pending_confirm",
        value_fn=lambda d: bool(d.get("pending_confirm")),
    ),
    GroundskeeperBinaryDescription(
        key="cloud_sign_in",
        translation_key="cloud_sign_in",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("cloud") == "needs login",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GroundskeeperConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GroundskeeperBinarySensor(coordinator, description) for description in BINARY_SENSORS)


class GroundskeeperBinarySensor(GroundskeeperEntity, BinarySensorEntity):
    entity_description: GroundskeeperBinaryDescription

    def __init__(self, coordinator, description: GroundskeeperBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self.robot_value = description.robot_value

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)
