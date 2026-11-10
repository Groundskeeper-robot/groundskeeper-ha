"""Groundskeeper: your Yarbo in Home Assistant, through your Groundskeeper panel.

This first version reads only: it sends nothing to the robot.
"""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GroundskeeperClient
from .coordinator import GroundskeeperConfigEntry, GroundskeeperCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.LAWN_MOWER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> bool:
    """Connect to the panel and set up the entities."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = GroundskeeperClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    coordinator = GroundskeeperCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> bool:
    """Unload the entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
