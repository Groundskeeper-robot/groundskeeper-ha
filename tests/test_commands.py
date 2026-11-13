"""Commands through the panel (the Commands option): the mower's actions and the card's websocket commands.
The panel is a mock; its answers were read from the demo (DEMO=1), which has no route to a robot, so it never sends.
A sent answer is the one shape the demo cannot give and is written here."""

from __future__ import annotations

import pytest

from custom_components.groundskeeper.const import (
    CONF_COMMANDS,
    CONTROL_PREVIEW_PATH,
    CONTROL_SEND_PATH,
    PLANS_PATH,
)
from homeassistant.components.lawn_mower import LawnMowerEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.exceptions import HomeAssistantError

from .conftest import URL, fixture, set_up, summary

MOWER = "lawn_mower.groundskeeper"
SEND_URL = f"{URL}{CONTROL_SEND_PATH}"
PREVIEW_URL = f"{URL}{CONTROL_PREVIEW_PATH}"
SENT = {"sent": True, "cmd": "pause", "payload": {}, "checks": [], "route": "cloud", "confirm": "checking"}
REFUSED = fixture("control_send_refused")   # control on, the checks failed
OFF = fixture("control_send_off")           # the panel's Control switch off


async def commands(hass, entry, aioclient_mock, on=True, data=None):
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_COMMANDS: on})
    await set_up(hass, entry, aioclient_mock, data=data)


def posts(aioclient_mock):
    return [(str(url), body) for method, url, body, *_ in aioclient_mock.mock_calls if method.upper() == "POST"]


async def press(hass, service):
    await hass.services.async_call("lawn_mower", service, {ATTR_ENTITY_ID: MOWER}, blocking=True)


async def test_off_by_default_the_mower_offers_no_actions(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    assert hass.states.get(MOWER).attributes[ATTR_SUPPORTED_FEATURES] == 0
    with pytest.raises(HomeAssistantError):
        await press(hass, "dock")
    assert posts(aioclient_mock) == []


async def test_with_commands_on_the_mower_starts_pauses_and_docks(hass, aioclient_mock, entry):
    await commands(hass, entry, aioclient_mock)
    features = hass.states.get(MOWER).attributes[ATTR_SUPPORTED_FEATURES]
    assert features == LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.DOCK
    aioclient_mock.post(SEND_URL, json=SENT)
    await press(hass, "pause")
    await press(hass, "dock")
    await press(hass, "start_mowing")    # mowing, not paused: the plan the panel's start check names
    assert posts(aioclient_mock) == [
        (SEND_URL, {"cmd": "pause", "args": {}, "confirm": True}),
        (SEND_URL, {"cmd": "cmd_recharge", "args": {}, "confirm": True}),
        (SEND_URL, {"cmd": "start_plan", "args": {"plan_id": summary()["can_start"]["plan_id"]}, "confirm": True}),
    ]


async def test_start_on_a_paused_robot_resumes(hass, aioclient_mock, entry):
    await commands(hass, entry, aioclient_mock, data=summary(activity="paused"))
    aioclient_mock.post(SEND_URL, json=SENT)
    await press(hass, "start_mowing")
    assert posts(aioclient_mock) == [(SEND_URL, {"cmd": "resume", "args": {}, "confirm": True})]


async def test_a_refusal_says_the_panels_reason(hass, aioclient_mock, entry):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, json=REFUSED)
    with pytest.raises(HomeAssistantError, match="The panel cannot reach the robot"):
        await press(hass, "start_mowing")
    aioclient_mock.clear_requests()
    aioclient_mock.post(SEND_URL, json=OFF)
    with pytest.raises(HomeAssistantError, match="Control is switched off"):
        await press(hass, "dock")


async def test_no_plan_to_start_sends_nothing(hass, aioclient_mock, entry):
    await commands(hass, entry, aioclient_mock, data=summary(can_start=None))
    with pytest.raises(HomeAssistantError, match="no plan"):
        await press(hass, "start_mowing")
    assert posts(aioclient_mock) == []


async def test_the_card_gets_the_plans_and_the_panels_choice(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.get(f"{URL}{PLANS_PATH}", json=fixture("control_plans"))
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/plans", "entry_id": entry.entry_id})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"] == {"plans": [{"id": 1, "name": "Mow All"}, {"id": 2, "name": "Front lawn"}],
                             "default": summary()["can_start"]["plan_id"]}
    assert all("fresh" not in str(call[1]) for call in aioclient_mock.mock_calls)   # never asks the robot


async def test_a_preview_passes_on_the_checks_and_sends_nothing(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    answer = fixture("control_preview_start")
    aioclient_mock.post(PREVIEW_URL, json=answer)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/preview", "entry_id": entry.entry_id,
                        "cmd": "start_plan", "plan_id": 2})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"]["would_send"] is False and msg["result"]["checks"] == answer["checks"]
    assert posts(aioclient_mock) == [(PREVIEW_URL, {"cmd": "start_plan", "args": {"plan_id": 2}})]


async def test_the_card_sends_and_hears_why_not(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, json={**REFUSED, "payload": {"id": 2}, "route": "cloud"})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/command", "entry_id": entry.entry_id,
                        "cmd": "start_plan", "plan_id": 2})
    msg = await ws.receive_json()
    assert msg["success"], msg
    result = msg["result"]
    assert result["sent"] is False and "The panel cannot reach the robot" in result["why_not"]
    assert "payload" not in result and "route" not in result
    body = posts(aioclient_mock)[0][1]
    assert body == {"cmd": "start_plan", "args": {"plan_id": 2}, "confirm": True} and "override" not in body


@pytest.mark.parametrize(
    ("msg", "code"),
    [
        ({"cmd": "cmd_vel"}, "invalid_format"),
        ({"cmd": "smart_vision_control"}, "invalid_format"),
        ({"cmd": "set_working_state"}, "invalid_format"),
        ({"cmd": "start_plan"}, "invalid_format"),
        ({"cmd": "pause", "override": True}, "invalid_format"),
        ({"cmd": "start_plan", "plan_id": True}, "invalid_format"),
        ({"cmd": "start_plan", "plan_id": -1}, "invalid_format"),
        ({"cmd": "start_plan", "plan_id": "1"}, "invalid_format"),
    ],
)
async def test_only_the_five_run_commands_go_through(hass, aioclient_mock, entry, hass_ws_client, msg, code):
    await commands(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    for i, kind in enumerate(("command", "preview"), start=1):
        await ws.send_json({"id": i, "type": f"groundskeeper/{kind}", "entry_id": entry.entry_id, **msg})
        reply = await ws.receive_json()
        assert not reply["success"] and reply["error"]["code"] == code
    assert posts(aioclient_mock) == []


async def test_commands_off_the_card_is_told_so(hass, aioclient_mock, entry, hass_ws_client):
    await set_up(hass, entry, aioclient_mock)
    ws = await hass_ws_client(hass)
    for i, kind in enumerate(("plans", "preview", "command"), start=1):
        msg = {"id": i, "type": f"groundskeeper/{kind}", "entry_id": entry.entry_id}
        await ws.send_json(msg if kind == "plans" else {**msg, "cmd": "pause"})
        reply = await ws.receive_json()
        assert not reply["success"] and reply["error"]["code"] == "commands_off"
    assert posts(aioclient_mock) == []


async def test_a_panel_that_turns_it_down_is_reported(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, status=400, json={"detail": "confirm: true is required"})
    with pytest.raises(HomeAssistantError, match="confirm: true is required"):
        await press(hass, "pause")


async def test_the_helpers_refuse_with_the_option_off(hass, aioclient_mock, entry):
    from custom_components.groundskeeper import commands as cmds

    await set_up(hass, entry, aioclient_mock)
    coordinator = entry.runtime_data
    for call in (cmds.send, cmds.preview):
        with pytest.raises(cmds.CommandsOff):
            await call(coordinator, "pause")
    assert posts(aioclient_mock) == []


async def test_switching_commands_off_takes_the_actions_away(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    assert hass.states.get(MOWER).attributes[ATTR_SUPPORTED_FEATURES] != 0
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"map": True, "follow_camera": True, "device_tracker": False, CONF_COMMANDS: False}
    )
    await hass.async_block_till_done()
    assert hass.states.get(MOWER).attributes[ATTR_SUPPORTED_FEATURES] == 0
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/command", "entry_id": entry.entry_id, "cmd": "pause"})
    reply = await ws.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "commands_off"
    assert posts(aioclient_mock) == []


async def test_a_panel_too_old_for_token_commands_says_so(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, status=403, json={"detail": "an API token cannot change this"})
    with pytest.raises(HomeAssistantError, match="an API token cannot change this"):
        await press(hass, "pause")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/command", "entry_id": entry.entry_id, "cmd": "pause"})
    reply = await ws.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "rejected"
    assert "an API token cannot change this" in reply["error"]["message"]


async def test_a_summary_without_a_start_check_is_not_a_crash(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock, data=summary(can_start="not a dict"))
    with pytest.raises(HomeAssistantError, match="no plan"):
        await press(hass, "start_mowing")
    aioclient_mock.get(f"{URL}{PLANS_PATH}", json=fixture("control_plans"))
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/plans", "entry_id": entry.entry_id})
    reply = await ws.receive_json()
    assert reply["success"] and reply["result"]["default"] is None


async def test_no_answer_is_not_called_not_sent(hass, aioclient_mock, entry, hass_ws_client):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, exc=TimeoutError)
    with pytest.raises(HomeAssistantError, match="may or may not have gone out"):
        await press(hass, "dock")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "groundskeeper/command", "entry_id": entry.entry_id, "cmd": "pause"})
    reply = await ws.receive_json()
    assert reply["error"]["code"] == "panel_unavailable" and "may or may not" in reply["error"]["message"]


async def test_a_user_without_control_of_the_mower_cannot_send(hass, aioclient_mock, entry, hass_ws_client,
                                                               hass_read_only_access_token):
    await commands(hass, entry, aioclient_mock)
    aioclient_mock.post(SEND_URL, json=SENT)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": "groundskeeper/command", "entry_id": entry.entry_id, "cmd": "pause"})
    reply = await ws.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "unauthorized"
    assert posts(aioclient_mock) == []
