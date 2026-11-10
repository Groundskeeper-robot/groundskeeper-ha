"""Setting up from the UI: the address and token, every way the check can fail, and a new token later."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.groundskeeper.const import DOMAIN
from homeassistant import config_entries
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType

from .conftest import SUMMARY_URL, TOKEN, URL, summary


async def _start(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return result


async def test_a_panel_address_and_token_make_an_entry(hass, aioclient_mock):
    aioclient_mock.get(SUMMARY_URL, json=summary())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: " 192.0.2.20:8425/ ", CONF_API_TOKEN: f" {TOKEN} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == summary()["robot_id"]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("mock", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 403}, "invalid_auth"),
        ({"status": 404}, "panel_too_old"),
        ({"json": {"detail": "not a summary"}}, "panel_too_old"),
        ({"json": {**summary(), "api": 2}}, "unsupported_api"),
        ({"json": {**summary(), "robot_id": None}}, "no_robot"),
        ({"status": 500}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
    ],
)
async def test_a_failed_check_says_why_and_can_be_retried(hass, aioclient_mock, mock, error):
    aioclient_mock.get(SUMMARY_URL, **mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_API_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=summary())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_API_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_address_that_is_not_a_web_address_is_refused(hass, aioclient_mock):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://192.0.2.20", CONF_API_TOKEN: TOKEN}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert not aioclient_mock.mock_calls


async def test_the_same_robot_twice_updates_the_first(hass, aioclient_mock, entry):
    entry.add_to_hass(hass)
    new_url = "http://192.0.2.21:8425"
    aioclient_mock.get(f"{new_url}/api/ha/summary", json=summary())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: new_url, CONF_API_TOKEN: "gk_new"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == new_url and entry.data[CONF_API_TOKEN] == "gk_new"


async def test_a_new_token_after_a_refusal(hass, aioclient_mock, entry):
    entry.add_to_hass(hass)
    aioclient_mock.get(SUMMARY_URL, json=summary())
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "gk_fresh"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "gk_fresh"


async def test_a_token_for_another_robot_is_refused_on_reauth(hass, aioclient_mock, entry):
    entry.add_to_hass(hass)
    aioclient_mock.get(SUMMARY_URL, json=summary(robot_id="0123456789abcdef"))
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "gk_other"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "wrong_robot"
    assert entry.data[CONF_API_TOKEN] == TOKEN
