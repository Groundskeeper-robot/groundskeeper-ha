"""Groundskeeper: your Yarbo in Home Assistant, through your Groundskeeper panel.

It reads only: it sends nothing to the robot. Besides the summary's entities it can follow the robot live for the
Groundskeeper card (the map and the follow camera, Options), through the panel's live feed.
"""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import views, websocket
from .api import GroundskeeperClient
from .const import CONF_DEVICE_TRACKER, CONF_FOLLOW_CAMERA, CONF_MAP, DOMAIN
from .coordinator import GroundskeeperConfigEntry, GroundskeeperCoordinator, options
from .live import GroundskeeperLive

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.LAWN_MOWER, Platform.SENSOR]


def _platforms(opts: dict[str, bool]) -> list[Platform]:
    """The summary's platforms, and the camera and device tracker only when switched on (so a camera or tracker
    component is not loaded for nothing)."""
    out = list(PLATFORMS)
    if opts[CONF_FOLLOW_CAMERA]:
        out.append(Platform.CAMERA)
    if opts[CONF_DEVICE_TRACKER]:
        out.append(Platform.DEVICE_TRACKER)
    return out


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The card's websocket commands and file view, once for every entry."""
    websocket.async_register(hass)
    views.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> bool:
    """Connect to the panel and set up the entities."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = GroundskeeperClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    coordinator = GroundskeeperCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    opts = options(entry)
    if opts[CONF_MAP] or opts[CONF_FOLLOW_CAMERA] or opts[CONF_DEVICE_TRACKER]:
        coordinator.live = GroundskeeperLive(hass, entry.entry_id, client)
    entry.runtime_data = coordinator
    coordinator.platforms = _platforms(opts)
    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)
    if coordinator.live:
        coordinator.live.start(entry)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def _options_changed(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> bool:
    """Unload the entities and close the live feed."""
    if entry.runtime_data.live:
        await entry.runtime_data.live.stop()
    return await hass.config_entries.async_unload_platforms(entry, entry.runtime_data.platforms)
