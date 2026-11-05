"""Real HA flow/runtime tests; only transport is mocked."""

import logging
import os
from unittest.mock import AsyncMock, patch

import aiohttp
import probatio as vol
import pytest
from aiohttp import web
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_integration import DATA, DOMAIN

from custom_components.voxl_chromium.client import (
    CannotConnect,
    Client,
    InvalidAuth,
    endpoint,
)
from custom_components.voxl_chromium.media import write_media

PNG = b"\x89PNG\r\n\x1a\nfixture"
MP4 = b"\x00\x00\x00\x18ftypisomfixture"


async def loaded(hass, data=DATA):
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=endpoint(data))
    entry.add_to_hass(hass)
    with patch.object(Client, "health", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(
    "error,code",
    [
        (InvalidAuth("Authentication failed"), "invalid_auth"),
        (CannotConnect("Unavailable"), "cannot_connect"),
    ],
)
async def test_flow_errors(hass, error, code, caplog):
    with (
        patch.object(Client, "health", side_effect=error),
        caplog.at_level(logging.DEBUG),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
    assert result["errors"] == {"base": code}
    assert DATA["token"] not in caplog.text


async def test_duplicate_canonical_endpoint(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    with patch.object(Client, "health", new_callable=AsyncMock) as health:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={**DATA, "host": "VOXL-ADDON."}
        )
    assert result["reason"] == "already_configured"
    health.assert_not_called()


@pytest.mark.parametrize("token", ["short", "x" * 31, "x" * 32 + "\n"])
async def test_invalid_token(hass, token):
    with patch.object(Client, "health", new_callable=AsyncMock) as health:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={**DATA, "token": token}
        )
    assert result["errors"] == {"base": "invalid_input"}
    health.assert_not_called()


async def test_reauth_and_reconfigure(hass):
    entry = await loaded(hass)
    with patch.object(Client, "health", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**DATA, "token": "z" * 40}
        )
        assert result["reason"] == "reauth_successful"
        await hass.async_block_till_done()
        assert entry.data["token"] == "z" * 40
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**DATA, "host": "192.168.1.8"}
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.unique_id == "192.168.1.8:8100"


async def test_media_actions_image_and_permissions(hass, tmp_path, caplog):
    entry = await loaded(hass)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    params = {"config_entry_id": entry.entry_id}
    with patch.object(
        Client, "request", new_callable=AsyncMock, return_value=PNG
    ) as request:
        result = await hass.services.async_call(
            DOMAIN, "screenshot", params, blocking=True, return_response=True
        )
        await hass.async_block_till_done()
        assert result == {"bytes": len(PNG), "saved": False}
        assert hass.data[DOMAIN][entry.entry_id].image == PNG
        state = hass.states.get("image.last_screenshot")
        assert state is not None and state.state != "unavailable"
        assert DATA["token"] not in str(state.attributes)
        assert str(tmp_path) not in str(state.attributes)
        target = tmp_path / "screen.png"
        await hass.services.async_call(
            DOMAIN, "screenshot", {**params, "filename": str(target)}, blocking=True
        )
        assert target.read_bytes() == PNG
        assert os.stat(target).st_mode & 0o777 == 0o600
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN, "screenshot", {**params, "filename": str(target)}, blocking=True
            )
        request.assert_awaited_with("GET", "/screenshot", None)
    with patch.object(
        Client, "request", new_callable=AsyncMock, return_value=MP4
    ) as request:
        target = tmp_path / "video.mp4"
        await hass.services.async_call(
            DOMAIN,
            "record",
            {**params, "filename": str(target), "seconds": 1},
            blocking=True,
        )
        assert target.read_bytes() == MP4
        request.assert_awaited_once_with("POST", "/record", {"seconds": 1})
    assert DATA["token"] not in caplog.text


async def test_multi_entry_and_action_validation(hass):
    first = await loaded(hass)
    second = await loaded(hass, {**DATA, "host": "other-addon"})
    with patch.object(
        Client, "request", new_callable=AsyncMock, return_value={"ok": True}
    ) as request:
        await hass.services.async_call(
            DOMAIN,
            "open_url",
            {"config_entry_id": second.entry_id, "url": "https://example.org"},
            blocking=True,
        )
        request.assert_awaited_with(
            "POST", "/open_url", {"url": "https://example.org", "new_tab": True}
        )
        await hass.services.async_call(
            DOMAIN,
            "restore_window",
            {"config_entry_id": second.entry_id},
            blocking=True,
        )
        request.assert_awaited_with("POST", "/restore_window", {})
    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "screenshot")
    for payload in ({}, {"config_entry_id": "missing"}):
        with pytest.raises((HomeAssistantError, vol.Invalid)):
            await hass.services.async_call(
                DOMAIN, "restore_window", payload, blocking=True
            )
    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "screenshot")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:secret@example.com",
        "https://example.com/\nsecret",
        "file:///etc/passwd",
    ],
)
async def test_url_validation(hass, url):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "open_url",
                {"config_entry_id": entry.entry_id, "url": url},
                blocking=True,
            )
        request.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "outside",
        "traversal",
        "symlink_parent",
        "symlink_file",
        "config",
        "extension",
        "existing",
    ],
)
async def test_export_security(hass, tmp_path, kind):
    root = tmp_path / "exports"
    root.mkdir()
    hass.config.allowlist_external_dirs = {str(root), hass.config.config_dir}
    target = root / "screen.png"
    if kind == "outside":
        target = tmp_path / "outside.png"
    if kind == "traversal":
        target = root / ".." / "outside.png"
    if kind == "symlink_parent":
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
        target = root / "link" / "escape.png"
    if kind == "symlink_file":
        target.symlink_to(tmp_path / "outside.png")
    if kind == "config":
        from pathlib import Path

        target = Path(hass.config.config_dir) / "critical.png"
    if kind == "extension":
        target = root / "configuration.yaml"
    if kind == "existing":
        target.write_bytes(b"keep")
    with pytest.raises(HomeAssistantError):
        await hass.async_add_executor_job(write_media, hass, str(target), PNG, ".png")
    if kind == "existing":
        assert target.read_bytes() == b"keep"
    assert not list(root.glob(".voxl-*"))


async def test_transport_sanitized(hass, caplog):
    session = AsyncMock()
    session.request = __import__("unittest.mock", fromlist=["Mock"]).Mock(
        side_effect=aiohttp.ClientError(DATA["token"] + "/sensitive-path")
    )
    client = Client(session, DATA)
    with caplog.at_level(logging.DEBUG), pytest.raises(CannotConnect) as error:
        await client.health()
    assert error.value.__suppress_context__
    assert DATA["token"] not in str(error.value)
    assert DATA["token"] not in caplog.text


@pytest.mark.allow_hosts("127.0.0.1")
async def test_real_aiohttp_contract(hass, aiohttp_server, socket_enabled):
    seen = []

    async def handler(request):
        seen.append(
            (request.method, request.path, request.headers.get("Authorization"))
        )
        if request.path == "/screenshot":
            return web.Response(body=PNG, content_type="image/png")
        if request.path == "/record":
            return web.Response(body=MP4, content_type="video/mp4")
        if request.path == "/wait":
            return web.json_response(
                {
                    "changed": False,
                    "timed_out": True,
                    "seq": 4,
                    "waited_ms": 10,
                    "private": "drop",
                }
            )
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = Client(session, {**DATA, "host": "127.0.0.1", "port": server.port})
        await client.health()
        assert await client.request("GET", "/screenshot") == PNG
        assert await client.request("POST", "/record", {"seconds": 1}) == MP4
        assert "private" not in await client.request("POST", "/wait", {})
    assert len(seen) == 4
    assert all(auth == "Bearer " + DATA["token"] for _, _, auth in seen)
