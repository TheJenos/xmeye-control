"""Device-targeted services for the XMEye integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHANNEL,
    ATTR_END,
    ATTR_START,
    DOMAIN,
    SERVICE_SEARCH_RECORDINGS,
    SERVICE_SET_CHANNEL_TITLE,
    SERVICE_SET_TIME,
)
from .coordinator import XmeyeCoordinator
from .dvrip import DvripError

ATTR_DEVICE_ID = "device_id"
ATTR_TITLE = "title"
ATTR_TIME = "time"

BASE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})

SET_TIME_SCHEMA = BASE_SCHEMA.extend({vol.Optional(ATTR_TIME): cv.datetime})

SET_CHANNEL_TITLE_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_CHANNEL): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required(ATTR_TITLE): cv.string,
    }
)

SEARCH_RECORDINGS_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_CHANNEL, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Required(ATTR_START): cv.datetime,
        vol.Required(ATTR_END): cv.datetime,
    }
)


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> XmeyeCoordinator:
    """Resolve a device id from the service target to its coordinator."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ):
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_loaded",
        translation_placeholders={"device_id": device_id},
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_TIME):
        return

    async def _set_time(call: ServiceCall) -> None:
        """Set the device clock, defaulting to Home Assistant's local time."""
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        # The device stores naive local wall-clock time, so send HA's.
        when: datetime = call.data.get(ATTR_TIME) or dt_util.now()
        await _run(coordinator, lambda c: c.set_time(when))

    async def _set_channel_title(call: ServiceCall) -> None:
        """Rename a channel on the device."""
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        channel = call.data[ATTR_CHANNEL]
        title = call.data[ATTR_TITLE]
        await _run(coordinator, lambda c: c.set_channel_title(channel, title))
        await coordinator.async_request_refresh()

    async def _search_recordings(call: ServiceCall) -> ServiceResponse:
        """Return the recordings stored for a channel in a time range."""
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        channel = call.data[ATTR_CHANNEL]
        start: datetime = call.data[ATTR_START]
        end: datetime = call.data[ATTR_END]
        files = await _run(
            coordinator,
            lambda c: c.search_recordings(
                channel=channel,
                start=start.strftime("%Y-%m-%d %H:%M:%S"),
                end=end.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        return {"recordings": _normalise_recordings(files)}

    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIME, _set_time, schema=SET_TIME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHANNEL_TITLE,
        _set_channel_title,
        schema=SET_CHANNEL_TITLE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_RECORDINGS,
        _search_recordings,
        schema=SEARCH_RECORDINGS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def _run(coordinator: XmeyeCoordinator, action: Any) -> Any:
    """Run a client call, translating protocol errors for the service caller."""
    try:
        client = await coordinator.async_ensure_connected()
        return await action(client)
    except DvripError as err:
        raise HomeAssistantError(f"{coordinator.host}: {err}") from err


def _normalise_recordings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce raw OPFileQuery records to the fields worth exposing."""
    return [
        {
            "name": item.get("FileName"),
            "start": item.get("BeginTime"),
            "end": item.get("EndTime"),
            "size_kb": item.get("FileLength"),
            "disk": item.get("DiskNo"),
        }
        for item in files
    ]
