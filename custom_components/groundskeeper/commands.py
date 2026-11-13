"""Commands through the panel, for the mower entity and the card (the Commands option, off by default).

Home Assistant only asks: the panel's POST /api/control/send sends a command only with its Control switch on and every
check passing, and holds an API token to its strictest reading. What it answers comes back as is, so a refusal says
the panel's own reason.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .api import GroundskeeperError
from .const import CONF_COMMANDS, LOGGER
from .coordinator import GroundskeeperCoordinator, options


class CommandsOff(HomeAssistantError):
    """The Commands option is off."""


def commands_on(coordinator: GroundskeeperCoordinator) -> bool:
    return bool(options(coordinator.config_entry)[CONF_COMMANDS])


def why_not(result: dict[str, Any]) -> str:
    """The panel's reason a command did not go, in its own words: the first reasons' titles, else its raw reason."""
    titles = [w.get("title") for w in result.get("why") or [] if isinstance(w, dict) and w.get("title")]
    if titles:
        return "; ".join(dict.fromkeys(titles))
    return str(result.get("refused") or result.get("error") or result.get("reason") or "the panel did not send it")


async def preview(coordinator: GroundskeeperCoordinator, cmd: str, plan_id: int | None = None) -> dict[str, Any]:
    if not commands_on(coordinator):
        raise CommandsOff("Commands are switched off in the Groundskeeper integration's options")
    return await coordinator.client.preview(cmd, plan_id)


async def send(coordinator: GroundskeeperCoordinator, cmd: str, plan_id: int | None = None) -> dict[str, Any]:
    """Ask the panel to send `cmd`; its answer, `sent` true or false. The summary is read again straight after, so
    the entities catch up with what the robot does."""
    if not commands_on(coordinator):
        raise CommandsOff("Commands are switched off in the Groundskeeper integration's options")
    result = await coordinator.client.send(cmd, plan_id)
    LOGGER.debug("Command %s: %s", cmd, "sent" if result.get("sent") else f"not sent ({why_not(result)})")
    await coordinator.async_request_refresh()
    return result


async def send_or_raise(coordinator: GroundskeeperCoordinator, cmd: str, plan_id: int | None = None) -> None:
    """For an entity action: send, or raise with the panel's reason so Home Assistant shows it."""
    try:
        result = await send(coordinator, cmd, plan_id)
    except GroundskeeperError as err:
        raise HomeAssistantError(f"The panel could not be asked ({type(err).__name__}: {err})".strip()) from err
    if not result.get("sent"):
        raise HomeAssistantError(f"The panel did not send it: {why_not(result)}")
