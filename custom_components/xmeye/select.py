"""PTZ preset selector for the XMEye integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_PTZ_PRESET_COUNT,
    CONF_SKIP_EMPTY_CHANNELS,
    DEFAULT_PTZ_PRESET_COUNT,
)
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .dvrip import DvripError
from .entity import XmeyeChannelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a PTZ preset selector per channel, unless preset count is 0."""
    preset_count = int(
        entry.options.get(CONF_PTZ_PRESET_COUNT, DEFAULT_PTZ_PRESET_COUNT)
    )
    if preset_count <= 0:
        return

    coordinator = entry.runtime_data
    skip_empty = entry.options.get(CONF_SKIP_EMPTY_CHANNELS, True)
    async_add_entities(
        XmeyePresetSelect(coordinator, cam["channel"], preset_count)
        for cam in coordinator.data.cameras
        if not skip_empty or cam["type"] != "empty"
    )


class XmeyePresetSelect(XmeyeChannelEntity, SelectEntity):
    """Move a PTZ camera to one of its stored presets.

    The device has no way to report which preset numbers are actually
    configured, or which one a camera is currently at — presets are just
    numbered slots set locally on the camera/PTZ head. Selecting an option
    here always issues a fresh "go to this preset" command; the option shown
    afterwards is only the last one requested, not a confirmation the camera
    arrived. Use the ``xmeye.ptz`` service's ``SetPreset``/``ClearPreset``
    commands to configure the presets themselves.
    """

    _attr_translation_key = "ptz_preset"

    def __init__(
        self, coordinator: XmeyeCoordinator, channel: int, preset_count: int
    ) -> None:
        """Initialise for ``channel`` with ``preset_count`` selectable presets."""
        super().__init__(coordinator, channel, "ptz_preset")
        self._attr_translation_placeholders = {"channel": self.channel_title}
        self._attr_options = [str(preset) for preset in range(1, preset_count + 1)]

    async def async_select_option(self, option: str) -> None:
        """Move the camera to the selected preset."""
        try:
            client = await self.coordinator.async_ensure_connected()
            await client.ptz("GotoPreset", self._channel, preset=int(option))
        except DvripError as err:
            raise HomeAssistantError(
                f"Could not go to preset {option} on {self.entity_id}: {err}"
            ) from err
        self._attr_current_option = option
        self.async_write_ha_state()
