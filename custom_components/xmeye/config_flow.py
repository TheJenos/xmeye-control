"""Config and options flow for the XMEye integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CAMERA_SPEAKERS_ALL,
    CAMERA_SPEAKERS_IP,
    CAMERA_SPEAKERS_NONE,
    CONF_CAMERA_SPEAKERS,
    CONF_PTZ_PRESET_COUNT,
    CONF_RTSP_PORT,
    CONF_RTSP_TEMPLATE,
    CONF_SKIP_EMPTY_CHANNELS,
    CONF_STREAM,
    CONF_TALK_CHANNEL,
    DEFAULT_CAMERA_SPEAKERS,
    DEFAULT_PORT,
    DEFAULT_PTZ_PRESET_COUNT,
    DEFAULT_RTSP_PORT,
    DEFAULT_RTSP_TEMPLATE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STREAM,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .coordinator import XmeyeConfigEntry
from .dvrip import DvripAuthError, DvripClient, DvripError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


async def _async_probe(data: dict[str, Any]) -> dict[str, Any]:
    """Log in once to validate credentials and read identifying info."""
    client = DvripClient(
        host=data[CONF_HOST],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        username=data.get(CONF_USERNAME, DEFAULT_USERNAME),
        password=data.get(CONF_PASSWORD, ""),
    )
    try:
        await client.connect()
        await client.login()
        info = await client.system_info()
    finally:
        await client.close()
    return info if isinstance(info, dict) else {}


class XmeyeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of an XMEye device."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details and verify them."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _async_probe(user_input)
            except DvripAuthError:
                errors["base"] = "invalid_auth"
            except DvripError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error probing XMEye device")
                errors["base"] = "unknown"
            else:
                serial = info.get("SerialNo") or user_input[CONF_HOST]
                await self.async_set_unique_id(str(serial))
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                device_type = info.get("DeviceType")
                if isinstance(device_type, str) and device_type:
                    title = device_type
                else:
                    title = f"XMEye {user_input[CONF_HOST]}"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a re-authentication flow after credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for fresh credentials for an existing entry."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**reauth_entry.data, **user_input}
            try:
                await _async_probe(data)
            except DvripAuthError:
                errors["base"] = "invalid_auth"
            except DvripError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    ): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: XmeyeConfigEntry) -> XmeyeOptionsFlow:
        """Return the options flow handler."""
        return XmeyeOptionsFlow()


class XmeyeOptionsFlow(OptionsFlow):
    """Tune polling, streaming and the talk channel after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            # An empty talk channel means "device default audio-out".
            if user_input.get(CONF_TALK_CHANNEL) == -1:
                user_input.pop(CONF_TALK_CHANNEL)
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=600, unit_of_measurement="s", mode="box"
                    )
                ),
                vol.Optional(
                    CONF_RTSP_PORT,
                    default=options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_STREAM, default=options.get(CONF_STREAM, DEFAULT_STREAM)
                ): vol.In({0: "Main stream", 1: "Sub stream"}),
                vol.Optional(
                    CONF_RTSP_TEMPLATE,
                    default=options.get(CONF_RTSP_TEMPLATE, DEFAULT_RTSP_TEMPLATE),
                ): str,
                vol.Optional(
                    CONF_TALK_CHANNEL,
                    default=options.get(CONF_TALK_CHANNEL, -1),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_CAMERA_SPEAKERS,
                    default=options.get(CONF_CAMERA_SPEAKERS, DEFAULT_CAMERA_SPEAKERS),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            CAMERA_SPEAKERS_IP,
                            CAMERA_SPEAKERS_ALL,
                            CAMERA_SPEAKERS_NONE,
                        ],
                        translation_key="camera_speakers",
                    )
                ),
                vol.Optional(
                    CONF_SKIP_EMPTY_CHANNELS,
                    default=options.get(CONF_SKIP_EMPTY_CHANNELS, True),
                ): bool,
                vol.Optional(
                    CONF_PTZ_PRESET_COUNT,
                    default=options.get(
                        CONF_PTZ_PRESET_COUNT, DEFAULT_PTZ_PRESET_COUNT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=255, mode="box")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
