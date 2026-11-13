"""The follow camera: the live picture from whichever Frigate camera covers the robot, through the panel.

The panel picks the camera (its follow-cam, with a hold so the picture does not flip at the edge of two coverages)
and serves Frigate's MJPEG and snapshots; this entity passes them on. If Frigate is already in Home Assistant, its
own camera entities show the same picture without the hop through the panel: the follow-camera sensor names which.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GroundskeeperError
from .const import CONF_FOLLOW_CAMERA
from .coordinator import GroundskeeperConfigEntry, options
from .entity import GroundskeeperLiveEntity

PARALLEL_UPDATES = 0
SNAPSHOT_HEIGHTS = (120, 1080)  # the panel's own bounds for a snapshot's height


async def async_setup_entry(
    hass: HomeAssistant, entry: GroundskeeperConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.live and options(entry)[CONF_FOLLOW_CAMERA]:
        async_add_entities([FollowCamera(coordinator)])


class FollowCamera(GroundskeeperLiveEntity, Camera):
    _attr_translation_key = "follow_camera"

    def __init__(self, coordinator) -> None:
        GroundskeeperLiveEntity.__init__(self, coordinator, "follow_camera_view")
        Camera.__init__(self)
        self._shown: str | None = None

    @property
    def frigate_camera(self) -> str | None:
        return (self.live_state.get("follow") or {}).get("camera")

    @property
    def available(self) -> bool:
        return super().available and bool(self.frigate_camera)

    @property
    def extra_state_attributes(self) -> dict:
        return {"frigate_camera": self.frigate_camera}

    @callback
    def _on_live(self, event, data) -> None:
        # A new camera changes the picture: tell the frontend so it fetches a new access token / still.
        if event in self.live_events and self.frigate_camera != self._shown:
            self._shown = self.frigate_camera
            self.async_update_token()
        super()._on_live(event, data)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        cam = self.frigate_camera
        if not cam:
            return None
        h = max(SNAPSHOT_HEIGHTS[0], min(height, SNAPSHOT_HEIGHTS[1])) if height else None
        try:
            return await self.coordinator.client.frigate_snapshot(cam, h)
        except GroundskeeperError:
            return None

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse | None:
        cam = self.frigate_camera
        if not cam:
            return None
        try:
            upstream = await self.coordinator.client.frigate_stream(cam)
        except (GroundskeeperError, ValueError):
            return None
        try:
            # Only an MJPEG stream passes, whatever the panel says it is: never HTML on Home Assistant's address.
            ctype = upstream.headers.get("Content-Type", "")
            if not ctype.lower().startswith("multipart/x-mixed-replace"):
                return None
            return await async_aiohttp_proxy_stream(self.hass, request, upstream.content, ctype)
        finally:
            upstream.close()
