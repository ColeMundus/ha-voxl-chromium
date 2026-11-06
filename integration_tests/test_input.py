"""Desktop input contracts through Home Assistant's real service registry."""

import json
import logging
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import probatio as vol
import pytest
from aiohttp import web
from test_integration import DOMAIN
from test_runtime import loaded

from custom_components.voxl_chromium.client import CannotConnect, Client

ALL_SERVICES = {
    "open_url",
    "restore_window",
    "screenshot",
    "record",
    "wait_for_screen",
    "click",
    "type_text",
    "press_key",
    "scroll",
}
COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN


async def test_all_nine_services_unload(hass):
    first = await loaded(hass)
    from test_integration import DATA

    second = await loaded(hass, {**DATA, "host": "other-addon"})
    assert set(hass.services.async_services()[DOMAIN]) == ALL_SERVICES
    assert await hass.config_entries.async_unload(first.entry_id)
    assert set(hass.services.async_services()[DOMAIN]) == ALL_SERVICES
    assert await hass.config_entries.async_unload(second.entry_id)
    assert all(not hass.services.has_service(DOMAIN, name) for name in ALL_SERVICES)


@pytest.mark.parametrize("failure", ["http", "protocol", "transport"])
@pytest.mark.allow_hosts("127.0.0.1")
async def test_typed_canary_not_in_errors_or_component_logs(
    hass, aiohttp_server, socket_enabled, caplog, failure
):
    canary = "PRIVATE_TYPED_CANARY_é🙂"

    async def handler(request):
        assert await request.json() == {"text": canary}
        if failure == "http":
            return web.Response(status=500, text=canary)
        return web.json_response({"ok": False, "error": canary})

    app = web.Application()
    app.router.add_post("/type_text", handler)
    server = await aiohttp_server(app)
    from test_integration import DATA

    entry = await loaded(hass, {**DATA, "host": "127.0.0.1", "port": server.port})
    with caplog.at_level(logging.DEBUG, logger="custom_components.voxl_chromium"):
        if failure == "transport":
            session = hass.data[DOMAIN][entry.entry_id].client.session
            with patch.object(
                session, "request", side_effect=aiohttp.ClientError(canary)
            ):
                with pytest.raises(CannotConnect) as error:
                    await hass.services.async_call(
                        DOMAIN,
                        "type_text",
                        {"config_entry_id": entry.entry_id, "text": canary},
                        blocking=True,
                    )
        else:
            with pytest.raises(CannotConnect) as error:
                await hass.services.async_call(
                    DOMAIN,
                    "type_text",
                    {"config_entry_id": entry.entry_id, "text": canary},
                    blocking=True,
                )
    assert canary not in "".join(traceback.format_exception(error.value))
    assert canary not in caplog.text


def test_input_metadata_consistent():
    services = json.loads((COMPONENT / "services.yaml").read_text())
    strings = json.loads((COMPONENT / "strings.json").read_text())
    translations = json.loads((COMPONENT / "translations/en.json").read_text())
    assert set(services) == ALL_SERVICES
    assert strings == translations
    expected = {
        "click": ({"x", "y", "button", "click_count"}, {"x", "y"}),
        "type_text": ({"text"}, {"text"}),
        "press_key": ({"key"}, {"key"}),
        "scroll": ({"direction", "steps", "x", "y"}, {"direction"}),
    }
    for action, (fields, required) in expected.items():
        meta = services[action]
        assert set(meta["fields"]) == fields | {"config_entry_id"}
        assert {k for k, v in meta["fields"].items() if v["required"]} == required | {
            "config_entry_id"
        }
        translated = strings["services"][action]
        assert meta["name"] == translated["name"]
        assert meta["description"] == translated["description"]
        for name, field in meta["fields"].items():
            assert field["name"] == translated["fields"][name]["name"]
            assert field["selector"]
    for action in ("click", "scroll"):
        for coord in ("x", "y"):
            assert services[action]["fields"][coord]["selector"]["number"] == {
                "min": 0,
                "max": 16383,
                "step": 1,
                "mode": "box",
            }
    assert services["click"]["fields"]["button"]["default"] == "left"
    assert services["click"]["fields"]["click_count"]["default"] == 1
    assert services["scroll"]["fields"]["steps"]["default"] == 1


@pytest.mark.parametrize(
    "payload,expected",
    [
        *[
            ({"direction": d}, {"direction": d, "steps": 1})
            for d in ("up", "down", "left", "right")
        ],
        (
            {"direction": "down", "steps": 20, "x": 0, "y": 16383},
            {"direction": "down", "steps": 20, "x": 0, "y": 16383},
        ),
    ],
)
async def test_scroll_payload(hass, payload, expected):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        await hass.services.async_call(
            DOMAIN,
            "scroll",
            {"config_entry_id": entry.entry_id, **payload},
            blocking=True,
        )
        request.assert_awaited_once_with("POST", "/scroll", expected)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"direction": "UP"},
        {"direction": None},
        {"direction": "diagonal"},
        {"direction": "down", "x": 0},
        {"direction": "down", "y": 0},
        *[
            {"direction": "down", "steps": v}
            for v in (0, 21, True, False, 1.0, "1", None)
        ],
        *[
            {"direction": "up", "x": v, "y": 1}
            for v in (-1, 16384, True, 1.0, "1", None)
        ],
        *[
            {"direction": "up", "x": 1, "y": v}
            for v in (-1, 16384, True, 1.0, "1", None)
        ],
        {"direction": "down", "extra": 1},
    ],
)
async def test_scroll_invalid_no_http(hass, payload):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "scroll",
                {"config_entry_id": entry.entry_id, **payload},
                blocking=True,
            )
        request.assert_not_called()


KEYS = [
    *"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "Enter",
    "Escape",
    "Tab",
    "Space",
    "Backspace",
    "Delete",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Left",
    "Right",
    "Up",
    "Down",
    *[f"F{i}" for i in range(1, 13)],
]


@pytest.mark.parametrize(
    "key", [*KEYS, "Ctrl+a", "Alt+Shift+F12", "Ctrl+Alt+Shift+Delete", "Shift+Ctrl+A"]
)
async def test_press_key_payload(hass, key):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        await hass.services.async_call(
            DOMAIN,
            "press_key",
            {"config_entry_id": entry.entry_id, "key": key},
            blocking=True,
        )
        request.assert_awaited_once_with("POST", "/press_key", {"key": key})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"key": "A", "extra": 1},
        *[
            {"key": key}
            for key in (
                None,
                1,
                True,
                "",
                "ctrl+A",
                "CTRL+A",
                "Control+A",
                "Meta+A",
                "Ctrl+Ctrl+A",
                "Ctrl+Alt+Shift+Ctrl+A",
                "Ctrl",
                "Ctrl+",
                "+A",
                "A+B",
                "Enter+A",
                "enter",
                "F0",
                "F13",
                "F01",
                "é",
                "a\n",
                " A",
                "Ctrl +A",
                "Ctrl++",
                "Ctrl+A+B",
            )
        ],
    ],
)
async def test_press_key_invalid_no_http(hass, payload):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "press_key",
                {"config_entry_id": entry.entry_id, **payload},
                blocking=True,
            )
        request.assert_not_called()


@pytest.mark.parametrize("text", ["literal + Ctrl+A", "é漢字🙂", "🙂" * 1024, " "])
async def test_type_text_payload(hass, text):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        await hass.services.async_call(
            DOMAIN,
            "type_text",
            {"config_entry_id": entry.entry_id, "text": text},
            blocking=True,
        )
        request.assert_awaited_once_with("POST", "/type_text", {"text": text})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "text": "",
        },
        {"text": "x" * 1025},
        {"text": 123},
        {"text": None},
        *[
            {"text": "canary" + chr(code)}
            for code in (*range(32), *range(127, 160), 0xD800, 0xDFFF)
        ],
        {"text": "valid", "extra": True},
    ],
)
async def test_type_text_invalid_no_http(hass, payload):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "type_text",
                {"config_entry_id": entry.entry_id, **payload},
                blocking=True,
            )
        request.assert_not_called()


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            {"x": 0, "y": 16383},
            {"x": 0, "y": 16383, "button": "left", "click_count": 1},
        ),
        (
            {"x": 16383, "y": 0, "button": "middle", "click_count": 2},
            {"x": 16383, "y": 0, "button": "middle", "click_count": 2},
        ),
        (
            {"x": 1, "y": 2, "button": "right"},
            {"x": 1, "y": 2, "button": "right", "click_count": 1},
        ),
    ],
)
async def test_click_payload(hass, payload, expected):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        await hass.services.async_call(
            DOMAIN,
            "click",
            {"config_entry_id": entry.entry_id, **payload},
            blocking=True,
        )
        request.assert_awaited_once_with("POST", "/click", expected)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"x": 1},
        {"y": 1},
        *[{"x": value, "y": 0} for value in (-1, 16384, True, False, 1.0, "1", None)],
        *[{"x": 0, "y": value} for value in (-1, 16384, True, 1.0, "1")],
        *[{"x": 0, "y": 0, "click_count": value} for value in (0, 3, True, 1.0, "1")],
        *[{"x": 0, "y": 0, "button": value} for value in ("LEFT", "wheel", None)],
        {"x": 0, "y": 0, "extra": "no"},
    ],
)
async def test_click_invalid_no_http(hass, payload):
    entry = await loaded(hass)
    with patch.object(Client, "request", new_callable=AsyncMock) as request:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "click",
                {"config_entry_id": entry.entry_id, **payload},
                blocking=True,
            )
        request.assert_not_called()
