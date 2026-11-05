"""Last explicitly captured screenshot, never polled."""

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .client import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([LastScreenshot(hass, entry)])


class LastScreenshot(ImageEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Last screenshot"
    _attr_content_type = "image/png"

    def __init__(self, hass, entry):
        super().__init__(hass)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_last_screenshot"
        self._runtime = hass.data[DOMAIN][entry.entry_id]

    @property
    def available(self):
        return self._runtime.image is not None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_{self._entry_id}", self._updated
            )
        )

    @callback
    def _updated(self):
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self):
        return self._runtime.image
