"""Native Home Assistant actions for the Voxl Chromium add-on."""

from dataclasses import dataclass

import probatio as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import DOMAIN, CannotConnect, Client, InvalidAuth, url
from .media import write_media


@dataclass
class Runtime:
    client: Client
    image: bytes | None = None


SERVICES = ("open_url", "restore_window", "screenshot", "record", "wait_for_screen")


def region(value):
    """Match the native [x, y, width, height] region contract."""
    if not isinstance(value, list) or len(value) != 4:
        raise vol.Invalid("Region must be [x, y, width, height]")
    for index, item in enumerate(value):
        if type(item) is not int or not (0 if index < 2 else 1) <= item <= 16384:
            raise vol.Invalid("Invalid region coordinate")
    return value


def register_services(hass):
    select = {vol.Required("config_entry_id"): str}

    def bounded(low, high):
        return vol.All(vol.Coerce(int), vol.Range(min=low, max=high))

    schemas = {
        "open_url": {
            vol.Required("url"): url,
            vol.Optional("new_tab", default=True): bool,
        },
        "restore_window": {},
        "screenshot": {vol.Optional("filename"): str},
        "record": {
            vol.Required("filename"): str,
            vol.Required("seconds"): bounded(1, 60),
        },
        "wait_for_screen": {
            vol.Optional("idle_ms", default=300): bounded(1, 60000),
            vol.Optional("timeout_ms", default=10000): bounded(1, 60000),
            vol.Optional("region"): region,
        },
    }

    async def handle(call):
        runtime = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if runtime is None:
            raise HomeAssistantError("Select a loaded Voxl Chromium config entry")
        data = {
            key: value for key, value in call.data.items() if key != "config_entry_id"
        }
        if call.service == "wait_for_screen":
            return await runtime.client.request("POST", "/wait", data)
        if call.service in ("screenshot", "record"):
            screenshot = call.service == "screenshot"
            content = await runtime.client.request(
                "GET" if screenshot else "POST",
                "/screenshot" if screenshot else "/record",
                None if screenshot else {"seconds": data["seconds"]},
            )
            if "filename" in data:
                await hass.async_add_executor_job(
                    write_media,
                    hass,
                    data["filename"],
                    content,
                    ".png" if screenshot else ".mp4",
                )
            if screenshot:
                runtime.image = content
                async_dispatcher_send(hass, f"{DOMAIN}_{call.data['config_entry_id']}")
            return {"bytes": len(content), "saved": "filename" in data}
        await runtime.client.request("POST", "/" + call.service, data)
        return None

    for name in SERVICES:
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=vol.Schema({**select, **schemas[name]}),
            supports_response=SupportsResponse.OPTIONAL
            if name in ("wait_for_screen", "screenshot", "record")
            else SupportsResponse.NONE,
        )


async def async_setup_entry(hass, entry):
    client = Client(async_get_clientsession(hass), entry.data)
    try:
        await client.health()
    except InvalidAuth:
        raise ConfigEntryAuthFailed("Authentication failed") from None
    except CannotConnect:
        raise ConfigEntryNotReady("Native API unavailable") from None
    runtimes = hass.data.setdefault(DOMAIN, {})
    runtimes[entry.entry_id] = Runtime(client)
    if not hass.services.has_service(DOMAIN, "open_url"):
        register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.IMAGE])
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.IMAGE]):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
    return True
