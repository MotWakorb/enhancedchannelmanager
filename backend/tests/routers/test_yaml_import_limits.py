"""Size and structure limits at the YAML import, validate and bundle boundaries."""

import io
import json
import tarfile
from unittest.mock import patch

import pytest
import yaml

from yaml_safety import IMPORT_MAX_CHARS, IMPORT_MAX_NODES

IMPORT_ROUTES = [
    ("/api/tags/import", "tags", "tags: {groups: []}"),
    ("/api/normalization/import", "normalization", "normalization_rules: {groups: []}"),
    ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles: []"),
    ("/api/auto-creation/import/yaml", "channel_pipeline", "rules: []"),
]


def flat(count):
    """A one-level sequence that composes to ``count + 1`` nodes."""
    return "[" + ",".join(["0"] * count) + "]"


def legacy_export(**extra):
    document = {"ecm_export": {"version": "0.18.2", "sections_included": []}}
    document.update(extra)
    return yaml.safe_dump(document).encode()


def bundle(members):
    """Build a tar.gz from ``{name: bytes}``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- general import routes -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path,module,root", IMPORT_ROUTES)
async def test_import_node_ceiling_refused_before_database(
    async_client, path, module, root
):
    content = root + "\npayload: " + flat(IMPORT_MAX_NODES)
    with patch(f"routers.{module}.get_session") as session:
        response = await async_client.post(
            path, json={"yaml_content": content, "overwrite": True}
        )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "exceeds the import size limit" in detail
    assert "100,000 YAML nodes" in detail
    assert "Invalid YAML" not in detail
    session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,module,root", IMPORT_ROUTES)
async def test_import_just_under_node_ceiling_is_not_size_refused(
    async_client, path, module, root
):
    content = root + "\npayload: " + flat(IMPORT_MAX_NODES - 1_000)
    response = await async_client.post(
        path, json={"yaml_content": content, "overwrite": False}
    )
    assert "exceeds the import size limit" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path,module,root", IMPORT_ROUTES)
async def test_import_character_cap_refused_before_parse(
    async_client, path, module, root
):
    content = root + "\n#" + "x" * IMPORT_MAX_CHARS
    with (
        patch(f"routers.{module}.safe_load_bounded") as parse,
        patch(f"routers.{module}.get_session") as session,
    ):
        response = await async_client.post(
            path, json={"yaml_content": content, "overwrite": True}
        )
    assert response.status_code == 422
    parse.assert_not_called()
    session.assert_not_called()


@pytest.mark.asyncio
async def test_import_at_character_cap_is_accepted(async_client):
    root = "tags: {groups: []}\n#"
    content = root + "x" * (IMPORT_MAX_CHARS - len(root))
    assert len(content) == IMPORT_MAX_CHARS
    response = await async_client.post(
        "/api/tags/import", json={"yaml_content": content, "overwrite": False}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_real_size_tag_config_still_imports(async_client, test_session):
    from models import Tag, TagGroup

    groups = [
        {
            "name": "Group %d" % g,
            "description": "Imported group %d" % g,
            "tags": [
                {"value": "TAG%d_%d" % (g, t), "case_sensitive": False, "enabled": True}
                for t in range(100)
            ],
        }
        for g in range(100)
    ]
    content = yaml.safe_dump({"tags": {"version": 1, "groups": groups}})
    response = await async_client.post(
        "/api/tags/import", json={"yaml_content": content, "overwrite": False}
    )
    assert response.status_code == 200
    assert test_session.query(TagGroup).filter(TagGroup.is_builtin == False).count() == 100  # noqa: E712 - SQLAlchemy column comparison
    assert test_session.query(Tag).filter(Tag.is_builtin == False).count() == 10_000  # noqa: E712 - SQLAlchemy column comparison


@pytest.mark.asyncio
@pytest.mark.parametrize("document", ["42", "my rules here", "null"])
async def test_rule_import_scalar_document_is_a_clean_400(async_client, document):
    response = await async_client.post(
        "/api/auto-creation/import/yaml",
        json={"yaml_content": document, "overwrite": False},
    )
    assert response.status_code == 400
    assert "rules" in response.json()["detail"]


# --- legacy /validate and /restore-yaml -------------------------------------


@pytest.mark.asyncio
async def test_validate_node_ceiling_refused(async_client):
    content = legacy_export(payload=list(range(60)))
    with patch("routers.backup.LEGACY_MAX_NODES", 50):
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "exceeds the backup size limit" in detail
    assert "50 YAML nodes" in detail
    assert "Invalid YAML" not in detail


@pytest.mark.asyncio
async def test_validate_under_node_ceiling_accepted(async_client):
    content = legacy_export(payload=list(range(30)))
    with patch("routers.backup.LEGACY_MAX_NODES", 50):
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_validate_alias_amplification_refused_under_node_ceiling(async_client):
    base = "[" + ",".join(["0"] * 1_000) + "]"
    content = (
        "ecm_export: {version: x}\nbase: &b " + base + "\nrefs: ["
        + ",".join(["*b"] * 1_100) + "]\n"
    ).encode()
    response = await async_client.post(
        "/api/backup/validate",
        files={"file": ("export.yaml", content, "text/yaml")},
    )
    assert response.status_code == 400
    assert "exceeds the backup size limit" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/api/backup/validate", "/api/backup/restore-yaml"])
async def test_legacy_upload_byte_cap_is_413(async_client, route):
    content = legacy_export(padding="x" * 4_096)
    form = {"sections": json.dumps(["tag_groups"])} if route.endswith("yaml") else {}
    with (
        patch("routers.backup._LEGACY_YAML_MAX_UPLOAD_BYTES", 1_024),
        patch("routers.backup._parse_yaml_export") as parse,
    ):
        response = await async_client.post(
            route, data=form, files={"file": ("export.yaml", content, "text/yaml")}
        )
    assert response.status_code == 413
    assert "exceeds the backup size limit" in response.json()["detail"]
    parse.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_upload_at_byte_cap_is_accepted(async_client):
    content = legacy_export()
    with patch("routers.backup._LEGACY_YAML_MAX_UPLOAD_BYTES", len(content)):
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_restore_yaml_node_refusal_restores_nothing(async_client):
    content = legacy_export(payload=list(range(60)))
    with (
        patch("routers.backup.LEGACY_MAX_NODES", 50),
        patch("routers.backup._restore_section") as restore,
    ):
        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": json.dumps(["tag_groups"])},
            files={"file": ("export.yaml", content, "text/yaml")},
        )
    assert response.status_code == 400
    assert "exceeds the backup size limit" in response.json()["detail"]
    restore.assert_not_called()


# --- analyze/from-bundle ----------------------------------------------------

BUNDLE_ROUTE = "/api/auto-creation/rules/analyze/from-bundle"
RULES = b"version: 1\nrules: []\n"


async def post_bundle(async_client, data):
    return await async_client.post(
        BUNDLE_ROUTE, files={"file": ("bundle.tar.gz", data, "application/gzip")}
    )


@pytest.mark.asyncio
async def test_bundle_with_real_layout_is_analyzed(async_client):
    data = bundle(
        {
            "rules.yaml": RULES,
            "channel_groups_diagnostic.json": b"{}",
            "logs.txt": b"log line\n" * 10_000,
            "channels.json": b"[]",
        }
    )
    response = await post_bundle(async_client, data)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bundle_rules_member_cap_is_413(async_client):
    data = bundle({"rules.yaml": RULES + b"#" + b"x" * 2_048})
    with patch("routers.channel_pipeline._BUNDLE_MAX_RULES_BYTES", 1_024):
        response = await post_bundle(async_client, data)
    assert response.status_code == 413
    assert "rules.yaml" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bundle_rules_member_at_cap_is_accepted(async_client):
    with patch("routers.channel_pipeline._BUNDLE_MAX_RULES_BYTES", len(RULES)):
        response = await post_bundle(async_client, bundle({"rules.yaml": RULES}))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bundle_diagnostic_member_cap_is_413(async_client):
    data = bundle(
        {"rules.yaml": RULES, "channel_groups_diagnostic.json": b"{}" + b" " * 2_048}
    )
    with patch("routers.channel_pipeline._BUNDLE_MAX_DIAGNOSTIC_BYTES", 1_024):
        response = await post_bundle(async_client, data)
    assert response.status_code == 413
    assert "channel_groups_diagnostic.json" in response.json()["detail"]


@pytest.mark.asyncio
async def test_gzip_bomb_member_refused_without_inflating_it(async_client):
    # A highly compressible member far larger than the aggregate cap.
    data = bundle({"rules.yaml": RULES, "logs.txt": b"\0" * (8 * 1024 * 1024)})
    assert len(data) < 64 * 1024
    with patch("routers.channel_pipeline._BUNDLE_MAX_TOTAL_BYTES", 1024 * 1024):
        response = await post_bundle(async_client, data)
    assert response.status_code == 413
    assert "uncompressed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bundle_member_count_cap_is_413(async_client):
    members = {"rules.yaml": RULES}
    members.update({"extra-%d.txt" % i: b"x" for i in range(20)})
    with patch("routers.channel_pipeline._BUNDLE_MAX_MEMBERS", 10):
        response = await post_bundle(async_client, bundle(members))
    assert response.status_code == 413
    assert "members" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bundle_upload_byte_cap_is_413(async_client):
    import random

    noise = random.Random(0).randbytes(16 * 1024)
    data = bundle({"rules.yaml": RULES, "noise.bin": noise})
    assert len(data) > 1_024
    with patch("routers.channel_pipeline._BUNDLE_MAX_UPLOAD_BYTES", 1_024):
        response = await post_bundle(async_client, data)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_bundle_rules_node_ceiling_is_import_limit(async_client):
    rules = ("version: 1\nrules: []\npayload: " + flat(IMPORT_MAX_NODES)).encode()
    response = await post_bundle(async_client, bundle({"rules.yaml": rules}))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "rules.yaml exceeds the import size limit" in detail
    assert "not valid YAML" not in detail


# --- legacy export preflight ------------------------------------------------


def test_legacy_export_preflight_refuses_what_restore_would_refuse():
    from fastapi import HTTPException

    from routers.backup import (
        BackupNotRestorableError,
        _parse_yaml_export,
        _verify_restorable_export,
    )

    export = {"ecm_export": {"version": "x"}, "database": {"tag_groups": list(range(80))}}
    dumped = yaml.dump(export, default_flow_style=False, sort_keys=False).encode()
    with patch("routers.backup.LEGACY_MAX_NODES", 50):
        with pytest.raises(BackupNotRestorableError) as error:
            _verify_restorable_export(export, "YAML export")
        with pytest.raises(HTTPException):
            _parse_yaml_export(dumped)
    assert "YAML export exceeds the backup size limit" in str(error.value)
    with patch("routers.backup.LEGACY_MAX_NODES", 500):
        _verify_restorable_export(export, "YAML export")
        assert _parse_yaml_export(dumped) == export


@pytest.mark.asyncio
async def test_export_route_refuses_unrestorable_export(async_client):
    with (
        patch("routers.backup.LEGACY_MAX_NODES", 50),
        patch("routers.backup._gather_db_tables", return_value={"tag_groups": list(range(80))}),
        patch("routers.backup._gather_dispatcharr_sections", return_value={}),
    ):
        response = await async_client.get("/api/backup/export?sections=tag_groups")
    assert response.status_code == 413
    assert "exceeds the backup size limit" in response.json()["detail"]


@pytest.mark.asyncio
async def test_export_route_under_ceiling_still_exports(async_client):
    with (
        patch("routers.backup.LEGACY_MAX_NODES", 500),
        patch("routers.backup._gather_db_tables", return_value={"tag_groups": list(range(80))}),
        patch("routers.backup._gather_dispatcharr_sections", return_value={}),
    ):
        response = await async_client.get("/api/backup/export?sections=tag_groups")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_scheduled_yaml_backup_never_writes_an_unrestorable_export(tmp_path):
    from tasks.yaml_backup import YamlBackupTask

    task = YamlBackupTask()
    task.sections = ["tag_groups"]
    with (
        patch("tasks.yaml_backup.BACKUPS_DIR", tmp_path),
        patch("routers.backup.LEGACY_MAX_NODES", 50),
        patch("routers.backup._gather_db_tables", return_value={"tag_groups": list(range(80))}),
        patch("routers.backup._gather_dispatcharr_sections", return_value={}),
    ):
        result = await task.execute()
    assert result.success is False
    assert "exceeds the backup size limit" in result.message
    assert not list(tmp_path.glob("*.yaml"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,module,key",
    [
        ("/api/tags/import", "tags", "tags"),
        ("/api/normalization/import", "normalization", "normalization_rules"),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles"),
    ],
)
@pytest.mark.parametrize("document", ["42", '"text"', "null", "my tags normalization_rules profiles"])
async def test_import_scalar_document_is_a_clean_400(
    async_client, path, module, key, document
):
    with patch(f"routers.{module}.get_session") as session:
        response = await async_client.post(
            path, json={"yaml_content": document, "overwrite": False}
        )
    assert response.status_code == 400
    assert key in response.json()["detail"]
    session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,module,document,key",
    [
        ("/api/tags/import", "tags", "tags: 42", "groups"),
        ("/api/tags/import", "tags", "tags: some groups", "groups"),
        ("/api/normalization/import", "normalization", "normalization_rules: 42", "groups"),
        ("/api/normalization/import", "normalization", "normalization_rules: groups", "groups"),
        ("/api/tags/import", "tags", "tags: {groups: 42}", "groups"),
        ("/api/normalization/import", "normalization", "normalization_rules: {groups: 42}", "groups"),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles: 42", "profiles"),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles: some text", "profiles"),
    ],
)
async def test_import_scalar_section_is_a_clean_400(
    async_client, path, module, document, key
):
    with patch(f"routers.{module}.get_session") as session:
        response = await async_client.post(
            path, json={"yaml_content": document, "overwrite": False}
        )
    assert response.status_code == 400
    assert key in response.json()["detail"]
    session.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_ceiling_is_separate_from_the_dbas_ceiling(async_client):
    content = legacy_export(payload=list(range(60)))
    with (
        patch("routers.backup.BACKUP_MAX_NODES", 50),
        patch("routers.backup.LEGACY_MAX_NODES", 500),
    ):
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
    assert response.status_code == 200


def test_legacy_export_preflight_walks_the_object_not_the_bytes():
    from routers import backup

    with (
        patch("yaml_safety.BoundedSafeLoader.compose_node") as compose,
        patch("routers.backup.LEGACY_MAX_NODES", 500),
    ):
        backup._verify_restorable_export({"a": list(range(10))}, "YAML export")
    compose.assert_not_called()


# --- list items must be mappings ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,module,document,key",
    [
        ("/api/tags/import", "tags", "tags: {groups: [42]}", "groups"),
        ("/api/tags/import", "tags", "tags: {groups: [{name: a}, text]}", "groups"),
        ("/api/normalization/import", "normalization", "normalization_rules: {groups: [42]}", "groups"),
        ("/api/normalization/import", "normalization", "normalization_rules: {groups: [[1]]}", "groups"),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles: [42]", "profiles"),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "[text]", "profiles"),
        ("/api/auto-creation/import/yaml", "channel_pipeline", "rules: [42]", "rules"),
        ("/api/auto-creation/import/yaml", "channel_pipeline", "rules: 42", "rules"),
        ("/api/auto-creation/import/yaml", "channel_pipeline", "[text]", "rules"),
    ],
)
async def test_import_list_items_must_be_mappings(async_client, path, module, document, key):
    with (
        patch(f"routers.{module}.get_session") as session,
        patch(f"routers.{module}.get_client") if module in ("dummy_epg", "channel_pipeline") else patch(f"routers.{module}.logger") as client,
    ):
        response = await async_client.post(
            path, json={"yaml_content": document, "overwrite": False}
        )
    assert response.status_code == 400
    assert key in response.json()["detail"]
    session.assert_not_called()
    if module in ("dummy_epg", "channel_pipeline"):
        client.assert_not_called()
