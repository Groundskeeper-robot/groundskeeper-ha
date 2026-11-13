"""The panel's live feed (GET /api/ha/stream): where the robot is and which camera covers it, between summary polls.

One connection per config entry, kept open while the entry is loaded and reopened with a growing wait when it
drops. Each event goes out on the entry's dispatcher signal to the follow-camera entities, the optional device
tracker and the card's websocket subscribers. The robot's position goes only to those: never into an entity's
attributes, so Home Assistant's recorder keeps no history of it unless the person turns the device tracker on.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import GroundskeeperClient, InvalidAuth, PanelTooOld, UnsupportedApi
from .const import LIVE_RETRY_S, LIVE_STALE_S, LOGGER, SIGNAL_LIVE


class GroundskeeperLive:
    """Holds the newest state and pose from the panel's live feed and passes each event on."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: GroundskeeperClient) -> None:
        self.hass = hass
        # One signal per load: after a reload (an option changed) nothing still listening to the old one hears the
        # new feed. stop() sends `closed` on it first, so the card's subscriptions end and it can ask again.
        self.signal = SIGNAL_LIVE.format(f"{entry_id}_{uuid4().hex}")
        self.client = client
        self.state: dict[str, Any] | None = None  # the last `state` event (ha.live on the panel)
        self.stale = False  # no state event for LIVE_STALE_S (set by a timer, cleared by the next state)
        self.pose: dict[str, Any] | None = None  # the newest pose sample
        self.connected = False
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._logged: str | None = None  # the last "not available" reason logged, so it is said once
        self._stale_unsub: CALLBACK_TYPE | None = None  # the nudge that marks live entities stale

    @callback
    def start(self, entry) -> None:
        self._task = entry.async_create_background_task(self.hass, self._run(), f"groundskeeper live {entry.entry_id}")

    async def stop(self) -> None:
        self._cancel_stale()
        async_dispatcher_send(self.hass, self.signal, "closed", None)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def fresh(self) -> bool:
        """A state event came within LIVE_STALE_S (a timer says when it has not): a short reconnect does not make the
        entities flicker, and a dead feed makes them unavailable on time."""
        return self.state is not None and not self.stale

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
            self.state, self.stale = data, False
            if data.get("position") is None:
                self.pose = None  # the panel has no position now (no fix): an older pose is not the current one
            self._cancel_stale()
            self._stale_unsub = async_call_later(self.hass, LIVE_STALE_S, self._stale)
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
    def _cancel_stale(self) -> None:
        if self._stale_unsub:
            self._stale_unsub()
            self._stale_unsub = None

    @callback
    def _stale(self, _now) -> None:
        """No state event for LIVE_STALE_S: the entities write themselves again and read as unavailable."""
        self._stale_unsub = None
        self.stale = True
        async_dispatcher_send(self.hass, self.signal, "stale", None)

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
                self._set_connected(False, type(err).__name__)  # the type only: aiohttp's text names the host
                LOGGER.debug("Live feed dropped: %s", self.error)
            await asyncio.sleep(wait)
            wait = min(wait * 2, LIVE_RETRY_S[1])
