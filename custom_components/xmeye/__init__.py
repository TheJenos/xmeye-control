"""The XMEye (Xiongmai / Sofia DVRIP) integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .services import async_register_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: XmeyeConfigEntry) -> bool:
    """Set up XMEye from a config entry."""
    coordinator = XmeyeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XmeyeConfigEntry) -> bool:
    """Unload a config entry and close its connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: XmeyeConfigEntry) -> None:
    """Reload when options change; most of them affect entity creation."""
    await hass.config_entries.async_reload(entry.entry_id)
