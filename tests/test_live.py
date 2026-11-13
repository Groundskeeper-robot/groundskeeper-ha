"""The live feed: the follow camera, the optional device tracker, the card's websocket commands and imagery views.
Synthetic data only; the panel is a mock."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import aiohttp

import pytest

from custom_components.groundskeeper.api import parse_sse
from custom_components.groundskeeper.const import (
    CONF_DEVICE_TRACKER,
    CONF_FOLLOW_CAMERA,
    CONF_MAP,
    IMAGERY_PATH,
    LIVE_API,
    LIVE_STALE_S,
    MAP_PATH,
)
from custom_components.groundskeeper.diagnostics import async_get_config_entry_diagnostics
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import LIVE_STATE, POSE, URL, set_up, sse

SENSOR = "sensor.groundskeeper_follow_camera"
CAMERA = "camera.groundskeeper_follow_camera"
TRACKER = "device_tracker.groundskeeper_position"
MAP = {
    "geojson": {"type": "FeatureCollection", "features": []},
    "bounds": [-1.6, 50.4, -1.4, 50.6],
    "far": [],
    "fetched": 1.0,
    "versions": [{"sha": "abc123", "at": 1.0}],
}
IMAGERY = {
    "yard": {"url": "/imagery/yard-abc.jpg", "bbox": [-1.6, 50.4, -1.4, 50.6]},
    "context": None,
    "far": [],
    "alignment": None,
    "ref": [50.5, -1.5],
}


def with_options(hass, entry, **opts):
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=opts)


def test_an_event_is_parsed_and_comments_are_skipped():
    assert parse_sse([": ping", "event: state", 'data: {"a": 1}']) == ("state", {"a": 1})
    assert parse_sse(["data: [1, 2]"]) == ("message", [1, 2])
    assert parse_sse(["event: state"]) is None
    assert parse_sse(["data: not json"]) is None


async def test_the_follow_camera_sensor_names_the_frigate_camera(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    sensor = hass.states.get(SENSOR)
    assert sensor.state == "garden" and sensor.attributes["covered"] is True
    assert "position" not in sensor.attributes and "lat" not in str(sensor.attributes)
    camera = hass.states.get(CAMERA)
    assert camera.attributes["frigate_camera"] == "garden"


async def test_a_new_camera_from_the_panel_updates_the_entities(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    live = entry.runtime_data.live
    live.handle("state", {**LIVE_STATE, "follow": {"camera": "drive", "covered": False}})
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "drive"
    assert hass.states.get(SENSOR).attributes["covered"] is False
    assert hass.states.get(CAMERA).attributes["frigate_camera"] == "drive"


async def test_the_camera_shows_the_panels_snapshot_of_the_followed_camera(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}/api/frigate/snapshot/garden.jpg", content=b"\xff\xd8jpeg")
    from homeassistant.components.camera import async_get_image

    image = await async_get_image(hass, CAMERA)
    assert image.content == b"\xff\xd8jpeg"


async def test_no_device_tracker_unless_switched_on(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    assert hass.states.get(TRACKER) is None
    assert not [
        e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) if e.domain == "device_tracker"
    ]


async def test_the_device_tracker_follows_the_newest_pose(hass, aioclient_mock, entry):
    with_options(hass, entry, **{CONF_DEVICE_TRACKER: True})
    await set_up(hass, entry, aioclient_mock, live=sse(("state", LIVE_STATE), ("pose", [POSE])))
    tracker = hass.states.get(TRACKER)
    assert (tracker.attributes["latitude"], tracker.attributes["longitude"]) == (50.5, -1.5)
    live = entry.runtime_data.live
    live.handle("pose", [{**POSE, "t": 101.0, "lat": 50.6}])
    await hass.async_block_till_done()
    assert hass.states.get(TRACKER).attributes["latitude"] == 50.5  # a pose alone does not write the state
    live.handle("state", LIVE_STATE)  # the next state event does, with the newer pose
    await hass.async_block_till_done()
    assert hass.states.get(TRACKER).attributes["latitude"] == 50.6


async def test_everything_live_switched_off_opens_no_feed(hass, aioclient_mock, entry):
    with_options(hass, entry, **{CONF_MAP: False, CONF_FOLLOW_CAMERA: False, CONF_DEVICE_TRACKER: False})
    await set_up(hass, entry, aioclient_mock)
    assert entry.runtime_data.live is None
    assert hass.states.get(SENSOR) is None and hass.states.get(CAMERA) is None
    assert all("/api/ha/stream" not in str(url) for _, url, *_ in aioclient_mock.mock_calls)


async def test_a_newer_live_shape_is_not_used(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, live=sse(("state", {**LIVE_STATE, "api": LIVE_API + 1})))
    assert entry.runtime_data.live.state is None
    assert entry.runtime_data.live.error == "UnsupportedApi"
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_a_panel_without_the_feed_leaves_the_rest_working(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.clear_requests()
    from .conftest import LIVE_URL, SUMMARY_URL, summary

    aioclient_mock.get(SUMMARY_URL, json=summary())
    aioclient_mock.get(LIVE_URL, status=HTTPStatus.NOT_FOUND)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.live.error == "PanelTooOld"
    assert hass.states.get("sensor.groundskeeper_battery").state == "72"


async def test_the_map_over_the_websocket(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}{MAP_PATH}", json=MAP)
    aioclient_mock.get(f"{URL}{IMAGERY_PATH}", json=IMAGERY)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/map", "entry_id": entry.entry_id})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"] == {
        "geojson": MAP["geojson"],
        "bounds": MAP["bounds"],
        "fetched": 1.0,
        "map": "abc123",
        "yard": {"url": f"/api/groundskeeper/{entry.entry_id}/imagery/yard-abc.jpg", "bbox": IMAGERY["yard"]["bbox"]},
    }


def test_a_yard_photo_at_any_other_path_has_no_address():
    from custom_components.groundskeeper.websocket import yard_for_card

    assert yard_for_card({"url": "/api/settings", "bbox": [1]}, "e1") == {"url": None, "bbox": [1]}
    assert yard_for_card({"url": "/imagery/../api/settings"}, "e1") == {"url": None}
    assert yard_for_card({"url": "/imagery/yard-1.jpg?v=2"}, "e1") == {
        "url": "/api/groundskeeper/e1/imagery/yard-1.jpg"
    }
    assert yard_for_card(None, "e1") is None


async def test_no_map_with_the_map_switched_off(hass, aioclient_mock, entry, hass_ws_client):
    with_options(hass, entry, **{CONF_MAP: False})
    await set_up(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/map", "entry_id": entry.entry_id})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "map_off"


async def test_an_unknown_entry_is_not_found(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/map", "entry_id": "nope"})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "not_found"


async def test_a_subscription_gets_a_snapshot_then_each_event(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/subscribe", "entry_id": entry.entry_id})
    assert (await ws.receive_json())["success"]
    snap = (await ws.receive_json())["event"]
    assert snap["event"] == "snapshot" and snap["data"]["state"]["position"] == LIVE_STATE["position"]
    async_dispatcher_send(hass, entry.runtime_data.live.signal, "pose", [POSE])
    ev = (await ws.receive_json())["event"]
    assert ev == {"event": "pose", "data": [POSE]}


async def test_without_the_map_a_subscription_never_sees_the_position(hass, aioclient_mock, entry, hass_ws_client):
    with_options(hass, entry, **{CONF_MAP: False})
    await set_up(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/subscribe", "entry_id": entry.entry_id})
    assert (await ws.receive_json())["success"]
    snap = (await ws.receive_json())["event"]["data"]
    assert "position" not in snap["state"] and snap["pose"] is None
    async_dispatcher_send(hass, entry.runtime_data.live.signal, "pose", [POSE])
    async_dispatcher_send(hass, entry.runtime_data.live.signal, "state", LIVE_STATE)
    ev = (await ws.receive_json())["event"]
    assert ev["event"] == "state" and "position" not in ev["data"]  # the pose was never sent


async def test_tiles_and_the_yard_photo_pass_through(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}/tiles/src/18/100/200.jpg", content=b"tile", headers={"Content-Type": "image/jpeg"})
    aioclient_mock.get(f"{URL}/imagery/yard.jpg", content=b"yard", headers={"Content-Type": "image/jpeg"})
    client = await hass_client()
    r = await client.get(f"/api/groundskeeper/{entry.entry_id}/tiles/src/18/100/200.jpg")
    assert r.status == 200 and await r.read() == b"tile"
    r = await client.get(f"/api/groundskeeper/{entry.entry_id}/imagery/yard.jpg")
    assert r.status == 200 and await r.read() == b"yard"
    assert "no-store" in r.headers["Cache-Control"]  # never kept in the browser past a sign-out


@pytest.mark.parametrize(
    "path",
    [
        "tiles/other/1/0/0.jpg",  # not a tile kind the panel serves
        "tiles/src/2/4/0.jpg",  # off the edge of the world at that zoom
        "tiles/src/30/0/0.jpg",  # deeper than any imagery
        "imagery/.hidden",  # the view's own check (a `..` never reaches it: the path is tidied first)
        "imagery/a%2Fb.jpg",  # an encoded slash is not a file name either
    ],
)
async def test_nothing_else_passes_through(hass, aioclient_mock, entry, hass_client, path):
    await set_up(hass, entry, aioclient_mock)
    calls = len(aioclient_mock.mock_calls)
    client = await hass_client()
    r = await client.get(f"/api/groundskeeper/{entry.entry_id}/{path}")
    assert r.status == 404
    assert len(aioclient_mock.mock_calls) == calls


async def test_only_image_types_pass_through(hass, aioclient_mock, entry, hass_client):
    await set_up(hass, entry, aioclient_mock)
    page = b"<script>alert(1)</script>"
    aioclient_mock.get(f"{URL}/imagery/page.html", content=page, headers={"Content-Type": "text/html"})
    r = await (await hass_client()).get(f"/api/groundskeeper/{entry.entry_id}/imagery/page.html")
    assert r.headers["Content-Type"] == "image/jpeg" and r.headers["X-Content-Type-Options"] == "nosniff"


async def test_a_missing_coverage_is_unknown(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    entry.runtime_data.live.handle("state", {**LIVE_STATE, "follow": {"camera": "drive"}})
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).attributes["covered"] is None


async def test_imagery_needs_a_signed_in_person(hass, aioclient_mock, entry, hass_client_no_auth):
    await set_up(hass, entry, aioclient_mock)
    client = await hass_client_no_auth()
    r = await client.get(f"/api/groundskeeper/{entry.entry_id}/imagery/yard.jpg")
    assert r.status == 401


def test_a_photo_name_is_a_plain_file_name():
    from custom_components.groundskeeper.views import photo_name_ok

    assert photo_name_ok("yard-d1c9.jpg")
    for bad in ("..", ".", ".hidden", "a/b.jpg", "a\b.jpg", "", "x" * 81):
        assert not photo_name_ok(bad), bad


async def test_diagnostics_never_carry_the_panels_address(hass, aioclient_mock, entry):
    refused = aiohttp.ClientError("Cannot connect to host 192.0.2.20:8425 ssl:default")
    await set_up(hass, entry, aioclient_mock, live_exc=refused)
    assert entry.runtime_data.live.error == "CannotConnect"  # aiohttp's text names the host; only the type is kept
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["live"]["error"] == "CannotConnect" and "192.0.2.20" not in str(diag)


async def test_live_entities_go_unavailable_when_the_feed_goes_quiet(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    assert hass.states.get(SENSOR).state == "garden"
    entry.runtime_data.live._task.cancel()  # no reconnect: nothing more comes from the panel
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=LIVE_STALE_S + 1))
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE
    entry.runtime_data.live.handle("state", LIVE_STATE)  # the next state brings it back
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "garden"


async def _subscribed(hass, entry, hass_ws_client):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/subscribe", "entry_id": entry.entry_id})
    assert (await ws.receive_json())["success"]
    assert (await ws.receive_json())["event"]["event"] == "snapshot"
    return ws


async def test_a_reload_ends_the_cards_subscription(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    ws = await _subscribed(hass, entry, hass_ws_client)
    old = entry.runtime_data.live.signal
    hass.config_entries.async_update_entry(entry, options={CONF_MAP: False})  # an option change reloads the entry
    await hass.async_block_till_done()
    assert (await ws.receive_json())["event"] == {"event": "closed", "data": None}
    assert entry.runtime_data.live.signal != old  # the new feed is on a new signal: the old subscription hears nothing
    async_dispatcher_send(hass, old, "pose", [POSE])
    async_dispatcher_send(hass, entry.runtime_data.live.signal, "pose", [POSE])
    await ws.send_json({"id": 2, "type": "ping"})
    assert (await ws.receive_json())["type"] == "pong"  # the next message is the pong: no pose came through


async def test_diagnostics_leave_out_the_position(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, live=sse(("state", LIVE_STATE), ("pose", [POSE])))
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["live"]["connected"] is not None and diag["live"]["has_pose"] is True
    assert "50.5" not in str(diag) and "-1.5" not in str(diag)
    assert diag["options"] == {CONF_MAP: True, CONF_FOLLOW_CAMERA: True, CONF_DEVICE_TRACKER: False}


async def test_the_options_flow(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MAP: True, CONF_FOLLOW_CAMERA: False, CONF_DEVICE_TRACKER: True}
    )
    await hass.async_block_till_done()
    assert entry.options == {CONF_MAP: True, CONF_FOLLOW_CAMERA: False, CONF_DEVICE_TRACKER: True}
    assert hass.states.get(SENSOR) is None and hass.states.get(CAMERA) is None  # gone, not "no longer provided"
    assert er.async_get(hass).async_get_entity_id("sensor", "groundskeeper", f"{entry.unique_id}_follow_camera") is None
    assert hass.states.get(TRACKER) is not None
