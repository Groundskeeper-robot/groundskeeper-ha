"""Diagnostics download: the panel's summary and the live feed's state, without the token, the address, the robot's
serial or its position."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import GroundskeeperConfigEntry, options

TO_REDACT = {CONF_API_TOKEN, CONF_URL, "sn", "position", "lat", "lon"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> dict[str, Any]:
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "summary": async_redact_data(entry.runtime_data.data, TO_REDACT),
        "options": options(entry),
        "live": _live(entry),
    }


def _live(entry: GroundskeeperConfigEntry) -> dict[str, Any] | None:
    live = entry.runtime_data.live
    if live is None:
        return None
    return {
        "connected": live.connected,
        "error": live.error,
        "state": async_redact_data(live.state, TO_REDACT) if live.state else None,
        "has_pose": live.pose is not None,
    }
