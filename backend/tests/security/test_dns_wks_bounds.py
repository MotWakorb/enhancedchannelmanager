"""CVE-2026-128089: tiny WKS boundaries, never resource-exhausting inputs.

Source: dnspython GHSA-vvf4-9x63-8xvv and the v2.9.2 WKS implementation.
Only the first out-of-range service (8193 bitmap bytes on the old parser) is
used. No external DNS, giant service number or production traffic.
"""

import dns.exception
import dns.rdata
import pytest


def test_wks_first_out_of_range_service_is_refused():
    with pytest.raises(dns.exception.SyntaxError, match="65535"):
        dns.rdata.from_text("IN", "WKS", "192.0.2.1 17 65536")


def test_wks_maximum_service_remains_valid_and_bounded():
    record = dns.rdata.from_text("IN", "WKS", "192.0.2.1 17 65535")
    assert len(record.bitmap) == 8192
    assert record.bitmap[-1] == 1
    assert record.address == "192.0.2.1"


def test_wks_oversized_wire_bitmap_is_refused():
    wire = b"\xc0\x00\x02\x01\x11" + bytes(8193)
    with pytest.raises(dns.exception.FormError, match="8192"):
        dns.rdata.from_wire("IN", "WKS", wire, 0, len(wire))
