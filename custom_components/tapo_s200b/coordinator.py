"""Hub-wide serialized polling and at-most-once cursor persistence."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from plugp100.errors import InvalidAuthentication

from .api import async_fetch_since
from .connection import HubConnection
from .const import (
    CONF_PAGE_SIZE,
    CONF_POLL_INTERVAL,
    DEFAULT_PAGE_SIZE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_PAGES_PER_POLL,
    MIN_PAGE_SIZE,
    STORE_VERSION,
)
from .models import ChildCursor, EventEmission
from .processor import plan_batch

_LOGGER = logging.getLogger(__name__)

ChildEventListener = Callable[[tuple[EventEmission, ...]], None]


@dataclass(slots=True)
class CoordinatorDiagnostics:
    """Non-sensitive runtime counters exposed through diagnostics."""

    polls: int = 0
    successful_child_fetches: int = 0
    failed_child_fetches: int = 0
    processed_records: int = 0
    duplicate_records: int = 0
    ignored_records: int = 0
    history_gaps: int = 0
    truncated_fetches: int = 0
    suppressed_emissions: int = 0
    emitted_events: int = 0
    last_poll_latency_ms: float | None = None
    last_poll_fetch_latency_ms: float | None = None
    last_poll_cursor_save_latency_ms: float | None = None
    last_event_child_fetch_latency_ms: float | None = None
    last_event_cursor_save_latency_ms: float | None = None
    last_event_delivery_offset_ms: float | None = None
    last_event_poll_latency_ms: float | None = None
    last_event_delivery_lead_ms: float | None = None


class TapoS200BCoordinator(DataUpdateCoordinator[dict[str, tuple[EventEmission, ...]]]):
    """Poll all supported children sequentially through one hub session."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        connection: HubConnection,
    ) -> None:
        self.connection = connection
        self.buttons = {button.device_id: button for button in connection.buttons}
        self.page_size = int(entry.options.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE))
        interval = float(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORE_VERSION,
            f"{DOMAIN}.{entry.entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._cursors: dict[str, ChildCursor] = {}
        self._child_event_listeners: dict[str, set[ChildEventListener]] = {}
        self._context_filtering_enabled = False
        self.diagnostics = CoordinatorDiagnostics()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def async_load_cursors(self) -> None:
        """Load and defensively validate persisted child cursors."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        children = stored.get("children")
        if not isinstance(children, dict):
            return
        for child_id, raw_cursor in children.items():
            if child_id not in self.buttons or not isinstance(raw_cursor, dict):
                continue
            latest_record_id = raw_cursor.get("latest_record_id")
            recent_event_ids = raw_cursor.get("recent_event_ids")
            if latest_record_id is not None and (
                isinstance(latest_record_id, bool)
                or not isinstance(latest_record_id, int)
            ):
                continue
            if not isinstance(recent_event_ids, list) or not all(
                isinstance(event_id, str) for event_id in recent_event_ids
            ):
                continue
            self._cursors[child_id] = ChildCursor(
                latest_record_id=latest_record_id,
                recent_event_ids=tuple(recent_event_ids),
            )

    @callback
    def async_add_child_listener(
        self, child_id: str, listener: ChildEventListener
    ) -> Callable[[], None]:
        """Listen for already-persisted events from one child."""
        listeners = self._child_event_listeners.setdefault(child_id, set())
        listeners.add(listener)

        @callback
        def remove_listener() -> None:
            current = self._child_event_listeners.get(child_id)
            if current is None:
                return
            current.discard(listener)
            if not current:
                self._child_event_listeners.pop(child_id, None)

        return remove_listener

    @callback
    def async_enable_context_filtering(self) -> None:
        """Limit subsequent polls to Event Entities added to Home Assistant."""
        self._context_filtering_enabled = True

    @callback
    def _child_ids_to_poll(self) -> tuple[str, ...]:
        if not self._context_filtering_enabled:
            return tuple(self.buttons)
        contexts = set(self.async_contexts())
        return tuple(child_id for child_id in self.buttons if child_id in contexts)

    @callback
    def _publish_child_events(
        self, child_id: str, emissions: tuple[EventEmission, ...]
    ) -> bool:
        """Notify one child's entity without blocking unrelated listeners."""
        published = False
        for listener in tuple(self._child_event_listeners.get(child_id, ())):
            try:
                listener(emissions)
                published = True
            except Exception:
                _LOGGER.exception(
                    "Failed to publish trigger events for child model %s",
                    getattr(self.buttons[child_id], "model", "unknown"),
                )
        return published

    async def _async_update_data(self) -> dict[str, tuple[EventEmission, ...]]:
        started = time.monotonic()
        next_cursors = dict(self._cursors)
        coordinator_data: dict[str, tuple[EventEmission, ...]] = {}
        successful_fetches = 0
        fetch_latency = 0.0
        save_latency = 0.0
        first_delivery_offset: float | None = None
        event_child_fetch_latency: float | None = None
        event_save_latency: float | None = None

        child_ids = self._child_ids_to_poll()
        for child_id in child_ids:
            child = self.buttons[child_id]
            cursor = self._cursors.get(child_id, ChildCursor())
            fetch_started = time.monotonic()
            try:
                fetch = await async_fetch_since(
                    child,
                    cursor.latest_record_id,
                    page_size=self.page_size,
                    first_page_size=MIN_PAGE_SIZE,
                    max_pages=MAX_PAGES_PER_POLL,
                )
                plan = plan_batch(fetch.records, cursor)
            except InvalidAuthentication as err:
                raise ConfigEntryAuthFailed from err
            except Exception:
                self.diagnostics.failed_child_fetches += 1
                _LOGGER.warning(
                    "Failed to read a trigger log from hub model %s",
                    self.connection.hub.model,
                )
                continue
            child_fetch_latency = time.monotonic() - fetch_started
            fetch_latency += child_fetch_latency

            successful_fetches += 1
            next_cursors[child_id] = plan.cursor
            coordinator_data[child_id] = plan.emissions
            self.diagnostics.processed_records += plan.processed_records
            self.diagnostics.duplicate_records += plan.duplicate_records
            self.diagnostics.ignored_records += plan.ignored_records
            self.diagnostics.suppressed_emissions += plan.suppressed_emissions
            self.diagnostics.emitted_events += len(plan.emissions)
            if fetch.history_gap:
                self.diagnostics.history_gaps += 1
            if fetch.truncated:
                self.diagnostics.truncated_fetches += 1

            if plan.emissions:
                # At-most-once policy: make this child's cursor durable before
                # its Event Entity sees data. Do not wait for later children.
                save_started = time.monotonic()
                await self._store.async_save(self._serialize_cursors(next_cursors))
                child_save_latency = time.monotonic() - save_started
                save_latency += child_save_latency
                self._cursors = dict(next_cursors)
                if self._publish_child_events(child_id, plan.emissions):
                    if first_delivery_offset is None:
                        first_delivery_offset = time.monotonic() - started
                        event_child_fetch_latency = child_fetch_latency
                        event_save_latency = child_save_latency

        if child_ids and not successful_fetches:
            raise UpdateFailed("All trigger-log reads failed")

        if next_cursors != self._cursors:
            # At-most-once policy: save every cursor before Event Entities see data.
            save_started = time.monotonic()
            await self._store.async_save(self._serialize_cursors(next_cursors))
            save_latency += time.monotonic() - save_started
            self._cursors = next_cursors

        self.diagnostics.polls += 1
        self.diagnostics.successful_child_fetches += successful_fetches
        poll_latency = time.monotonic() - started
        self.diagnostics.last_poll_latency_ms = round(poll_latency * 1000, 1)
        self.diagnostics.last_poll_fetch_latency_ms = round(fetch_latency * 1000, 1)
        self.diagnostics.last_poll_cursor_save_latency_ms = round(
            save_latency * 1000, 1
        )
        if first_delivery_offset is not None:
            self.diagnostics.last_event_child_fetch_latency_ms = round(
                (event_child_fetch_latency or 0.0) * 1000, 1
            )
            self.diagnostics.last_event_cursor_save_latency_ms = round(
                (event_save_latency or 0.0) * 1000, 1
            )
            self.diagnostics.last_event_delivery_offset_ms = round(
                first_delivery_offset * 1000, 1
            )
            self.diagnostics.last_event_poll_latency_ms = round(poll_latency * 1000, 1)
            self.diagnostics.last_event_delivery_lead_ms = round(
                (poll_latency - first_delivery_offset) * 1000, 1
            )
        return coordinator_data

    @staticmethod
    def _serialize_cursors(cursors: dict[str, ChildCursor]) -> dict[str, Any]:
        return {
            "children": {
                child_id: {
                    "latest_record_id": cursor.latest_record_id,
                    "recent_event_ids": list(cursor.recent_event_ids),
                }
                for child_id, cursor in cursors.items()
            }
        }

    def diagnostics_dict(self) -> dict[str, Any]:
        """Return serializable, non-identifying runtime counters."""
        return asdict(self.diagnostics)
