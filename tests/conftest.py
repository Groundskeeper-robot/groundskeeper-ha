"""Shared fixtures. The summaries in fixtures/ were read from the panel's demo mode (DEMO=1): a made-up robot in a
made-up yard. Tests never reach a real panel or robot."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.groundskeeper.const import LIVE_API, LIVE_PATH, DOMAIN, SUMMARY_PATH
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL

URL = "http://192.0.2.20:8425"
SUMMARY_URL = f"{URL}{SUMMARY_PATH}"
LIVE_URL = f"{URL}{LIVE_PATH}"
TOKEN = "gk_test_token"
FIXTURES = Path(__file__).parent / "fixtures"


def summary(name: str = "mowing", **changes: Any) -> dict[str, Any]:
    """A demo summary, with top-level fields changed."""
    data = copy.deepcopy(json.loads((FIXTURES / f"summary_{name}.json").read_text()))
    data.update(changes)
    return data


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# The live feed as the panel's demo sends it (GET /api/ha/stream with DEMO=1): a state event and the pose samples
# after it. The demo has no Frigate, so LIVE_STATE adds a follow camera to the demo state.
DEMO_STATE = fixture("live_state")
DEMO_POSES = fixture("live_poses")
POSE = DEMO_POSES[-1]
LIVE_STATE = {**DEMO_STATE, "follow": {"camera": "garden", "covered": True}}
assert DEMO_STATE["api"] == LIVE_API


def sse(*events: tuple[str, Any]) -> bytes:
    """Server-sent events as the panel writes them."""
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


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


async def set_up(
    hass,
    entry,
    aioclient_mock,
    data: dict[str, Any] | None = None,
    status: int = 200,
    live: bytes | None = None,
    live_exc: Exception | None = None,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=data if data is not None else summary(), status=status)
    if live_exc is not None:
        aioclient_mock.get(LIVE_URL, exc=live_exc)
    else:
        aioclient_mock.get(LIVE_URL, content=live if live is not None else sse(("state", LIVE_STATE)))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
