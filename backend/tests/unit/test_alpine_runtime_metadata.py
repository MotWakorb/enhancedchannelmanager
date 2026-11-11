"""Alpine metadata checks retain identity behavior and exact owned library bytes."""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def metadata():
    spec = importlib.util.spec_from_file_location(
        "alpine_runtime_metadata",
        ROOT / "tests/native_image/alpine_runtime_metadata.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_identity_checks_require_equal_named_and_numeric_records(metadata, monkeypatch):
    user = ["appuser", "x", "1000", "1000", "", "/home/appuser", "/bin/bash"]
    group = ["appuser", "x", "1000", ""]

    def command(*args):
        if args == ("getent", "passwd", "1000"):
            return ":".join(user) + "\n"
        if args == ("getent", "group", "1000"):
            return ":".join(group) + "\n"
        return "/usr/bin/getent is owned by musl-utils-1.2.6-r3\n"

    monkeypatch.setattr(
        metadata, "missing_identity", lambda: {"exit": 2, "stdout": "", "stderr": ""}
    )
    result = metadata.getent_metadata(command, user, group)
    assert result["getent_backend"] == "musl-utils"
    with pytest.raises(AssertionError, match="numeric"):
        metadata.getent_metadata(lambda *args: "different identity\n", user, group)


def test_missing_identity_is_a_real_failure_contract_not_a_version_banner(
    metadata, monkeypatch
):
    user = ["appuser", "x", "1000", "1000", "", "/home/appuser", "/bin/bash"]
    group = ["appuser", "x", "1000", ""]

    def command(*args):
        if args[0] == "getent":
            return ":".join(user if args[1] == "passwd" else group) + "\n"
        return "/usr/bin/getent is owned by musl-utils-1.2.6-r3\n"

    monkeypatch.setattr(
        metadata,
        "missing_identity",
        lambda: {"exit": 0, "stdout": "unexpected record", "stderr": ""},
    )
    with pytest.raises(AssertionError, match="missing identity"):
        metadata.getent_metadata(command, user, group)


def test_owned_files_cannot_substitute_same_version_with_changed_bytes(
    metadata, tmp_path
):
    path = tmp_path / "usr/lib/libxml2.so.2.13.9"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original bytes")
    path.chmod(0o755)
    row = {
        "path": "usr/lib/libxml2.so.2.13.9",
        "type": "file",
        "size": len(path.read_bytes()),
        "mode": 0o755,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    metadata.verify_owned(tmp_path, [row])
    path.write_bytes(b"changed bytes")
    with pytest.raises(AssertionError, match="owned file"):
        metadata.verify_owned(tmp_path, [row])


def test_recorded_actual_getent_contract_drives_the_alpine_adapter(
    metadata, monkeypatch
):
    fixture = json.loads(
        (ROOT / "backend/tests/fixtures/alpine/getent-contract.json").read_text()
    )
    cases = {tuple(row["args"]): row for row in fixture["cases"]}
    user = cases["passwd", "appuser"]["stdout"].strip().split(":")
    group = cases["group", "appuser"]["stdout"].strip().split(":")

    def command(*args):
        if args[0] == "getent":
            return cases[tuple(args[1:])]["stdout"]
        return fixture["owner"]

    missing = cases["passwd", "ecm-missing-user-for-port-check"]
    monkeypatch.setattr(
        metadata,
        "missing_identity",
        lambda: {key: missing[key] for key in ["exit", "stdout", "stderr"]},
    )
    assert (
        metadata.getent_metadata(command, user, group)["getent_backend"] == "musl-utils"
    )
    assert cases["--version",]["exit"] == 1


def test_all_shared_behavioral_checks_and_source_gate_remain_unchanged():
    recorded = json.loads(
        (ROOT / "backend/tests/fixtures/alpine/runtime-function-ast.json").read_text()
    )["functions"]
    tree = ast.parse((ROOT / "tests/native_image/runtime_checks.py").read_text())
    functions = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert set(functions) == set(recorded) | {"alpine_metadata_adapter"}
    platform_test = ast.dump(
        ast.parse("if Path('/etc/alpine-release').is_file(): pass").body[0].test,
        include_attributes=False,
    )
    for name, node in functions.items():
        if name == "alpine_metadata_adapter":
            continue
        if name in {"getent_checks", "security_runtime_packages"}:
            node.body = [
                part
                for part in node.body
                if not isinstance(part, ast.If)
                or ast.dump(part.test, include_attributes=False) != platform_test
            ]
        assert (
            hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest()
            == recorded[name]
        ), name


def test_shared_runtime_loads_only_the_pinned_read_only_platform_adapter():
    path = ROOT / "tests/native_image/runtime_checks.py"
    spec = importlib.util.spec_from_file_location("shared_runtime_checks", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.alpine_metadata_adapter()
    assert (
        adapter.SECURITY_RECIPE_SHA256
        == "4940090f0dd959262a986933a43856279f5f33bda1c37980371939cf183f7bfa"
    )


@pytest.mark.parametrize("check", ["getent_checks", "security_runtime_packages"])
@pytest.mark.parametrize("reject", [False, True])
def test_shared_runtime_selects_alpine_metadata_explicitly_and_preserves_failures(
    monkeypatch, check, reject
):
    path = ROOT / "tests/native_image/runtime_checks.py"
    spec = importlib.util.spec_from_file_location("alpine_runtime_dispatch", path)
    assert spec and spec.loader
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True if str(path) == "/etc/alpine-release" else real_is_file(path),
    )
    outputs = {
        (
            "getent",
            "passwd",
            "appuser",
        ): "appuser:x:1000:1000::/home/appuser:/bin/bash\n",
        ("getent", "group", "appuser"): "appuser:x:1000:\n",
    }

    def command(*args):
        return outputs[args]

    monkeypatch.setattr(runtime, "command", command)
    calls = []

    def adapted(*args):
        calls.append(args)
        assert args[0] is command
        if check == "getent_checks":
            assert args[1:] == (
                ["appuser", "x", "1000", "1000", "", "/home/appuser", "/bin/bash"],
                ["appuser", "x", "1000", ""],
            )
        else:
            assert len(args) == 1
        if reject:
            raise AssertionError("actual Alpine metadata mismatch")
        return {"selected": "alpine-exact-metadata"}

    monkeypatch.setattr(
        runtime,
        "alpine_metadata_adapter",
        lambda: SimpleNamespace(
            getent_metadata=adapted, security_runtime_packages=adapted
        ),
    )
    if reject:
        with pytest.raises(AssertionError, match="actual Alpine metadata mismatch"):
            getattr(runtime, check)()
    else:
        assert getattr(runtime, check)() == {"selected": "alpine-exact-metadata"}
    assert len(calls) == 1
