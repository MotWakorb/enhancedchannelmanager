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


def _readme_published_image_recipe() -> dict:
    """Parse the README's copy-paste compose recipe for the published images.

    This recipe — not ``docker-compose.mcp.yml`` — is what most operators use;
    the overlay is documented as "if you're building from source". It was left
    mounting ``./config:/config:ro`` into the sidecar for a full review cycle
    after the overlay stopped doing so, which meant the published-image path
    still had the exposure the change exists to remove, and (because the
    sidecar image now defaults ``MCP_SECRETS_DIR``) no working projection
    either. Untested prose drifts; this pins it.
    """
    readme = (ROOT / "README.md").read_text()
    heading = "### With MCP Server (Claude AI Integration)"
    assert heading in readme, "README MCP quickstart heading moved — update test"
    section = readme[readme.index(heading) :]
    opening = section.index("```yaml") + len("```yaml")
    closing = section.index("```", opening)
    return yaml.safe_load(section[opening:closing])


def test_readme_published_image_recipe_projects_credentials_only() -> None:
    recipe = _readme_published_image_recipe()
    sidecar = recipe["services"]["ecm-mcp"]
    backend = recipe["services"]["ecm"]

    # The sidecar sees the projection and nothing else.
    assert sidecar["volumes"] == [f"{PROJECTION_VOLUME}:{PROJECTION_DIR}:ro"]
    assert all("/config" not in volume for volume in sidecar["volumes"])
    assert f"{PROJECTION_VOLUME}:{PROJECTION_DIR}" in backend["volumes"]
    assert PROJECTION_VOLUME in recipe["volumes"]

    # Both sides must agree on where the projection lives, or the sidecar
    # image's own ENV default wins and it reads an empty directory forever.
    for service in ("ecm", "ecm-mcp"):
        environment = recipe["services"][service]["environment"]
        setting = next(
            item for item in environment if item.startswith("MCP_SECRETS_DIR=")
        )
        assert setting.removeprefix("MCP_SECRETS_DIR=") == PROJECTION_DIR


def test_readme_published_image_recipe_matches_the_overlays_hardening() -> None:
    """The published-image path must not be weaker than the source overlay."""
    sidecar = _readme_published_image_recipe()["services"]["ecm-mcp"]
    overlay = _compose()["services"]["ecm-mcp"]

    for key in ("read_only", "cap_drop", "security_opt", "tmpfs", "pids_limit",
                "mem_limit", "cpus"):
        assert sidecar[key] == overlay[key], f"README recipe drifted on {key}"

    # The overlay expresses identity as ${PUID}/${PGID}; the README recipe
    # hardcodes the same defaults it sets on the ecm service. Either way the
    # two services must run as the same account or the owner-only projection
    # is unreadable.
    assert sidecar["user"] == "1000:1000"
