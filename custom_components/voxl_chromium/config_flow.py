"""UI configuration for native Voxl Chromium control."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import DOMAIN, CannotConnect, Client, InvalidAuth, endpoint, host, token


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _form(self, step, user_input=None):
        errors = {}
        entry = (
            self._get_reauth_entry()
            if step == "reauth_confirm"
            else self._get_reconfigure_entry()
            if step == "reconfigure"
            else None
        )
        defaults = entry.data if entry else {}
        if user_input is not None:
            try:
                data = {
                    "host": host(user_input.get("host", defaults.get("host"))),
                    "port": vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))(
                        user_input.get("port", defaults.get("port", 8100))
                    ),
                    "token": token(user_input["token"]),
                }
                await self.async_set_unique_id(endpoint(data))
                if entry is None:
                    self._abort_if_unique_id_configured()
                elif step == "reauth_confirm":
                    self._abort_if_unique_id_mismatch()
                if entry is not None and any(
                    other.entry_id != entry.entry_id
                    and other.unique_id == endpoint(data)
                    for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                await Client(async_get_clientsession(self.hass), data).health()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except (vol.Invalid, KeyError, TypeError):
                errors["base"] = "invalid_input"
            else:
                if entry:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=endpoint(data),
                        data=data,
                        reason="reauth_successful"
                        if step == "reauth_confirm"
                        else "reconfigure_successful",
                    )
                return self.async_create_entry(
                    title=f"Voxl Chromium ({endpoint(data)})", data=data
                )
        schema = vol.Schema(
            {
                vol.Required("host", default=defaults.get("host", "")): str,
                vol.Required("port", default=defaults.get("port", 8100)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required("token"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def async_step_user(self, user_input=None):
        return await self._form("user", user_input)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._form("reauth_confirm", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._form("reconfigure", user_input)
