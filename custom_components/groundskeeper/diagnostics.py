"""Diagnostics download: the panel's summary, without the token, the address or the robot's serial."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import GroundskeeperConfigEntry

TO_REDACT = {CONF_API_TOKEN, CONF_URL, "sn"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GroundskeeperConfigEntry) -> dict[str, Any]:
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "summary": async_redact_data(entry.runtime_data.data, TO_REDACT),
    }
