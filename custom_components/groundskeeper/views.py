"""Files for the Groundskeeper card's map, passed through Home Assistant: the panel's imagery tiles and yard photo,
and for the 3D view its ground tiles and the two libraries that draw it.

A browser cannot hold the integration's token, so the card asks Home Assistant (signed in as the person) and the
integration fetches from the panel. Only these kinds of file pass; every other panel path is refused here. All of
them need the Map option.

- GET /api/groundskeeper/{entry_id}/tiles/{world|src}/{z}/{x}/{y}.jpg
- GET /api/groundskeeper/{entry_id}/imagery/{name}
- GET /api/groundskeeper/{entry_id}/ground/{z}/{x}/{y}.png?v={version}: Terrarium elevation tiles
- GET /api/groundskeeper/{entry_id}/vendor/{name}: MapLibre and three.js, only with the contents VENDOR names
"""

from __future__ import annotations

from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS

from .api import GROUND_VERSION, IMAGERY_NAME, TILE_KINDS, GroundskeeperError
from .const import CONF_MAP, DOMAIN, VENDOR
from .coordinator import options

MAX_ZOOM = 24
# What a tile or the yard photo may be served as. Anything else the panel says is replaced: a compromised panel (or a
# cleartext connection someone sits on) must not get an HTML page served from Home Assistant's own address.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


def photo_name_ok(name: str) -> bool:
    """A yard photo's file name: letters, digits, `_`, `-` and `.`, never starting with a dot (so never `..`)."""
    return bool(IMAGERY_NAME.fullmatch(name)) and not name.startswith(".")


@callback
def async_register(hass: HomeAssistant) -> None:
    hass.http.register_view(TileView())
    hass.http.register_view(YardPhotoView())
    hass.http.register_view(GroundTileView())
    hass.http.register_view(VendorView())


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
        status, body, ctype, _cache = await client.file(path)
    except GroundskeeperError:
        return web.Response(status=HTTPStatus.BAD_GATEWAY)
    if status == HTTPStatus.NO_CONTENT:  # the panel has no tile there
        return web.Response(status=HTTPStatus.NO_CONTENT)
    if status != HTTPStatus.OK:
        return web.Response(status=HTTPStatus.NOT_FOUND)
    kind = (ctype or "").split(";")[0].strip().lower()
    return web.Response(
        body=body,
        content_type=kind if kind in IMAGE_TYPES else "image/jpeg",
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )


def _tile(z: str, x: str, y: str) -> tuple[int, int, int] | None:
    """A tile address on the world's grid, or None."""
    if not (z.isdigit() and x.isdigit() and y.isdigit()):
        return None
    zi, xi, yi = int(z), int(x), int(y)
    if zi > MAX_ZOOM or xi >= 2**zi or yi >= 2**zi:
        return None
    return zi, xi, yi


class TileView(HomeAssistantView):
    url = "/api/groundskeeper/{entry_id}/tiles/{kind}/{z}/{x}/{y}.jpg"
    name = "api:groundskeeper:tiles"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, kind: str, z: str, x: str, y: str) -> web.Response:
        tile = _tile(z, x, y)
        if kind not in TILE_KINDS or tile is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        zi, xi, yi = tile
        return await _pass(
            request.app[KEY_HASS], entry_id, f"/tiles/{kind}/{zi}/{xi}/{yi}.jpg", "private, no-store"
        )


class YardPhotoView(HomeAssistantView):
    url = "/api/groundskeeper/{entry_id}/imagery/{name}"
    name = "api:groundskeeper:imagery"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, name: str) -> web.Response:
        if not photo_name_ok(name):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return await _pass(request.app[KEY_HASS], entry_id, f"/imagery/{name}", "private, no-store")


class GroundTileView(HomeAssistantView):
    """The 3D view's ground, one Terrarium tile at a time. `v` is the ground version the card read: a tile of that
    ground may be kept a day; asked under an older version, the panel sends the new ground and it is not kept."""

    url = "/api/groundskeeper/{entry_id}/ground/{z}/{x}/{y}.png"
    name = "api:groundskeeper:ground"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, z: str, x: str, y: str) -> web.Response:
        tile = _tile(z, x, y)
        version = request.query.get("v")
        if tile is None or (version is not None and not GROUND_VERSION.fullmatch(version)):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        client = _client(request.app[KEY_HASS], entry_id)
        if client is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            status, body, keep = await client.ground_tile(*tile, version)
        except GroundskeeperError:
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        if status != HTTPStatus.OK:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(
            body=body,
            content_type="image/png",
            headers={
                "Cache-Control": "private, max-age=86400" if keep and version else "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


class VendorView(HomeAssistantView):
    """MapLibre and three.js for the 3D view, from the panel, with the exact contents VENDOR names (see const.py).
    Kept in memory once checked: the same bytes whichever panel sent them."""

    url = "/api/groundskeeper/{entry_id}/vendor/{name}"
    name = "api:groundskeeper:vendor"
    requires_auth = True

    def __init__(self) -> None:
        self._checked: dict[str, bytes] = {}

    async def get(self, request: web.Request, entry_id: str, name: str) -> web.Response:
        if name not in VENDOR:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        client = _client(request.app[KEY_HASS], entry_id)
        if client is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        body = self._checked.get(name)
        if body is None:
            try:
                body = await client.vendor(name)
            except GroundskeeperError:
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            self._checked[name] = body
        return web.Response(
            body=body,
            content_type=VENDOR[name][0],
            charset="utf-8",
            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
        )
