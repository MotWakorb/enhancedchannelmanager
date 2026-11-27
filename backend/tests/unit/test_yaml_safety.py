"""Composition and expanded-alias limits, including actual import boundaries."""

import io
import zipfile
from unittest.mock import patch

import pytest
import yaml
from fastapi import HTTPException

from yaml_safety import BACKUP_MAX_NODES, IMPORT_MAX_NODES


def nested(depth):
    return "[" * depth + "0" + "]" * depth


def test_backup_export_parser_rejects_deep_structure_before_restore():
    from routers.backup import _parse_yaml_export

    with pytest.raises(HTTPException) as error:
        _parse_yaml_export(("ecm_export: {}\npayload: " + nested(90)).encode())
    assert error.value.status_code == 400
    assert "exceeds the backup size limit" in error.value.detail
    assert "Invalid YAML" not in error.value.detail


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
    assert "exceeds the import size limit" in response.json()["detail"]
    assert "Invalid YAML" not in response.json()["detail"]
    session.assert_not_called()


def test_dbas_budget_violation_is_not_silently_an_empty_category():
    from dbas.restore_artifact import ArtifactCategoryRefused, _decode_categories

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("categories/m3u_accounts.yaml", "payload: " + nested(90))
    with (
        zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive,
        pytest.raises(ArtifactCategoryRefused) as error,
    ):
        _decode_categories(archive)
    assert "m3u_accounts" in str(error.value)
    assert "exceeds the backup size limit" in str(error.value)


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
        pytest.raises(restore_artifact.ArtifactCategoryRefused) as error,
    ):
        getattr(restore_artifact, reader)(archive)
    assert section in str(error.value)
    assert "exceeds the backup size limit" in str(error.value)


@pytest.mark.parametrize("document", ["&a [*a]", "a: &a {again: *a}"])
def test_recursive_aliases_refused(document):
    from yaml_safety import safe_load_bounded

    with pytest.raises(yaml.YAMLError):
        safe_load_bounded(document, max_nodes=BACKUP_MAX_NODES)


def test_alias_dag_expansion_is_bounded_before_construction():
    from yaml_safety import safe_load_bounded

    document = "a: &a [one, two]\n"
    prior = "a"
    for name in "bcdefghijklmnopqrst":
        document += f"{name}: &{name} [*{prior}, *{prior}]\n"
        prior = name
    with pytest.raises(yaml.YAMLError):
        safe_load_bounded(document, max_nodes=BACKUP_MAX_NODES)


def test_harmless_aliases_merges_and_large_valid_backups_preserved():
    from yaml_safety import safe_load_bounded

    document = (
        "defaults: &d {enabled: true, name: source}\ncopy: {<<: *d, name: copy}\n"
    )
    assert safe_load_bounded(document, max_nodes=IMPORT_MAX_NODES) == yaml.safe_load(
        document
    )
    large = "streams:\n" + "".join(
        f"- {{id: {i}, name: 'stream {i}'}}\n" for i in range(20_000)
    )
    assert len(safe_load_bounded(large, max_nodes=BACKUP_MAX_NODES)["streams"]) == 20_000


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
        safe_load_bounded("date: 2026-99-99", max_nodes=IMPORT_MAX_NODES)


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
        alias_expansion_budget = 200

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
        safe_load_bounded(document, max_nodes=BACKUP_MAX_NODES)


def test_budget_constants_match_the_documented_contract():
    import yaml_safety

    assert yaml_safety.ALIAS_EXPANSION_BUDGET == 1_000_000
    assert yaml_safety.MAX_DEPTH == 64
    assert yaml_safety.BACKUP_MAX_NODES == 10_000_000
    assert yaml_safety.IMPORT_MAX_NODES == 100_000


def test_every_caller_chooses_its_composed_ceiling():
    from yaml_safety import safe_load_bounded

    with pytest.raises(TypeError):
        safe_load_bounded("a: 1")


def test_composed_ceiling_is_per_caller():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    # 1 sequence node + 150,000 scalars: over the import ceiling, well inside
    # the backup ceiling.
    document = "[" + ",".join(["0"] * 150_000) + "]"
    with pytest.raises(YamlSafetyError) as error:
        safe_load_bounded(document, max_nodes=IMPORT_MAX_NODES)
    assert error.value.reason == "nodes"
    assert error.value.limit == IMPORT_MAX_NODES
    assert len(safe_load_bounded(document, max_nodes=BACKUP_MAX_NODES)) == 150_000


def _fanout(width, references):
    """One anchored list of ``width`` scalars referenced ``references`` times."""
    return (
        "base: &b [" + ",".join(["0"] * width) + "]\n"
        "refs: [" + ",".join(["*b"] * references) + "]\n"
    )


def test_alias_amplification_budget_is_the_same_for_every_ceiling():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    # Composed: about 2,200 nodes. Expanded: about 1.1 million.
    document = _fanout(1_000, 1_100)
    for ceiling in (IMPORT_MAX_NODES, BACKUP_MAX_NODES, 10**12):
        with pytest.raises(YamlSafetyError) as error:
            safe_load_bounded(document, max_nodes=ceiling)
        assert error.value.reason == "aliases"


def test_alias_amplification_under_the_budget_still_loads():
    from yaml_safety import safe_load_bounded

    # Expanded minus composed is about 900,000: inside the budget.
    data = safe_load_bounded(_fanout(1_000, 900), max_nodes=IMPORT_MAX_NODES)
    assert len(data["refs"]) == 900
    assert data["refs"][0] is data["base"]


def test_depth_refusal_reports_its_reason():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    with pytest.raises(YamlSafetyError) as error:
        safe_load_bounded(nested(65), max_nodes=BACKUP_MAX_NODES)
    assert error.value.reason == "depth"
    assert safe_load_bounded(nested(63), max_nodes=BACKUP_MAX_NODES) is not None


def test_recursive_alias_refusal_reports_its_reason():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    with pytest.raises(YamlSafetyError) as error:
        safe_load_bounded("&a [*a]", max_nodes=BACKUP_MAX_NODES)
    assert error.value.reason == "recursive"


@pytest.mark.parametrize(
    "document,ceiling,reason",
    [
        ("[" + ",".join(["0"] * 20) + "]", 10, "nodes"),
        (nested(70), 1_000, "depth"),
        ("&a [*a]", 10, "recursive"),
        (_fanout(1_000, 1_100), BACKUP_MAX_NODES, "aliases"),
    ],
)
def test_measure_applies_the_same_refusals_without_constructing(
    document, ceiling, reason
):
    from yaml_safety import BoundedSafeLoader, YamlSafetyError, measure_bounded

    with (
        patch.object(BoundedSafeLoader, "construct_object") as construct,
        pytest.raises(YamlSafetyError) as error,
    ):
        measure_bounded(document, max_nodes=ceiling)
    assert error.value.reason == reason
    construct.assert_not_called()


def test_measure_returns_the_composed_node_count():
    from yaml_safety import measure_bounded

    # mapping + 2 keys + 2 values, one value a 3-item sequence.
    assert measure_bounded("a: 1\nb: [1, 2, 3]\n", max_nodes=100) == 8
    assert measure_bounded(_fanout(1_000, 900), max_nodes=IMPORT_MAX_NODES) == (
        1 + 2 + 1 + 1_000 + 1 + 900
    )


def test_refusal_description_names_the_limit():
    from yaml_safety import YamlSafetyError, safe_load_bounded

    with pytest.raises(YamlSafetyError) as error:
        safe_load_bounded("[1, 2, 3, 4]", max_nodes=3)
    assert error.value.describe() == "more than 3 YAML nodes"
