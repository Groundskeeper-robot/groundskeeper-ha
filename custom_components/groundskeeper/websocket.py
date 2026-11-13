"""Websocket commands for the Groundskeeper card.

The card never talks to the panel: it asks Home Assistant, over the websocket the frontend already holds, and the
integration asks the panel with its own token. So the token stays on the Home Assistant server and Home Assistant's
own sign-in decides who sees the map. What these commands send is never written to an entity.

- `groundskeeper/map` {entry_id}: the yard map (GeoJSON, bounds, map version, the yard photo's details with its
  address on this integration's imagery view). Map option.
- `groundskeeper/track` {entry_id, run_id, since?}: a run's track as [time, lon, lat, rtk status] samples after
  `since` (the card asks for the current run, then only what is new). Map option.
- `groundskeeper/subscribe` {entry_id}: the live feed. First a `snapshot` event (the last state, the newest pose,
  whether the feed is connected), then each `state`, `pose`, `connection` and `stale` event as it comes. A `closed`
  event ends it: the entry unloaded or reloaded (an option changed), and the card subscribes again if it wants.
  Without the Map option the robot's position is left out of everything sent.
- `groundskeeper/ground` {entry_id}: the ground for the card's 3D view: its version, bounds, zooms and relief, the
  address of its tiles on this integration's ground view, and of the libraries on its vendor view. Map option. The
  first ask after the panel starts can take a while (it may download lidar). `no_ground`: the panel has none.
- `groundskeeper/plans` {entry_id}: the robot's plans ({id, name}) for the card's Start. Commands option.
- `groundskeeper/preview` {entry_id, cmd, plan_id?}: would the panel send this now: its checks and why not, nothing
  sent. Commands option.
- `groundskeeper/command` {entry_id, cmd, plan_id?}: ask the panel to send one of COMMANDS (start_plan needs a
  plan). Its answer: `sent`, and when not, `refused` and `why`. Commands option, and the caller needs Home
  Assistant's control permission on the mower entity, as its own actions do. The panel decides: its Control switch
  and every check, held to the strict rules it keeps for an API token, with no override.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import commands
from .api import IMAGERY_NAME, CommandRejected, GroundskeeperError, NoGround
from .const import COMMANDS, CONF_MAP, DOMAIN, VENDOR
from .coordinator import GroundskeeperCoordinator, options


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_map)
    websocket_api.async_register_command(hass, ws_ground)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_track)
    websocket_api.async_register_command(hass, ws_plans)
    websocket_api.async_register_command(hass, ws_preview)
    websocket_api.async_register_command(hass, ws_command)


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


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/ground", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_ground(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is None:
        return
    if not options(coordinator.config_entry)[CONF_MAP]:
        connection.send_error(msg["id"], "map_off", "The map is switched off in the integration's options")
        return
    try:
        ground = await coordinator.client.ground()
    except NoGround:
        connection.send_error(msg["id"], "no_ground", "The panel has no ground for the 3D view here yet")
        return
    except GroundskeeperError as err:
        connection.send_error(
            msg["id"], "panel_unavailable", f"The panel did not give its ground ({type(err).__name__})"
        )
        return
    if not options(coordinator.config_entry)[CONF_MAP]:  # switched off while the panel answered
        connection.send_error(msg["id"], "map_off", "The map is switched off in the integration's options")
        return
    base = f"/api/groundskeeper/{msg['entry_id']}"
    connection.send_result(
        msg["id"],
        {
            **ground,
            "tiles": f"{base}/ground/{{z}}/{{x}}/{{y}}.png?v={ground['version']}",
            "vendor": {name: f"{base}/vendor/{name}" for name in VENDOR},
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "groundskeeper/track",
        vol.Required("entry_id"): str,
        vol.Required("run_id"): vol.All(int, vol.Range(min=0)),
        vol.Optional("since", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=1e12)),
    }
)
@websocket_api.async_response
async def ws_track(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is None:
        return
    if not options(coordinator.config_entry)[CONF_MAP]:
        connection.send_error(msg["id"], "map_off", "The map is switched off in the integration's options")
        return
    try:
        points = await coordinator.client.run_track(msg["run_id"], msg["since"])
    except GroundskeeperError as err:
        connection.send_error(
            msg["id"], "panel_unavailable", f"The panel did not give the track ({type(err).__name__})"
        )
        return
    if not options(coordinator.config_entry)[CONF_MAP]:  # switched off while the panel answered
        connection.send_error(msg["id"], "map_off", "The map is switched off in the integration's options")
        return
    connection.send_result(msg["id"], {"run_id": msg["run_id"], "points": points})


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


def plan_id(value: Any) -> int:
    """A plan id: a whole number, 0 or more (not true or false, which Python counts as ints)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise vol.Invalid("plan_id must be a whole number, 0 or more")
    return value


COMMAND_SCHEMA = {
    vol.Required("entry_id"): str,
    vol.Required("cmd"): vol.In(COMMANDS),
    vol.Optional("plan_id"): plan_id,
}


def _commands_coordinator(hass: HomeAssistant, connection, msg: dict) -> GroundskeeperCoordinator | None:
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is None:
        return None
    if not commands.commands_on(coordinator):
        connection.send_error(msg["id"], "commands_off", "Commands are switched off in the integration's options")
        return None
    if msg.get("cmd") == "start_plan" and msg.get("plan_id") is None:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "start_plan needs a plan_id")
        return None
    return coordinator


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/plans", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_plans(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _commands_coordinator(hass, connection, msg)
    if coordinator is None:
        return
    try:
        plans = await coordinator.client.plans()
    except GroundskeeperError as err:
        connection.send_error(
            msg["id"], "panel_unavailable", f"The panel did not give its plans ({type(err).__name__})"
        )
        return
    default = (coordinator.data or {}).get("can_start")
    plan = default.get("plan_id") if isinstance(default, dict) else None
    connection.send_result(msg["id"], {"plans": plans, "default": plan if isinstance(plan, int) else None})


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/preview", **COMMAND_SCHEMA})
@websocket_api.async_response
async def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _commands_coordinator(hass, connection, msg)
    if coordinator is None:
        return
    try:
        result = await commands.preview(coordinator, msg["cmd"], msg.get("plan_id"))
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    except CommandRejected as err:
        connection.send_error(msg["id"], "rejected", f"The panel turned it down: {err}")
        return
    except GroundskeeperError as err:
        connection.send_error(msg["id"], "panel_unavailable", f"The panel could not be asked ({type(err).__name__})")
        return
    connection.send_result(msg["id"], result)


def _may_control_mower(hass: HomeAssistant, connection, coordinator: GroundskeeperCoordinator) -> bool:
    """The same right the mower entity's own actions need: Home Assistant's control permission on that entity, so a
    user whose permissions leave the mower out cannot reach it through the card either."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "lawn_mower", DOMAIN, f"{coordinator.config_entry.unique_id}_mower"
    )
    return entity_id is not None and connection.user.permissions.check_entity(entity_id, POLICY_CONTROL)


@websocket_api.websocket_command({vol.Required("type"): "groundskeeper/command", **COMMAND_SCHEMA})
@websocket_api.async_response
async def ws_command(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _commands_coordinator(hass, connection, msg)
    if coordinator is None:
        return
    if not _may_control_mower(hass, connection, coordinator):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, str(Unauthorized()))
        return
    try:
        result = await commands.send(coordinator, msg["cmd"], msg.get("plan_id"))
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    except CommandRejected as err:
        connection.send_error(msg["id"], "rejected", f"The panel turned it down: {err}")
        return
    except GroundskeeperError as err:
        connection.send_error(
            msg["id"], "panel_unavailable",
            f"No answer from the panel ({type(err).__name__}): the command may or may not have gone out",
        )
        return
    connection.send_result(msg["id"], {**result, "why_not": None if result.get("sent") else commands.why_not(result)})
