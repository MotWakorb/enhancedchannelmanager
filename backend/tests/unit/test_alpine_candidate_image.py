"""The integration candidate preserves production contracts without switching production."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_candidate_preserves_application_identity_healthcheck_and_entrypoint_contracts():
    text = (ROOT / "docker/alpine-native/Dockerfile.candidate").read_text()
    assert (
        "AS frontend-builder" in text
        and "RUN npm ci" in text
        and "RUN npm run build" in text
    )
    assert (
        "--require-hashes" in text and "inventory_musllinux_wheels.py --verify" in text
    )
    assert "groupadd --gid 1000 appuser" in text
    assert (
        "useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser"
        in text
    )
    assert "COPY backend/ /app/" in text
    assert "COPY --from=frontend-builder /app/frontend/dist /app/static" in text
    assert (
        "ENV PUID=1000 PGID=1000 CONFIG_DIR=/config ECM_PORT=6100 ECM_HTTPS_PORT=6143"
        in text
    )
    assert "CMD /opt/venv/bin/python -c" in text
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in text
    assert "EXPOSE 6100 6143" in text and "chmod 700 /config/tls /run/ecm" in text
    assert (
        hashlib.sha256((ROOT / "Dockerfile").read_bytes()).hexdigest()
        == "b2f9e5079591b633c269a71ed4237bf5d15b324dced98b44993bae18f62fd8ee"
    )
    assert (
        hashlib.sha256((ROOT / "mcp-server/Dockerfile").read_bytes()).hexdigest()
        == "c5362f558ffbb15522bda00cc3a088b6e3e82429123c06720549c8a93e56b125"
    )


def test_candidate_installs_signed_native_and_busybox_products_and_preserves_incomplete_source_status():
    text = (ROOT / "docker/alpine-native/Dockerfile.candidate").read_text()
    assert "install_alpine_native.py" in text and "install_busybox_provider.py" in text
    assert "probe_busybox_runtime.py" in text
    assert "--mount=type=bind,from=native-packages" in text
    assert "--mount=type=bind,from=busybox-packages" in text
    assert "apk query --from installed --format json --all-matches" in text
    assert "source-and-advisory-closure=pending" in text
    assert (
        "--allow-untrusted" not in text
        and "edge/" not in text
        and "gcompat" not in text
    )


def test_external_source_builder_consumes_the_prior_runtime_capture_not_its_git_dependencies():
    text = (ROOT / "docker/alpine-native/Dockerfile.candidate").read_text()
    stage = text.split("FROM runtime-base AS apk-source-builder\n", 1)[1].split(
        "\nFROM ", 1
    )[0]
    assert "apk add --no-cache git" in stage
    assert "/usr/share/ecm/provenance/installed-apks.json" in stage
    assert "/usr/share/ecm/provenance/installed-apk-database.txt" in stage
    assert (
        "prepare_alpine_external_sources.py" in stage
        and "--runtime-inventory /proof/runtime-inventory.json" in stage
    )
    assert "apk query" not in stage and "--from installed" not in stage
    assert (
        "COPY --from=apk-source-builder /proof/external-sources/ /usr/share/ecm/sources/apk/"
        in text
    )
    assert (
        "COPY --from=apk-source-builder /proof/external-source-receipt.json /usr/share/ecm/provenance/external-sources.json"
        in text
    )
    assert "source-and-advisory-closure=pending" in text


def test_python_base_source_builder_delivers_only_verified_original_evidence():
    text = (ROOT / "docker/alpine-native/Dockerfile.candidate").read_text()
    stage = text.split("FROM runtime-base AS python-base-source-builder\n", 1)[1].split(
        "\nFROM ", 1
    )[0]
    assert (
        "apk add --no-cache gnupg" in stage and "prepare_alpine_python_base.py" in stage
    )
    assert "--output-dir /proof/python-base-sources" in stage
    assert (
        "COPY --from=python-base-source-builder /proof/python-base-sources/ /usr/share/ecm/sources/python-base/"
        in text
    )
    assert (
        "COPY --from=python-base-source-builder /proof/python-base-source-receipt.json /usr/share/ecm/provenance/python-base-sources.json"
        in text
    )
    assert "scripts/verify_alpine_python_base.py" in text
    final = text.split("FROM runtime-base AS application-candidate\n", 1)[1]
    assert "apk add" not in final and "GNUPGHOME" not in final
