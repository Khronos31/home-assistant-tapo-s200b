"""Bounded, identity-checked discovery for Tapo hubs."""

from __future__ import annotations

import asyncio
import string
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from plugp100.discovery import TapoDiscovery
from plugp100.models.discovery import DiscoveredDevice

from .const import (
    DISCOVERY_CACHE_SECONDS,
    DISCOVERY_TIMEOUT_SECONDS,
    DOMAIN,
    SUPPORTED_HUB_MODELS,
)

_DISCOVERY_STATE = "discovery_state"


class HubNotFoundError(ConnectionError):
    """Raised when discovery cannot find the configured hub."""


class AmbiguousHubError(ConnectionError):
    """Raised when discovery returns contradictory hub identities or hosts."""


@dataclass(frozen=True, slots=True)
class DiscoveredHub:
    """A discovery result with normalized stable identifiers."""

    host: str
    model: str
    device_id: str | None
    mac: str | None


@dataclass(slots=True)
class _DiscoveryState:
    task: asyncio.Task[tuple[DiscoveredDevice, ...]] | None = None
    results: tuple[DiscoveredDevice, ...] | None = None
    expires_at: float = 0.0


def normalize_mac(value: str | None) -> str | None:
    """Return twelve uppercase hexadecimal characters, or None if invalid."""
    if not value:
        return None
    normalized = "".join(
        character for character in value if character.isalnum()
    ).upper()
    if len(normalized) != 12 or any(
        character not in string.hexdigits.upper() for character in normalized
    ):
        return None
    return normalized


def format_mac(value: str | None) -> str | None:
    """Return a normalized colon-separated MAC address."""
    if not (normalized := normalize_mac(value)):
        return None
    return ":".join(normalized[index : index + 2] for index in range(0, 12, 2))


def normalize_model(value: str | None) -> str:
    """Remove regional suffixes from a discovery model."""
    return (value or "").partition("(")[0].strip().upper()


async def async_resolve_hub(
    hass: HomeAssistant,
    *,
    expected_device_id: str | None,
    expected_mac: str | None,
) -> DiscoveredHub:
    """Resolve exactly one supported hub without authenticating to candidates."""
    expected_device_id = expected_device_id or None
    expected_mac = normalize_mac(expected_mac)
    if expected_device_id is None and expected_mac is None:
        raise ValueError("A device ID or MAC is required for discovery")

    matches: dict[str, DiscoveredHub] = {}
    contradiction = False
    for device in await _async_discover_devices(hass):
        model = normalize_model(device.device_model)
        if model not in SUPPORTED_HUB_MODELS:
            continue

        candidate_id = device.device_id or None
        candidate_mac = normalize_mac(device.mac)
        id_matches = bool(
            expected_device_id and candidate_id and candidate_id == expected_device_id
        )
        mac_matches = bool(expected_mac and candidate_mac == expected_mac)
        if not id_matches and not mac_matches:
            continue

        if (
            id_matches
            and expected_mac
            and candidate_mac
            and candidate_mac != expected_mac
        ):
            contradiction = True
            continue

        # Some discovery responses expose device_id_hash in the device_id
        # field, while an authenticated connection exposes the raw device ID.
        # A matching MAC is therefore sufficient for candidate selection. The
        # authenticated connection verifies both stable identifiers before the
        # address is persisted.

        matches[device.ip] = DiscoveredHub(
            host=device.ip,
            model=model,
            device_id=candidate_id,
            mac=format_mac(candidate_mac),
        )

    if contradiction:
        raise AmbiguousHubError("Discovery returned conflicting identity fields")
    if len(matches) > 1:
        raise AmbiguousHubError("Discovery matched more than one address")
    if not matches:
        raise HubNotFoundError("Configured hub was not found by discovery")
    return next(iter(matches.values()))


async def _async_discover_devices(
    hass: HomeAssistant,
) -> tuple[DiscoveredDevice, ...]:
    """Share one short-lived discovery result among concurrent config entries."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    state = domain_data.get(_DISCOVERY_STATE)
    if not isinstance(state, _DiscoveryState):
        state = _DiscoveryState()
        domain_data[_DISCOVERY_STATE] = state

    loop = asyncio.get_running_loop()
    if state.results is not None and loop.time() < state.expires_at:
        return state.results
    if state.task is None:
        state.task = hass.async_create_task(
            _async_scan(), "tapo_s200b discovery", eager_start=True
        )

    task = state.task
    try:
        results = await asyncio.shield(task)
    except BaseException:
        if state.task is task:
            state.task = None
        raise
    if state.task is task:
        state.task = None
        state.results = results
        state.expires_at = loop.time() + DISCOVERY_CACHE_SECONDS
    return results


async def _async_scan() -> tuple[DiscoveredDevice, ...]:
    """Perform one credential-free local broadcast scan."""
    return tuple(await TapoDiscovery.scan(timeout=DISCOVERY_TIMEOUT_SECONDS))
