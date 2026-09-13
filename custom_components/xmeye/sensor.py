"""Diagnostic sensors for the XMEye integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SKIP_EMPTY_CHANNELS
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .entity import XmeyeChannelEntity, XmeyeEntity


def parse_device_number(value: Any) -> int | None:
    """Parse a device numeric field, which may be an int or a "0x..." string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        try:
            return int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError:
            return None
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up per-channel bitrate sensors plus device-level diagnostics."""
    coordinator = entry.runtime_data
    skip_empty = entry.options.get(CONF_SKIP_EMPTY_CHANNELS, True)

    entities: list[SensorEntity] = [
        XmeyeUptimeSensor(coordinator),
        XmeyeStorageSensor(coordinator, "storage_total"),
        XmeyeStorageSensor(coordinator, "storage_free"),
        XmeyeStorageUsedSensor(coordinator),
    ]
    entities.extend(
        XmeyeBitrateSensor(coordinator, cam["channel"])
        for cam in coordinator.data.cameras
        if not skip_empty or cam["type"] != "empty"
    )
    async_add_entities(entities)


class XmeyeBitrateSensor(XmeyeChannelEntity, SensorEntity):
    """Live encoder bitrate for one channel."""

    _attr_translation_key = "bitrate"
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise for ``channel``."""
        super().__init__(coordinator, channel, "bitrate")
        self._attr_translation_placeholders = {"channel": self.channel_title}

    @property
    def native_value(self) -> int | None:
        """The channel's current bitrate."""
        return self.channel_data.get("bitrate")


class XmeyeUptimeSensor(XmeyeEntity, SensorEntity):
    """How long the device has been running.

    ``DeviceRunTime`` is reported as a hex-encoded minute count on the firmware
    families this was tested against.
    """

    _attr_translation_key = "uptime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XmeyeCoordinator) -> None:
        """Initialise the uptime sensor."""
        super().__init__(coordinator, "uptime")

    @property
    def native_value(self) -> int | None:
        """Device uptime in minutes."""
        return parse_device_number(
            self.coordinator.data.system_info.get("DeviceRunTime")
        )


class XmeyeStorageSensor(XmeyeEntity, SensorEntity):
    """Total or free recording space, summed over every partition."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: XmeyeCoordinator, key: str) -> None:
        """Initialise either the total or the free-space sensor."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._field = "TotalSpace" if key == "storage_total" else "RemainSpace"

    @property
    def native_value(self) -> float | None:
        """Summed space in GB, or None when the device reports no disk."""
        return _sum_space(self.coordinator.data.storage, self._field)


class XmeyeStorageUsedSensor(XmeyeEntity, SensorEntity):
    """Percentage of recording space in use."""

    _attr_translation_key = "storage_used"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: XmeyeCoordinator) -> None:
        """Initialise the used-space sensor."""
        super().__init__(coordinator, "storage_used")

    @property
    def native_value(self) -> float | None:
        """Used space as a percentage of total."""
        storage = self.coordinator.data.storage
        total = _sum_space(storage, "TotalSpace")
        free = _sum_space(storage, "RemainSpace")
        if not total or free is None:
            return None
        return round((total - free) / total * 100, 1)


def _sum_space(partitions: list[dict[str, Any]], field: str) -> float | None:
    """Sum a space field across partitions, converting MB to GB."""
    values = [
        parse_device_number(part.get(field))
        for part in partitions
        if part.get(field) is not None
    ]
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(sum(usable) / 1024, 2)
