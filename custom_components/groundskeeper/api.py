"""A small client for the Groundskeeper panel's Home Assistant API.

It reads only. Home Assistant talks to the panel, never to the robot, and the panel keeps the robot connection, the
safety checks and the audit log.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import REQUEST_TIMEOUT_S, SUMMARY_API, SUMMARY_PATH


class GroundskeeperError(Exception):
    """Base error."""


class CannotConnect(GroundskeeperError):
    """The panel did not answer."""


class InvalidAuth(GroundskeeperError):
    """The panel refused the token."""


class PanelTooOld(GroundskeeperError):
    """The panel has no Home Assistant summary yet."""


class UnsupportedApi(GroundskeeperError):
    """The panel speaks a newer summary than this integration knows."""


def normalize_url(url: str) -> str:
    """The panel's address as typed, with a scheme and without a trailing slash. Raises ValueError if unusable."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(url)
    return url


def robot_id(serial: str) -> str:
    """A stable id for the robot that is not its serial number, so the serial never lands in Home Assistant."""
    return hashlib.sha256(serial.encode()).hexdigest()[:16]


class GroundskeeperClient:
    """Reads GET /api/ha/summary with an API token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url
        self._token = token

    async def summary(self) -> dict[str, Any]:
        """The panel's summary of the robot. Raises a GroundskeeperError when it cannot be had."""
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.get(
                f"{self.url}{SUMMARY_PATH}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuth
                if resp.status == 404:
                    raise PanelTooOld
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err
        if not isinstance(data, dict) or not isinstance(data.get("api"), int):
            raise PanelTooOld
        if data["api"] != SUMMARY_API:
            raise UnsupportedApi
        return data
