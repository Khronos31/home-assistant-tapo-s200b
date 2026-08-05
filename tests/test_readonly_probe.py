from __future__ import annotations

import argparse
import stat
from pathlib import Path

import pytest

from tools import tapo_readonly_probe as probe


def test_private_ip_accepts_lan_address() -> None:
    assert probe.private_ip("192.168.50.10") == "192.168.50.10"


@pytest.mark.parametrize(
    "value", ["example.com", "8.8.8.8", "127.0.0.1", "169.254.1.1", "::1"]
)
def test_private_ip_rejects_non_lan_targets(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        probe.private_ip(value)


def test_credentials_require_mode_600(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text('email="user@example.com"\npassword="secret"\n')
    credentials.chmod(0o640)
    with pytest.raises(probe.ProbeConfigurationError):
        probe.load_credentials(credentials)

    credentials.chmod(0o600)
    loaded = probe.load_credentials(credentials)
    assert loaded.email == "user@example.com"
    assert loaded.password == "secret"
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600


def test_page_summary_preserves_behavior_without_raw_event_id() -> None:
    response = {
        "start_id": 25,
        "sum": 25,
        "logs": [
            {
                "id": 25,
                "timestamp": 100,
                "eventId": "event-25",
                "event": "rotation",
                "params": {"rotate_deg": 90},
            },
            {
                "id": 24,
                "timestamp": 99,
                "eventId": "event-24",
                "event": "singleClick",
            },
        ],
    }

    summary = probe.summarize_page(response, requested_start_id=0)

    assert summary["ids"] == [25, 24]
    assert summary["strictly_descending"] is True
    assert summary["events"][0]["degrees"] == 90
    assert summary["events"][0]["event_id"] != "event-25"
    assert probe.pagination_candidates(summary) == [25, 24, 23, 26]


def test_probe_argument_safety_bounds() -> None:
    args = probe._build_parser().parse_args([
        "--host",
        "192.168.50.10",
        "--page-size",
        "101",
    ])
    with pytest.raises(probe.ProbeConfigurationError):
        probe._validate_args(args)
