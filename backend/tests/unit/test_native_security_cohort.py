"""Four P1 fixes must travel as one source-closed native vendor transaction."""

import copy
import hashlib
import importlib.util
import json
import shutil
import ssl
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/native"
VERSIONS = {
    "librsvg-2-2": ("2.63.4-1.1", "librsvg-2.63.4-1.1.src.rpm"),
    "libspeex1": ("1.2.1-3.1", "speex-1.2.1-3.1.src.rpm"),
    "ca-certificates-mozilla": ("2.92-2.1", "ca-certificates-mozilla-2.92-2.1.src.rpm"),
    **dict.fromkeys(
        ("libbrotlicommon1", "libbrotlidec1", "libbrotlienc1"),
        ("1.2.1-1.1", "brotli-1.2.1-1.1.src.rpm"),
    ),
}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def builder():
    return module("native_security_builder", ROOT / "scripts/build_native_runtime.py")


def manifest():
    return json.loads((ASSETS / "manifest.json").read_text())


@pytest.mark.parametrize("arch,rpm_arch", [("amd64", "x86_64"), ("arm64", "aarch64")])
def test_fixed_runtime_descriptors_bind_each_native_architecture(
    builder, arch, rpm_arch
):
    selected = builder.load_recipe(ASSETS / "manifest.json", arch)["architectures"][
        arch
    ]
    assert selected["builder_repository"] == (
        "https://download.opensuse.org/history/20261105/tumbleweed/repo/oss/"
        if arch == "amd64" else selected["official_repository"]
    )
    assert selected["cohort"] == "20261105"
    rows = selected["security_runtime_rpms"]
    assert len(rows) == 6 and {p["name"] for p in rows} == set(VERSIONS)
    for row in rows:
        assert (row["version"], row["sourcerpm"]) == VERSIONS[row["name"]]
        assert row["arch"] == (
            "noarch" if row["name"] == "ca-certificates-mozilla" else rpm_arch
        )
        assert row["epoch"] == "0" and row["metadata_checksum_type"] == "sha512"
        assert len(row["metadata_checksum"]) == 128
    ca = next(p for p in rows if p["name"] == "ca-certificates-mozilla")
    recorded = json.loads(
        (
            ROOT
            / "backend/tests/fixtures/native_runtime/security-20261105-ca-primary.json"
        ).read_text()
    )[arch]
    assert selected["repository_metadata_sha256"] == recorded["repomd_sha256"]
    assert ca["metadata_checksum"] == recorded["package"]["checksum"]
    assert (
        ca["url"] == selected["builder_repository"] + recorded["package"]["location"]
    )


@pytest.mark.parametrize("name", VERSIONS)
@pytest.mark.parametrize(
    "field,value",
    [("version", "1.0-1.1"), ("sourcerpm", "wrong-1.0-1.1.src.rpm"), ("arch", "i586")],
)
def test_runtime_security_descriptor_downgrade_or_misbinding_is_rejected(
    builder, tmp_path, name, field, value
):
    assets = tmp_path / "native"
    shutil.copytree(ASSETS, assets)
    data = manifest()
    row = next(
        p
        for p in data["architectures"]["amd64"]["security_runtime_rpms"]
        if p["name"] == name
    )
    row[field] = value
    (assets / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(builder.BuildError, match="security|architecture|RPM"):
        builder.load_recipe(assets / "manifest.json", "amd64")


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "wrong-source", "wrong-version", "wrong-arch"]
)
def test_actual_installed_security_inventory_is_checked(builder, mutation):
    recipe = manifest()["architectures"]["amd64"]
    inventory = [
        {
            "name": p["name"],
            "arch": p["arch"],
            "epoch": p["epoch"],
            "version": p["version"].rsplit("-", 1)[0],
            "release": p["version"].rsplit("-", 1)[1],
            "sourcerpm": p["sourcerpm"],
            "license": "MIT",
        }
        for p in recipe["security_runtime_rpms"]
    ]
    assert len(builder.security_runtime_packages(recipe, inventory)) == 6
    broken = copy.deepcopy(inventory)
    if mutation == "missing":
        broken.pop()
    elif mutation == "duplicate":
        broken.append(broken[0].copy())
    else:
        key = {
            "wrong-source": "sourcerpm",
            "wrong-version": "version",
            "wrong-arch": "arch",
        }[mutation]
        broken[0][key] = "wrong"
    with pytest.raises(builder.BuildError, match="security"):
        builder.security_runtime_packages(recipe, broken)


def test_builder_install_enforces_security_packages_in_same_solver_transaction(
    builder, monkeypatch, tmp_path
):
    recipe = manifest()["architectures"]["amd64"]
    calls = []
    inventory = [
        "\t".join(
            [
                p["name"],
                p["epoch"],
                *p["version"].rsplit("-", 1),
                p["arch"],
                p["sourcerpm"],
                "MIT",
            ]
        )
        for p in recipe["builder_rpms"] + recipe["security_runtime_rpms"]
    ]
    real_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            'ID="opensuse-tumbleweed"'
            if str(path) == "/etc/os-release"
            else real_read(path, *a, **kw)
        ),
    )
    monkeypatch.setattr(Path, "glob", lambda *a: iter(()))

    def run(args, **kwargs):
        calls.append(args)
        return "\n".join(inventory) if args[:2] == ["/usr/bin/rpm", "-qa"] else ""

    monkeypatch.setattr(builder, "run", run)
    monkeypatch.setattr(builder, "check_repository_cohort", lambda recipe: None)
    builder._install_builder(recipe, tmp_path, {})
    install = next(
        c
        for c in calls
        if c[:4] == ["/usr/bin/zypper", "--non-interactive", "--no-refresh", "install"]
    )
    assert all(
        builder._rpm_spec(row) in install for row in recipe["security_runtime_rpms"]
    )


def test_docker_cohort_and_fresh_source_collection_are_not_old_carrier_relabelling():
    docker = (ROOT / "Dockerfile").read_text()
    assert "history/20261022" not in docker
    source_stage = docker.split(" AS source-builder\n", 1)[1].split("\nFROM ", 1)[0]
    assert "--inventory /tmp/runtime-inventory.tsv" in source_stage
    assert "--cache-dir" not in source_stage and "--expected-sha256" not in source_stage
    assert "ecm-native-sources" not in source_stage
    assert "--from=ecm-native-sources" not in docker
    assert (
        "--suse-key /tmp/suse.asc --packman-key /tmp/native/packman.asc" in source_stage
    )
    assert (
        "--native-manifest /tmp/native/manifest.json --native-sources /tmp/native-output/sources"
        in source_stage
    )


def test_source_consumer_binds_current_recipe():
    consumer = module(
        "native_security_source_consumer", ROOT / "tests/native_image/source_checks.py"
    )
    assert (
        consumer.RECIPE_SHA256
        == hashlib.sha256((ASSETS / "manifest.json").read_bytes()).hexdigest()
    )


def test_refreshed_repository_must_match_reviewed_signed_cohort(builder, tmp_path):
    recipe = manifest()["architectures"]["amd64"]
    path = tmp_path / "repomd.xml"
    path.write_bytes(b"different signed vendor snapshot")
    with pytest.raises(builder.BuildError, match="cohort"):
        builder.check_repository_cohort(recipe, path)
    recipe["repository_metadata_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    builder.check_repository_cohort(recipe, path)


def test_sbom_contract_accepts_fresh_all_origin_native_source_path():
    sbom = module("native_security_sbom", ROOT / "scripts/generate_sbom.py")
    native = json.loads((ROOT / "sbom/native-dependencies.json").read_text())
    docker = (ROOT / "Dockerfile").read_text()
    sbom.validate_resdet_docker_contract(native, docker, "Dockerfile")
    with pytest.raises(sbom.SbomError):
        sbom.validate_resdet_docker_contract(
            native,
            docker.replace(
                "--out /tmp/rpm-source-output",
                "--out /tmp/rpm-source-output --cache-dir /tmp/old-carrier",
            ),
            "Dockerfile",
        )


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_final_runtime_gate_checks_fixed_native_rpm_identities(monkeypatch, arch):
    gate = module(
        "native_security_runtime_consumer",
        ROOT / "tests/native_image/runtime_checks.py",
    )
    monkeypatch.setattr(gate.platform, "machine", lambda: arch)
    rows = []
    for name, (version, source) in VERSIONS.items():
        rpm_arch = "noarch" if name == "ca-certificates-mozilla" else arch
        rows.append(f"{name}\t{version}\t{rpm_arch}\t{source}\topenSUSE")
    monkeypatch.setattr(gate, "command", lambda *a, **kw: "\n".join(rows))
    assert len(gate.security_runtime_packages()) == 6
    rows[0] = rows[0].replace("2.63.4-1.1", "2.62.4-1.1")
    with pytest.raises(AssertionError, match="security"):
        gate.security_runtime_packages()


@pytest.mark.parametrize("arch,rpm_arch", [("amd64", "x86_64"), ("arm64", "aarch64")])
def test_fresh_collection_reuses_only_exact_architecture_bound_native_packman_sources(
    tmp_path, arch, rpm_arch
):
    collector = module(
        "native_security_collector", ROOT / "scripts/collect_native_sources.py"
    )
    directory = tmp_path / "sources"
    directory.mkdir()
    selected = manifest()["architectures"][arch]["source_rpms"]
    origins = [(p["filename"], "http://packman.links2linux.de") for p in selected]
    for entry in selected:
        shutil.copyfile(
            ROOT / "docker/native-cache" / entry["sha256"],
            directory / entry["filename"],
        )
    inputs = collector.native_source_inputs(
        ASSETS / "manifest.json", directory, rpm_arch, origins
    )
    assert set(inputs) == {p["filename"] for p in selected}
    for entry in selected:
        assert inputs[entry["filename"]] == (
            directory / entry["filename"],
            entry["sha256"],
        )
    with pytest.raises(ValueError, match="native.*source"):
        collector.native_source_inputs(
            ASSETS / "manifest.json",
            directory,
            "aarch64" if arch == "amd64" else "x86_64",
            origins,
        )
    (directory / selected[0]["filename"]).write_bytes(b"tampered archive")
    with pytest.raises(ValueError, match="native.*source"):
        collector.native_source_inputs(
            ASSETS / "manifest.json", directory, rpm_arch, origins
        )


def test_native_source_handoff_cannot_supply_suse_origins(tmp_path):
    collector = module(
        "native_security_collector_suse", ROOT / "scripts/collect_native_sources.py"
    )
    with pytest.raises(ValueError, match="native.*source"):
        collector.native_source_inputs(
            ASSETS / "manifest.json",
            tmp_path,
            "x86_64",
            [("invented-1-1.src.rpm", "http://packman.links2linux.de")],
        )
    # No SUSE row is mapped to native inputs, even if a same-named file exists.
    assert (
        collector.native_source_inputs(
            ASSETS / "manifest.json",
            tmp_path,
            "x86_64",
            [("libx264-20250608.b35605ac-1699.1.pm.18.src.rpm", "openSUSE")],
        )
        == {}
    )


def test_source_failure_logs_exact_allowed_url_without_retry(
    monkeypatch, tmp_path, capsys
):
    collector = module(
        "native_security_collector_diagnostic",
        ROOT / "scripts/collect_native_sources.py",
    )
    url = collector.source_url("openSUSE", "x86_64", "librsvg-2.63.4-1.1.src.rpm")
    calls = []

    def fail(request, **kwargs):
        calls.append(request)
        raise HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(
        collector.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=fail)
    )
    with pytest.raises(HTTPError):
        collector.download(url, tmp_path / "archive", collector.Budget(1, 1024, 10))
    assert calls == [url] and url in capsys.readouterr().out


@pytest.mark.parametrize("reject_signature", [False, True])
def test_native_handoff_replays_collector_verification_and_never_skips_origins(
    monkeypatch, tmp_path, reject_signature
):
    collector = module(
        "native_security_collector_replay", ROOT / "scripts/collect_native_sources.py"
    )
    selected = manifest()["architectures"]["amd64"]
    directory = tmp_path / "native-sources"
    directory.mkdir()
    for item in selected["source_rpms"]:
        shutil.copyfile(
            ROOT / "docker/native-cache" / item["sha256"], directory / item["filename"]
        )
    rows = []
    for item in selected["codec_rpms"]:
        if item["name"] in ("libx264-165", "libx265-215"):
            version, release = item["version"].rsplit("-", 1)
            rows.append(
                f"{item['name']}\t{version}\t{release}\tx86_64\thttp://packman.links2linux.de\t{item['sourcerpm']}\tGPL-2.0-or-later\n"
            )
    inventory = tmp_path / "inventory.tsv"
    inventory.write_text("".join(rows))
    output = tmp_path / "collected"
    verified = []
    monkeypatch.setattr(collector, "prepare_key", lambda *a: tmp_path / "isolated-db")
    monkeypatch.setattr(
        collector,
        "download",
        lambda *a: pytest.fail("reviewed native source must be reused"),
    )

    def verify(path, name, vendor, database, seconds):
        verified.append(name)
        assert database == tmp_path / "isolated-db"
        if reject_signature:
            raise ValueError("signature rejected")
        return {
            "filename": name,
            "vendor": vendor,
            "sha256": collector.sha256(path),
            "fingerprint": collector.REPOSITORIES[vendor]["fingerprint"],
        }

    monkeypatch.setattr(collector, "verify_source", verify)
    args = [
        "--inventory",
        str(inventory),
        "--arch",
        "x86_64",
        "--out",
        str(output),
        "--packman-key",
        str(ASSETS / "packman.asc"),
        "--native-manifest",
        str(ASSETS / "manifest.json"),
        "--native-sources",
        str(directory),
    ]
    if reject_signature:
        with pytest.raises(ValueError, match="signature"):
            collector.main(args)
        assert not (output / "index.json").exists()
    else:
        collector.main(args)
        index = json.loads((output / "index.json").read_text())
        assert len(index["sources"]) == len(verified) == 2
        assert {r["retrieval"] for r in index["sources"]} == {"reverified-native-input"}
        assert set(verified) == {p["filename"] for p in selected["source_rpms"]}
        expected = tmp_path / "incomplete-expected.json"
        expected.write_text(
            json.dumps({"architecture": "x86_64", "sources": index["sources"][:1]})
        )
        with pytest.raises(ValueError, match="cover every selected origin"):
            collector.main(
                [
                    *args,
                    "--cache-dir",
                    str(directory),
                    "--expected-sha256",
                    str(expected),
                ]
            )


def test_both_tls_contexts_must_also_reject_comodo_aaa_subject(monkeypatch):
    gate = module(
        "native_security_ca_consumer", ROOT / "tests/native_image/runtime_checks.py"
    )
    monkeypatch.setattr(gate, "_UNAFFECTED_G1", hashlib.sha256(b"g1").hexdigest())
    certificates = []
    context = SimpleNamespace(
        verify_mode=ssl.CERT_REQUIRED,
        check_hostname=True,
        get_ca_certs=lambda *, binary_form: [b"g1"] if binary_form else certificates,
    )
    assert gate._trust_policy(context)["comodo_aaa_subject_trusted"] is False
    certificates.append({"subject": ((("commonName", "AAA Certificate Services"),),)})
    with pytest.raises(AssertionError, match="AAA"):
        gate._trust_policy(context)
