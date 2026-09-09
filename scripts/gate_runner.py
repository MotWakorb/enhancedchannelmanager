"""Local gate policy shared by Bash and cmd, not a replacement for CI.

Receipts describe a completed invocation, not a signed source attestation.
Runtime controls: backend/tests/unit/test_quality_gate_runtime.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
# Compared independently with CI by test_backend_gate_contract.py.
BACKEND_FLAGS = [
    "--ignore=tests/e2e", "--ignore=tests/performance",
    "-m", "not slow", "--tb=short", "--no-header", "-p", "no:warnings",
]
_PIN_RE = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([^\s;*]+)")
_NAME_RE = re.compile(r"[-_.]+")


def project_python(root: Path) -> str:
    """Select an explicit/project/worktree interpreter, never ambient Python."""
    override = os.environ.get("ECM_PYTHON")
    if override:
        candidate = shutil.which(override)
        if not candidate:
            raise ValueError(f"ECM_PYTHON is not executable: {override}")
        return os.path.abspath(candidate)
    relative = Path(".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    candidate = root / relative
    if not candidate.is_file():
        common = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        candidate = (root / common).resolve().parent / relative
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("No project interpreter; set ECM_PYTHON to an aligned private venv. "
                         "Ambient Python is not a backend gate environment.")
    # Do not resolve the executable symlink: that would bypass the venv.
    return os.path.abspath(candidate)


def verify_pins(requirements: Path) -> str:
    """Check the generated, exact-pin lock format; unsupported syntax fails closed."""
    from packaging.version import Version

    content = requirements.read_bytes()
    pins = {}
    for number, raw in enumerate(content.decode("utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"{requirements}:{number}: expected one exact name==version pin")
        name, expected = match.groups()
        Version(expected)  # Reject malformed versions, not merely malformed operators.
        name = _NAME_RE.sub("-", name).lower()
        if name in pins:
            raise ValueError(f"{requirements}:{number}: duplicate pin {name}")
        pins[name] = expected
    if not pins:
        raise ValueError(f"{requirements}: no dependency pins")
    errors = []
    for name, expected in pins.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = "MISSING"
        if actual != expected:
            errors.append(f"  {name}: installed={actual}, pinned={expected}")
    if errors:
        raise ValueError("Backend dependency drift:\n" + "\n".join(errors) +
                         "\nUse an aligned private venv via ECM_PYTHON; do not repair a shared venv in use.")
    print(f"Backend pins verified: {len(pins)} matched ({sys.executable})", flush=True)
    return hashlib.sha256(content).hexdigest()


def run_command(argv: list[str], cwd: Path, commands: list[dict]) -> None:
    """One foreground invocation; keep diagnostics and bound heartbeat waits."""
    print(f"RUN {cwd}: {json.dumps(argv)}", flush=True)
    with subprocess.Popen(argv, cwd=cwd) as process:
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print(f"RUNNING pid={process.pid}: {json.dumps(argv)}", flush=True)
        except BaseException:
            process.kill()
            process.wait()
            raise
    # Pytest uses 2 for interrupted execution (including collection failures).
    # Conservatively withhold completion rather than certify a partial suite.
    if code < 0 or code in (130, 137, 143) or (argv[1:3] == ["-m", "pytest"] and code == 2):
        raise KeyboardInterrupt(f"command interrupted (exit {code})")
    commands.append({"argv": argv, "cwd": str(cwd), "exit_code": code})
    if code:
        raise subprocess.CalledProcessError(code, argv)


def interrupted(signum: int, _frame: object) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def main(args: list[str]) -> int:
    python = project_python(ROOT)
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(python):
        os.execv(python, [python, str(Path(__file__).resolve()), *args])

    signal.signal(signal.SIGTERM, interrupted)
    # No shared 'latest' or caller-reused exit file: every invocation owns a
    # fresh directory, even if its predecessor was killed before cleanup.
    directory = Path(tempfile.mkdtemp(prefix="ecm-gate-"))
    receipt = directory / "receipt.json"
    quality = bool(args and args[0] == "--quality")
    deps_only = args == ["--check-deps"]
    subset = bool(args and args[0] == "--subset")
    skip = os.environ.get("SKIP_E2E", "0")
    scope = "quality-without-e2e" if quality and skip == "1" else "quality" if quality else "backend"
    if not quality and args:
        scope = "dependencies" if deps_only else "backend-subset" if subset else "backend-custom"
    data = {"run_id": directory.name, "receipt": str(receipt), "tree": str(ROOT),
            "interpreter": python, "scope": scope, "arguments": args,
            "started_at": datetime.now(timezone.utc).isoformat(), "commands": []}
    print("ECM_GATE_START " + json.dumps(data, sort_keys=True), flush=True)
    code = 0
    try:
        if quality and args != ["--quality"]:
            raise ValueError("quality-gates takes no arguments; use SKIP_E2E=1 for an explicit partial run")
        if quality and skip not in ("0", "1"):
            raise ValueError("SKIP_E2E must be 0 or 1")
        if scope in ("backend", "quality", "quality-without-e2e") and os.environ.get("PYTEST_ADDOPTS"):
            raise ValueError("Unset PYTEST_ADDOPTS for the canonical gate; it can override selection/coverage")
        data["requirements_sha256"] = verify_pins(ROOT / "backend/requirements.txt")
        if not deps_only:
            run_command([python, "-c", "import pytest"], ROOT, data["commands"])
            if not any(path.is_file() for path in (ROOT / "backend/tests").rglob("test_*.py")):
                raise ValueError("Missing required backend/tests test files")
            if quality:
                source = ROOT / "backend/main.py"
                compile(source.read_bytes(), str(source), "exec")
            flags = BACKEND_FLAGS
            extra = [] if quality else args
            if subset:
                print("SUBSET: NOT THE GATE; --no-cov disables whole-tree --cov-fail-under", flush=True)
                flags = ["--no-cov", "--tb=short", "--no-header", "-p", "no:warnings"]
                extra = args[1:]
            elif not quality and args:
                print("CUSTOM: NOT THE UNMODIFIED BACKEND GATE", flush=True)
            if not subset:
                print("Default exclusions: tests/e2e (live localhost:6100), "
                      "tests/performance (perf-benchmarks); slow tests named in docs/testing.md", flush=True)
            run_command([python, "-m", "pytest", *flags, *extra], ROOT / "backend", data["commands"])
        if quality:
            npm = shutil.which("npm")
            if not npm:
                raise ValueError("Missing required npm executable")
            frontend = ROOT / "frontend"
            for path in (frontend / "node_modules", frontend / "vitest.config.ts"):
                if not path.exists():
                    raise ValueError(f"Missing required frontend setup: {path}; install locked dependencies separately")
            if not any(path.is_file() for pattern in ("*.test.ts", "*.test.tsx")
                       for path in (frontend / "src").rglob(pattern)):
                raise ValueError("Missing required frontend tests")
            for task in ("typecheck", "build", "test"):
                run_command([npm, "run", task], frontend, data["commands"])
            if skip == "1":
                print("SKIPPED E2E (SKIP_E2E=1): partial quality verification", flush=True)
            else:
                if not (ROOT / "playwright.config.ts").is_file() or not any(
                    path.is_file() for path in (ROOT / "e2e").rglob("*.spec.ts")
                ):
                    raise ValueError("Missing required Playwright configuration/tests")
                run_command([npm, "run", "test:e2e"], ROOT, data["commands"])
    except KeyboardInterrupt as error:
        print(f"INTERRUPTED: {error}; no completion receipt", file=sys.stderr, flush=True)
        return 130
    except subprocess.CalledProcessError as error:
        code = error.returncode
        print(str(error), file=sys.stderr, flush=True)
    except (OSError, ValueError, ImportError, SyntaxError) as error:
        code = 2
        print(str(error), file=sys.stderr, flush=True)
    data.update(exit_code=code, status="failed" if code else "passed",
                completed_at=datetime.now(timezone.utc).isoformat())
    encoded = json.dumps(data, sort_keys=True)
    temporary = directory / "receipt.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, receipt)
    print("ECM_GATE_COMPLETE " + encoded, flush=True)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr, end="")
        print(f"Gate could not complete: {error}", file=sys.stderr)
        sys.exit(2)
