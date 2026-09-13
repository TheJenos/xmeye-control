"""Diagnostics support for the XMEye integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import XmeyeConfigEntry

TO_REDACT = {
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    "ip",
    "SerialNo",
    "HostName",
    "MAC",
    # The per-camera credentials the recorder hands back.
    "UserName",
    "PassWord",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: XmeyeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connected": coordinator.client.connected,
        "channel_count": coordinator.client.channel_count,
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT),
    }
