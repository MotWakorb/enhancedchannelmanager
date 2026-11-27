"""AMD history changes transport, never the frozen binary/source identities."""

import copy
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/native"
HISTORY = "https://download.opensuse.org/history/20261105/tumbleweed/repo/oss/"
CANONICAL = "https://download.opensuse.org/tumbleweed/repo/oss/"
PIN = "36db62d4ce6887faa6ec09802a5b1311c1e14e075e5d7011fc590ceccbdabbe6"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_all_47_builder_and_six_security_pins_keep_identity_but_bind_history_transport():
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    amd = recipe["architectures"]["amd64"]
    assert amd["official_repository"] == CANONICAL
    assert amd["builder_repository"] == HISTORY
    assert amd["cohort"] == "20261105" and amd["repository_metadata_sha256"] == PIN
    for group, count, identity in [
        (
            "builder_rpms",
            47,
            "23608d5ff12f1730bc557e416365bf2bd6f834ca9327e264055da3280210a35b",
        ),
        (
            "security_runtime_rpms",
            6,
            "14edb9b59a8251d3f0b7334089b08f79f148c610c901b52ca7395715189422b0",
        ),
    ]:
        assert len(amd[group]) == count
        assert (
            digest([{k: v for k, v in row.items() if k != "url"} for row in amd[group]])
            == identity
        )
        for row in amd[group]:
            assert (
                row["url"]
                == HISTORY
                + row["arch"]
                + "/"
                + row["name"]
                + "-"
                + row["version"]
                + "."
                + row["arch"]
                + ".rpm"
            )
    assert (
        digest(recipe["architectures"]["arm64"])
        == "d8bb3ead4c8d9851f21fb9a26bebced6bace9fd17335d21551cd971681b19149"
    )
    assert (
        amd["official_source_repository"]
        == "https://download.opensuse.org/tumbleweed/repo/src-oss/"
    )


def test_helper_accepts_only_the_reviewed_history_transport(tmp_path):
    builder = load("history_native_builder", "scripts/build_native_runtime.py")
    assets = tmp_path / "native"
    shutil.copytree(ASSETS, assets)
    path = assets / "manifest.json"
    recipe = json.loads(path.read_text())
    recipe["architectures"]["amd64"]["builder_repository"] = HISTORY
    for group in ("builder_rpms", "security_runtime_rpms"):
        for row in recipe["architectures"]["amd64"][group]:
            row["url"] = row["url"].replace(CANONICAL, HISTORY)
    path.write_text(json.dumps(recipe))
    builder.load_recipe(path, "amd64")
    for bad in [
        CANONICAL,
        HISTORY.replace("20261105", "20261106"),
        HISTORY.replace("https:", "http:"),
        HISTORY + "?fallback=1",
    ]:
        mutant = copy.deepcopy(recipe)
        mutant["architectures"]["amd64"]["builder_repository"] = bad
        path.write_text(json.dumps(mutant))
        with pytest.raises(builder.BuildError, match="builder repository"):
            builder.load_recipe(path, "amd64")
    for group in ("builder_rpms", "security_runtime_rpms"):
        mutant = copy.deepcopy(recipe)
        row = mutant["architectures"]["amd64"][group][0]
        row["url"] = row["url"].replace(HISTORY, CANONICAL)
        path.write_text(json.dumps(mutant))
        with pytest.raises(builder.BuildError, match="RPM URL"):
            builder.load_recipe(path, "amd64")


def test_historical_suse_bootstrap_runtime_and_resdet_share_one_exact_cohort():
    docker = (ROOT / "docker/native/Dockerfile.suse").read_text()
    assert docker.count("repository=" + HISTORY + "; metadata=" + PIN) == 2
    assert "repository=" + CANONICAL not in docker
    assert "FROM runtime-base AS resdet-builder" in docker
    resdet = docker.split(" AS resdet-builder\n", 1)[1].split("\nFROM ", 1)[0]
    assert "&& zypper --non-interactive refresh" in resdet
    assert "| sha256sum --check --strict" in resdet
    assert (
        "--no-refresh install --no-recommends gcc make pkgconf-pkg-config gawk"
        in resdet
    )
    sbom = load("history_transport_sbom", "scripts/generate_sbom.py")
    sbom.validate_historical_suse_docker_contract(docker, "Dockerfile.suse")
    for before, after in [
        (HISTORY, CANONICAL),
        (HISTORY, HISTORY.replace("20261105", "20261022")),
        ("--no-refresh ", ""),
    ]:
        mutant = docker.replace(before, after)
        assert mutant != docker
        with pytest.raises(sbom.SbomError):
            sbom.validate_historical_suse_docker_contract(mutant, "Dockerfile.suse")


def test_history_binary_transport_does_not_retarget_source_collection():
    collector = load("history_transport_collector", "scripts/collect_native_sources.py")
    assert (
        collector.source_url("openSUSE", "x86_64", "zlib-1.3.1-3.3.src.rpm")
        == "https://download.opensuse.org/tumbleweed/repo/src-oss/src/zlib-1.3.1-3.3.src.rpm"
    )
    route = collector.EXACT_SOURCE_ROUTES[
        ("openSUSE", "x86_64", "openSUSE-release-20261105-4389.1.src.rpm")
    ]
    assert (
        route["sha256"]
        == "93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123"
    )
    assert "history/" not in route["url"]


def test_recorded_real_history_providers_match_exact_manifest_identities():
    fixture = json.loads(
        (
            ROOT
            / "backend/tests/fixtures/native_runtime/history-20261105-transport.json"
        ).read_text()
    )
    amd = json.loads((ASSETS / "manifest.json").read_text())["architectures"]["amd64"]
    assert fixture["repository"] == amd["builder_repository"] == HISTORY
    assert fixture["repomd_sha256"] == amd["repository_metadata_sha256"] == PIN
    assert (
        fixture["appdata"]["location"]
        == "repodata/" + fixture["appdata"]["sha512"] + "-appdata.xml.gz"
    )
    for observed in fixture["providers"]:
        descriptor = next(
            row
            for row in amd["builder_rpms"] + amd["security_runtime_rpms"]
            if row["name"] == observed["name"]
        )
        assert observed["vendor"] == "openSUSE"
        assert all(
            descriptor[field] == observed[field]
            for field in (
                "name",
                "arch",
                "epoch",
                "version",
                "sourcerpm",
                "metadata_checksum_type",
                "metadata_checksum",
            )
        )
        assert descriptor["url"] == HISTORY + observed["location"]
