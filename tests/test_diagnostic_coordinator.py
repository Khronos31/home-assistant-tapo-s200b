"""Tests for listener-driven child diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.connection import ButtonDiagnostics
from custom_components.tapo_s200b.const import DOMAIN
from custom_components.tapo_s200b.diagnostic_coordinator import (
    TapoS200BDiagnosticCoordinator,
)


def diagnostic_coordinator(hass, *buttons) -> TapoS200BDiagnosticCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    connection = SimpleNamespace(buttons=buttons)
    return TapoS200BDiagnosticCoordinator(hass, entry, connection)


async def test_diagnostics_do_not_fetch_without_a_listener(hass) -> None:
    fetch = AsyncMock()
    diagnostic_coordinator(
        hass,
        SimpleNamespace(
            device_id="child-id",
            model="S200D",
            diagnostic_features=frozenset({"signal_level"}),
            async_get_diagnostics=fetch,
        ),
    )

    await hass.async_block_till_done()

    fetch.assert_not_awaited()


async def test_diagnostics_return_successful_children(hass) -> None:
    expected = ButtonDiagnostics(True, 3, False, -42)
    good = SimpleNamespace(
        device_id="good-child",
        model="S200D",
        diagnostic_features=frozenset({"signal_level"}),
        async_get_diagnostics=AsyncMock(return_value=expected),
    )
    bad = SimpleNamespace(
        device_id="bad-child",
        model="S200B",
        diagnostic_features=frozenset({"signal_level"}),
        async_get_diagnostics=AsyncMock(side_effect=TimeoutError),
    )
    coordinator = diagnostic_coordinator(hass, bad, good)

    assert await coordinator._async_update_data() == {"good-child": expected}


async def test_all_diagnostic_failures_mark_update_failed(hass) -> None:
    child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        diagnostic_features=frozenset({"signal_level"}),
        async_get_diagnostics=AsyncMock(side_effect=TimeoutError),
    )
    coordinator = diagnostic_coordinator(hass, child)

    with pytest.raises(UpdateFailed, match="All child diagnostic reads failed"):
        await coordinator._async_update_data()
