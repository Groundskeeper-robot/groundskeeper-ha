"""Keeps the panel's summary fresh for every entity."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GroundskeeperClient, GroundskeeperError, InvalidAuth
from .const import DOMAIN, LOGGER, ROBOT_STALE_S, UPDATE_INTERVAL

type GroundskeeperConfigEntry = ConfigEntry[GroundskeeperCoordinator]


def robot_online(data: dict[str, Any] | None) -> bool:
    """The panel's own test: robot data no older than ROBOT_STALE_S."""
    age = (data or {}).get("snapshot_age_s")
    return isinstance(age, (int, float)) and age < ROBOT_STALE_S


class GroundskeeperCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls GET /api/ha/summary."""

    config_entry: GroundskeeperConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GroundskeeperConfigEntry, client: GroundskeeperClient) -> None:
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.summary()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("the panel refused the API token") from err
        except GroundskeeperError as err:
            raise UpdateFailed(f"could not read the panel: {type(err).__name__} {err}".strip()) from err
