"""The real signed-APK ELF is the fixture; labels never authenticate its graph."""

import base64
import copy
import gzip
import hashlib
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ELF_SHA256 = "6b6216bae67a3e1aae236e8f772067005429ed6d90223daca358a81174002c32"


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_rust_auditable", ROOT / "scripts/alpine_rust_auditable.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def elf():
    data = gzip.decompress(
        (
            ROOT / "backend/tests/fixtures/alpine/rav1e-arm64-original-elf.gz"
        ).read_bytes()
    )
    assert hashlib.sha256(data).hexdigest() == ELF_SHA256
    return data


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from alpine_rust_source_inputs import locked_crates

    recorded = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/alpine/rust-patched-locks-and-cfg-if.json.gz"
            ).read_bytes()
        )
    )
    paths = {}
    for origin in ["glycin", "libglycin", "rav1e"]:
        path = tmp_path / (origin + "-Cargo.lock")
        path.write_bytes(base64.b64decode(recorded["files"][path.name]))
        paths[origin] = path
    return locked_crates(paths)


def section_headers(elf):
    offset = struct.unpack_from("<Q", elf, 40)[0]
    width, count, names = struct.unpack_from("<HHH", elf, 58)
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", elf, offset + i * width) for i in range(count)
    ]
    entry = sections[names]
    strings = elf[entry[4] : entry[4] + entry[5]]
    return (
        offset,
        width,
        [strings[s[0] : strings.index(b"\0", s[0])].decode() for s in sections],
        sections,
    )


def replace_payload(elf, payload):
    """Repack only an ELF metadata section; outer hash is recomputed by each test."""
    offset, width, names, _ = section_headers(elf)
    index = names.index(".dep-v0")
    changed = bytearray(elf)
    struct.pack_into(
        "<QQ", changed, offset + index * width + 24, len(changed), len(payload)
    )
    changed.extend(payload)
    return bytes(changed)


def test_real_elf_decodes_the_original_graph_and_default_fields(consumer, elf):
    result = consumer.decode_elf(elf, ELF_SHA256, "arm64")
    assert result["format"] == 1 and len(result["packages"]) == 130
    assert (
        result["section_sha256"]
        == "f06bb7e7a17e60bd0b9acf81237e093b592d2c05ef0425dac67c880edf3987ff"
    )
    assert sum(p["source"] == "crates.io" for p in result["packages"]) == 128
    assert sum(p["kind"] == "build" for p in result["packages"]) == 46
    assert sum(p["kind"] == "runtime" for p in result["packages"]) == 84
    assert sum(p["root"] for p in result["packages"]) == 1
    assert result["packages"][0]["root"] is False


def test_every_real_graph_package_joins_its_origin_lock_not_other_origins(
    consumer, elf, plan
):
    result = consumer.join_lock(
        consumer.decode_elf(elf, ELF_SHA256, "arm64"), plan, "rav1e"
    )
    assert result["registry_packages"] == 128 and result["local_packages"] == 2
    assert result["root"] == {"name": "rav1e", "version": "0.8.1"}
    assert all(p["archive_sha256"] for p in result["registry"])
    assert result["full_runtime_source_acceptance"] is False
    assert result["exact_compiled_features_established"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "changed-bytes",
        "architecture",
        "class",
        "endian",
        "table-range",
        "table-width",
        "names-range",
        "duplicate-section",
        "missing-section",
        "section-range",
        "section-type",
    ],
)
def test_invalid_elf_structure_cannot_become_a_missing_metadata_success(
    consumer, elf, mutation
):
    changed = bytearray(elf)
    offset, width, names, sections = section_headers(elf)
    index = names.index(".dep-v0")
    digest = ELF_SHA256
    if mutation == "changed-bytes":
        changed[128] ^= 1
    elif mutation == "architecture":
        struct.pack_into("<H", changed, 18, 62)
    elif mutation == "class":
        changed[4] = 1
    elif mutation == "endian":
        changed[5] = 2
    elif mutation == "table-range":
        struct.pack_into("<Q", changed, 40, len(changed))
    elif mutation == "table-width":
        struct.pack_into("<H", changed, 58, 1)
    elif mutation == "names-range":
        struct.pack_into("<H", changed, 62, len(sections))
    elif mutation == "duplicate-section":
        struct.pack_into(
            "<I", changed, offset + (index - 1) * width, sections[index][0]
        )
    elif mutation == "missing-section":
        struct.pack_into("<I", changed, offset + index * width, 0)
    elif mutation == "section-range":
        struct.pack_into("<Q", changed, offset + index * width + 24, len(changed))
    else:
        struct.pack_into("<I", changed, offset + index * width + 4, 8)
    if mutation != "changed-bytes":
        digest = hashlib.sha256(changed).hexdigest()
    with pytest.raises(consumer.InputError):
        consumer.decode_elf(bytes(changed), digest, "arm64")


@pytest.mark.parametrize(
    "payload",
    [
        b"not-zlib",
        zlib.compress(b"{}") + b"hidden",
        zlib.compress(b"{}") + zlib.compress(b"{}"),
        zlib.compress(b"{}")[:-1],
        zlib.compress(b"x" * (4 * 1024**2 + 1)),
        zlib.compress(b'{"format":1,"format":1,"packages":[]}'),
    ],
)
def test_metadata_is_a_single_bounded_zlib_stream_and_unique_json_document(
    consumer, elf, payload
):
    changed = replace_payload(elf, payload)
    with pytest.raises(consumer.InputError):
        consumer.decode_elf(changed, hashlib.sha256(changed).hexdigest(), "arm64")


@pytest.mark.parametrize(
    "mutation",
    [
        "format",
        "boolean-format",
        "unknown-field",
        "empty-packages",
        "unknown-package-field",
        "bad-source",
        "bad-kind",
        "bad-root",
        "two-roots",
        "no-root",
        "duplicate-package",
        "bad-dependency",
        "boolean-dependency",
        "duplicate-dependency",
        "cycle",
    ],
)
def test_graph_schema_and_indexes_are_checked_before_lock_join(consumer, elf, mutation):
    _, _, names, sections = section_headers(elf)
    s = sections[names.index(".dep-v0")]
    document = json.loads(zlib.decompress(elf[s[4] : s[4] + s[5]]))
    p = document["packages"]
    if mutation == "format":
        document["format"] = 2
    elif mutation == "boolean-format":
        document["format"] = True
    elif mutation == "unknown-field":
        document["accepted"] = True
    elif mutation == "empty-packages":
        document["packages"] = []
    elif mutation == "unknown-package-field":
        p[0]["accepted"] = True
    elif mutation == "bad-source":
        p[0]["source"] = "git"
    elif mutation == "bad-kind":
        p[0]["kind"] = "dev"
    elif mutation == "bad-root":
        p[0]["root"] = 1
    elif mutation == "two-roots":
        p[0]["root"] = True
    elif mutation == "no-root":
        p[92].pop("root")
    elif mutation == "duplicate-package":
        p.append(copy.deepcopy(p[0]))
    elif mutation == "bad-dependency":
        p[0]["dependencies"] = [len(p)]
    elif mutation == "boolean-dependency":
        p[0]["dependencies"] = [True]
    elif mutation == "duplicate-dependency":
        p[0]["dependencies"] = [9, 9]
    else:
        p[0]["dependencies"] = [0]
    changed = replace_payload(elf, zlib.compress(json.dumps(document).encode()))
    with pytest.raises(consumer.InputError):
        consumer.decode_elf(changed, hashlib.sha256(changed).hexdigest(), "arm64")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-registry",
        "other-origin",
        "missing-workspace",
        "wrong-version",
        "wrong-root",
        "unknown-origin",
    ],
)
def test_a_well_formed_graph_cannot_invent_source_correspondence(
    consumer, elf, plan, mutation
):
    metadata = consumer.decode_elf(elf, ELF_SHA256, "arm64")
    p = metadata["packages"][0]
    if mutation == "missing-registry":
        plan["registry"] = [
            r
            for r in plan["registry"]
            if (r["name"], r["version"]) != (p["name"], p["version"])
        ]
    elif mutation == "other-origin":
        for r in plan["registry"]:
            if (r["name"], r["version"]) == (p["name"], p["version"]):
                r["origins"] = ["glycin"]
    elif mutation == "missing-workspace":
        plan["workspace"]["rav1e"] = []
    elif mutation == "wrong-version":
        p["version"] = "99.0.0"
    elif mutation == "wrong-root":
        metadata["packages"][92]["name"] = "ivf"
    with pytest.raises(consumer.InputError):
        consumer.join_lock(
            metadata, plan, "unknown" if mutation == "unknown-origin" else "rav1e"
        )
