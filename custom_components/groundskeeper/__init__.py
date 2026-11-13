"""Groundskeeper: your Yarbo in Home Assistant, through your Groundskeeper panel.

It reads only: it sends nothing to the robot. Besides the summary's entities it can follow the robot live for the
Groundskeeper card (the map and the follow camera, Options), through the panel's live feed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import views, websocket
from .api import GroundskeeperClient
from .const import CARD_URL, CONF_DEVICE_TRACKER, CONF_FOLLOW_CAMERA, CONF_MAP, DOMAIN, LOGGER
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

# The entities each option makes, by unique id suffix and domain: removed from the registry when it is switched off,
# so they don't linger as "no longer provided" (and a tracker's history is not kept growing on a dead entity).
OPTION_ENTITIES = {
    CONF_FOLLOW_CAMERA: (("sensor", "follow_camera"), ("camera", "follow_camera_view")),
    CONF_DEVICE_TRACKER: (("device_tracker", "tracker"),),
}


def _remove_switched_off(hass: HomeAssistant, entry: GroundskeeperConfigEntry, opts: dict[str, bool]) -> None:
    registry = er.async_get(hass)
    for option, entities in OPTION_ENTITIES.items():
        if opts[option]:
            continue
        for domain, key in entities:
            if entity_id := registry.async_get_entity_id(domain, DOMAIN, f"{entry.unique_id}_{key}"):
                registry.async_remove(entity_id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The card's websocket commands and file view, once for every entry."""
    websocket.async_register(hass)
    views.async_register(hass)
    await _register_card(hass)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the Groundskeeper card and load it on every dashboard, so there is no resource to add by hand. A hash
    of the file in its address makes browsers fetch it again whenever it changes, version bump or not."""
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / "groundskeeper-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), cache_headers=False)])
    if "frontend" not in hass.config.components:
        LOGGER.debug("No frontend: the Groundskeeper card is served but not added to dashboards")
        return
    from homeassistant.components.frontend import add_extra_js_url

    digest = await hass.async_add_executor_job(lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12])
    add_extra_js_url(hass, f"{CARD_URL}?v={digest}")


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
    _remove_switched_off(hass, entry, opts)
    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)
    if coordinator.live:
        coordinator.live.start(entry)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def _options_changed(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> bool:
    """Unload the entities, then close the live feed (it keeps feeding them if they failed to unload)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, entry.runtime_data.platforms)
    if unloaded and entry.runtime_data.live:
        await entry.runtime_data.live.stop()
    return unloaded
