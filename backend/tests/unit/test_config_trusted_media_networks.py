"""Tests for trusted_media_networks setting + bridge-gateway auto-detect (bd-mlcla).

The setting + the auto-detect helper feed media-server attribution
RANKING only. They are correctness-neutral by design — these tests pin
the default, the round-trip, and the never-raise contract on the
auto-detector.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import config as cfg
from config import (
    DispatcharrSettings,
    clear_settings_cache,
    detect_local_bridge_gateways,
)


def teardown_function():
    clear_settings_cache()


def test_trusted_media_networks_default_empty():
    s = DispatcharrSettings()
    assert s.trusted_media_networks == []


def test_trusted_media_networks_round_trips():
    s = DispatcharrSettings(trusted_media_networks=["172.16.0.0/24", "10.0.0.5"])
    assert s.trusted_media_networks == ["172.16.0.0/24", "10.0.0.5"]
    # Survives a model_dump round-trip (settings.json persistence shape).
    dumped = s.model_dump()
    assert dumped["trusted_media_networks"] == ["172.16.0.0/24", "10.0.0.5"]


def test_detect_gateways_parses_proc_net_route(tmp_path):
    # /proc/net/route format: Iface Destination Gateway ... (hex, LE).
    # Default route (dest 00000000) with gateway 172.18.0.1.
    # 172.18.0.1 little-endian hex = 0112 12 AC -> "010012AC".
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t0000120A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    )
    with patch.object(cfg, "Path", return_value=route):
        # cfg.Path is used to build the route path; patch it to our temp file
        # only for the route lookup.
        with patch("config.Path") as mock_path:
            mock_path.return_value = route
            gateways = detect_local_bridge_gateways()
    assert gateways == ["172.18.0.1"]


def test_detect_gateways_missing_file_returns_empty():
    with patch("config.Path") as mock_path:
        fake = mock_path.return_value
        fake.exists.return_value = False
        gateways = detect_local_bridge_gateways()
    assert gateways == []


def test_detect_gateways_never_raises_on_garbage():
    """Malformed /proc/net/route content must degrade to [] not raise."""
    with patch("config.Path") as mock_path:
        fake = mock_path.return_value
        fake.exists.return_value = True
        fake.read_text.return_value = "totally\tnot\tvalid\nrows here\n"
        gateways = detect_local_bridge_gateways()
    assert gateways == []
