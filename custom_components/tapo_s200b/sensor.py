"""Disabled-by-default numeric diagnostics for Tapo child controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TapoS200BConfigEntry
from .connection import ButtonConnection, ButtonDiagnostics
from .const import DIAGNOSTIC_RSSI, DIAGNOSTIC_SIGNAL_LEVEL
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator
from .entity import TapoChildDiagnosticEntity


@dataclass(frozen=True, kw_only=True)
class TapoDiagnosticSensorDescription(SensorEntityDescription):
    """Describe one numeric child diagnostic."""

    value_fn: Callable[[ButtonDiagnostics], int | None]


SENSOR_DESCRIPTIONS = (
    TapoDiagnosticSensorDescription(
        key=DIAGNOSTIC_SIGNAL_LEVEL,
        translation_key=DIAGNOSTIC_SIGNAL_LEVEL,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.signal_level,
    ),
    TapoDiagnosticSensorDescription(
        key=DIAGNOSTIC_RSSI,
        translation_key=DIAGNOSTIC_RSSI,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.rssi,
    ),
)


async def async_setup_entry(
    _hass,
    entry: TapoS200BConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register supported numeric diagnostic entities."""
    runtime = entry.runtime_data
    async_add_entities(
        TapoDiagnosticSensor(runtime.diagnostic_coordinator, child, description)
        for child in runtime.connection.buttons
        for description in SENSOR_DESCRIPTIONS
        if description.key in child.diagnostic_features
    )


class TapoDiagnosticSensor(TapoChildDiagnosticEntity, SensorEntity):
    """One numeric diagnostic read from an S200B/S200D."""

    entity_description: TapoDiagnosticSensorDescription

    def __init__(
        self,
        coordinator: TapoS200BDiagnosticCoordinator,
        child: ButtonConnection,
        description: TapoDiagnosticSensorDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(coordinator, child, description.key)

    @property
    def native_value(self) -> int | None:
        """Return the most recently read value."""
        if self.snapshot is None:
            return None
        return self.entity_description.value_fn(self.snapshot)

    @property
    def available(self) -> bool:
        """Return availability only after a valid value was read."""
        return super().available and self.native_value is not None
