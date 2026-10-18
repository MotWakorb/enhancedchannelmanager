"""CVE-2026-104874: small synthetic native items-view ownership regression."""

import sys

from multidict import CIMultiDict
import pytest


@pytest.mark.parametrize("operation", ["reflected_union", "subtraction"])
def test_items_view_operations_release_temporary_value_references(operation):
    value = object()
    mapping = CIMultiDict()
    mapping["existing"] = value
    operand = [(f"synthetic-{index}", value) for index in range(128)]
    before = sys.getrefcount(value)
    result = operand | mapping.items() if operation == "reflected_union" else mapping.items() - operand
    del result
    assert sys.getrefcount(value) == before
