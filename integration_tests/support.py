"""Shared Home Assistant setup for the integration suite."""

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_integration import DATA, DOMAIN

from custom_components.voxl_chromium.client import Client, endpoint


async def loaded(hass, data=None):
    if data is None:
        data = DATA
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=endpoint(data))
    entry.add_to_hass(hass)
    with patch.object(Client, "health", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry
