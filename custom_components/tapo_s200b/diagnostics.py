"""Redacted diagnostics for the Tapo S200B/S200D integration."""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.core import HomeAssistant

from . import TapoS200BConfigEntry


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: TapoS200BConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    return {
        "entry": {
            "title": entry.title,
            "unique_id": _opaque(entry.unique_id or ""),
            "data": "REDACTED",
            "options": dict(entry.options),
        },
        "hub": {
            "model": runtime.connection.hub.model,
            "protocol": runtime.connection.hub.protocol_version,
            "transport": runtime.connection.transport,
            "button_count": len(runtime.connection.buttons),
        },
        "buttons": [
            {
                "model": child.model,
                "id": _opaque(child.device_id),
            }
            for child in runtime.connection.buttons
        ],
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "page_size": coordinator.page_size,
            "counters": coordinator.diagnostics_dict(),
        },
    }
