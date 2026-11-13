"""Websocket commands for the Groundskeeper card.

The card never talks to the panel: it asks Home Assistant, over the websocket the frontend already holds, and the
integration asks the panel with its own token. So the token stays on the Home Assistant server and Home Assistant's
own sign-in decides who sees the map. What these commands send is never written to an entity.

- `groundskeeper/map` {entry_id}: the yard map (GeoJSON, bounds, map version, the yard photo's details with its
  address on this integration's imagery view). Map option.
- `groundskeeper/subscribe` {entry_id}: the live feed. First a `snapshot` event (the last state, the newest pose,
  whether the feed is connected), then each `state`, `pose`, `connection` and `stale` event as it comes. A `closed`
  event ends it: the entry unloaded or reloaded (an option changed), and the card subscribes again if it wants.
  Without the Map option the robot's position is left out of everything sent.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .api import IMAGERY_NAME, GroundskeeperError
from .const import CONF_MAP, DOMAIN
from .coordinator import GroundskeeperCoordinator, options


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_map)
    websocket_api.async_register_command(hass, ws_subscribe)


def _coordinator(hass: HomeAssistant, connection, msg: dict) -> GroundskeeperCoordinator | None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No loaded Groundskeeper entry with that id")
        return None
    return entry.runtime_data


def without_position(event: str, data: Any) -> Any:
    """What a card may see with the Map option off: no pose events and no position in a state."""
    if event == "pose":
        return None
    if event == "state" and isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "position"}
    return data


def yard_for_card(yard: Any, entry_id: str) -> dict | None:
    """The yard photo's details with its panel path (`/imagery/<name>`) swapped for this integration's view, which the
    card can load signed in to Home Assistant. A path of any other shape is dropped."""
    if not isinstance(yard, dict):
        return None
    out = {k: v for k, v in yard.items() if k != "url"}
    url = yard.get("url")
    name = url.removeprefix("/imagery/").split("?")[0] if isinstance(url, str) and url.startswith("/imagery/") else ""
    out["url"] = f"/api/groundskeeper/{entry_id}/imagery/{name}" if IMAGERY_NAME.fullmatch(name) else None
    return out


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/map", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_map(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is None:
        return
    if not options(coordinator.config_entry)[CONF_MAP]:
        connection.send_error(msg["id"], "map_off", "The map is switched off in the integration's options")
        return
    try:
        data = await coordinator.client.map()
    except GroundskeeperError as err:
        connection.send_error(msg["id"], "panel_unavailable", f"The panel did not give its map ({type(err).__name__})")
        return
    connection.send_result(msg["id"], {**data, "yard": yard_for_card(data.get("yard"), msg["entry_id"])})


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/subscribe", vol.Required("entry_id"): str})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is None:
        return
    live = coordinator.live
    if live is None:
        connection.send_error(msg["id"], "live_off", "The map and follow camera are switched off in the options")
        return
    entry = coordinator.config_entry

    def shown(event: str, data: Any) -> Any:
        # Read on every event, not once: what the person switched off stops at once, whatever else happens.
        return data if options(entry)[CONF_MAP] else without_position(event, data)

    @callback
    def forward(event: str, data: Any) -> None:
        if event == "closed":
            connection.send_message(websocket_api.event_message(msg["id"], {"event": "closed", "data": None}))
            unsub = connection.subscriptions.pop(msg["id"], None)
            if unsub:
                unsub()
            return
        if event == "pose" and not options(entry)[CONF_MAP]:
            return  # the only event held back: positions, with the map switched off
        out = shown(event, data)
        connection.send_message(websocket_api.event_message(msg["id"], {"event": event, "data": out}))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, live.signal, forward)
    connection.send_result(msg["id"])
    snapshot = {
        "state": shown("state", live.state),
        "pose": live.pose if options(entry)[CONF_MAP] else None,
        "connected": live.connected,
    }
    connection.send_message(websocket_api.event_message(msg["id"], {"event": "snapshot", "data": snapshot}))
