"""Per-channel binary sensors for the XMEye integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SKIP_EMPTY_CHANNELS
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .entity import XmeyeChannelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up recording and connectivity sensors per channel."""
    coordinator = entry.runtime_data
    skip_empty = entry.options.get(CONF_SKIP_EMPTY_CHANNELS, True)
    entities: list[XmeyeChannelEntity] = []
    for cam in coordinator.data.cameras:
        if skip_empty and cam["type"] == "empty":
            continue
        entities.append(XmeyeRecordingSensor(coordinator, cam["channel"]))
        entities.append(XmeyeOnlineSensor(coordinator, cam["channel"]))
        entities.append(XmeyeMotionSensor(coordinator, cam["channel"]))
    async_add_entities(entities)


class XmeyeRecordingSensor(XmeyeChannelEntity, BinarySensorEntity):
    """On while the device is recording this channel."""

    _attr_translation_key = "recording"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise for ``channel``."""
        super().__init__(coordinator, channel, "recording")
        self._attr_translation_placeholders = {"channel": self.channel_title}

    @property
    def is_on(self) -> bool:
        """Whether this channel is being recorded."""
        return bool(self.channel_data.get("recording"))


class XmeyeOnlineSensor(XmeyeChannelEntity, BinarySensorEntity):
    """On while the channel is carrying a video signal."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise for ``channel``."""
        super().__init__(coordinator, channel, "online")
        self._attr_translation_placeholders = {"channel": self.channel_title}

    @property
    def is_on(self) -> bool:
        """Whether the channel currently has a signal."""
        return bool(self.channel_data.get("online"))


class XmeyeMotionSensor(XmeyeChannelEntity, BinarySensorEntity):
    """On while the device reports an active alert (motion, AI detection, ...) here.

    Unlike the other channel sensors, this is not read from the poll cycle:
    the device pushes alarm events unprompted, and the coordinator applies
    them to its data as they arrive (see ``XmeyeCoordinator._handle_alarm_event``).
    Which events fire, and their exact ``Event`` name, varies by firmware — the
    raw name is exposed as an attribute rather than filtered, since we cannot
    predict it across every Xiongmai variant.
    """

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise for ``channel``."""
        super().__init__(coordinator, channel, "alarm")
        self._attr_translation_placeholders = {"channel": self.channel_title}

    @property
    def _alarm(self) -> dict[str, Any]:
        """This channel's latest alarm event, or an empty mapping."""
        return self.coordinator.data.motion.get(self._channel, {})

    @property
    def is_on(self) -> bool:
        """Whether the most recent alarm event for this channel is still active."""
        return bool(self._alarm.get("active"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw event name the device reported."""
        return {"channel": self._channel, "event": self._alarm.get("event")}
