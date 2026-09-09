"""Executable, offline controls for the local gate entrypoints (rdnia).

Run without application conftest: these tests never need ECM or a database.
"""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def gate_tree(tmp_path):
    root = tmp_path / "checkout with spaces"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    for directory in ("backend/tests", "frontend/src", "frontend/node_modules", "e2e", "bin", "modules"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for name in ("backend/tests/test_ok.py", "frontend/src/ok.test.ts", "e2e/ok.spec.ts",
                 "frontend/vitest.config.ts", "playwright.config.ts"):
        (root / name).touch()
    (root / "backend/main.py").write_text("pass\n")
    (root / "backend/requirements.txt").write_text("demo==1.0\n")
    metadata = root / "modules/demo-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: demo\nVersion: 1.0\n")
    (root / "modules/pytest.py").write_text(
        "import json, os, signal, sys, time\n"
        "if os.environ.get('MISSING_PYTEST'): raise ImportError('missing pytest diagnostic')\n"
        "if __name__ == '__main__':\n"
        " with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([os.getcwd(), sys.executable, sys.argv[1:]]) + '\\n')\n"
        " print('pytest diagnostic', file=sys.stderr, flush=True)\n"
        " if os.environ.get('BLOCK'): \n"
        "  print('BLOCKING', flush=True)\n"
        "  time.sleep(30)\n"
        " sys.exit(int(os.environ.get('PYTEST_EXIT', '0')))\n"
    )
    npm = root / "bin/npm"
    npm.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([os.getcwd(), 'npm', sys.argv[1:]]) + '\\n')\n"
        "print('npm diagnostic ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
        "sys.exit(7 if os.environ.get('FAIL_NPM') in sys.argv[1:] else 0)\n"
    )
    npm.chmod(0o755)
    if os.name != "nt":
        for tool in ("bash", "dirname", "readlink", "find", "cat"):
            (root / "bin" / tool).symlink_to(shutil.which(tool))
        (root / "bin/python").symlink_to(sys.executable)
    else:
        (root / "bin/npm.cmd").write_text(
            f'@echo off\n"{sys.executable}" "%~dp0npm" %*\nexit /b %ERRORLEVEL%\n'
        )
    pytest_command = root / "bin/pytest"
    pytest_command.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_module('pytest', run_name='__main__')\n")
    pytest_command.chmod(0o755)
    env = {**os.environ, "ECM_PYTHON": sys.executable, "PYTHONPATH": str(root / "modules"),
           "PATH": str(root / "bin"),
           "TMPDIR": str(tmp_path), "CALLS": str(root / "calls.jsonl")}
    for key in ("SKIP_E2E", "BLOCK", "MISSING_PYTEST", "PYTEST_EXIT", "FAIL_NPM", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    return root, env


def invoke(gate_tree, script="quality-gates.sh", args=(), **changes):
    root, env = gate_tree
    command = ["bash", str(root / "scripts" / script), *args]
    if os.name == "nt":
        command = ([os.environ["COMSPEC"], "/d", "/c", str(root / "scripts/quality-gates.bat"), *args]
                   if script == "quality-gates.sh" else
                   [sys.executable, str(root / "scripts/gate_runner.py"), *args])
    return subprocess.run(command,
                          cwd=root.parent, env={**env, **changes}, text=True,
                          capture_output=True, timeout=10)


def calls(gate_tree):
    path = gate_tree[0] / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def receipt(result):
    starts = [json.loads(line.split(" ", 1)[1]) for line in result.stdout.splitlines()
              if line.startswith("ECM_GATE_START ")]
    assert len(starts) == 1, result.stdout + result.stderr
    data = json.loads(Path(starts[0]["receipt"]).read_text())
    assert result.stdout.splitlines()[-1] == "ECM_GATE_COMPLETE " + json.dumps(data, sort_keys=True)
    assert data["run_id"] == starts[0]["run_id"]
    assert data["exit_code"] == result.returncode
    return data


def test_wrapper_uses_canonical_selection_once_and_explicit_typecheck(gate_tree):
    result = invoke(gate_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls(gate_tree)
    backend = [call for call in recorded if call[1] != "npm"]
    assert len(backend) == 1
    assert backend[0][2] == ["--ignore=tests/e2e", "--ignore=tests/performance",
                              "-m", "not slow", "--tb=short", "--no-header", "-p", "no:warnings"]
    assert Path(backend[0][0]) == gate_tree[0] / "backend"
    assert backend[0][1] == sys.executable
    assert [call[2] for call in recorded if call[1] == "npm"] == [
        ["run", "typecheck"], ["run", "build"], ["run", "test"], ["run", "test:e2e"]]
    assert receipt(result)["scope"] == "quality"


@pytest.mark.parametrize("changes, diagnostic", [
    ({"MISSING_PYTEST": "1"}, "missing pytest diagnostic"),
    ({"PYTEST_EXIT": "7"}, "pytest diagnostic"),
    ({"FAIL_NPM": "build"}, "npm diagnostic run build"),
    ({"FAIL_NPM": "typecheck"}, "npm diagnostic run typecheck"),
    ({"FAIL_NPM": "test"}, "npm diagnostic run test"),
    ({"FAIL_NPM": "test:e2e"}, "npm diagnostic run test:e2e"),
])
def test_failed_commands_keep_diagnostics_without_retry(gate_tree, changes, diagnostic):
    if "MISSING_PYTEST" in changes:
        (gate_tree[0] / "bin/pytest").unlink()
    result = invoke(gate_tree, **changes)
    assert result.returncode != 0
    assert diagnostic in result.stderr
    recorded = calls(gate_tree)
    assert len({json.dumps(call) for call in recorded}) == len(recorded)
    assert receipt(result)["status"] == "failed"


@pytest.mark.parametrize("missing", ["backend/tests", "frontend/src", "frontend/vitest.config.ts",
                                    "frontend/node_modules", "e2e", "playwright.config.ts"])
def test_missing_required_tests_or_setup_fails(gate_tree, missing):
    path = gate_tree[0] / missing
    shutil.rmtree(path) if path.is_dir() else path.unlink()
    result = invoke(gate_tree)
    assert result.returncode != 0, result.stdout
    assert "All quality gates passed" not in result.stdout


def test_missing_npm_fails_without_install(gate_tree):
    root, env = gate_tree
    (root / "bin/npm").unlink()
    (root / "bin/npm.cmd").unlink(missing_ok=True)
    env["PATH"] = str(root / "bin")
    result = invoke(gate_tree)
    assert result.returncode != 0
    assert "npm" in result.stderr
    assert receipt(result)["status"] == "failed"


def test_explicit_e2e_skip_is_not_full_success(gate_tree):
    result = invoke(gate_tree, SKIP_E2E="1")
    assert result.returncode == 0
    assert "SKIPPED" in result.stdout
    assert "All quality gates passed" not in result.stdout
    assert not any("test:e2e" in call[2] for call in calls(gate_tree))
    assert receipt(result)["scope"] == "quality-without-e2e"


@pytest.mark.parametrize("text", ["", "# comments only\n", "demo>=1.0\n", "-r other.txt\n",
                                "demo==1.*\n", "demo==nonsense\n", "demo==1.0; os_name=='posix'\n",
                                "demo==1.0\ndemo==2.0\n", "demo==2.0\n", "missing==1.0\n"])
def test_malformed_or_drifted_pins_fail_before_pytest(gate_tree, text):
    (gate_tree[0] / "backend/requirements.txt").write_text(text)
    result = invoke(gate_tree, "backend-gate.sh")
    assert result.returncode != 0, result.stdout
    assert not calls(gate_tree)
    assert receipt(result)["status"] == "failed"


@pytest.mark.parametrize("code", [0, 1, 5, 7])
def test_backend_receipt_binds_actual_terminal_exit(gate_tree, code):
    result = invoke(gate_tree, "backend-gate.sh", PYTEST_EXIT=str(code))
    assert result.returncode == code
    data = receipt(result)
    assert data["scope"] == "backend"
    assert data["status"] == ("passed" if code == 0 else "failed")
    assert data["requirements_sha256"]
    assert data["commands"][-1]["exit_code"] == code


def test_narrowed_run_cannot_claim_backend_gate(gate_tree):
    result = invoke(gate_tree, "backend-gate.sh", args=("--subset", "tests/test_ok.py"))
    assert result.returncode == 0
    assert "--no-cov" in calls(gate_tree)[0][2]
    assert receipt(result)["scope"] == "backend-subset"


@pytest.mark.parametrize("code", [2, 130, 137, 143])
def test_pytest_interrupted_exit_cannot_publish_completion(gate_tree, code):
    result = invoke(gate_tree, "backend-gate.sh", PYTEST_EXIT=str(code))
    assert result.returncode != 0
    start = json.loads(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                            if line.startswith("ECM_GATE_START ")))
    assert not Path(start["receipt"]).exists()
    assert "ECM_GATE_COMPLETE" not in result.stdout


def test_pytest_addopts_cannot_silently_narrow_full_gate(gate_tree):
    result = invoke(gate_tree, "backend-gate.sh", PYTEST_ADDOPTS="-k nonexistent --no-cov")
    assert result.returncode != 0
    assert not calls(gate_tree)
    assert "PYTEST_ADDOPTS" in result.stderr


@pytest.fixture
def policy():
    spec = importlib.util.spec_from_file_location("rdnia_gate_runner", ROOT / "scripts/gate_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = signal.getsignal(signal.SIGTERM)
    yield module
    signal.signal(signal.SIGTERM, handler)


def test_receipt_is_absent_until_atomic_publication(gate_tree, policy, monkeypatch, capsys):
    root, env = gate_tree
    monkeypatch.setattr(policy, "ROOT", root)
    monkeypatch.setattr(policy.tempfile, "tempdir", str(root.parent))
    monkeypatch.setattr(policy, "verify_pins", lambda _: "verified-fixture")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    replace = policy.os.replace
    observations = []

    def observe(source, destination):
        assert not destination.exists()
        data = json.loads(source.read_text())
        assert data["completed_at"] and data["status"] == "passed"
        observations.append(data)
        replace(source, destination)

    monkeypatch.setattr(policy.os, "replace", observe)
    assert policy.main(["--check-deps"]) == 0
    assert len(observations) == 1
    assert "ECM_GATE_COMPLETE" in capsys.readouterr().out


def test_failed_receipt_publication_has_no_terminal_marker(gate_tree, policy, monkeypatch, capsys):
    monkeypatch.setattr(policy, "ROOT", gate_tree[0])
    monkeypatch.setattr(policy.tempfile, "tempdir", str(gate_tree[0].parent))
    monkeypatch.setenv("ECM_PYTHON", sys.executable)
    monkeypatch.setattr(policy, "verify_pins", lambda _: "verified-fixture")

    def fail(source, destination):
        assert not destination.exists()
        raise OSError("publication failed")

    monkeypatch.setattr(policy.os, "replace", fail)
    with pytest.raises(OSError, match="publication failed"):
        policy.main(["--check-deps"])
    assert "ECM_GATE_COMPLETE" not in capsys.readouterr().out


@pytest.mark.parametrize("source", ["override", "repo", "worktree", "missing"])
def test_interpreter_selection_never_uses_ambient_for_tests(gate_tree, policy, monkeypatch, source):
    root, env = gate_tree
    monkeypatch.delenv("ECM_PYTHON", raising=False)
    relative = Path(".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    candidate = root / relative
    if source == "override":
        monkeypatch.setenv("ECM_PYTHON", sys.executable)
        assert policy.project_python(root) == sys.executable
        return
    if source == "worktree":
        main = root.parent / "main checkout"
        candidate = main / relative
        monkeypatch.setattr(policy.subprocess, "run", lambda *a, **k:
                            subprocess.CompletedProcess(a, 0, stdout=str(main / ".git")))
    if source == "missing":
        monkeypatch.setattr(policy.subprocess, "run", lambda *a, **k:
                            subprocess.CompletedProcess(a, 0, stdout=str(root / ".git")))
        with pytest.raises(ValueError, match="No project interpreter"):
            policy.project_python(root)
    else:
        candidate.parent.mkdir(parents=True)
        candidate.touch()
        candidate.chmod(0o755)
        assert policy.project_python(root) == str(candidate)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows cmd.exe unavailable on this host")
def test_native_cmd_success_and_failure_receipts(gate_tree):
    assert receipt(invoke(gate_tree))["status"] == "passed"
    assert receipt(invoke(gate_tree, FAIL_NPM="typecheck"))["status"] == "failed"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal control; native cmd has a separate acceptance lane")
@pytest.mark.parametrize("sig", [signal.SIGTERM, getattr(signal, "SIGKILL", 9)])
def test_interruption_has_no_receipt_and_cannot_reuse_previous_success(gate_tree, sig):
    old = receipt(invoke(gate_tree, "backend-gate.sh"))
    root, env = gate_tree
    process = subprocess.Popen(["bash", str(root / "scripts/backend-gate.sh")],
                               env={**env, "BLOCK": "1"}, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=True)
    output = ""
    try:
        # select bounds each foreground wait even if the child never reaches its milestone.
        import select
        deadline = time.monotonic() + 5
        while "BLOCKING" not in output and time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            if ready:
                output += os.read(process.stdout.fileno(), 65536).decode()
        assert "BLOCKING" in output, output
        start = json.loads(next(line.split(" ", 1)[1] for line in output.splitlines()
                                if line.startswith("ECM_GATE_START ")))
        assert start["run_id"] != old["run_id"]
        assert not Path(start["receipt"]).exists()
        os.killpg(process.pid, sig)
        remaining, _ = process.communicate(timeout=5)
        assert process.returncode != 0
        assert "ECM_GATE_COMPLETE" not in output + remaining
        assert not Path(start["receipt"]).exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
