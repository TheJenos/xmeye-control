"""Buttons for device-level actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .dvrip import DvripError
from .entity import XmeyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the reboot button."""
    async_add_entities([XmeyeRebootButton(entry.runtime_data)])


class XmeyeRebootButton(XmeyeEntity, ButtonEntity):
    """Reboot the DVR/NVR.

    Shutdown is deliberately not exposed as a button: most of these devices have
    no remote power-on, so a stray tap would take the recorder offline until
    somebody visits it.
    """

    _attr_translation_key = "reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: XmeyeCoordinator) -> None:
        """Initialise the reboot button."""
        super().__init__(coordinator, "reboot")

    async def async_press(self) -> None:
        """Reboot the device; the session drops as a result."""
        try:
            client = await self.coordinator.async_ensure_connected()
            await client.reboot()
        except DvripError as err:
            raise HomeAssistantError(f"Could not reboot the device: {err}") from err
        finally:
            # The device tears the socket down on its way out.
            await self.coordinator.client.close()
