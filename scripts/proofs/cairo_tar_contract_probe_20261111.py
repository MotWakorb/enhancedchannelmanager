"""Disposable native contract witnesses; never a package/advisory clearance gate.

Run only in the documented non-root, networkless, read-only container, with a
private /tmp. Inputs are a trusted ordinary font and locally generated scratch
archives. No published exploit attachment is used. Cairo's live-object case
deliberately violates its documented debug-cleanup contract; it does NOT prove
the reported Poppler input-triggered vulnerability or justify removing asserts.
"""

import argparse
import ctypes
import hashlib
import io
import json
import os
import resource
import signal
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def tar_fixture_bytes(write_member: bool) -> bytes:
    """A hardlink and optionally an overwrite, with no absolute/parent names."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("alias")
        member.type = tarfile.LNKTYPE
        member.linkname = "sentinel"
        archive.addfile(member)
        if write_member:
            member = tarfile.TarInfo("alias")
            member.size = len(b"changed\n")
            archive.addfile(member, io.BytesIO(b"changed\n"))
    return buffer.getvalue()


def tar_confinement_satisfied(report: dict) -> bool:
    """Narrow witness predicate, NOT a general tar-confinement assertion."""
    boundaries = report.get("boundaries")
    return (
        report.get("compatibility_ok") is True
        and isinstance(boundaries, list)
        and len(boundaries) == 2
        and all(
            row.get("sentinel_unchanged") is True
            and row.get("outside_hardlink") is False
            for row in boundaries
        )
    )


def tar_repeated_fixture_bytes() -> tuple[bytes, bytes]:
    """Two archives for the documented repeated-extraction pattern."""
    first, second = io.BytesIO(), io.BytesIO()
    with tarfile.open(fileobj=first, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("escape")
        member.type = tarfile.SYMTYPE
        member.linkname = "../victim"
        archive.addfile(member)
    with tarfile.open(fileobj=second, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("escape/sentinel")
        member.size = len(b"changed\n")
        archive.addfile(member, io.BytesIO(b"changed\n"))
    return first.getvalue(), second.getvalue()


def _command(argv: list[str], cwd: Path) -> dict:
    process = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    return {
        "argv": argv,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def _regular_archive(path: Path) -> None:
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("data")
        member.size = len(b"ordinary\n")
        archive.addfile(member, io.BytesIO(b"ordinary\n"))


def observe_tar(tar: str, root: Path) -> dict:
    """Exercise only fixed scratch paths; never read an external archive."""
    compatibility = []
    archive = root / "ordinary.tar"
    _regular_archive(archive)
    for mode in ("relative", "absolute"):
        work = root / f"compatible-{mode}"
        work.mkdir()
        top = "top" if mode == "relative" else str(work / "top")
        result = _command([tar, "-xf", str(archive), f"--one-top-level={top}"], work)
        result["contents_ok"] = (work / "top/data").read_bytes() == b"ordinary\n"
        compatibility.append(result)
    boundaries = []
    for write_member in (False, True):
        work = root / ("overwrite" if write_member else "hardlink")
        work.mkdir()
        sentinel = work / "sentinel"
        sentinel.write_bytes(b"original\n")
        archive = root / f"boundary-{write_member}.tar"
        archive.write_bytes(tar_fixture_bytes(write_member))
        argv = [tar, "-xf", str(archive), "--one-top-level=top"]
        if write_member:
            argv.append("--overwrite")
        result = _command(argv, work)
        alias = work / "top/alias"
        result.update(
            write_member=write_member,
            sentinel_unchanged=sentinel.read_bytes() == b"original\n",
            outside_hardlink=alias.exists()
            and os.path.samestat(alias.stat(), sentinel.stat()),
        )
        boundaries.append(result)
    repeated = root / "repeated"
    repeated.mkdir()
    victim = repeated / "victim"
    victim.mkdir()
    sentinel = victim / "sentinel"
    sentinel.write_bytes(b"original\n")
    destination = repeated / "destination"
    destination.mkdir()
    first, second = tar_repeated_fixture_bytes()
    first_archive, second_archive = repeated / "first.tar", repeated / "second.tar"
    first_archive.write_bytes(first)
    second_archive.write_bytes(second)
    first_result = _command([tar, "-xf", str(first_archive)], destination)
    second_result = _command([tar, "-xf", str(second_archive)], destination)
    fresh = repeated / "independent-destination"
    fresh.mkdir()
    fresh_result = _command([tar, "-xf", str(second_archive)], fresh)
    return {
        "compatibility": compatibility,
        "compatibility_ok": all(
            row["returncode"] == 0 and row["contents_ok"] for row in compatibility
        ),
        "boundaries": boundaries,
        "repeated_extraction": {
            "first": first_result,
            "second": second_result,
            "sentinel_unchanged": sentinel.read_bytes() == b"original\n",
            "independent_second": fresh_result,
            "independent_contents_ok": (fresh / "escape/sentinel").read_bytes()
            == b"changed\n",
            "scope": "No concurrent attacker or incremental rename race tested",
        },
        "incremental_rename_race": "NOT_TESTED",
    }


def cairo_child(font: Path, released: bool) -> None:
    """Use an ordinary FT face; make the invalid cleanup order explicit."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    ft = ctypes.CDLL("libfreetype.so.6")
    cairo = ctypes.CDLL("libcairo.so.2")
    pointer = ctypes.c_void_p
    ft.FT_Init_FreeType.argtypes = [ctypes.POINTER(pointer)]
    ft.FT_New_Face.argtypes = [
        pointer,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.POINTER(pointer),
    ]
    ft.FT_Done_Face.argtypes = [pointer]
    ft.FT_Done_FreeType.argtypes = [pointer]
    cairo.cairo_ft_font_face_create_for_ft_face.argtypes = [pointer, ctypes.c_int]
    cairo.cairo_ft_font_face_create_for_ft_face.restype = pointer
    cairo.cairo_font_face_status.argtypes = [pointer]
    cairo.cairo_font_face_destroy.argtypes = [pointer]
    cairo.cairo_font_face_destroy.restype = None
    cairo.cairo_debug_reset_static_data.argtypes = []
    cairo.cairo_debug_reset_static_data.restype = None
    library, face = pointer(), pointer()
    if ft.FT_Init_FreeType(ctypes.byref(library)):
        raise RuntimeError("FT_Init_FreeType failed")
    if ft.FT_New_Face(library, os.fsencode(font), 0, ctypes.byref(face)):
        raise RuntimeError("FT_New_Face failed")
    cairo_face = cairo.cairo_ft_font_face_create_for_ft_face(face, 0)
    if not cairo_face or cairo.cairo_font_face_status(cairo_face):
        raise RuntimeError("Cairo font creation failed")
    if released:
        cairo.cairo_font_face_destroy(cairo_face)
        if ft.FT_Done_Face(face):
            raise RuntimeError("FT_Done_Face failed")
    cairo.cairo_debug_reset_static_data()
    if released and ft.FT_Done_FreeType(library):
        raise RuntimeError("FT_Done_FreeType failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--tar", default="/bin/tar")
    parser.add_argument("--require-tar-confinement", action="store_true")
    parser.add_argument("--cairo-child", choices=("live", "released"))
    args = parser.parse_args()
    # This is a guard against accidental host execution, not a sandbox. The
    # documented Docker invocation supplies the actual isolation boundary.
    if os.geteuid() == 0 or not os.statvfs("/").f_flag & os.ST_RDONLY:
        parser.error("requires non-root execution with a read-only root filesystem")
    if args.cairo_child:
        cairo_child(args.font, args.cairo_child == "released")
        return 0
    cairo_results = {}
    for state in ("live", "released"):
        process = subprocess.run(
            [
                sys.executable,
                __file__,
                "--font",
                str(args.font),
                "--cairo-child",
                state,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        cairo_results[state] = {
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
    with tempfile.TemporaryDirectory(prefix="cairo-tar-contract-", dir="/tmp") as temp:
        tar_results = observe_tar(args.tar, Path(temp))
    cairo_results["documented_cleanup_contract_observed"] = (
        cairo_results["live"]["returncode"] == -signal.SIGABRT
        and "unscaled->face == NULL" in cairo_results["live"]["stderr"]
        and cairo_results["released"]["returncode"] == 0
    )
    narrow_tar_pass = tar_confinement_satisfied(tar_results)
    print(
        json.dumps(
            {
                "status": "OBSERVATIONS_ONLY_NOT_ADVISORY_CLEARANCE",
                "font_sha256": hashlib.sha256(args.font.read_bytes()).hexdigest(),
                "tar_sha256": hashlib.sha256(Path(args.tar).read_bytes()).hexdigest(),
                "cairo": cairo_results,
                "tar": tar_results,
                "narrow_tar_boundary_satisfied": narrow_tar_pass,
                "poppler_input_trigger": "NOT_TESTED",
            },
            indent=2,
        )
    )
    if not cairo_results["documented_cleanup_contract_observed"]:
        return 2
    return int(args.require_tar_confinement and not narrow_tar_pass)


if __name__ == "__main__":
    raise SystemExit(main())
