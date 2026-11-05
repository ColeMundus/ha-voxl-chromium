"""Protocol and service boundary regression tests on real HA."""

from unittest.mock import AsyncMock, patch

import aiohttp
import probatio as vol
import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_integration import DATA, DOMAIN
from test_runtime import loaded

from custom_components.voxl_chromium.client import CannotConnect, Client, InvalidAuth


@pytest.mark.parametrize("seconds", [0, 61, -1])
async def test_record_duration(hass, seconds):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "record",
                {
                    "config_entry_id": entry.entry_id,
                    "filename": "/media/test.mp4",
                    "seconds": seconds,
                },
                blocking=True,
            )
        request.assert_not_called()


async def test_region_array(hass):
    entry = await loaded(hass)
    with patch.object(
        Client, "request", new_callable=AsyncMock, return_value={}
    ) as request:
        await hass.services.async_call(
            DOMAIN,
            "wait_for_screen",
            {
                "config_entry_id": entry.entry_id,
                "region": [0, 0, 640, 480],
            },
            blocking=True,
        )
        request.assert_awaited_once_with(
            "POST",
            "/wait",
            {
                "idle_ms": 300,
                "timeout_ms": 10000,
                "region": [0, 0, 640, 480],
            },
        )


@pytest.mark.parametrize(
    "region", [{"x": 0}, [0, 0, 0, 1], [0, 0, 1], [False, 0, 1, 1]]
)
async def test_invalid_region(hass, region):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "wait_for_screen",
                {
                    "config_entry_id": entry.entry_id,
                    "region": region,
                },
                blocking=True,
            )
        request.assert_not_called()


@pytest.mark.parametrize(
    "error,state",
    [
        (InvalidAuth("Authentication failed"), ConfigEntryState.SETUP_ERROR),
        (CannotConnect("Native API unavailable"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failure(hass, error, state, caplog):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    with patch.object(Client, "health", side_effect=error):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state == state
    assert not hass.services.has_service(DOMAIN, "screenshot")
    assert DATA["token"] not in caplog.text


async def test_media_failure_keeps_last_image(hass):
    entry = await loaded(hass)
    hass.data[DOMAIN][entry.entry_id].image = b"previous"
    with patch.object(
        Client, "request", side_effect=CannotConnect("Native API unavailable")
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "screenshot",
                {
                    "config_entry_id": entry.entry_id,
                },
                blocking=True,
            )
    assert hass.data[DOMAIN][entry.entry_id].image == b"previous"


@pytest.mark.allow_hosts("127.0.0.1")
@pytest.mark.parametrize(
    "path,status,body,error",
    [
        ("/health", 401, b"secret-canary", InvalidAuth),
        ("/health", 403, b"secret-canary", InvalidAuth),
        ("/health", 302, b"secret-canary", CannotConnect),
        ("/health", 500, b"secret-canary", CannotConnect),
        ("/health", 200, b'{"ok":false}', CannotConnect),
        ("/health", 200, b"invalid-json-secret-canary", CannotConnect),
        ("/screenshot", 200, b"not-png-secret-canary", CannotConnect),
        ("/record", 200, b"not-mp4-secret-canary", CannotConnect),
        ("/wait", 200, b'{"changed":true}', CannotConnect),
    ],
)
async def test_bad_upstream(
    hass, aiohttp_server, socket_enabled, path, status, body, error, caplog
):
    async def handler(request):
        return web.Response(status=status, body=body)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = Client(session, {**DATA, "host": "127.0.0.1", "port": server.port})
        with pytest.raises(error) as raised:
            await client.request("GET", path)
    assert "secret-canary" not in str(raised.value)
    assert DATA["token"] not in caplog.text
