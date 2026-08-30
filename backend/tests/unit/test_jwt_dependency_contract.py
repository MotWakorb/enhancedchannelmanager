"""Dependency boundary for ECM's HS256 JWT implementation."""

import ast
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(canonicalize_name(Requirement(line).name))
    return names


def _imports_package(path: Path, package: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == package or node.module.startswith(f"{package}."))
        ):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == package or alias.name.startswith(f"{package}.")
            for alias in node.names
        ):
            return True
    return False


def test_backend_runtime_uses_pyjwt_without_python_jose_or_ecdsa():
    direct = _requirement_names(BACKEND / "requirements.in")
    locked = _requirement_names(BACKEND / "requirements.txt")

    assert "pyjwt" in direct
    assert "pyjwt" in locked
    assert "python-jose" not in direct
    assert "python-jose" not in locked
    assert "ecdsa" not in locked


def test_backend_code_imports_pyjwt_not_python_jose():
    python_files = list(BACKEND.glob("**/*.py"))

    assert not [path for path in python_files if _imports_package(path, "jose")]
    assert _imports_package(BACKEND / "auth" / "tokens.py", "jwt")


def test_josepy_remains_the_acme_dependency():
    direct = _requirement_names(BACKEND / "requirements.in")

    assert "josepy" in direct
    assert _imports_package(BACKEND / "tls" / "acme_client.py", "josepy")
