"""What Home Assistant shows for each summary, and that this version sends nothing to the robot."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.groundskeeper.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import SUMMARY_URL, set_up, summary

MOWER = "lawn_mower.groundskeeper"


async def test_a_mowing_robot(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    mower = hass.states.get(MOWER)
    assert mower.state == "mowing"
    assert mower.attributes["supported_features"] == 0
    assert mower.attributes["panel_state"] == "mowing" and mower.attributes["head"] == "Mower Pro"
    assert hass.states.get("sensor.groundskeeper_battery").state == "72"
    assert hass.states.get("sensor.groundskeeper_positioning").state == "RTK fixed"
    assert hass.states.get("sensor.groundskeeper_lawn").state == "Dry"
    assert hass.states.get("binary_sensor.groundskeeper_run_in_progress").state == STATE_ON
    assert hass.states.get("binary_sensor.groundskeeper_robot_online").state == STATE_ON
    assert hass.states.get("binary_sensor.groundskeeper_can_start").state == STATE_OFF
    assert hass.states.get("binary_sensor.groundskeeper_control").state == STATE_OFF
    assert hass.states.get("binary_sensor.groundskeeper_needs_attention").state == STATE_OFF
    assert hass.states.get("binary_sensor.groundskeeper_lost_contact").state == STATE_OFF
    last = hass.states.get("sensor.groundskeeper_last_run")
    assert dt_util.parse_datetime(last.state) == dt_util.utc_from_timestamp(summary()["last_run"]["ended"])
    assert last.attributes["result"] == "finished"


async def test_the_start_check_gives_the_panels_first_reason(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    check = hass.states.get("sensor.groundskeeper_start_check")
    assert check.state == "Control is switched off"
    assert check.attributes["plan"] == "Mow All"
    assert check.attributes["reasons"][0] == "Control is switched off: Switch it on in Settings → Safety"


async def test_a_start_that_would_go_out_reads_ready(hass, aioclient_mock, entry):
    data = summary()
    data["can_start"] = {**data["can_start"], "would_send": True, "why": [], "reason": None}
    await set_up(hass, entry, aioclient_mock, data)
    assert hass.states.get("sensor.groundskeeper_start_check").state == "Ready"
    assert hass.states.get("binary_sensor.groundskeeper_can_start").state == STATE_ON


async def test_no_start_check_from_the_panel_is_unknown(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, summary(can_start=None))
    assert hass.states.get("sensor.groundskeeper_start_check").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.groundskeeper_can_start").state == STATE_UNKNOWN


async def test_the_next_window_is_a_time_with_its_plan(hass, aioclient_mock, entry):
    data = summary()
    await set_up(hass, entry, aioclient_mock, data)
    nxt = hass.states.get("sensor.groundskeeper_next_window")
    assert dt_util.parse_datetime(nxt.state) == dt_util.utc_from_timestamp(data["next_window"]["next"])
    assert nxt.attributes["window"] == "Weekday mornings" and nxt.attributes["plan"] == "Mow All"


@pytest.mark.parametrize(
    ("name", "activity"),
    [("mowing", "mowing"), ("transit", "mowing"), ("returning", "returning"), ("docked", "docked")],
)
async def test_each_demo_activity(hass, aioclient_mock, entry, name, activity):
    await set_up(hass, entry, aioclient_mock, summary(name))
    assert hass.states.get(MOWER).state == activity


@pytest.mark.parametrize(
    ("changes", "activity"),
    [
        ({"activity": "paused", "state": "paused", "pause_reason": "bumper"}, "paused"),
        ({"activity": "charging_break", "state": "charging mid-plan"}, "docked"),
        ({"activity": "moving", "state": "moving"}, "idle"),
        ({"activity": "idle", "state": "idle"}, "idle"),
        ({"state": "error", "error_code": 7}, "error"),
        ({"stalled": True, "stalled_s": 420}, "error"),
        ({"rtk_wait": True, "stalled_s": 420}, "mowing"),
        ({"activity": None, "state": "waiting for data"}, STATE_UNKNOWN),
    ],
)
async def test_the_other_activities(hass, aioclient_mock, entry, changes, activity):
    await set_up(hass, entry, aioclient_mock, summary(**changes))
    assert hass.states.get(MOWER).state == activity


async def test_a_robot_that_stopped_reporting(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, summary(snapshot_age_s=300))
    assert hass.states.get(MOWER).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.groundskeeper_battery").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.groundskeeper_robot_online").state == STATE_OFF
    assert hass.states.get("sensor.groundskeeper_state").state == "mowing"
    assert hass.states.get("sensor.groundskeeper_lawn").state == "Dry"


async def test_a_panel_that_stops_answering(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, status=502)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(MOWER).state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.groundskeeper_robot_online").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=summary("docked"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert hass.states.get(MOWER).state == "docked"


async def test_a_panel_that_is_down_at_start_retries(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, status=502)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_refused_token_asks_for_a_new_one(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, status=401)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


@pytest.mark.parametrize("service", ["start_mowing", "pause", "dock"])
async def test_this_version_sends_no_commands(hass, aioclient_mock, entry, service):
    await set_up(hass, entry, aioclient_mock)
    calls = len(aioclient_mock.mock_calls)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("lawn_mower", service, {"entity_id": MOWER}, blocking=True)
    assert len(aioclient_mock.mock_calls) == calls
    assert all(method == "GET" for method, *_ in aioclient_mock.mock_calls)


async def test_noisy_values_start_disabled(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    reg = er.async_get(hass)
    for eid in ("sensor.groundskeeper_satellites", "sensor.groundskeeper_run_distance", "sensor.groundskeeper_cloud_connection"):
        assert reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(eid) is None


async def test_diagnostics_leave_out_the_token_address_and_serial(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    text = str(await async_get_config_entry_diagnostics(hass, entry))
    assert "gk_test_token" not in text and "192.0.2.20" not in text and "YB0000DEMO0001" not in text
    assert "Mow All" in text


async def test_unload(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_a_stuck_robot_needs_attention(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, summary(stalled=True, stalled_s=420, lost_contact=True))
    assert hass.states.get(MOWER).state == "error"
    assert hass.states.get(MOWER).attributes["stalled_s"] == 420
    assert hass.states.get("binary_sensor.groundskeeper_needs_attention").state == STATE_ON
    assert hass.states.get("binary_sensor.groundskeeper_lost_contact").state == STATE_ON


async def test_waiting_for_the_rtk_fix_is_not_stuck(hass, aioclient_mock, entry):
    await set_up(hass, entry, aioclient_mock, summary(rtk_wait=True, stalled_s=420))
    assert hass.states.get("binary_sensor.groundskeeper_needs_attention").state == STATE_OFF
    assert hass.states.get("binary_sensor.groundskeeper_waiting_for_rtk_fix").state == STATE_ON
