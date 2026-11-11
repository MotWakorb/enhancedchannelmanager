"""Execute original c9da977c vectors in isolated native musl subprocesses.

The caller supplies a private compiled gdbusintrospection.c prototype, not a
production GLib installation. Abnormal old-source exits remain visible failures.
"""

import json
import os
import subprocess
import sys

import pytest

WORKER = """
import ctypes, json, sys
ctypes.CDLL('libgobject-2.0.so.0', mode=ctypes.RTLD_GLOBAL)
parser = ctypes.CDLL(sys.argv[1])
class GError(ctypes.Structure):
    _fields_ = [('domain', ctypes.c_uint32), ('code', ctypes.c_int),
               ('message', ctypes.c_char_p)]
error = ctypes.POINTER(GError)()
parser.g_dbus_node_info_new_for_xml.argtypes = [ctypes.c_char_p,
                                             ctypes.POINTER(ctypes.POINTER(GError))]
parser.g_dbus_node_info_new_for_xml.restype = ctypes.c_void_p
node = parser.g_dbus_node_info_new_for_xml(sys.argv[2].encode(), ctypes.byref(error))
glib = ctypes.CDLL('libglib-2.0.so.0')
glib.g_markup_error_quark.restype = ctypes.c_uint32
result = {'node': bool(node), 'error': None,
          'markup_domain': glib.g_markup_error_quark()}
if error:
    result['error'] = {'domain': error.contents.domain, 'code': error.contents.code}
    glib.g_error_free.argtypes = [ctypes.POINTER(GError)]
    glib.g_error_free(error)
if node:
    parser.g_dbus_node_info_unref.argtypes = [ctypes.c_void_p]
    parser.g_dbus_node_info_unref(node)
print(json.dumps(result))
"""


def parse_in_subprocess(xml):
    result = subprocess.run(
        [sys.executable, "-c", WORKER, os.environ["ECM_GLIB_PROOF_LIBRARY"], xml],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("xml", "expected_code"),
    [
        ("", 1),  # G_MARKUP_ERROR_EMPTY
        (
            '<node><interface name="I"><method name="M"><node>'
            '<interface name="I2"></interface></node></method>',
            5,
        ),
        (
            '<node><interface name="I"><signal name="S"><node>'
            '<interface name="I2"><signal name="S2"></signal>'
            "</interface></node></signal>",
            5,
        ),
        (
            '<node><interface name="I"><property name="P" type="s" access="read">'
            '<node><interface name="I2"></interface></node></property>',
            5,
        ),
        (
            '<node><interface name="I"><method name="M"><arg type=""><node>'
            '<interface name="I2"><method name="M2"></method>'
            "</interface></node></arg>",
            5,
        ),
    ],
)
def test_original_upstream_invalid_vectors(xml, expected_code):
    result = parse_in_subprocess(xml)
    assert result["node"] is False
    assert result["error"] == {
        "domain": result["markup_domain"],
        "code": expected_code,
    }


@pytest.mark.parametrize("xml", ["<node/>", '<node><node name="child"/></node>'])
def test_valid_node_nesting_remains_accepted(xml):
    result = parse_in_subprocess(xml)
    assert result["node"] is True
    assert result["error"] is None
