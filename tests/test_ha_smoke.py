"""Minimal compatibility check for the pinned Home Assistant test runtime."""

from __future__ import annotations


async def test_home_assistant_fixture_starts(hass) -> None:
    """Prove the third-party fixture works with the pinned HA release."""
    assert hass.is_running is True
