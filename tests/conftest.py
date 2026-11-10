"""Shared fixtures. The summaries in fixtures/ were read from the panel's demo mode (DEMO=1): a made-up robot in a
made-up yard. Tests never reach a real panel or robot."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.groundskeeper.const import DOMAIN, SUMMARY_PATH
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL

URL = "http://192.0.2.20:8425"
SUMMARY_URL = f"{URL}{SUMMARY_PATH}"
TOKEN = "gk_test_token"
FIXTURES = Path(__file__).parent / "fixtures"


def summary(name: str = "mowing", **changes: Any) -> dict[str, Any]:
    """A demo summary, with top-level fields changed."""
    data = copy.deepcopy(json.loads((FIXTURES / f"summary_{name}.json").read_text()))
    data.update(changes)
    return data


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Groundskeeper",
        unique_id=summary()["robot_id"],
        data={CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


async def set_up(hass, entry, aioclient_mock, data: dict[str, Any] | None = None, status: int = 200) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=data if data is not None else summary(), status=status)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
