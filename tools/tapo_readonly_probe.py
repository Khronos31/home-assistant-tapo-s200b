#!/usr/bin/env python3
"""Inspect S200B/S200D trigger logs without issuing actuator commands.

This tool intentionally uses only device-info, child-list, component-negotiation,
and get-trigger-logs requests. Credentials are never printed. The supplied host
must be a private IP address, and aiohttp requests to any other host are rejected.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import stat
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

DEFAULT_CREDENTIALS = Path("/config/.tools/tapo-dev/credentials")
MIN_POLL_INTERVAL = 0.5
MAX_PAGE_SIZE = 100
SUPPORTED_MODELS = frozenset({"S200B", "S200D"})
PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class ProbeConfigurationError(ValueError):
    """Raised when probe input would violate its safety constraints."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """Tapo credentials held only in memory."""

    email: str
    password: str


def private_ip(value: str) -> str:
    """Return a normalized RFC 1918 IPv4 address or reject the input."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("host must be an IP address") from err
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in PRIVATE_IPV4_NETWORKS
    ):
        raise argparse.ArgumentTypeError("host must be an RFC 1918 IPv4 address")
    return str(address)


def load_credentials(path: Path) -> Credentials:
    """Load a mode-0600 key/value credential file without logging its values."""
    file_stat = path.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode != 0o600:
        raise ProbeConfigurationError(
            f"credentials file must have mode 600, found {mode:o}"
        )

    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as credential_file:
        for raw_line in credential_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            values[key.strip().lower()] = raw_value.strip().strip('"').strip("'")

    email = values.get("email", "")
    password = values.get("password", "")
    if not email or not password:
        raise ProbeConfigurationError("credentials file needs email and password")
    return Credentials(email=email, password=password)


def opaque_id(value: str) -> str:
    """Return a stable non-reversible identifier suitable for probe output."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def event_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw event to fields needed to establish API behavior."""
    params = item.get("params")
    degrees = params.get("rotate_deg") if isinstance(params, dict) else None
    event_id = item.get("eventId")
    return {
        "id": item.get("id"),
        "event_id": opaque_id(event_id) if isinstance(event_id, str) else None,
        "event": item.get("event"),
        "degrees": degrees,
        "timestamp": item.get("timestamp"),
    }


def summarize_page(response: dict[str, Any], requested_start_id: int) -> dict[str, Any]:
    """Summarize one get_trigger_logs response without device identity data."""
    logs = response.get("logs")
    if not isinstance(logs, list):
        raise ProbeConfigurationError("trigger-log response has no logs list")
    summaries = [event_summary(item) for item in logs if isinstance(item, dict)]
    ids = [item["id"] for item in summaries if isinstance(item["id"], int)]
    return {
        "requested_start_id": requested_start_id,
        "response_start_id": response.get("start_id"),
        "sum": response.get("sum"),
        "count": len(summaries),
        "ids": ids,
        "strictly_descending": all(left > right for left, right in pairwise(ids)),
        "events": summaries,
    }


def pagination_candidates(page: dict[str, Any]) -> list[int]:
    """Choose bounded read-only cursors that reveal the API's paging semantics."""
    ids = page.get("ids", [])
    if not ids:
        return []
    newest = max(ids)
    oldest = min(ids)
    return list(dict.fromkeys([newest, oldest, max(1, oldest - 1), newest + 1]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=private_ip)
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS,
        help="mode-0600 email/password file (values are never printed)",
    )
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument(
        "--mode", choices=("inspect", "poll", "concurrency"), default="inspect"
    )
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.page_size <= MAX_PAGE_SIZE:
        raise ProbeConfigurationError(
            f"page-size must be between 1 and {MAX_PAGE_SIZE}"
        )
    if args.duration <= 0:
        raise ProbeConfigurationError("duration must be positive")
    if args.interval < MIN_POLL_INTERVAL:
        raise ProbeConfigurationError(
            f"interval must be at least {MIN_POLL_INTERVAL} seconds"
        )


async def _connect(args: argparse.Namespace, credentials: Credentials):
    import aiohttp
    from plugp100.common.credentials import AuthCredential
    from plugp100.devices.factory import DeviceConnectConfiguration, connect

    expected_host = args.host
    observed_hosts: set[str] = set()
    trace = aiohttp.TraceConfig()

    async def guard_request(
        _session: aiohttp.ClientSession,
        _context: aiohttp.TraceConfigCtx,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        request_host = params.url.host
        if request_host != expected_host:
            raise ProbeConfigurationError(
                "blocked an HTTP request outside the configured hub"
            )
        observed_hosts.add(request_host)

    trace.on_request_start.append(guard_request)
    # KLAP returns its session cookie from a numeric LAN address. aiohttp rejects
    # such cookies unless the jar is explicitly marked unsafe; plugp100 uses the
    # same settings when it owns the session.
    session = aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False),
        trace_configs=[trace],
    )
    try:
        config = DeviceConnectConfiguration(
            host=expected_host,
            credentials=AuthCredential(credentials.email, credentials.password),
        )
        hub = await connect(config=config, session=session)
        await hub.update()
        return hub, session, observed_hosts
    except BaseException:
        await session.close()
        raise


def _button_children(hub: Any) -> list[Any]:
    return [
        child
        for child in hub.children
        if str(getattr(child, "model", "")).upper() in SUPPORTED_MODELS
        and hasattr(child, "client")
        and getattr(child, "device_id", None)
    ]


async def _raw_logs(child: Any, page_size: int, start_id: int) -> dict[str, Any]:
    from plugp100.api.requests.tapo_request import TapoRequest
    from plugp100.api.requests.trigger_logs_params import GetTriggerLogsParams

    request = TapoRequest.get_child_event_logs(
        GetTriggerLogsParams(page_size=page_size, start_id=start_id)
    )
    result = await child.client.control_child(child.device_id, request)
    response = result.get_or_raise()
    if not isinstance(response, dict):
        raise ProbeConfigurationError("trigger-log response is not an object")
    return response


async def _inspect(buttons: Iterable[Any], page_size: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for child in buttons:
        started = time.monotonic()
        response = await _raw_logs(child, page_size, 0)
        first = summarize_page(response, 0)
        pages = [first]
        for candidate in pagination_candidates(first):
            await asyncio.sleep(0.25)
            page_response = await _raw_logs(child, min(page_size, 10), candidate)
            pages.append(summarize_page(page_response, candidate))
        results.append({
            "model": child.model,
            "child": opaque_id(child.device_id),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "pages": pages,
        })
    return {"buttons": results}


async def _poll(
    buttons: Iterable[Any], page_size: int, duration: float, interval: float
) -> dict[str, Any]:
    button_list = list(buttons)
    seen: dict[str, set[str]] = {child.device_id: set() for child in button_list}
    cycles = 0
    errors = 0
    latencies: list[float] = []
    duplicate_observations = 0
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        cycle_started = time.monotonic()
        for child in button_list:
            request_started = time.monotonic()
            try:
                response = await _raw_logs(child, page_size, 0)
                summary = summarize_page(response, 0)
            except Exception:
                errors += 1
                continue
            latencies.append((time.monotonic() - request_started) * 1000)
            for event in summary["events"]:
                event_id = event.get("event_id")
                if not isinstance(event_id, str):
                    continue
                if event_id in seen[child.device_id]:
                    duplicate_observations += 1
                seen[child.device_id].add(event_id)
        cycles += 1
        remaining = interval - (time.monotonic() - cycle_started)
        if remaining > 0:
            await asyncio.sleep(remaining)

    return {
        "cycles": cycles,
        "errors": errors,
        "latency_ms": {
            "minimum": round(min(latencies), 1) if latencies else None,
            "maximum": round(max(latencies), 1) if latencies else None,
            "average": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },
        "unique_events_per_button": {
            opaque_id(child_id): len(event_ids) for child_id, event_ids in seen.items()
        },
        "duplicate_page_observations": duplicate_observations,
    }


async def _concurrency(buttons: Iterable[Any], page_size: int) -> dict[str, Any]:
    button_list = list(buttons)
    started = time.monotonic()
    responses = await asyncio.gather(
        *(_raw_logs(child, page_size, 0) for child in button_list),
        return_exceptions=True,
    )
    return {
        "requests": len(responses),
        "errors": sum(isinstance(item, BaseException) for item in responses),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials(args.credentials)
    hub, session, observed_hosts = await _connect(args, credentials)
    try:
        buttons = _button_children(hub)
        if not buttons:
            raise ProbeConfigurationError("no S200B/S200D children found")
        if args.mode == "inspect":
            result = await _inspect(buttons, args.page_size)
        elif args.mode == "poll":
            result = await _poll(buttons, args.page_size, args.duration, args.interval)
        else:
            result = await _concurrency(buttons, args.page_size)
        return {
            "mode": args.mode,
            "hub": opaque_id(hub.device_id),
            "model": hub.model,
            "protocol": hub.protocol_version,
            "button_count": len(buttons),
            "observed_http_hosts": sorted(observed_hosts),
            "result": result,
        }
    finally:
        await hub.client.close()
        if not session.closed:
            await session.close()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        _validate_args(args)
        result = asyncio.run(_run(args))
    except (OSError, ProbeConfigurationError) as err:
        print(
            json.dumps(
                {"status": "configuration_error", "error": type(err).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as err:
        print(
            json.dumps(
                {"status": "probe_error", "error": type(err).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"status": "ok", **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
