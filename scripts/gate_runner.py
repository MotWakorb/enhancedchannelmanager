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
import shlex
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
# tests/e2e mutates whatever ECM answers on localhost:6100. pytest reads an
# empty argument as its working directory, and --ignore does not apply to
# paths named on the command line, so selections are checked before pytest.
E2E_IGNORE = "--ignore=tests/e2e"
LIVE_E2E_FLAG = "--allow-live-e2e"
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


def check_selection(backend: Path, tokens: list[str], *, subset: bool,
                    allow_live_e2e: bool = False, source: str = "argument") -> None:
    """Refuse pytest arguments that could select the live tests/e2e suite.

    Every token that does not start with '-' is treated as a possible path,
    including option values, so 'tests' after -k is refused: write -k=tests.
    A subset may name files or directories, but not a directory that contains
    tests/e2e, and must name at least one existing path; otherwise pytest falls
    back to testpaths, the whole tree.
    """
    e2e = (backend / "tests/e2e").resolve()
    named = 0
    for token in tokens:
        if not token.strip():
            raise ValueError(f"Refusing an empty pytest {source}: pytest reads it as the "
                             "whole backend tree, including the live tests/e2e suite")
        if token.startswith("@"):
            raise ValueError(f"Refusing pytest argument file {token!r} ({source}): "
                             "its contents would bypass the selection checks")
        if token == "--pyargs" or token.startswith("--pyargs="):
            raise ValueError(f"Refusing --pyargs ({source}): package names bypass the selection checks")
        if token == LIVE_E2E_FLAG:
            raise ValueError(f"{LIVE_E2E_FLAG} is only accepted directly in --subset arguments")
        if token.startswith("-"):
            continue
        path = (backend / token.split("::", 1)[0]).resolve()
        if path == e2e or e2e in path.parents:
            if not allow_live_e2e:
                raise ValueError(f"Refusing {token!r} ({source}): tests/e2e runs against the live "
                                 f"ECM on localhost:6100; pass --subset {LIVE_E2E_FLAG} only "
                                 "when that instance may be mutated")
        elif path in e2e.parents and subset:
            raise ValueError(f"Refusing {token!r} ({source}): it names the whole test tree, which "
                             "is not a subset; name test files or a directory below tests/")
        if path.exists():
            named += 1
    if subset and source == "argument" and not named:
        raise ValueError("Refusing --subset with no explicit test path: pytest would fall back to "
                         "testpaths, the whole tree")


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
    allow_live_e2e = subset and LIVE_E2E_FLAG in args[1:]
    selection = [arg for arg in args[1:] if arg != LIVE_E2E_FLAG] if subset else args
    scope = "quality-without-e2e" if quality and skip == "1" else "quality" if quality else "backend"
    if not quality and args:
        scope = "dependencies" if deps_only else "backend-subset" if subset else "backend-custom"
    data = {"run_id": directory.name, "receipt": str(receipt), "tree": str(ROOT),
            "interpreter": python, "scope": scope, "arguments": args, "allow_live_e2e": allow_live_e2e,
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
        if not quality and not deps_only and args:
            check_selection(ROOT / "backend", selection, subset=subset, allow_live_e2e=allow_live_e2e)
            check_selection(ROOT / "backend", shlex.split(os.environ.get("PYTEST_ADDOPTS", "")),
                            subset=subset, source="PYTEST_ADDOPTS")
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
                # The ignore stays even with the opt-in: it still lets an explicitly
                # named tests/e2e path run, and nothing else under tests/e2e.
                flags = ["--no-cov", E2E_IGNORE, "--tb=short", "--no-header", "-p", "no:warnings"]
                extra = selection
                if allow_live_e2e:
                    print("!" * 72 + "\nLIVE E2E: --allow-live-e2e lets this run reach the ECM on "
                          "localhost:6100 and MUTATE it (notifications, cache, pipeline rules).\n"
                          "The e2e suite still skips unless ECM_E2E_LIVE=1 is also set.\n" + "!" * 72,
                          file=sys.stderr, flush=True)
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
