"""The Alpine image must build from a checkout without external named contexts."""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def composition():
    spec = importlib.util.spec_from_file_location(
        "compose_alpine_dockerfile", ROOT / "scripts/compose_alpine_dockerfile.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_standalone_recipe_is_exact_component_composition(composition):
    assert (ROOT / composition.OUTPUT).read_text() == composition.render(ROOT)


def test_standard_dockerfile_uses_the_self_contained_alpine_graph(composition):
    assert (ROOT / "Dockerfile").read_text() == composition.render(ROOT)


def test_all_component_contexts_are_internal_previous_stages(composition):
    text = composition.render(ROOT)
    completed = set()
    for line in text.splitlines():
        if line.startswith("FROM "):
            base, alias = line.split()[1::2]
            assert base in completed or "@sha256:" in base or base == "scratch"
            assert alias not in completed
            completed.add(alias)
        for source in re.findall(r"(?:--from=|,from=)([a-z0-9-]+)", line):
            assert source in completed, f"unresolved external context: {source}"
    assert {
        "owned-cache",
        "security-cache",
        "jxl-cache",
        "ffmpeg-cache",
        "resdet-cache",
        "busybox-cache",
        "proof-wheels",
        "gnu-sources",
        "native-packages",
        "busybox-packages",
        "application-candidate",
    } <= completed


def test_original_builder_recipes_and_checks_are_not_reimplemented(composition):
    text = composition.render(ROOT)
    for relative in composition.COMPONENTS:
        assert (ROOT / relative).read_text().strip() in text
    assert "build_busybox_provider.py check" in text
    assert "verify_alpine_ffmpeg.py" in text
    assert "--allow-untrusted" not in text
    assert "zypper" not in text and "tumbleweed" not in text


def test_source_acquisition_is_separate_from_networkless_builds(composition):
    text = composition.render(ROOT)
    for script in (
        "prepare_alpine_native_cache.py",
        "prepare_busybox_cache.py",
        "prepare_musllinux_wheels.py",
        "prepare_musllinux_gnu_sources.py",
    ):
        assert script in text
    candidate = (ROOT / "docker/alpine-native/Dockerfile.candidate").read_text()
    assert candidate in text
    assert text.endswith("COPY Dockerfile /usr/share/ecm/sources/trust/Dockerfile\n")
    assert "--require-hashes" in text and "--network=none" in text


def test_production_contract_rejects_historical_suse_fallback(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from generate_sbom import SbomError, validate_native_runtime_docker_contract

    with pytest.raises(SbomError, match="exact reviewed Alpine recipe"):
        validate_native_runtime_docker_contract(
            (ROOT / "docker/native/Dockerfile.suse").read_text(), "historical fixture"
        )


def test_source_sbom_includes_all_owned_alpine_provider_identities(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from generate_sbom import _alpine_runtime_native_inputs

    sources = {}
    packages = _alpine_runtime_native_inputs(ROOT, sources)
    assert {p["id"] for p in packages} == {
        "codec2",
        "twolame",
        "shine",
        "libxml2",
        "librsvg",
        "speex",
        "libjxl",
        "ffmpeg",
        "gosu",
        "busybox",
    }
    assert "docker/alpine-native/Dockerfile.inputs" in sources
    assert "scripts/compose_alpine_dockerfile.py" in sources
