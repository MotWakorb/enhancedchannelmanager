"""Composition and expanded-alias limits, including actual import boundaries."""

import io
import zipfile
from unittest.mock import patch

import pytest
import yaml
from fastapi import HTTPException


def nested(depth):
    return "[" * depth + "0" + "]" * depth


def test_backup_export_parser_rejects_deep_structure_before_restore():
    from routers.backup import _parse_yaml_export

    with pytest.raises(HTTPException) as error:
        _parse_yaml_export(("ecm_export: {}\npayload: " + nested(90)).encode())
    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,module,root",
    [
        ("/api/tags/import", "tags", "tags: {groups: []}"),
        (
            "/api/normalization/import",
            "normalization",
            "normalization_rules: {groups: []}",
        ),
        ("/api/dummy-epg/profiles/import/yaml", "dummy_epg", "profiles: []"),
        ("/api/auto-creation/import/yaml", "channel_pipeline", "rules: []"),
    ],
)
async def test_import_refuses_budget_failure_before_database(
    async_client, path, module, root
):
    with patch(f"routers.{module}.get_session") as session:
        response = await async_client.post(
            path,
            json={"yaml_content": root + "\npayload: " + nested(90), "overwrite": True},
        )
    assert response.status_code == 400
    session.assert_not_called()


def test_dbas_budget_violation_is_not_silently_an_empty_category():
    from dbas.restore_artifact import _decode_categories

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("categories/m3u_accounts.yaml", "payload: " + nested(90))
    with (
        zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive,
        pytest.raises(HTTPException) as error,
    ):
        _decode_categories(archive)
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "section,reader",
    [
        ("core_settings", "_decode_settings_category"),
        ("settings", "_decode_ecm_settings_category"),
    ],
)
def test_dbas_settings_budget_violation_stops_restore(section, reader):
    from dbas import restore_artifact

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"categories/{section}.yaml", "payload: " + nested(90))
    with (
        zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive,
        pytest.raises(HTTPException) as error,
    ):
        getattr(restore_artifact, reader)(archive)
    assert error.value.status_code == 400


@pytest.mark.parametrize("document", ["&a [*a]", "a: &a {again: *a}"])
def test_recursive_aliases_refused(document):
    from yaml_safety import safe_load_bounded

    with pytest.raises(yaml.YAMLError):
        safe_load_bounded(document)


def test_alias_dag_expansion_is_bounded_before_construction():
    from yaml_safety import safe_load_bounded

    document = "a: &a [one, two]\n"
    prior = "a"
    for name in "bcdefghijklmnopqrst":
        document += f"{name}: &{name} [*{prior}, *{prior}]\n"
        prior = name
    with pytest.raises(yaml.YAMLError):
        safe_load_bounded(document)


def test_harmless_aliases_merges_and_large_valid_backups_preserved():
    from yaml_safety import safe_load_bounded

    document = (
        "defaults: &d {enabled: true, name: source}\ncopy: {<<: *d, name: copy}\n"
    )
    assert safe_load_bounded(document) == yaml.safe_load(document)
    large = "streams:\n" + "".join(
        f"- {{id: {i}, name: 'stream {i}'}}\n" for i in range(20_000)
    )
    assert len(safe_load_bounded(large)["streams"]) == 20_000


def test_small_explicit_budgets_have_both_positive_and_negative_controls():
    from yaml_safety import BoundedSafeLoader

    class SmallLoader(BoundedSafeLoader):
        max_nodes = 8
        max_depth = 4

    assert yaml.load("[1, 2, 3]", Loader=SmallLoader) == [1, 2, 3]
    with pytest.raises(yaml.YAMLError):
        yaml.load("[1,2,3,4,5,6,7,8]", Loader=SmallLoader)
    with pytest.raises(yaml.YAMLError):
        yaml.load(nested(5), Loader=SmallLoader)


def test_invalid_scalar_constructor_is_a_yaml_error():
    from yaml_safety import safe_load_bounded

    with pytest.raises(yaml.YAMLError):
        safe_load_bounded("date: 2026-99-99")


@pytest.mark.parametrize("limit", ["depth", "nodes"])
def test_editor_budget_error_retains_actionable_mark(limit, monkeypatch):
    import pipeline_yaml_editor as editor

    if limit == "nodes":
        monkeypatch.setattr(editor._EditorLoader, "max_nodes", 8)
        content = "version: 1\nrules: [1, 2, 3, 4, 5, 6]\n"
    else:
        content = "version: 1\nrules: " + nested(45)
    with pytest.raises(HTTPException) as error:
        editor._parse(content)
    assert error.value.status_code == 422
    assert (
        error.value.detail["message"]
        == "YAML structure exceeds the editor safety limit"
    )
    assert error.value.detail["line"] == 2
    assert error.value.detail["column"] > 1


@pytest.mark.parametrize("merge", [False, True])
def test_expanded_graph_refusal_precedes_constructors_and_merge_flattening(merge):
    from yaml_safety import BoundedSafeLoader, YamlSafetyError

    class SmallLoader(BoundedSafeLoader):
        max_nodes = 200

    document = "a: &a {enabled: true}\n" if merge else "a: &a [one, two]\n"
    prior = "a"
    for name in "bcdefghij":
        value = f"{{<<: [*{prior}, *{prior}]}}" if merge else f"[*{prior}, *{prior}]"
        document += f"{name}: &{name} {value}\n"
        prior = name
    with (
        patch.object(SmallLoader, "construct_object") as construct,
        patch.object(SmallLoader, "flatten_mapping") as flatten,
        pytest.raises(YamlSafetyError),
    ):
        yaml.load(document, Loader=SmallLoader)
    construct.assert_not_called()
    flatten.assert_not_called()


def test_backward_alias_chain_cannot_hide_expanded_depth():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    document = "a0: &a0 [0]\n" + "".join(
        f"a{i}: &a{i} [*a{i - 1}]\n" for i in range(1, 100)
    )
    with pytest.raises(YamlSafetyError):
        safe_load_bounded(document)
