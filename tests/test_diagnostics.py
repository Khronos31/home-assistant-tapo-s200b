"""Tests that diagnostics never expose credentials or device identifiers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.const import DOMAIN
from custom_components.tapo_s200b.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redact_secrets_and_hash_identifiers(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hub",
        unique_id="real-hub-id",
        data={
            "host": "192.168.50.10",
            "email": "user@example.com",
            "password": "secret-password",
        },
        options={"poll_interval": 1.0},
    )
    child = SimpleNamespace(model="S200D", device_id="real-child-id")
    coordinator = SimpleNamespace(
        last_update_success=True,
        page_size=50,
        diagnostics_dict=lambda: {"polls": 12},
    )
    entry.runtime_data = SimpleNamespace(
        connection=SimpleNamespace(
            hub=SimpleNamespace(model="H110", protocol_version="Klap V2"),
            buttons=(child,),
            transport="standalone_plugp100",
        ),
        coordinator=coordinator,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result)

    assert "secret-password" not in serialized
    assert "user@example.com" not in serialized
    assert "192.168.50.10" not in serialized
    assert "real-hub-id" not in serialized
    assert "real-child-id" not in serialized
    assert result["entry"]["data"] == "REDACTED"
