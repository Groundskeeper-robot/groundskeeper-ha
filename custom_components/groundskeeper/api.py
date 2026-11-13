"""A small client for the Groundskeeper panel's Home Assistant API.

It reads only. Home Assistant talks to the panel, never to the robot, and the panel keeps the robot connection, the
safety checks and the audit log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from .const import (
    IMAGERY_PATH,
    LIVE_API,
    LIVE_PATH,
    LIVE_READ_TIMEOUT_S,
    MAP_PATH,
    REQUEST_TIMEOUT_S,
    SUMMARY_API,
    SUMMARY_PATH,
)


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


# The only panel files the integration passes on to its card: imagery tiles and the yard photo. Anything else the
# card asks for is refused here, so the integration's token never reads another panel path for a browser.
TILE_KINDS = ("world", "src")
IMAGERY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")
CAMERA_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def parse_sse(lines: list[str]) -> tuple[str, Any] | None:
    """One server-sent event (its lines, without the blank one that ends it) as (event, JSON data), or None."""
    event, data = "message", []
    for line in lines:
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if not data:
        return None
    try:
        return event, json.loads("\n".join(data))
    except ValueError:
        return None


class GroundskeeperClient:
    """Reads the panel's Home Assistant API with an API token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url
        self._token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _get_json(self, path: str) -> Any:
        try:
            async with self._session.get(
                f"{self.url}{path}", headers=self._headers, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuth
                if resp.status == 404:
                    raise PanelTooOld
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def map(self) -> dict[str, Any]:
        """The yard map for the card: GeoJSON, its bounds and the yard photo's details. A panel that has not read
        its map yet answers 503, which raises CannotConnect."""
        data = await self._get_json(MAP_PATH)
        imagery = await self._get_json(IMAGERY_PATH)
        if not isinstance(data, dict):
            raise CannotConnect("the map is not a JSON object")
        versions = data.get("versions") or []
        return {
            "geojson": data.get("geojson"),
            "bounds": data.get("bounds"),
            "fetched": data.get("fetched"),
            "map": versions[0].get("sha") if versions and isinstance(versions[0], dict) else None,
            "yard": imagery.get("yard") if isinstance(imagery, dict) else None,
        }

    async def live(self) -> AsyncIterator[tuple[str, Any]]:
        """The panel's live feed as (event, data) pairs until it ends. Raises a GroundskeeperError on failure,
        UnsupportedApi when its state events are a newer shape."""
        try:
            async with self._session.get(
                f"{self.url}{LIVE_PATH}",
                headers={**self._headers, "Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=REQUEST_TIMEOUT_S, sock_read=LIVE_READ_TIMEOUT_S
                ),
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuth
                if resp.status == 404:
                    raise PanelTooOld
                resp.raise_for_status()
                block: list[str] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line:
                        block.append(line)
                        continue
                    ev = parse_sse(block)
                    block = []
                    if ev is None:
                        continue
                    if ev[0] == "state" and isinstance(ev[1], dict) and ev[1].get("api") != LIVE_API:
                        raise UnsupportedApi
                    yield ev
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def file(self, path: str) -> tuple[int, bytes, str | None]:
        """A tile or the yard photo for the card, by its panel path (checked by the caller): status, body and
        content type. Raises CannotConnect when the panel does not answer."""
        try:
            async with self._session.get(
                f"{self.url}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
                allow_redirects=False,  # only the checked path: a redirect must not carry the token elsewhere
            ) as resp:
                return resp.status, await resp.read(), resp.headers.get("Content-Type")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def frigate_stream(self, camera: str) -> aiohttp.ClientResponse:
        """The panel's live MJPEG of one Frigate camera. The caller closes the response."""
        if not CAMERA_NAME.fullmatch(camera):
            raise ValueError(camera)
        try:
            resp = await self._session.get(
                f"{self.url}/api/frigate/stream/{quote(camera)}.mjpeg",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=REQUEST_TIMEOUT_S, sock_read=LIVE_READ_TIMEOUT_S
                ),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err
        if resp.status != 200:
            resp.close()
            raise CannotConnect(f"stream answered {resp.status}")
        return resp

    async def frigate_snapshot(self, camera: str, height: int | None = None) -> bytes | None:
        """The latest JPEG of one Frigate camera through the panel, or None."""
        if not CAMERA_NAME.fullmatch(camera):
            return None
        path = f"/api/frigate/snapshot/{quote(camera)}.jpg"
        if height:
            path += f"?height={int(height)}"
        status, body, _ = await self.file(path)
        return body if status == 200 else None

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
