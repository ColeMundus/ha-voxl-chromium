from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "voxl_chromium"
DATA = {"host": "voxl-addon", "port": 8100, "token": "secret-canary-" + "x" * 32}


async def test_flow(hass: HomeAssistant):
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] == "create_entry"
    assert result["data"] == DATA
    assert result["result"].unique_id == "voxl-addon:8100"


@pytest.mark.parametrize(
    "host",
    ["http://host", "host/a", "user@host", "host:8100", "host\n", "host?token=x"],
)
async def test_invalid_host(hass, host):
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ) as health:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={**DATA, "host": host}
        )
    assert result["type"] == "form"
    assert result["errors"]
    health.assert_not_called()


async def test_setup_actions_and_unload(hass, tmp_path):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "wait_for_screen")
    from homeassistant.helpers.service import async_get_all_descriptions

    descriptions = await async_get_all_descriptions(hass)
    assert set(descriptions[DOMAIN]) == {
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
    assert descriptions[DOMAIN]["open_url"]["fields"]["config_entry_id"][
        "selector"
    ] == {"config_entry": {"integration": DOMAIN}}
    with patch(
        "custom_components.voxl_chromium.client.Client.request",
        new_callable=AsyncMock,
        return_value={"changed": True, "timed_out": False, "seq": 2, "waited_ms": 300},
    ) as request:
        response = await hass.services.async_call(
            DOMAIN,
            "wait_for_screen",
            {"config_entry_id": entry.entry_id},
            blocking=True,
            return_response=True,
        )
        assert response["changed"] is True
        request.assert_awaited_once_with(
            "POST", "/wait", {"idle_ms": 300, "timeout_ms": 10000}
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "wait_for_screen")
