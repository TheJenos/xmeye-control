"""Connection management and polling for an XMEye device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN
from .dvrip import DvripAuthError, DvripClient, DvripError, safe_call

_LOGGER = logging.getLogger(__name__)

type XmeyeConfigEntry = ConfigEntry[XmeyeCoordinator]


@dataclass(slots=True)
class XmeyeData:
    """A single poll's worth of device state."""

    cameras: list[dict[str, Any]] = field(default_factory=list)
    system_info: dict[str, Any] = field(default_factory=dict)
    storage: list[dict[str, Any]] = field(default_factory=list)
    alarm: dict[str, Any] = field(default_factory=dict)

    def camera(self, channel: int) -> dict[str, Any]:
        """Return the state of one channel, or an empty mapping."""
        for cam in self.cameras:
            if cam["channel"] == channel:
                return cam
        return {}


class XmeyeCoordinator(DataUpdateCoordinator[XmeyeData]):
    """Owns the DVRIP connection and keeps device state fresh.

    A single TCP session is shared by every entity: the protocol serialises
    request/reply by arrival order, so one connection with an internal lock is
    both simpler and kinder to the device than a socket per entity.
    """

    config_entry: XmeyeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: XmeyeConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        self.client = DvripClient(
            host=self.host,
            port=self.port,
            username=entry.data.get(CONF_USERNAME, "admin"),
            password=entry.data.get(CONF_PASSWORD, ""),
            timeout=DEFAULT_TIMEOUT,
        )
        self._connect_lock = asyncio.Lock()

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.host}",
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def serial_number(self) -> str | None:
        """The device serial number, once SystemInfo has been read."""
        info = self.data.system_info if self.data else {}
        return info.get("SerialNo") or None

    @property
    def device_name(self) -> str:
        """A human-friendly name for the device."""
        info = self.data.system_info if self.data else {}
        device_type = info.get("DeviceType")
        if isinstance(device_type, str) and device_type:
            return device_type
        return f"XMEye {self.host}"

    async def async_ensure_connected(self) -> DvripClient:
        """Return a logged-in client, reconnecting if the session has dropped."""
        async with self._connect_lock:
            if self.client.connected and self.client.session:
                return self.client
            await self.client.close()
            await self.client.connect()
            await self.client.login()
            _LOGGER.debug(
                "%s: logged in (%d channels)", self.host, self.client.channel_count
            )
            return self.client

    async def async_shutdown(self) -> None:
        """Close the connection when the entry unloads."""
        await super().async_shutdown()
        await self.client.close()

    async def _async_update_data(self) -> XmeyeData:
        """Poll the device for channel state, system info and storage."""
        try:
            client = await self.async_ensure_connected()
            cameras = await client.list_cameras()
            system_info = await client.system_info()
            storage = await safe_call(client.storage_info(), [])
            work_state = await safe_call(client.work_state(), {})
        except DvripAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DvripError as err:
            # Force a fresh login on the next cycle; a half-open socket will
            # otherwise keep failing the same way.
            await self.client.close()
            raise UpdateFailed(f"{self.host}: {err}") from err

        return XmeyeData(
            cameras=cameras,
            system_info=system_info,
            storage=_flatten_storage(storage),
            alarm=work_state if isinstance(work_state, dict) else {},
        )


def _flatten_storage(storage: Any) -> list[dict[str, Any]]:
    """Normalise StorageInfo's nested partition layout into a flat list.

    Firmwares report either a list of disks each holding a ``Partition`` list,
    or the partitions directly; both shapes end up as one list of partitions.
    """
    if not isinstance(storage, list):
        return []
    partitions: list[dict[str, Any]] = []
    for disk in storage:
        if not isinstance(disk, dict):
            continue
        parts = disk.get("Partition")
        if isinstance(parts, list):
            partitions.extend(p for p in parts if isinstance(p, dict))
        else:
            partitions.append(disk)
    return partitions
