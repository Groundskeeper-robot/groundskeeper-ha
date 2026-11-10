"""Set up Groundskeeper from the UI: the panel's address and an API token from Settings → Access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CannotConnect,
    GroundskeeperClient,
    InvalidAuth,
    PanelTooOld,
    UnsupportedApi,
    normalize_url,
    robot_id,
)
from .const import DOMAIN, LOGGER

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): str})


class NoRobot(Exception):
    """The panel has not heard from its robot yet, so there is nothing to name the device by."""


class GroundskeeperConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Groundskeeper."""

    VERSION = 1

    async def _check(self, url: str, token: str, verify_ssl: bool) -> str:
        """The robot's id (not its serial) if the panel answers with this token."""
        client = GroundskeeperClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token)
        data = await client.summary()
        serial = data.get("sn")
        if not serial:
            raise NoRobot
        return robot_id(serial)

    async def _errors_for(self, url: str, token: str, verify_ssl: bool) -> tuple[str | None, dict[str, str]]:
        try:
            return await self._check(url, token, verify_ssl), {}
        except InvalidAuth:
            return None, {"base": "invalid_auth"}
        except PanelTooOld:
            return None, {"base": "panel_too_old"}
        except UnsupportedApi:
            return None, {"base": "unsupported_api"}
        except CannotConnect:
            return None, {"base": "cannot_connect"}
        except NoRobot:
            return None, {"base": "no_robot"}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error while checking the panel")
            return None, {"base": "unknown"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            verify = user_input.get(CONF_VERIFY_SSL, True)
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                rid, errors = await self._errors_for(url, token, verify)
                if rid:
                    await self.async_set_unique_id(rid)
                    self._abort_if_unique_id_configured(
                        updates={CONF_URL: url, CONF_API_TOKEN: token, CONF_VERIFY_SSL: verify}
                    )
                    return self.async_create_entry(
                        title="Groundskeeper", data={CONF_URL: url, CONF_API_TOKEN: token, CONF_VERIFY_SSL: verify}
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            rid, errors = await self._errors_for(entry.data[CONF_URL], token, entry.data.get(CONF_VERIFY_SSL, True))
            if rid:
                await self.async_set_unique_id(rid)
                self._abort_if_unique_id_mismatch(reason="wrong_robot")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
