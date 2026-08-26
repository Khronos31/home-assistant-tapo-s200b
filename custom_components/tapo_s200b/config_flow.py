"""UI configuration for the Tapo S200B/S200D integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
from plugp100.errors import InvalidAuthentication

from .connection import (
    InvalidHostError,
    NoButtonsError,
    UnsupportedHubError,
    async_get_connection,
    normalize_host,
)
from .const import (
    CONF_EMAIL,
    CONF_MAC,
    CONF_PAGE_SIZE,
    CONF_POLL_INTERVAL,
    DEFAULT_PAGE_SIZE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_PAGE_SIZE,
    MAX_POLL_INTERVAL,
    MIN_PAGE_SIZE,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnectError(ConnectionError):
    """Raised when a hub cannot be reached or initialized."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    hub_id: str
    title: str
    mac: str


def _credentials_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(
            CONF_HOST, default=defaults.get(CONF_HOST, "")
        ): selector.TextSelector(),
        vol.Required(
            CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    })


REAUTH_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
    ),
    vol.Required(CONF_PASSWORD): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    ),
})


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(
            CONF_POLL_INTERVAL,
            default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        ): vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
        ),
        vol.Required(
            CONF_PAGE_SIZE,
            default=options.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_PAGE_SIZE, max=MAX_PAGE_SIZE)),
    })


def _normalize_host(value: str) -> str:
    return normalize_host(value)


async def async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> ValidationResult:
    """Validate credentials and return stable, non-secret hub metadata."""
    host = _normalize_host(data[CONF_HOST])
    connection = None
    try:
        connection = await async_get_connection(
            hass, host, data[CONF_EMAIL], data[CONF_PASSWORD]
        )
        return ValidationResult(
            hub_id=connection.hub.device_id,
            title=connection.hub.nickname or f"Tapo hub {host}",
            mac=connection.hub.mac,
        )
    except (InvalidAuthentication, UnsupportedHubError, NoButtonsError):
        raise
    except Exception as err:
        raise CannotConnectError from err
    finally:
        if connection is not None:
            await connection.async_close()


class TapoS200BConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one H110C hub."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = dict(user_input)
                normalized[CONF_HOST] = _normalize_host(normalized[CONF_HOST])
                result = await async_validate_input(self.hass, normalized)
                await self.async_set_unique_id(result.hub_id)
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match({CONF_HOST: normalized[CONF_HOST]})
            except InvalidHostError:
                errors["base"] = "invalid_host"
            except InvalidAuthentication:
                errors["base"] = "invalid_auth"
            except UnsupportedHubError:
                errors["base"] = "unsupported_hub"
            except NoButtonsError:
                errors["base"] = "no_buttons"
            except AbortFlow:
                return self.async_abort(reason="already_configured")
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception while validating Tapo hub")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=result.title,
                    data={**normalized, CONF_MAC: result.mac},
                    options={
                        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                        CONF_PAGE_SIZE: DEFAULT_PAGE_SIZE,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_EMAIL: user_input[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                result = await async_validate_input(self.hass, candidate)
                if result.hub_id != entry.unique_id:
                    raise CannotConnectError
            except InvalidAuthentication:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return TapoS200BOptionsFlow()


class TapoS200BOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(dict(self.config_entry.options))
        )
