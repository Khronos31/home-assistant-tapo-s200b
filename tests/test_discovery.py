"""Tests for bounded identity-based Tapo hub discovery."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from plugp100.models.discovery import DiscoveredDevice

from custom_components.tapo_s200b.discovery import (
    AmbiguousHubError,
    HubNotFoundError,
    async_resolve_hub,
    normalize_mac,
)


def discovered(
    host: str,
    *,
    model: str = "H110(EU)",
    device_id: str | None = "hub-id",
    mac: str | None = "AA-BB-CC-DD-EE-FF",
) -> DiscoveredDevice:
    return DiscoveredDevice(
        device_type="SMART.TAPOHUB",
        device_model=model,
        ip=host,
        mac=mac,
        device_id=device_id,
        mgt_encrypt_schm=None,
    )


def test_normalize_mac_accepts_common_formats_and_rejects_invalid() -> None:
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "AABBCCDDEEFF"
    assert normalize_mac("AABBCCDDEEFF") == "AABBCCDDEEFF"
    assert normalize_mac("not-a-mac") is None


async def test_resolves_h110c_by_exact_device_id_and_mac(hass) -> None:
    with patch(
        "custom_components.tapo_s200b.discovery.TapoDiscovery.scan",
        AsyncMock(return_value=[discovered("192.168.50.20", model="H110C(JP)")]),
    ):
        result = await async_resolve_hub(
            hass,
            expected_device_id="hub-id",
            expected_mac="aa:bb:cc:dd:ee:ff",
        )

    assert result.host == "192.168.50.20"
    assert result.model == "H110C"
    assert result.mac == "AA:BB:CC:DD:EE:FF"


async def test_accepts_missing_device_id_when_mac_matches(hass) -> None:
    with patch(
        "custom_components.tapo_s200b.discovery.TapoDiscovery.scan",
        AsyncMock(return_value=[discovered("192.168.50.20", device_id=None)]),
    ):
        result = await async_resolve_hub(
            hass,
            expected_device_id="hub-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )

    assert result.host == "192.168.50.20"


@pytest.mark.parametrize(
    "devices",
    [
        [discovered("192.168.50.20", mac="11:22:33:44:55:66")],
        [
            discovered("192.168.50.20"),
            discovered("192.168.50.21"),
        ],
    ],
)
async def test_rejects_conflicting_or_ambiguous_identity(hass, devices) -> None:
    with patch(
        "custom_components.tapo_s200b.discovery.TapoDiscovery.scan",
        AsyncMock(return_value=devices),
    ):
        with pytest.raises(AmbiguousHubError):
            await async_resolve_hub(
                hass,
                expected_device_id="hub-id",
                expected_mac="AA:BB:CC:DD:EE:FF",
            )


async def test_accepts_hashed_discovery_id_when_mac_matches(hass) -> None:
    with patch(
        "custom_components.tapo_s200b.discovery.TapoDiscovery.scan",
        AsyncMock(
            return_value=[
                discovered("192.168.50.20", device_id="discovery-device-id-hash")
            ]
        ),
    ):
        result = await async_resolve_hub(
            hass,
            expected_device_id="authenticated-raw-device-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )

    assert result.host == "192.168.50.20"


async def test_ignores_unsupported_models(hass) -> None:
    with patch(
        "custom_components.tapo_s200b.discovery.TapoDiscovery.scan",
        AsyncMock(return_value=[discovered("192.168.50.20", model="P110")]),
    ):
        with pytest.raises(HubNotFoundError):
            await async_resolve_hub(
                hass,
                expected_device_id="hub-id",
                expected_mac="AA:BB:CC:DD:EE:FF",
            )


async def test_concurrent_and_cached_resolution_uses_one_scan(hass) -> None:
    scan = AsyncMock(return_value=[discovered("192.168.50.20")])
    with patch("custom_components.tapo_s200b.discovery.TapoDiscovery.scan", scan):
        await asyncio.gather(
            async_resolve_hub(
                hass,
                expected_device_id="hub-id",
                expected_mac="AA:BB:CC:DD:EE:FF",
            ),
            async_resolve_hub(
                hass,
                expected_device_id="hub-id",
                expected_mac="AA:BB:CC:DD:EE:FF",
            ),
        )
        await async_resolve_hub(
            hass,
            expected_device_id="hub-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )

    scan.assert_awaited_once()
