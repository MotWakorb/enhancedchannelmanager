"""CVE-2026-114605: retain the upstream decompression hop boundary.

The tiny wire fixture follows the maintainer's regression in commit
b485e460c266540a1a09ca6d8d1d7b685b1d3023. No DNS queries or large DoS inputs.
"""

import struct

import dns.exception
import dns.name
import dns.resolver
import pytest
from pydantic import EmailStr, TypeAdapter, ValidationError


def compression_chain(hops: int) -> bytes:
    return b"\x00\x00" + b"".join(struct.pack("!H", 0xC000 | (2 * hop)) for hop in range(hops))


def test_sixteen_compression_pointers_remain_valid():
    wire = compression_chain(16)
    name, consumed = dns.name.from_wire(wire, 32)
    assert name == dns.name.root
    assert consumed == 2


def test_seventeenth_compression_pointer_is_refused():
    wire = compression_chain(17)
    with pytest.raises(dns.exception.FormError, match="chain"):
        dns.name.from_wire(wire, 34)


def test_email_validation_remains_syntax_only(monkeypatch):
    def no_dns(*args, **kwargs):
        raise AssertionError("Pydantic email validation must not perform DNS I/O")

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", no_dns)
    adapter = TypeAdapter(EmailStr)
    assert adapter.validate_python("operator@example.com") == "operator@example.com"
    with pytest.raises(ValidationError):
        adapter.validate_python("not an email")
