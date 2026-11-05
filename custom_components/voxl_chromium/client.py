"""Validated native API transport. Never include upstream text in errors."""

import ipaddress
import json
import re
from urllib.parse import urlsplit

import aiohttp
import probatio as vol
from homeassistant.exceptions import HomeAssistantError

DOMAIN = "voxl_chromium"
MAX_PNG = 32 * 1024 * 1024
MAX_MP4 = 128 * 1024 * 1024


class CannotConnect(HomeAssistantError):
    """Sanitized transport or protocol failure."""


class InvalidAuth(CannotConnect):
    """Rejected credentials."""


def host(value):
    if (
        not isinstance(value, str)
        or not value
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
    ):
        raise vol.Invalid("Invalid host")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        pass
    value = value.lower().rstrip(".")
    if len(value) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise vol.Invalid("Invalid host")
    return value


def token(value):
    if (
        not isinstance(value, str)
        or len(value) < 32
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise vol.Invalid(
            "Token must contain at least 32 printable non-space ASCII characters"
        )
    return value


def endpoint(data):
    name = host(data["host"])
    return f"[{name}]:{data['port']}" if ":" in name else f"{name}:{data['port']}"


def url(value):
    if (
        not isinstance(value, str)
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        raise vol.Invalid("Invalid HTTP URL")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError
        _ = parts.port
    except ValueError:
        raise vol.Invalid("Invalid HTTP URL") from None
    return value


class Client:
    def __init__(self, session, data):
        self.session = session
        self.base = "http://" + endpoint(data)
        self._token = token(data["token"])

    async def request(self, method, path, data=None):
        try:
            async with self.session.request(
                method,
                self.base + path,
                json=data,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=75),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise InvalidAuth("Authentication failed")
                if response.status != 200:
                    raise CannotConnect("Native API request failed")
                limit = MAX_MP4 if path == "/record" else MAX_PNG
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise CannotConnect("Native API response too large")
                if path in ("/screenshot", "/record"):
                    if path == "/screenshot" and not body.startswith(
                        b"\x89PNG\r\n\x1a\n"
                    ):
                        raise CannotConnect("Invalid screenshot response")
                    if path == "/record" and body[4:8] != b"ftyp":
                        raise CannotConnect("Invalid recording response")
                    return bytes(body)
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError
                if path != "/wait" and result.get("ok") is not True:
                    raise ValueError
                if path == "/wait":
                    if any(
                        type(result.get(k)) is not bool
                        for k in ("changed", "timed_out")
                    ) or any(
                        type(result.get(k)) is not int or result[k] < 0
                        for k in ("seq", "waited_ms")
                    ):
                        raise ValueError
                    result = {
                        k: result[k]
                        for k in ("changed", "timed_out", "seq", "waited_ms")
                    }
                return result
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError):
            raise CannotConnect("Unable to communicate with native API") from None

    async def health(self):
        await self.request("GET", "/health")
