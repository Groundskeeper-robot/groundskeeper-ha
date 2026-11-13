"""Imagery for the Groundskeeper card's map: the panel's tiles and yard photo, passed through Home Assistant.

A browser cannot hold the integration's token, so the card asks Home Assistant (signed in as the person) and the
integration fetches from the panel. Only these two kinds of file pass; every other panel path is refused here.

- GET /api/groundskeeper/{entry_id}/tiles/{world|src}/{z}/{x}/{y}.jpg
- GET /api/groundskeeper/{entry_id}/imagery/{name}
"""

from __future__ import annotations

from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS

from .api import IMAGERY_NAME, TILE_KINDS, GroundskeeperError
from .const import CONF_MAP, DOMAIN
from .coordinator import options

MAX_ZOOM = 24


@callback
def async_register(hass: HomeAssistant) -> None:
    hass.http.register_view(TileView())
    hass.http.register_view(YardPhotoView())


def _client(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        return None
    if not options(entry)[CONF_MAP]:
        return None
    return entry.runtime_data.client


async def _pass(hass: HomeAssistant, entry_id: str, path: str, cache: str) -> web.Response:
    client = _client(hass, entry_id)
    if client is None:
        return web.Response(status=HTTPStatus.NOT_FOUND)
    try:
        status, body, ctype = await client.file(path)
    except GroundskeeperError:
        return web.Response(status=HTTPStatus.BAD_GATEWAY)
    if status == HTTPStatus.NO_CONTENT:  # the panel has no tile there
        return web.Response(status=HTTPStatus.NO_CONTENT)
    if status != HTTPStatus.OK:
        return web.Response(status=HTTPStatus.NOT_FOUND)
    return web.Response(body=body, content_type=(ctype or "image/jpeg").split(";")[0], headers={"Cache-Control": cache})


class TileView(HomeAssistantView):
    url = "/api/groundskeeper/{entry_id}/tiles/{kind}/{z}/{x}/{y}.jpg"
    name = "api:groundskeeper:tiles"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, kind: str, z: str, x: str, y: str) -> web.Response:
        if kind not in TILE_KINDS or not (z.isdigit() and x.isdigit() and y.isdigit()):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        zi, xi, yi = int(z), int(x), int(y)
        if zi > MAX_ZOOM or xi >= 2**zi or yi >= 2**zi:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return await _pass(
            request.app[KEY_HASS], entry_id, f"/tiles/{kind}/{zi}/{xi}/{yi}.jpg", "private, max-age=86400"
        )


class YardPhotoView(HomeAssistantView):
    url = "/api/groundskeeper/{entry_id}/imagery/{name}"
    name = "api:groundskeeper:imagery"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, name: str) -> web.Response:
        if not IMAGERY_NAME.fullmatch(name) or name.startswith("."):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return await _pass(request.app[KEY_HASS], entry_id, f"/imagery/{name}", "private, max-age=86400")
