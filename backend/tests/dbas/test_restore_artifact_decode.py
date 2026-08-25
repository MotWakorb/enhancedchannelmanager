"""Tests for the DBAS artifact decoder (bead enhancedchannelmanager-o8tbv).

Covers ``dbas.restore_artifact.decode_artifact_to_plan`` — the glue that turns a
validated new-format DBAS artifact ZIP into the orchestrator's
:class:`~dbas.preflight.ImportPlan`:

  * per-category YAML (``categories/<section>.yaml``) -> PlanCategory entities,
  * the binary logo subtree (``binary/logos/<rel>``) -> metadata-only .15 logo
    records (``name`` / ``filename`` / ``archive_member`` / ``size``),
  * the cleartext manifest carried through for pre-flight's version gate,
  * zip-slip / path-traversal member-name safety.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import yaml

from dbas.restore_artifact import (
    _is_safe_member,
    decode_artifact_to_plan,
)
from dbas.restore_contracts import EntityType


def _category_yaml(section_key: str, rows: list[dict], container: str = "dispatcharr") -> bytes:
    """Build a category YAML the way build_yaml_export does (section under a container)."""
    data = {
        "ecm_export": {"version": "0.17.6-test", "sections_included": [section_key]},
        container: {section_key: rows},
    }
    return yaml.dump(data, sort_keys=False).encode("utf-8")


# 1x1 PNG so the logos importer's later magic-byte check would pass.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _build_artifact(
    *,
    m3u=None,
    epg=None,
    groups=None,
    user_agents=None,
    dvr_rules=None,
    core_settings=None,
    comskip=None,
    logos=None,
    logo_metadata=None,
    schema_version=1,
    extra_members=None,
) -> bytes:
    """Build a new-format artifact ZIP with chosen categories + logo files."""
    members: dict[str, bytes] = {}
    if m3u is not None:
        members["categories/m3u_accounts.yaml"] = _category_yaml("m3u_accounts", m3u)
    if epg is not None:
        members["categories/epg_sources.yaml"] = _category_yaml("epg_sources", epg)
    if groups is not None:
        members["categories/channel_groups.yaml"] = _category_yaml("channel_groups", groups)
    if user_agents is not None:
        members["categories/user_agents.yaml"] = _category_yaml("user_agents", user_agents)
    if dvr_rules is not None:
        members["categories/dvr_rules.yaml"] = _category_yaml("dvr_rules", dvr_rules)
    # The two SETTINGS-blob sections are key/value MAPPINGS, not entity lists —
    # _category_yaml serializes whatever it is given under the container.
    if core_settings is not None:
        members["categories/core_settings.yaml"] = _category_yaml("core_settings", core_settings)
    if comskip is not None:
        members["categories/comskip.yaml"] = _category_yaml("comskip", comskip)
    for filename, blob in (logos or {}).items():
        members["binary/logos/%s" % filename] = blob
    if logo_metadata is not None:
        members["binary/metadata.json"] = json.dumps(logo_metadata).encode("utf-8")
    if extra_members:
        members.update(extra_members)

    import hashlib

    file_hashes = {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}
    manifest = {
        "schema_version": schema_version,
        "app_version": "0.17.6-test",
        "files": [{"path": p, "sha256": h} for p, h in sorted(file_hashes.items())],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, blob in members.items():
            zf.writestr(path, blob)
    buf.seek(0)
    return buf.getvalue()


def _open(zip_bytes: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(zip_bytes), "r")


class TestCategoryDecode:
    def test_decodes_m3u_accounts(self):
        art = _build_artifact(m3u=[{"id": 1, "name": "Provider A"}, {"id": 2, "name": "Provider B"}])
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        cat = plan.category(EntityType.M3U_ACCOUNT)
        assert cat is not None
        assert [e["name"] for e in cat.entities] == ["Provider A", "Provider B"]

    def test_decodes_multiple_categories(self):
        art = _build_artifact(
            m3u=[{"id": 1, "name": "P"}],
            epg=[{"id": 5, "name": "XMLTV"}],
            groups=[{"id": 10, "name": "Sports"}],
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        assert len(plan.category(EntityType.M3U_ACCOUNT).entities) == 1
        assert len(plan.category(EntityType.EPG_SOURCE).entities) == 1
        assert plan.category(EntityType.CHANNEL_GROUP).entities[0]["name"] == "Sports"

    def test_missing_category_is_empty_not_crash(self):
        art = _build_artifact(m3u=[{"id": 1, "name": "P"}])
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        # EPG was not in the artifact — present in the plan but empty.
        epg = plan.category(EntityType.EPG_SOURCE)
        assert epg is not None and epg.entities == []

    def test_manifest_carried_through(self):
        art = _build_artifact(m3u=[{"id": 1, "name": "P"}], schema_version=1)
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        assert plan.manifest.get("schema_version") == 1

    def test_database_container_section(self):
        # A section emitted under `database` (ECM DB) is also extracted.
        members = {
            "categories/channel_groups.yaml": _category_yaml(
                "channel_groups", [{"id": 7, "name": "DB-sourced"}], container="database"
            )
        }
        art = _build_artifact(extra_members=members)
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        assert plan.category(EntityType.CHANNEL_GROUP).entities[0]["name"] == "DB-sourced"


class TestSettingsAgentsDecode:
    """lc6zu — user_agents / dvr_rules decode as entity categories; the
    core_settings / comskip blobs decode into the single EntityType.SETTINGS
    category as self-describing {"section", "values"} records (the orchestrator
    settings-step contract — restore_orchestrator._settings)."""

    def test_decodes_user_agents_and_dvr_rules(self):
        art = _build_artifact(
            user_agents=[{"id": 31, "name": "ECM UA", "user_agent": "ECM/1.0"}],
            dvr_rules=[{"id": 41, "name": "Record CNN", "channel": 5}],
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        ua = plan.category(EntityType.USER_AGENT)
        assert ua is not None
        assert [e["name"] for e in ua.entities] == ["ECM UA"]
        dvr = plan.category(EntityType.DVR_RULE)
        assert dvr is not None
        assert dvr.entities[0]["channel"] == 5

    def test_legacy_dvr_rules_warning_stub_decodes_to_an_empty_category(self):
        """Artifacts taken BEFORE lsa0s restore without crashing.

        Every backup written while ``_DVR_RULES_PATH`` pointed at the dead
        ``/api/dvr/rules/`` route carries a ``categories/dvr_rules.yaml`` whose
        ``dispatcharr`` block is the gather failure stub — a MAPPING with a
        ``_warning`` key and no ``dvr_rules`` list at all. Those artifacts stay
        readable: the category decodes to zero entities (so the operator sees
        "0" rather than a gap or a traceback), and the rest of the artifact
        decodes normally.
        """
        legacy_stub = yaml.dump(
            {
                "ecm_export": {"version": "0.18.1-0015", "sections_included": ["dvr_rules"]},
                "dispatcharr": {
                    "_warning": (
                        "Dispatcharr not connected — Expecting value: line 1 column 1 (char 0)"
                    )
                },
            },
            sort_keys=False,
        ).encode("utf-8")
        art = _build_artifact(
            m3u=[{"id": 1, "name": "P"}],
            extra_members={"categories/dvr_rules.yaml": legacy_stub},
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)

        dvr = plan.category(EntityType.DVR_RULE)
        assert dvr is not None
        assert dvr.entities == []
        # The neighbouring categories are unaffected by the stub.
        assert plan.category(EntityType.M3U_ACCOUNT).entities[0]["name"] == "P"

    def test_decodes_settings_blobs_into_section_records(self):
        art = _build_artifact(
            core_settings={"default_user_agent": "ECM/1.0"},
            comskip={"comskip_ini": "[main]"},
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        settings = plan.category(EntityType.SETTINGS)
        assert settings is not None
        assert settings.entities == [
            {"section": "core_settings", "values": {"default_user_agent": "ECM/1.0"}},
            {"section": "comskip", "values": {"comskip_ini": "[main]"}},
        ]

    def test_missing_settings_sections_yield_empty_settings_category(self):
        art = _build_artifact(m3u=[{"id": 1, "name": "P"}])
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        settings = plan.category(EntityType.SETTINGS)
        assert settings is not None and settings.entities == []

    def test_non_mapping_settings_blob_is_ignored(self):
        # A malformed (list-shaped) core_settings blob decodes to nothing rather
        # than producing a record the importer would choke on.
        art = _build_artifact(core_settings=[{"key": "x", "value": "y"}])
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        assert plan.category(EntityType.SETTINGS).entities == []


class TestLogoDecode:
    def test_decodes_logo_records_without_materializing_payloads(self):
        art = _build_artifact(
            logos={
                "espn.png": _PNG_BYTES,
                "cnn.png": _PNG_BYTES,
                "bbc.png": _PNG_BYTES,
            }
        )
        with _open(art) as zf:
            read_members = []
            original_read = zf.read

            def tracked_read(member, *args, **kwargs):
                name = member.filename if isinstance(member, zipfile.ZipInfo) else member
                read_members.append(name)
                return original_read(member, *args, **kwargs)

            zf.read = tracked_read
            plan = decode_artifact_to_plan(zf)
        logos = plan.category(EntityType.LOGO).entities
        assert len(logos) == 3
        rec = logos[0]
        assert rec["filename"] == "espn.png"
        assert rec["name"] == "espn"
        assert rec["size"] == len(_PNG_BYTES)
        assert rec["archive_member"] == "binary/logos/espn.png"
        assert all("content_b64" not in logo for logo in logos)
        assert not any(name.startswith("binary/logos/") for name in read_members)

    def test_nested_logo_basename(self):
        art = _build_artifact(logos={"sub/dir/cnn.png": _PNG_BYTES})
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        logos = plan.category(EntityType.LOGO).entities
        # The decoder records the basename, not the nested path.
        assert logos[0]["filename"] == "cnn.png"

    def test_logo_record_carries_source_id_and_name_from_metadata(self):
        """PR #743 review item 1 (cm9bi): binary/metadata.json carries the SOURCE
        Dispatcharr logo id + display name per file (producer-joined); the decoder
        must attach them to the logo record so the importer's affected-channel
        lookup (keyed on the integer source id) works on genuine artifacts."""
        art = _build_artifact(
            logos={"espn.png": _PNG_BYTES},
            logo_metadata={
                "logo_count": 1,
                "logos": [
                    {"filename": "espn.png", "size_bytes": len(_PNG_BYTES),
                     "id": 21, "name": "ESPN"},
                ],
            },
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        rec = plan.category(EntityType.LOGO).entities[0]
        assert rec["id"] == 21
        assert rec["name"] == "ESPN"  # display name preferred over basename stem
        assert rec["filename"] == "espn.png"

    def test_logo_record_without_metadata_entry_has_no_id(self):
        # A file the producer could not correlate decodes without a fabricated
        # id — the importer then simply reports no affected channels for it.
        art = _build_artifact(
            logos={"espn.png": _PNG_BYTES},
            logo_metadata={
                "logo_count": 1,
                "logos": [{"filename": "espn.png", "size_bytes": len(_PNG_BYTES)}],
            },
        )
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        rec = plan.category(EntityType.LOGO).entities[0]
        assert "id" not in rec
        assert rec["name"] == "espn"  # basename-stem fallback unchanged

    def test_no_logos_is_empty_category(self):
        art = _build_artifact(m3u=[{"id": 1, "name": "P"}])
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        assert plan.category(EntityType.LOGO).entities == []


class TestZipSlipSafety:
    def test_is_safe_member_rejects_traversal(self):
        assert not _is_safe_member("../etc/passwd")
        assert not _is_safe_member("/abs/path")
        assert not _is_safe_member("binary/logos/../../x")
        assert not _is_safe_member("binary\\logos\\x.png")
        assert _is_safe_member("binary/logos/ok.png")
        assert _is_safe_member("categories/m3u_accounts.yaml")

    def test_malicious_logo_member_skipped(self):
        # A crafted entry escaping the logos subtree must not become a record.
        members = {"binary/logos/../../evil.png": _PNG_BYTES}
        art = _build_artifact(logos={"good.png": _PNG_BYTES}, extra_members=members)
        with _open(art) as zf:
            plan = decode_artifact_to_plan(zf)
        names = {r["filename"] for r in plan.category(EntityType.LOGO).entities}
        assert "good.png" in names
        assert "evil.png" not in names
