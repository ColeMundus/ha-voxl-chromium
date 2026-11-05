"""Regressions found by independent automation boundary review."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant.helpers import entity_registry as er
from test_integration import DATA, DOMAIN
from test_runtime import loaded

from custom_components.voxl_chromium import client as transport


async def test_endpoint_reconfigure_preserves_image_entity(hass):
    entry = await loaded(hass)
    registry = er.async_get(hass)
    before = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(before) == 1
    entity_id = before[0].entity_id
    unique_id = before[0].unique_id
    with patch.object(transport.Client, "health", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
            data={**DATA, "host": "new-addon"},
        )
        assert result["type"] == "abort"
        await hass.async_block_till_done()
    after = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(after) == 1
    assert after[0].entity_id == entity_id
    assert after[0].unique_id == unique_id == f"{entry.entry_id}_last_screenshot"


@pytest.mark.allow_hosts("127.0.0.1")
@pytest.mark.parametrize(
    "path,size,signature",
    [
        ("/screenshot", 17 * 1024 * 1024, b"\x89PNG\r\n\x1a\n"),
        ("/record", 65 * 1024 * 1024, b"\x00\x00\x00\x18ftypisom"),
    ],
)
async def test_media_above_previous_caps_matches_backend(
    aiohttp_server, socket_enabled, path, size, signature
):
    async def handler(request):
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(signature)
        remaining = size - len(signature)
        while remaining:
            chunk = b"x" * min(65536, remaining)
            await response.write(chunk)
            remaining -= len(chunk)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get(path, handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = transport.Client(
            session, {**DATA, "host": "127.0.0.1", "port": server.port}
        )
        content = await client.request("GET", path)
    assert len(content) == size
    assert content.startswith(signature)
    assert transport.MAX_PNG == 32 * 1024 * 1024
    assert transport.MAX_MP4 == 128 * 1024 * 1024


@pytest.mark.allow_hosts("127.0.0.1")
async def test_oversize_media_still_rejected(
    aiohttp_server, socket_enabled, monkeypatch
):
    monkeypatch.setattr(transport, "MAX_PNG", 32)
    app = web.Application()

    async def handler(request):
        return web.Response(body=b"x" * 33)

    app.router.add_get("/screenshot", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = transport.Client(
            session, {**DATA, "host": "127.0.0.1", "port": server.port}
        )
        with pytest.raises(transport.CannotConnect, match="too large"):
            await client.request("GET", "/screenshot")
