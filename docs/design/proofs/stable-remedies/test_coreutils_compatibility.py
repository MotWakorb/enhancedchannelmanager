"""Bounded upstream-derived GNU semantics tests for private musl prototypes.

Set ECM_COREUTILS_PROOF_PREFIX to /research/old or /research/fixed. Every child
has a timeout; no shell, operator file, privilege or chroot is used.
"""

import os
import subprocess

import pytest


def utility(name, *arguments, data=b""):
    return subprocess.run(
        [os.environ["ECM_COREUTILS_PROOF_PREFIX"] + "-" + name, *arguments],
        input=data,
        capture_output=True,
        timeout=10,
        check=False,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )


@pytest.mark.parametrize("name", ["uniq", "unexpand"])
def test_gnu_provider_identity(name):
    result = utility(name, "--version")
    assert result.returncode == 0
    assert b"GNU coreutils) 9.11" in result.stdout


@pytest.mark.parametrize("zero_terminated", [False, True])
def test_upstream_uniq_multibyte_width_regression(zero_terminated):
    terminator = b"\0" if zero_terminated else b"\n"
    line = "😀".encode() + b"A" * 255 + terminator
    result = utility(
        "uniq", "-w256", *(["-z"] if zero_terminated else []), data=line * 2
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == line


@pytest.mark.parametrize("divisor", [4, 6])
def test_upstream_unexpand_allocation_overflow_is_handled(divisor):
    result = utility("unexpand", "-t", str((2**64 - 1) // divisor + 1), data=b"   \n")
    # These are the two allowed statuses in upstream b60a159f's original test.
    assert result.returncode in {0, 1}, (result.returncode, result.stderr)


@pytest.mark.parametrize("blank", ["\u3000", "\u0085"])
def test_wide_and_negative_width_blanks_are_bounded(blank):
    result = utility("unexpand", "-t1", data=(blank * 40000 + "\n").encode())
    assert result.returncode == 0, (result.returncode, result.stderr)


def test_gnu_tab_and_field_semantics_preserved():
    result = utility("unexpand", "-a", "-t4", data=b"a   b       c\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"a\tb\t\tc\n"
    result = utility("uniq", "-f1", data=b"one alpha\ntwo alpha\nthree beta\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"one alpha\nthree beta\n"
