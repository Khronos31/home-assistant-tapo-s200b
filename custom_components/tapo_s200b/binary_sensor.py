"""Disabled-by-default binary diagnostics for Tapo child controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TapoS200BConfigEntry
from .connection import ButtonConnection, ButtonDiagnostics
from .const import DIAGNOSTIC_BATTERY_LOW, DIAGNOSTIC_CLOUD_CONNECTION
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator
from .entity import TapoChildDiagnosticEntity


@dataclass(frozen=True, kw_only=True)
class TapoDiagnosticBinarySensorDescription(BinarySensorEntityDescription):
    """Describe one binary child diagnostic."""

    value_fn: Callable[[ButtonDiagnostics], bool | None]


BINARY_SENSOR_DESCRIPTIONS = (
    TapoDiagnosticBinarySensorDescription(
        key=DIAGNOSTIC_CLOUD_CONNECTION,
        translation_key=DIAGNOSTIC_CLOUD_CONNECTION,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.cloud_connected,
    ),
    TapoDiagnosticBinarySensorDescription(
        key=DIAGNOSTIC_BATTERY_LOW,
        translation_key=DIAGNOSTIC_BATTERY_LOW,
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.battery_low,
    ),
)


async def async_setup_entry(
    _hass,
    entry: TapoS200BConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register supported cloud-state entities."""
    runtime = entry.runtime_data
    async_add_entities(
        TapoDiagnosticBinarySensor(runtime.diagnostic_coordinator, child, description)
        for child in runtime.connection.buttons
        for description in BINARY_SENSOR_DESCRIPTIONS
        if description.key in child.diagnostic_features
    )


class TapoDiagnosticBinarySensor(TapoChildDiagnosticEntity, BinarySensorEntity):
    """One binary diagnostic read from an S200B/S200D."""

    entity_description: TapoDiagnosticBinarySensorDescription

    def __init__(
        self,
        coordinator: TapoS200BDiagnosticCoordinator,
        child: ButtonConnection,
        description: TapoDiagnosticBinarySensorDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(coordinator, child, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return the most recently read binary value."""
        if self.snapshot is None:
            return None
        return self.entity_description.value_fn(self.snapshot)

    @property
    def available(self) -> bool:
        """Return availability only after a valid value was read."""
        return super().available and self.is_on is not None
