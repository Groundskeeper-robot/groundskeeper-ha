"""The panel's live feed (GET /api/ha/stream): where the robot is and which camera covers it, between summary polls.

One connection per config entry, kept open while the entry is loaded and reopened with a growing wait when it
drops. Each event goes out on the entry's dispatcher signal to the follow-camera entities, the optional device
tracker and the card's websocket subscribers. The robot's position goes only to those: never into an entity's
attributes, so Home Assistant's recorder keeps no history of it unless the person turns the device tracker on.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import GroundskeeperClient, InvalidAuth, PanelTooOld, UnsupportedApi
from .const import LIVE_RETRY_S, LIVE_STALE_S, LOGGER, SIGNAL_LIVE


class GroundskeeperLive:
    """Holds the newest state and pose from the panel's live feed and passes each event on."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: GroundskeeperClient) -> None:
        self.hass = hass
        self.signal = SIGNAL_LIVE.format(entry_id)
        self.client = client
        self.state: dict[str, Any] | None = None  # the last `state` event (ha.live on the panel)
        self.state_at: float | None = None  # when it came (monotonic)
        self.pose: dict[str, Any] | None = None  # the newest pose sample
        self.connected = False
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._logged: str | None = None  # the last "not available" reason logged, so it is said once

    @callback
    def start(self, entry) -> None:
        self._task = entry.async_create_background_task(self.hass, self._run(), f"groundskeeper live {entry.entry_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def fresh(self) -> bool:
        """A state event came within LIVE_STALE_S: a short reconnect does not make the entities flicker."""
        return self.state is not None and self.state_at is not None and time.monotonic() - self.state_at < LIVE_STALE_S

    @property
    def position(self) -> dict[str, Any] | None:
        """The newest known position: a pose sample if one came after the last state event, else the state's."""
        state_pos = (self.state or {}).get("position")
        pose = self.pose
        if pose and (not state_pos or (pose.get("t") or 0) >= (state_pos.get("t") or 0)):
            return pose
        return state_pos

    @callback
    def handle(self, event: str, data: Any) -> None:
        """One event from the panel: keep it and pass it on."""
        if event == "state" and isinstance(data, dict):
            self.state, self.state_at = data, time.monotonic()
        elif event == "pose" and isinstance(data, list):
            poses = [p for p in data if isinstance(p, dict) and p.get("lon") is not None and p.get("lat") is not None]
            if not poses:
                return
            self.pose = poses[-1]
            data = poses
        else:
            return
        async_dispatcher_send(self.hass, self.signal, event, data)

    @callback
    def _set_connected(self, connected: bool, error: str | None = None) -> None:
        if connected != self.connected:
            self.connected = connected
            async_dispatcher_send(self.hass, self.signal, "connection", {"connected": connected})
        self.error = error

    async def _run(self) -> None:
        wait = LIVE_RETRY_S[0]
        while True:
            try:
                async for event, data in self.client.live():
                    if not self.connected:
                        self._set_connected(True)
                        wait = LIVE_RETRY_S[0]
                    self.handle(event, data)
                self._set_connected(False, "the panel ended the live feed")
            except (PanelTooOld, UnsupportedApi, InvalidAuth) as err:
                # Nothing to gain by asking again soon: update the panel or the integration, or a new token.
                self._set_connected(False, type(err).__name__)
                if self.error != self._logged:
                    LOGGER.warning(
                        "The panel's live feed is not available (%s); the map and follow camera wait",
                        type(err).__name__,
                    )
                    self._logged = self.error
                wait = LIVE_RETRY_S[1]
            except Exception as err:  # noqa: BLE001  any failure: say so once, wait, reconnect
                self._set_connected(False, f"{type(err).__name__} {err}".strip())
                LOGGER.debug("Live feed dropped: %s", self.error)
            await asyncio.sleep(wait)
            wait = min(wait * 2, LIVE_RETRY_S[1])
