"""Exact SUSE G3 distrust; do not replace the system/admin trust store."""

import hashlib
import importlib.util
import re
import ssl
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
G3 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
G1 = "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367"
PEM = "docker/trust/emsign-ecc-root-ca-g3.pem"
BLOCKLIST = "/etc/pki/trust/blocklist/ecm-emsign-ecc-g3.pem"


def load(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blacklist_contains_only_exact_compromised_public_root():
    pem = (ROOT / PEM).read_text()
    assert pem.count("-----BEGIN CERTIFICATE-----") == 1
    assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == G3


def test_reviewed_recipe_selectively_blacklists_and_regenerates_system_trust():
    sbom = load("scripts/generate_sbom.py")
    dockerfile = (ROOT / "Dockerfile").read_text()
    sbom.validate_native_runtime_docker_contract(dockerfile, "Dockerfile")
    runtime = sbom._docker_stages(dockerfile, "Dockerfile")[2]["instructions"]
    instructions = [(kind, args) for kind, args, _ in runtime]
    assert ("COPY", f"{PEM} {BLOCKLIST}") in instructions
    run = next(args for kind, args in instructions if kind == "RUN" and G3 in args)
    assert BLOCKLIST in run and "ssl.PEM_cert_to_DER_cert" in run
    assert run.endswith("&& update-ca-certificates")
    assert (
        "COPY",
        "docker/trust/ /usr/share/ecm/sources/trust/",
    ) in sbom.SUSE_SOURCE_DELIVERY
    assert (
        "COPY",
        "Dockerfile /usr/share/ecm/sources/trust/Dockerfile",
    ) in sbom.SUSE_SOURCE_DELIVERY
    assert "SSL_CERT_FILE=" not in dockerfile and "SSL_CERT_DIR=" not in dockerfile


@pytest.mark.parametrize(
    "old,new",
    [
        (f"COPY {PEM} {BLOCKLIST}\n", ""),
        ("    && update-ca-certificates\n", "\n"),
        (G3, "0" * 64),
        (G3, G1),
        ("COPY docker/trust/ /usr/share/ecm/sources/trust/\n", ""),
        ("COPY Dockerfile /usr/share/ecm/sources/trust/Dockerfile\n", ""),
    ],
)
def test_producer_rejects_missing_or_mutated_distrust_recipe(old, new):
    sbom = load("scripts/generate_sbom.py")
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert old in dockerfile, "mutation must change the actual production recipe"
    with pytest.raises(sbom.SbomError, match="exact reviewed SUSE recipe"):
        sbom.validate_native_runtime_docker_contract(
            dockerfile.replace(old, new), "mutant"
        )


@pytest.mark.parametrize(
    "include_g3,include_g1", [(False, True), (True, True), (False, False)]
)
def test_existing_runtime_gate_requires_exact_negative_and_positive_controls(
    include_g3, include_g1
):
    gate = load("tests/native_image/runtime_checks.py")
    assert gate._COMPROMISED_G3 == G3 and gate._UNAFFECTED_G1 == G1
    bundle = (
        ROOT / "docker/python-third-party/sources/certifi-2026.10.19-cacert.pem"
    ).read_text()
    g1 = next(
        pem
        for pem in re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            bundle,
            re.DOTALL,
        )
        if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == G1
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if include_g1:
        context.load_verify_locations(cadata=g1)
    if include_g3:
        context.load_verify_locations(cadata=(ROOT / PEM).read_text())
    if include_g3 or not include_g1:
        with pytest.raises(AssertionError):
            gate._trust_policy(context)
    else:
        assert gate._trust_policy(context) == {
            "compromised_g3_trusted": False,
            "unaffected_g1_trusted": True,
            "anchor_count": 1,
        }
