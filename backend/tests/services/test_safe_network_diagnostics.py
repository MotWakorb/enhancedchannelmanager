"""Fresh synthetic malformed settings; no network or stored artifacts (2026-09-13)."""

import importlib
import ipaddress
import json
import logging
from unittest.mock import patch

import pytest

from observability import JsonFormatter
from services.attribution_reconciler import _parse_networks


_USER = "synthetic-resolver-user-20260913-" + "U" * 20
_PASSWORD = "synthetic-resolver-password-20260913-" + "P" * 20


def test_invalid_trusted_networks_identify_only_original_list_index(caplog):
    caplog.set_level(logging.DEBUG, logger="services.attribution_reconciler")
    result = _parse_networks([
        "192.0.2.8", "", _PASSWORD, "2001:db8::/32", _USER,
    ])
    assert result == [ipaddress.ip_network("192.0.2.8/32"), ipaddress.ip_network("2001:db8::/32")]
    records = [r for r in caplog.records if r.name == "services.attribution_reconciler"]
    messages = [json.loads(JsonFormatter().format(r))["msg"] for r in records]
    assert messages == [
        "[ATTR-RECONCILE] Skipping unparsable trusted-network entry at index 2",
        "[ATTR-RECONCILE] Skipping unparsable trusted-network entry at index 4",
    ]
    assert [r.args for r in records] == [(2,), (4,)]


@pytest.mark.parametrize("service", ["emby", "plex", "jellyfin"])
@pytest.mark.parametrize("shape", ["parse", "missing_host"])
def test_malformed_resolver_url_warns_without_url_or_userinfo(caplog, service, shape):
    module = importlib.import_module(f"services.{service}_resolver")
    resolver = getattr(module, f"_resolve_{service}_server_ip")
    caplog.set_level(logging.WARNING, logger=module.__name__)
    url = f"http://{_USER}:{_PASSWORD}@" + ("[" if shape == "parse" else "")
    with patch.object(module.socket, "gethostbyname") as dns:
        assert resolver(url) is None
    dns.assert_not_called()
    records = [r for r in caplog.records if r.name == module.__name__]
    assert len(records) == 1
    rendered = JsonFormatter().format(records[0])
    assert _USER not in rendered
    assert _PASSWORD not in rendered
    assert url not in rendered
    expected = (
        f"[{service.upper()}] Could not parse {service}_base_url"
        if shape == "parse" else
        f"[{service.upper()}] {service}_base_url has no extractable hostname"
    )
    assert json.loads(rendered)["msg"] == expected


@pytest.mark.parametrize("service", ["emby", "plex", "jellyfin"])
def test_resolver_empty_and_ip_literal_behavior_is_preserved(caplog, service):
    module = importlib.import_module(f"services.{service}_resolver")
    resolver = getattr(module, f"_resolve_{service}_server_ip")
    with patch.object(module.socket, "gethostbyname") as dns:
        assert resolver("") is None
        assert resolver("http://192.0.2.8:8096") == "192.0.2.8"
        assert resolver("http://[2001:db8::8]:8096") == "2001:db8::8"
    dns.assert_not_called()
    assert not [r for r in caplog.records if r.name == module.__name__]
