"""UI configuration for native Voxl Chromium control."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .client import DOMAIN, CannotConnect, Client, InvalidAuth, endpoint, host, token


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _reserve_endpoint(self, data):
        """Serialize manual and discovered flows targeting the same endpoint."""
        address = endpoint(data)
        if self._async_in_progress(
            include_uninitialized=True, match_context={"endpoint": address}
        ):
            raise AbortFlow("already_in_progress", translation_domain="homeassistant")
        self.context["endpoint"] = address

    def _discovered_entry(self):
        """Prefer stable identity; never take an endpoint owned by another entry."""
        slug = self._discovery_data["addon_slug"]
        entries = self._async_current_entries()
        identities = [
            entry
            for entry in entries
            if entry.unique_id == self.unique_id or entry.data.get("addon_slug") == slug
        ]
        owners = [
            entry
            for entry in entries
            if endpoint(entry.data) == endpoint(self._discovery_data)
        ]
        if len(identities) > 1 or len(owners) > 1:
            raise AbortFlow("already_configured")
        identity = identities[0] if identities else None
        owner = owners[0] if owners else None
        if identity and owner and identity.entry_id != owner.entry_id:
            raise AbortFlow("already_configured")
        if (
            owner
            and not identity
            and (
                owner.data.get("addon_slug")
                or (owner.unique_id or "").startswith("hassio:")
            )
        ):
            raise AbortFlow("already_configured")
        return identity or owner

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        """Accept only Core's trusted Supervisor discovery, never web input.

        Supervisor authenticates the add-on's discovery submission. This step
        performs no scanning and does not treat an unauthenticated HTTP request
        as a discovery. Connection credentials stay private until confirmation.
        """
        if not isinstance(discovery_info, HassioServiceInfo):
            return self.async_abort(reason="invalid_discovery")
        try:
            config = discovery_info.config
            self._discovery_data = {
                "host": host(config["host"]),
                "port": vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))(
                    config["port"]
                ),
                "token": token(config["token"]),
            }
            slug = discovery_info.slug
            if not isinstance(slug, str) or not slug:
                return self.async_abort(reason="invalid_discovery")
            self._discovery_data["addon_slug"] = slug
        except (vol.Invalid, KeyError, TypeError):
            return self.async_abort(reason="invalid_discovery")
        # Reserve the stable add-on identity before awaiting network I/O.
        await self.async_set_unique_id(f"hassio:{slug}")
        self._reserve_endpoint(self._discovery_data)
        self.context["title_placeholders"] = {"name": "Voxl Chromium"}
        try:
            await Client(
                async_get_clientsession(self.hass), self._discovery_data
            ).health()
        except InvalidAuth:
            return self.async_abort(reason="invalid_auth")
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        entry = self._discovered_entry()
        if (
            entry
            and entry.unique_id == self.unique_id
            and all(
                entry.data.get(key) == value
                for key, value in self._discovery_data.items()
            )
        ):
            return self.async_abort(reason="already_configured")
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        """Require explicit approval before creating or changing credentials."""
        errors = {}
        if user_input is not None:
            try:
                await Client(
                    async_get_clientsession(self.hass), self._discovery_data
                ).health()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                # Recheck after the await: another manual flow may have finished.
                if entry := self._discovered_entry():
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=self.unique_id,
                        data_updates=self._discovery_data,
                        reason="discovery_updated",
                        reload_even_if_entry_is_unchanged=False,
                    )
                return self.async_create_entry(
                    title="Voxl Chromium", data=self._discovery_data
                )
        return self.async_show_form(
            step_id="hassio_confirm", data_schema=vol.Schema({}), errors=errors
        )

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
                self._reserve_endpoint(data)
                if entry is None:
                    self._abort_if_unique_id_configured()
                elif step == "reauth_confirm" and endpoint(data) != endpoint(
                    entry.data
                ):
                    return self.async_abort(reason="unique_id_mismatch")
                if any(
                    (entry is None or other.entry_id != entry.entry_id)
                    and endpoint(other.data) == endpoint(data)
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
                        unique_id=entry.unique_id
                        if entry.data.get("addon_slug") or step == "reauth_confirm"
                        else endpoint(data),
                        data={**entry.data, **data},
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
