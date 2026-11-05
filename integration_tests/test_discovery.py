"""Supervisor discovery through the actual Core config-flow harness."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_integration import DATA, DOMAIN
from test_runtime import loaded

from custom_components.voxl_chromium.client import CannotConnect, InvalidAuth


def discovery(config=None, slug="local_voxl_chromium"):
    return HassioServiceInfo(
        config=DATA if config is None else config,
        name="Voxl Chromium",
        slug=slug,
        uuid="supervisor-instance",
    )


async def start(hass, info=None):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "hassio"}, data=info or discovery()
    )


async def test_confirmation_and_repeated_discovery(hass, caplog):
    caplog.set_level(logging.DEBUG)
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        result = await start(hass)
        assert result["type"] == "form"
        assert result["step_id"] == "hassio_confirm"
        assert not hass.config_entries.async_entries(DOMAIN)
        assert DATA["token"] not in repr(result)
        duplicate = await start(hass)
        assert duplicate["type"] == "abort"
        assert duplicate["reason"] == "already_in_progress"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        assert result["type"] == "create_entry"
        assert result["result"].unique_id == "hassio:local_voxl_chromium"
        again = await start(hass)
        assert again["reason"] == "already_configured"
    assert DATA["token"] not in caplog.text


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {},
        {**DATA, "host": "http://bad"},
        {**DATA, "port": 0},
        {**DATA, "token": "short"},
    ],
)
async def test_malformed(hass, config):
    info = discovery()
    info.config = config
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ) as health:
        result = await start(hass, info)
    assert result["type"] == "abort"
    assert result["reason"] == "invalid_discovery"
    health.assert_not_called()
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    "error,reason", [(InvalidAuth, "invalid_auth"), (CannotConnect, "cannot_connect")]
)
async def test_discovery_failure_preserves_entry(hass, caplog, error, reason):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    caplog.set_level(logging.DEBUG)
    with patch(
        "custom_components.voxl_chromium.client.Client.health",
        side_effect=error(DATA["token"]),
    ):
        result = await start(
            hass, discovery({**DATA, "token": "new-token-" + "z" * 40})
        )
    assert result["reason"] == reason
    assert entry.data == DATA
    assert entry.unique_id == "voxl-addon:8100"
    assert DATA["token"] not in repr(result) + caplog.text


async def test_manual_migration_keeps_entry_image_and_requires_confirmation(hass):
    entry = await loaded(hass)
    registry = er.async_get(hass)
    before = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
    identity = (before.entity_id, before.unique_id)
    replacement = {**DATA, "token": "new-canary-" + "z" * 40}
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        result = await start(hass, discovery(replacement))
        assert result["type"] == "form"
        assert entry.data == DATA
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        assert result["reason"] == "discovery_updated"
        assert entry.unique_id == "hassio:local_voxl_chromium"
        assert entry.data == {**replacement, "addon_slug": "local_voxl_chromium"}
        changed_host = {**replacement, "host": "new-addon"}
        result = await start(hass, discovery(changed_host))
        assert result["type"] == "form"
        await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    after = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
    assert (after.entity_id, after.unique_id) == identity
    assert entry.data["host"] == "new-addon"


async def test_health_rechecked_on_confirmation(hass):
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ) as health:
        result = await start(hass)
        health.side_effect = InvalidAuth("secret-canary")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_manual_deduplicates_discovered_endpoint(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, unique_id="hassio:local_voxl_chromium"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={**DATA, "host": "VOXL-addon."}
    )
    assert result["reason"] == "already_configured"


async def test_parallel_discoveries_and_manual_share_endpoint_guard(hass):
    started = asyncio.Event()
    release = asyncio.Event()

    async def health():
        started.set()
        await release.wait()

    with patch(
        "custom_components.voxl_chromium.client.Client.health", side_effect=health
    ):
        task = asyncio.create_task(start(hass))
        await started.wait()
        second = await start(hass, discovery(slug="other_slug"))
        assert second["reason"] == "already_in_progress"
        manual = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
        assert manual["reason"] == "already_in_progress"
        release.set()
        first = await task
        assert first["type"] == "form"
        assert not hass.config_entries.async_entries(DOMAIN)


async def test_discovered_entry_manual_reauth_and_reconfigure(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DATA, "addon_slug": "local_voxl_chromium"},
        unique_id="hassio:local_voxl_chromium",
    )
    entry.add_to_hass(hass)
    replacement = {**DATA, "token": "replacement-" + "y" * 40}
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], replacement
        )
        assert result["reason"] == "reauth_successful"
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
            data={**replacement, "host": "moved-addon"},
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
    assert entry.unique_id == "hassio:local_voxl_chromium"
    assert entry.data["addon_slug"] == "local_voxl_chromium"
    assert entry.data["host"] == "moved-addon"


async def test_confirmation_failure_preserves_existing_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ) as health:
        result = await start(
            hass, discovery({**DATA, "token": "replacement-" + "z" * 40})
        )
        assert result["type"] == "form"
        health.side_effect = CannotConnect("secret-canary")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == DATA
    assert entry.unique_id == "voxl-addon:8100"


async def test_untrusted_mapping_is_not_supervisor_discovery(hass):
    result = await start(hass, DATA)
    assert result["reason"] == "invalid_discovery"


@pytest.mark.parametrize("reverse", [False, True])
async def test_stable_identity_cannot_take_another_entries_endpoint(hass, reverse):
    stable = MockConfigEntry(
        domain=DOMAIN,
        data={**DATA, "addon_slug": "local_voxl_chromium"},
        unique_id="hassio:local_voxl_chromium",
    )
    target = {**DATA, "host": "second-addon"}
    other = MockConfigEntry(domain=DOMAIN, data=target, unique_id="second-addon:8100")
    for entry in [other, stable] if reverse else [stable, other]:
        entry.add_to_hass(hass)
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        result = await start(hass, discovery(target))
    assert result["reason"] == "already_configured"
    assert stable.data == {**DATA, "addon_slug": "local_voxl_chromium"}
    assert other.data == target


async def test_endpoint_conflict_is_rechecked_at_confirmation(hass):
    stable = MockConfigEntry(
        domain=DOMAIN,
        data={**DATA, "addon_slug": "local_voxl_chromium"},
        unique_id="hassio:local_voxl_chromium",
    )
    stable.add_to_hass(hass)
    target = {**DATA, "host": "second-addon"}
    with patch(
        "custom_components.voxl_chromium.client.Client.health", new_callable=AsyncMock
    ):
        result = await start(hass, discovery(target))
        assert result["type"] == "form"
        other = MockConfigEntry(
            domain=DOMAIN, data=target, unique_id="second-addon:8100"
        )
        other.add_to_hass(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "already_configured"
    assert stable.data["host"] == DATA["host"]
    assert other.data == target


async def test_migration_releases_former_manual_unique_id(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="voxl-addon:8100")
    entry.add_to_hass(hass)
    original_id = entry.entry_id
    with (
        patch(
            "custom_components.voxl_chromium.client.Client.health",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new_callable=AsyncMock,
        ),
    ):
        for config in (DATA, {**DATA, "host": "moved-addon"}):
            flow = await start(hass, discovery(config))
            assert flow["type"] == "form"
            await hass.config_entries.flow.async_configure(flow["flow_id"], {})
            await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.entry_id == original_id
    assert entry.unique_id == "hassio:local_voxl_chromium"
    assert entry.data["host"] == "moved-addon"
