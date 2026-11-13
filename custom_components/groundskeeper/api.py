"""A small client for the Groundskeeper panel's Home Assistant API.

Home Assistant talks to the panel, never to the robot, and the panel keeps the robot connection, the safety checks and
the audit log. It reads, and with the Commands option asks the panel to send one of five run commands (COMMANDS):
the panel decides, with its Control switch and its checks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import hashlib
import json
import math
import re
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from .const import (
    COMMANDS,
    CONTROL_PREVIEW_PATH,
    CONTROL_SEND_PATH,
    FILE_MAX_BYTES,
    GROUND_PATH,
    GROUND_TILE_MAX_BYTES,
    GROUND_TIMEOUT_S,
    IMAGERY_PATH,
    LIVE_API,
    LIVE_PATH,
    LIVE_READ_TIMEOUT_S,
    MAP_PATH,
    PLANS_PATH,
    REQUEST_TIMEOUT_S,
    SUMMARY_API,
    SUMMARY_PATH,
    VENDOR,
    VENDOR_MAX_BYTES,
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


class NoGround(GroundskeeperError):
    """The panel has no ground for the 3D view yet (no lidar there, or nothing read)."""


class CommandRejected(GroundskeeperError):
    """The panel turned the request down before its checks (a 400: a missing plan, say)."""


# What the integration passes on from the panel's answer to a preview or a send: the verdict and why, not the payload.
COMMAND_KEYS = ("cmd", "enabled", "would_send", "sent", "reason", "refused", "error", "checks", "why", "confirm")


def command_body(cmd: str, plan_id: int | None) -> dict[str, Any]:
    """The request for one of COMMANDS; start_plan needs a plan. Raises ValueError for anything else."""
    if cmd not in COMMANDS:
        raise ValueError(f"{cmd} is not a command Home Assistant may ask for")
    if cmd == "start_plan":
        if not isinstance(plan_id, int) or isinstance(plan_id, bool) or plan_id < 0:
            raise ValueError("start_plan needs a plan")
        return {"cmd": cmd, "args": {"plan_id": plan_id}}
    return {"cmd": cmd, "args": {}}


GROUND_VERSION = re.compile(r"[0-9a-f]{1,40}")


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def ground_meta(data: Any) -> dict[str, Any]:
    """The panel's ground for the 3D view, checked and cut to what the card needs. Raises CannotConnect if it is not
    of the expected shape."""
    if not isinstance(data, dict):
        raise CannotConnect("the ground is not a JSON object")
    version, bounds = data.get("version"), data.get("bounds")
    lo, hi = data.get("minzoom"), data.get("maxzoom")
    if not (isinstance(version, str) and GROUND_VERSION.fullmatch(version)):
        raise CannotConnect("the ground has no version")
    if not (isinstance(bounds, list) and len(bounds) == 4 and all(_finite(b) for b in bounds)):
        raise CannotConnect("the ground has no bounds")
    if not all(isinstance(z, int) and not isinstance(z, bool) and 0 <= z <= 24 for z in (lo, hi)) or lo > hi:
        raise CannotConnect("the ground has no zooms")
    if data.get("encoding") != "terrarium":
        raise CannotConnect("the ground is in an encoding the card cannot read")
    relief = data.get("relief_m")
    sources = data.get("sources")
    return {
        "version": version,
        "bounds": bounds,
        "minzoom": lo,
        "maxzoom": hi,
        "relief_m": relief if _finite(relief) else None,
        "sources": [s for s in sources if isinstance(s, str)] if isinstance(sources, list) else [],
        "encoding": "terrarium",
    }


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

    async def _get_json(self, path: str, timeout: float = REQUEST_TIMEOUT_S) -> Any:
        try:
            async with self._session.get(
                f"{self.url}{path}", headers=self._headers, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuth
                if resp.status == 404:
                    raise PanelTooOld
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{self.url}{path}",
                json=body,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
                allow_redirects=False,
            ) as resp:
                if resp.status == 401:
                    raise InvalidAuth
                if resp.status == 404:
                    raise PanelTooOld
                if 300 <= resp.status < 400:
                    raise CannotConnect(f"the panel answered with a redirect ({resp.status}): use its final address")
                if resp.status in (400, 403):    # 403: a panel too old to take commands from a token, say
                    try:
                        detail = (await resp.json(content_type=None) or {}).get("detail")
                    except (ValueError, AttributeError):
                        detail = None
                    raise CommandRejected(str(detail or f"the panel turned the request down ({resp.status})"))
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err
        if not isinstance(data, dict):
            raise CannotConnect("the panel's answer is not a JSON object")
        return {k: data[k] for k in COMMAND_KEYS if k in data}

    async def plans(self) -> list[dict[str, Any]]:
        """The robot's plans as the panel last read them, as {id, name}."""
        data = await self._get_json(PLANS_PATH)
        plans = data.get("plans") if isinstance(data, dict) else None
        if not isinstance(plans, list):
            return []
        return [
            {"id": p["id"], "name": str(p.get("name") or f"Plan {p['id']}")}
            for p in plans
            if isinstance(p, dict) and isinstance(p.get("id"), int) and not isinstance(p.get("id"), bool)
        ]

    async def preview(self, cmd: str, plan_id: int | None = None) -> dict[str, Any]:
        """Would the panel send this now, and why not: its checks, without sending anything."""
        return await self._post_json(CONTROL_PREVIEW_PATH, command_body(cmd, plan_id))

    async def send(self, cmd: str, plan_id: int | None = None) -> dict[str, Any]:
        """Ask the panel to send one of COMMANDS. It sends only with its Control switch on and every check passing;
        `sent` says whether it went. Never an override: the panel gives a token none anyway."""
        return await self._post_json(CONTROL_SEND_PATH, {**command_body(cmd, plan_id), "confirm": True})

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

    async def run_track(self, run_id: int, since: float = 0) -> list[list[Any]]:
        """A run's position samples after `since`, as [time, lon, lat, rtk status] (4 fixed, 5 float, 1 single)."""
        path = f"/api/runs/{int(run_id)}/points"
        if since:
            path += f"?since={float(since)}"
        data = await self._get_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("fields"), list):
            raise CannotConnect("the run's points are not in the expected shape")
        fields = data["fields"]
        try:
            ix = [fields.index(k) for k in ("ts", "lon", "lat")]
        except ValueError as err:
            raise CannotConnect("the run's points have no position") from err
        rtk = fields.index("rtk_status") if "rtk_status" in fields else None
        rows = data.get("rows") or []
        if not isinstance(rows, list):
            raise CannotConnect("the run's points are not in the expected shape")
        out = []
        for row in rows:
            if not isinstance(row, list) or len(row) < len(fields):
                continue
            t, lon, lat = (row[i] for i in ix)
            if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (t, lon, lat)):
                out.append([t, lon, lat, row[rtk] if rtk is not None else None])
        return out

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

    async def file(
        self, path: str, timeout: float = REQUEST_TIMEOUT_S, max_bytes: int = FILE_MAX_BYTES
    ) -> tuple[int, bytes, str | None, str | None]:
        """A tile, the yard photo or a library for the card, by its panel path (checked by the caller): status, body,
        content type and cache header. Raises CannotConnect when the panel does not answer, or sends more than
        `max_bytes`."""
        try:
            async with self._session.get(
                f"{self.url}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,  # only the checked path: a redirect must not carry the token elsewhere
            ) as resp:
                length = resp.headers.get("Content-Length", "")
                if length.isascii() and length.isdigit() and int(length) > max_bytes:
                    raise CannotConnect("the panel sent more than a file for the card can be")
                chunks, size = [], 0
                async for chunk in resp.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise CannotConnect("the panel sent more than a file for the card can be")
                    chunks.append(chunk)
                body = b"".join(chunks)
                return resp.status, body, resp.headers.get("Content-Type"), resp.headers.get("Cache-Control")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def ground(self) -> dict[str, Any]:
        """The ground for the card's 3D view (see ground_meta). The first read after a panel starts can wait on a
        lidar download. Raises NoGround when the panel has none."""
        try:
            data = await self._get_json(GROUND_PATH, timeout=GROUND_TIMEOUT_S)
        except PanelTooOld as err:  # the route is older than the summary; a 404 here is the panel's "no terrain"
            raise NoGround from err
        return ground_meta(data)

    async def ground_tile(self, z: int, x: int, y: int, version: str | None) -> tuple[int, bytes, bool]:
        """One Terrarium tile of the ground: status, body, and whether it is of the ground `version` names (the panel
        says so with its cache header: an older version's address gets the new ground, not to be kept)."""
        path = f"{GROUND_PATH}/{int(z)}/{int(x)}/{int(y)}.png"
        if version is not None:
            if not GROUND_VERSION.fullmatch(version):
                raise ValueError(version)
            path += f"?v={version}"
        status, body, _ctype, cache = await self.file(path, timeout=GROUND_TIMEOUT_S, max_bytes=GROUND_TILE_MAX_BYTES)
        cache = (cache or "").lower()
        return status, body, "max-age" in cache and "no-store" not in cache  # kept only when the panel says so

    async def vendor(self, name: str) -> bytes:
        """One of the panel's libraries for the card's 3D view, only with the exact contents VENDOR names. Raises
        ValueError for a name not in VENDOR, CannotConnect for anything but those contents."""
        if name not in VENDOR:
            raise ValueError(name)
        status, body, _ctype, _cache = await self.file(f"/vendor/{name}", max_bytes=VENDOR_MAX_BYTES)
        if status != 200:
            raise CannotConnect(f"the panel answered {status} for {name}")
        if hashlib.sha256(body).hexdigest() != VENDOR[name][1]:
            raise CannotConnect(f"the panel's {name} is not the version this integration knows")
        return body

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
        status, body, *_ = await self.file(path)
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
