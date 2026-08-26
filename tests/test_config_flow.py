"""Tests for UI setup, duplicate prevention, reauthentication, and options."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.config_flow import ValidationResult
from custom_components.tapo_s200b.const import (
    CONF_EMAIL,
    CONF_PAGE_SIZE,
    CONF_POLL_INTERVAL,
    DEFAULT_PAGE_SIZE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

USER_INPUT = {
    CONF_HOST: "192.168.50.10",
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
}


async def test_user_flow_creates_entry(hass, enable_custom_integrations) -> None:
    with (
        patch(
            "custom_components.tapo_s200b.config_flow.async_validate_input",
            AsyncMock(
                return_value=ValidationResult(
                    "hub-id", "Living room hub", "AA:BB:CC:DD:EE:FF"
                )
            ),
        ),
        patch(
            "custom_components.tapo_s200b.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living room hub"
    assert result["data"] == {**USER_INPUT, "mac": "AA:BB:CC:DD:EE:FF"}
    assert result["options"] == {
        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
        CONF_PAGE_SIZE: DEFAULT_PAGE_SIZE,
    }


async def test_user_flow_rejects_public_or_named_host(
    hass, enable_custom_integrations
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={**USER_INPUT, CONF_HOST: "tapo.example.com"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_user_flow_aborts_duplicate_hub(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hub-id", data=USER_INPUT)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.tapo_s200b.config_flow.async_validate_input",
        AsyncMock(
            return_value=ValidationResult(
                "hub-id", "Living room hub", "AA:BB:CC:DD:EE:FF"
            )
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_only_credentials(
    hass, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hub-id",
        data=USER_INPUT,
        options={CONF_POLL_INTERVAL: 2.0, CONF_PAGE_SIZE: 75},
    )
    entry.add_to_hass(hass)
    updated = {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "new-secret"}

    with (
        patch(
            "custom_components.tapo_s200b.config_flow.async_validate_input",
            AsyncMock(
                return_value=ValidationResult(
                    "hub-id", "Living room hub", "AA:BB:CC:DD:EE:FF"
                )
            ),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=dict(entry.data),
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], updated
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_HOST: "192.168.50.10", **updated}
    assert entry.options == {CONF_POLL_INTERVAL: 2.0, CONF_PAGE_SIZE: 75}


async def test_options_flow_validates_polling_bounds(
    hass, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
            CONF_PAGE_SIZE: DEFAULT_PAGE_SIZE,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLL_INTERVAL: 2.5, CONF_PAGE_SIZE: 80},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_POLL_INTERVAL: 2.5, CONF_PAGE_SIZE: 80}
