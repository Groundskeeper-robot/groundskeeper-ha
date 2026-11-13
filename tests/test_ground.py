"""The card's 3D view: the panel's ground over the websocket, its tiles and the two libraries through the views.

fixtures/ground.json and ground_tile.png were read from the panel's demo mode (DEMO=1) with an API token."""

from __future__ import annotations

import hashlib
from http import HTTPStatus

import pytest

from custom_components.groundskeeper import api
from custom_components.groundskeeper.api import CannotConnect, ground_meta
from custom_components.groundskeeper.const import CONF_MAP, GROUND_PATH, VENDOR

from .conftest import FIXTURES, URL, fixture, set_up

GROUND = fixture("ground")
TILE = (FIXTURES / "ground_tile.png").read_bytes()
V = GROUND["version"]


async def ws_ground(hass, entry, hass_ws_client):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/ground", "entry_id": entry.entry_id})
    return await ws.receive_json()


async def test_the_ground_over_the_websocket(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}{GROUND_PATH}", json=GROUND)
    res = await ws_ground(hass, entry, hass_ws_client)
    assert res["success"], res
    out = res["result"]
    base = f"/api/groundskeeper/{entry.entry_id}"
    assert out["version"] == V and out["bounds"] == GROUND["bounds"] and out["encoding"] == "terrarium"
    assert (out["minzoom"], out["maxzoom"]) == (GROUND["minzoom"], GROUND["maxzoom"])
    assert out["tiles"] == f"{base}/ground/{{z}}/{{x}}/{{y}}.png?v={V}"
    assert out["vendor"] == {name: f"{base}/vendor/{name}" for name in VENDOR}
    assert "robot_share" not in out  # only what the card draws with


async def test_no_ground_without_the_map(hass, aioclient_mock, entry, hass_client, hass_ws_client):
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_MAP: False})
    await set_up(hass, entry, aioclient_mock)
    calls = len(aioclient_mock.mock_calls)
    res = await ws_ground(hass, entry, hass_ws_client)
    assert res["error"]["code"] == "map_off"
    assert len(aioclient_mock.mock_calls) == calls  # the panel was never asked
    http = await hass_client()
    for path in (f"ground/16/1/1.png?v={V}", "vendor/maplibre-gl.js"):
        r = await http.get(f"/api/groundskeeper/{entry.entry_id}/{path}")
        assert r.status == 404, path
    assert len(aioclient_mock.mock_calls) == calls


@pytest.mark.parametrize(
    ("status", "code"),
    [(HTTPStatus.NOT_FOUND, "no_ground"), (HTTPStatus.SERVICE_UNAVAILABLE, "panel_unavailable")],
)
async def test_a_panel_without_a_ground(hass, aioclient_mock, entry, hass_ws_client, status, code):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}{GROUND_PATH}", status=status, json={"detail": "no terrain for the 3D view yet"})
    res = await ws_ground(hass, entry, hass_ws_client)
    assert res["error"]["code"] == code


@pytest.mark.parametrize(
    "change",
    [
        {"version": "../x"},
        {"version": None},
        {"bounds": [1, 2, 3]},
        {"bounds": [1, 2, 3, float("nan")]},
        {"bounds": [1, 2, 3, True]},
        {"minzoom": 19},
        {"maxzoom": 30},
        {"encoding": "mapbox"},
    ],
)
def test_a_ground_of_the_wrong_shape_is_refused(change):
    with pytest.raises(CannotConnect):
        ground_meta({**GROUND, **change})
    with pytest.raises(CannotConnect):
        ground_meta([GROUND])


async def test_ground_tiles_pass_through(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    path = f"{GROUND_PATH}/16/10434/23419.png"
    aioclient_mock.get(
        f"{URL}{path}",
        params={"v": V},
        content=TILE,
        headers={"Content-Type": "image/png", "Cache-Control": "max-age=86400"},
    )
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/ground/16/10434/23419.png?v={V}")
    assert r.status == 200 and await r.read() == TILE
    assert r.headers["Content-Type"] == "image/png" and r.headers["Cache-Control"] == "private, max-age=86400"


async def test_a_tile_of_a_newer_ground_is_not_kept(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(
        f"{URL}{GROUND_PATH}/16/1/1.png",
        params={"v": "abc"},
        content=TILE,
        headers={"Content-Type": "image/png", "Cache-Control": "no-store"},
    )
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/ground/16/1/1.png?v=abc")
    assert r.status == 200 and r.headers["Cache-Control"] == "private, no-store"


async def test_a_ground_tile_is_always_served_as_a_png(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    page = b"<script>alert(1)</script>"
    aioclient_mock.get(
        f"{URL}{GROUND_PATH}/16/1/1.png", params={"v": V}, content=page, headers={"Content-Type": "text/html"}
    )
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/ground/16/1/1.png?v={V}")
    assert r.headers["Content-Type"] == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "ground/2/4/0.png",  # off the edge of the world at that zoom
        "ground/30/0/0.png",  # deeper than any ground
        "ground/16/1/1.png?v=../../api/settings",  # a version is hex only
        "ground/16/1/1.png?v=",
        "vendor/AgoraRTC_N-production.js",  # the panel serves it, but not for the card
        "vendor/THIRD_PARTY_NOTICES.md",
        "vendor/..%2Fapi%2Fsettings",
    ],
)
async def test_nothing_else_passes_through(hass, aioclient_mock, entry, hass_client, path):
    await set_up(hass, entry, aioclient_mock)
    calls = len(aioclient_mock.mock_calls)
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/{path}")
    assert r.status in (400, 404)  # 400: Home Assistant's own filter turned it away before the view
    assert len(aioclient_mock.mock_calls) == calls


async def test_a_library_passes_only_with_the_pinned_contents(hass, aioclient_mock, entry, hass_client, monkeypatch):
    await set_up(hass, entry, aioclient_mock)
    lib = b"export const REVISION = '170';\n"
    monkeypatch.setitem(VENDOR, "three.module.min.js", ("text/javascript", hashlib.sha256(lib).hexdigest()))
    aioclient_mock.get(f"{URL}/vendor/three.module.min.js", content=lib, headers={"Content-Type": "text/html"})
    http = await hass_client()
    r = await http.get(f"/api/groundskeeper/{entry.entry_id}/vendor/three.module.min.js")
    assert r.status == 200 and await r.read() == lib
    assert r.headers["Content-Type"].startswith("text/javascript")  # its own type, never the panel's
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    calls = len(aioclient_mock.mock_calls)
    r = await http.get(f"/api/groundskeeper/{entry.entry_id}/vendor/three.module.min.js")
    assert r.status == 200 and len(aioclient_mock.mock_calls) == calls  # checked once, then kept


async def test_a_library_with_other_contents_is_refused(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}/vendor/maplibre-gl.js", content=b"fetch('/api/config').then(steal)")
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/vendor/maplibre-gl.js")
    assert r.status == HTTPStatus.BAD_GATEWAY


async def test_a_file_larger_than_any_library_is_refused(hass, aioclient_mock, entry, hass_client, monkeypatch):
    await set_up(hass, entry, aioclient_mock)
    monkeypatch.setattr(api, "VENDOR_MAX_BYTES", 10)
    aioclient_mock.get(f"{URL}/vendor/maplibre-gl.css", content=b"x" * 11)
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/vendor/maplibre-gl.css")
    assert r.status == HTTPStatus.BAD_GATEWAY


async def test_the_ground_needs_a_signed_in_person(hass, aioclient_mock, entry, hass_client_no_auth):
    await set_up(hass, entry, aioclient_mock)
    client = await hass_client_no_auth()
    for path in (f"ground/16/1/1.png?v={V}", "vendor/maplibre-gl.js"):
        r = await client.get(f"/api/groundskeeper/{entry.entry_id}/{path}")
        assert r.status == 401, path


def test_the_pins_are_the_panels_libraries():
    """Each pinned file is one the panel's image ships, with a SHA-256 the length of one."""
    assert set(VENDOR) == {"maplibre-gl.js", "maplibre-gl.css", "three.module.min.js"}
    for ctype, digest in VENDOR.values():
        assert ctype in ("text/javascript", "text/css") and len(bytes.fromhex(digest)) == 32
