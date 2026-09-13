"""Shared entity base for the XMEye integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import XmeyeCoordinator


class XmeyeEntity(CoordinatorEntity[XmeyeCoordinator]):
    """Base entity tied to the device as a whole."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XmeyeCoordinator, key: str) -> None:
        """Initialise with a key that makes the entity unique within the device."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        info = coordinator.data.system_info if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=info.get("DeviceType"),
            sw_version=info.get("SoftWareVersion"),
            hw_version=info.get("HardWare"),
            serial_number=info.get("SerialNo"),
            configuration_url=f"http://{coordinator.host}",
        )


class XmeyeChannelEntity(XmeyeEntity):
    """Base entity scoped to one channel of the device."""

    def __init__(self, coordinator: XmeyeCoordinator, channel: int, key: str) -> None:
        """Initialise for ``channel``."""
        super().__init__(coordinator, f"{key}_{channel}")
        self._channel = channel

    @property
    def channel_data(self) -> dict[str, Any]:
        """The latest polled state for this channel."""
        return self.coordinator.data.camera(self._channel)

    @property
    def channel_title(self) -> str:
        """The device-configured channel name, or a positional fallback."""
        return self.channel_data.get("title") or f"Channel {self._channel + 1}"
