"""Container-isolation contract for the MCP sidecar (…-04c0u.8).

These assertions pin the deployment shape that keeps a compromised AI-facing
sidecar away from ECM's secrets: no ``/config`` mount, a credential-only
projection, non-root shared identity, and a locked-down runtime.
"""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]

PROJECTION_DIR = "/run/secrets/ecm-mcp"
PROJECTION_VOLUME = "ecm-mcp-secrets"


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.mcp.yml").read_text())


def test_compose_applies_sidecar_runtime_isolation() -> None:
    service = _compose()["services"]["ecm-mcp"]
    # Shared non-root identity with the backend (…-04c0u.7). A fixed UID that
    # differs from ECM's would make the owner-only private projection
    # unreadable, so this must stay expressed as PUID/PGID.
    assert service["user"] == "${PUID:-1000}:${PGID:-1000}"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777"]
    assert service["pids_limit"] == 128
    assert service["mem_limit"] == "256m"
    assert service["cpus"] == 1.0
    assert service["volumes"] == [f"{PROJECTION_VOLUME}:{PROJECTION_DIR}:ro"]


def test_compose_projects_credentials_without_mounting_config_volume() -> None:
    compose = _compose()
    assert compose["services"]["ecm"]["volumes"] == [
        f"{PROJECTION_VOLUME}:{PROJECTION_DIR}"
    ]
    for service_name in ("ecm", "ecm-mcp"):
        environment = compose["services"][service_name]["environment"]
        setting = next(
            item for item in environment if item.startswith("MCP_SECRETS_DIR=")
        )
        assert setting.removeprefix("MCP_SECRETS_DIR=") == PROJECTION_DIR
    sidecar_environment = compose["services"]["ecm-mcp"]["environment"]
    assert all("CONFIG_DIR" not in item for item in sidecar_environment)
    assert PROJECTION_VOLUME in compose["volumes"]


def test_sidecar_image_defaults_to_the_projection_and_non_root_user() -> None:
    dockerfile = (ROOT / "mcp-server" / "Dockerfile").read_text()
    assert f"ENV MCP_SECRETS_DIR={PROJECTION_DIR}" in dockerfile
    assert "ENV CONFIG_DIR=" not in dockerfile
    assert "USER appuser:appgroup" in dockerfile
