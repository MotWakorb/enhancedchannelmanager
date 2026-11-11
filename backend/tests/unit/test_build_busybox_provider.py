"""The extra approved provider preserves Alpine's applets/config and full checks."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_busybox_provider", ROOT / "scripts/build_busybox_provider.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sealed_recipe_has_all_vendor_inputs_and_no_removed_runtime_package(builder):
    recipe = builder.load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    assert recipe["applet_count"] == 304 and recipe["configuration_settings"] == 1084
    assert recipe["runtime_package_names"] == ["busybox", "busybox-binsh", "ssl_client"]
    assert len(recipe["aports"]["vendor_patch_witnesses"]) == 44
    assert len(recipe["prepared_source_sha256"]) == 50


def test_derived_apkbuild_retains_original_test_commands_and_checksum_values(
    builder, tmp_path
):
    recipe = builder.load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    builder.safe_extract(
        ROOT / "docker/alpine-busybox" / recipe["aports"]["file"], tmp_path / "aports"
    )
    original = (tmp_path / "aports/busybox/APKBUILD").read_text()
    derived = builder.derive_apkbuild(original, recipe)
    assert (
        hashlib.sha256(derived.encode()).hexdigest()
        == recipe["aports"]["derived_recipe_sha256"]
    )
    assert 'SKIP_KNOWN_BUGS=1 make KBUILD_OUTPUT="$_dyndir" V=1 check' in derived
    assert 'SKIP_KNOWN_BUGS=1 make KBUILD_OUTPUT="$_dyndir_extras" V=1 check' in derived
    assert derived.count("SKIP_KNOWN_BUGS") == original.count("SKIP_KNOWN_BUGS") == 2
    assert "SKIP_INTERNET_TESTS" not in derived and "!check" not in derived
    assert "pkgver=1.37.0_p20261109" in derived
    assert "https://busybox.net/downloads/busybox-1.37.0.tar.bz2" in derived


def test_every_original_config_setting_must_survive(builder):
    assert builder.config_settings("CONFIG_FOO=y\n# CONFIG_BAR is not set\n") == {
        "CONFIG_FOO": "y",
        "CONFIG_BAR": "n",
    }
    with pytest.raises(builder.BuildError, match="duplicate"):
        builder.config_settings("CONFIG_FOO=y\nCONFIG_FOO=n\n")


def test_unknown_vendor_recipe_is_not_a_build_override(builder):
    recipe = builder.load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    with pytest.raises(builder.BuildError, match="original.*recipe"):
        builder.derive_apkbuild("unreviewed APKBUILD", recipe)


def test_a_partial_log_is_not_a_completed_upstream_check(builder, tmp_path):
    (tmp_path / "check.log").write_bytes(b"PASS: one test before interruption\n")
    with pytest.raises(builder.BuildError, match="completed.*receipt"):
        builder.require_checks(tmp_path / "work", tmp_path)


def test_unicode_test_goldens_use_the_original_utf8_locale_not_ascii(builder, tmp_path):
    recipe = builder.load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    assert (
        builder.environment(tmp_path / "work", tmp_path / "output", recipe)["LC_ALL"]
        == "C"
    )
    assert (
        builder.check_environment(tmp_path / "work", tmp_path / "output", recipe)[
            "LC_ALL"
        ]
        == "C.UTF-8"
    )


def test_runtime_namespace_and_nonbinary_bytes_cannot_change(builder):
    recipe = builder.load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    group = recipe["architecture_inputs"]["x86_64"]
    original = next(
        p for p in group["signed_packages"] if p["index_record"]["P"] == "busybox"
    )["original_payload"]
    candidate = copy.deepcopy(original)
    binary = next(row for row in candidate if row["path"] == "bin/busybox")
    binary["sha256"] = "a" * 64
    binary["size"] += 100
    builder.validate_payload(group, "busybox", candidate)
    with pytest.raises(builder.BuildError, match="namespace"):
        builder.validate_payload(group, "busybox", candidate[:-1])
    changed = copy.deepcopy(candidate)
    notice = next(
        row for row in changed if row["path"] == "etc/busybox-paths.d/busybox"
    )
    notice["sha256"] = "b" * 64
    with pytest.raises(builder.BuildError, match="original.*payload"):
        builder.validate_payload(group, "busybox", changed)
