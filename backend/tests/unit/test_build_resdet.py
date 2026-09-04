"""The dedicated resdet builder has one immutable, manifest-backed recipe."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "build_resdet.py"
MANIFEST = ROOT / "sbom" / "native-dependencies.json"


def _load():
    spec = importlib.util.spec_from_file_location("build_resdet", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def builder():
    return _load()


def test_cli_accepts_locations_only(builder):
    actions = {action.dest for action in builder._parser()._actions}
    assert actions == {"help", "manifest", "work_dir", "output_dir"}


def test_checksum_precedes_extract_and_recipe_is_exact_without_network(
    builder, tmp_path: Path, monkeypatch
):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    archive_sha = "a" * 64
    manifest["subjects"]["ecm"]["packages"][0]["build"]["archiveSha256"] = archive_sha
    manifest_path = tmp_path / "native-dependencies.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    events = []
    commands = []

    def download(_url, destination):
        events.append("download")
        destination.write_bytes(b"archive")

    def digest(_path):
        events.append("checksum")
        return archive_sha

    def extract(_archive, source):
        events.append("extract")
        (source / "lib" / "image").mkdir(parents=True)
        (source / "lib" / "kissfft" / "LICENSES").mkdir(parents=True)
        (source / "lib" / "image" / "y4m.c").write_text(
            "resdet_dims_exceed_limit(*width,*height,4,unsigned char)", encoding="utf-8"
        )
        for relative in (
            "COPYING", "COPYING.LGPL.txt", "COPYING.MIT.txt",
            "lib/kissfft/COPYING", "lib/kissfft/LICENSES/BSD-3-Clause",
        ):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative, encoding="utf-8")

    def run(command, *, cwd, env):
        commands.append((tuple(command), cwd, env))
        if command[0] == "./configure":
            (cwd / "config.mak").write_text(
                "PIXEL_MAX=8847360\nCFLAGS=-O2 -march=native -mtune=native\nREADERS=Y4M\n",
                encoding="utf-8",
            )
        elif command[:2] == ["make", "resdet"]:
            (cwd / "resdet").write_bytes(b"binary")
            (cwd / "resdet").chmod(0o755)
        if command[-1:] == ["-V"]:
            return SimpleNamespace(
                stdout=(
                    "resdet version 2.4.3\n"
                    "libresdet version 3.2.0\n"
                    "Built with image readers: Y4M\n"
                )
            )
        if command[0] == "nm":
            return SimpleNamespace(stdout="00000000 T kiss_fft\n")
        if command[0] == "ldd":
            return SimpleNamespace(stdout="libc.so.6 => /lib/libc.so.6\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(builder, "_download", download)
    monkeypatch.setattr(builder, "sha256_file", digest)
    monkeypatch.setattr(builder, "_safe_extract", extract)
    monkeypatch.setattr(builder, "_run", run)
    monkeypatch.setenv("CFLAGS", "attacker")
    monkeypatch.setenv("CC", "attacker-cc")
    monkeypatch.setenv("PKG_CONFIG_PATH", "/attacker")

    output = tmp_path / "output"
    builder.build(manifest_path, tmp_path / "work", output)

    assert events == ["download", "checksum", "extract"]
    assert commands[0][0] == (
        "./configure", "--disable-everything", "--disable-ffmpeg",
        "--omit-pgm-reader", "--omit-pfm-reader", "--pixel-max=8847360",
    )
    assert commands[1][0] == ("make", "resdet")
    for _command, _cwd, env in commands:
        assert env["CFLAGS"] == "-O2"
        assert env["CC"] == "cc"
        assert "PKG_CONFIG_PATH" not in env
    patched = (tmp_path / "work" / "source" / "lib" / "image" / "y4m.c").read_text()
    assert ",4,unsigned char)" not in patched
    assert patched.count(",1,unsigned char)") == 1
    assert (output / "resdet").is_file()
    assert (output / "licenses" / "kissfft" / "LICENSES" / "BSD-3-Clause").is_file()


def test_checksum_mismatch_never_extracts(builder, tmp_path: Path, monkeypatch):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_path = tmp_path / "native-dependencies.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(builder, "_download", lambda _url, path: path.write_bytes(b"bad"))
    monkeypatch.setattr(builder, "sha256_file", lambda _path: "0" * 64)
    extracted = False

    def extract(_archive, _source):
        nonlocal extracted
        extracted = True

    monkeypatch.setattr(builder, "_safe_extract", extract)
    with pytest.raises(builder.BuildError, match="checksum"):
        builder.build(manifest_path, tmp_path / "work", tmp_path / "output")
    assert extracted is False


def test_patch_requires_exactly_one_upstream_expression(builder, tmp_path: Path):
    source = tmp_path / "y4m.c"
    source.write_text("no multiplier here", encoding="utf-8")
    with pytest.raises(builder.BuildError, match="exactly one"):
        builder._patch_y4m_limit(source, 1)
    source.write_text(builder.UPSTREAM_Y4M_LIMIT * 2, encoding="utf-8")
    with pytest.raises(builder.BuildError, match="exactly one"):
        builder._patch_y4m_limit(source, 1)


def test_safe_extract_rejects_traversal_and_links(builder, tmp_path: Path):
    import io
    import tarfile

    for name, link in (("../escape", None), ("resdet/link", "../../escape")):
        archive = tmp_path / (hashlib.sha256(name.encode()).hexdigest() + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            if link is None:
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                tar.addfile(info)
        with pytest.raises(builder.BuildError, match="unsafe archive"):
            builder._safe_extract(archive, tmp_path / "source")


@pytest.mark.parametrize(
    ("version", "symbols", "linkage"),
    [
        (
            "resdet version 2.4.3\nlibresdet version 3.2.0\n"
            "Built with image readers: Y4M PGM\n",
            "kiss_fft",
            "libc.so",
        ),
        (
            "resdet 2.4.3\nlibresdet version 3.2.0\nBuilt with image readers: Y4M\n",
            "kiss_fft",
            "libc.so",
        ),
        (
            "resdet version 2.4.3\nlibresdet version 2.4.3\n"
            "Built with image readers: Y4M\n",
            "kiss_fft",
            "libc.so",
        ),
        (
            "resdet version 2.4.3\nlibresdet version 3.2.0\n"
            "Built with image readers: Y4M\n",
            "other_symbol",
            "libc.so",
        ),
        (
            "resdet version 2.4.3\nlibresdet version 3.2.0\n"
            "Built with image readers: Y4M\n",
            "kiss_fft",
            "libkissfft.so",
        ),
    ],
    ids=["wrong-reader", "wrong-cli-version", "wrong-library-version", "wrong-fft-backend", "dynamic-fft"],
)
def test_wrong_reader_or_fft_artifact_is_rejected(
    builder, tmp_path: Path, monkeypatch, version, symbols, linkage
):
    binary = tmp_path / "resdet"
    binary.write_bytes(b"binary")

    def run(command, *, cwd, env):
        if command[-1:] == ["-V"]:
            return SimpleNamespace(stdout=version)
        if command[0] == "nm":
            return SimpleNamespace(stdout=symbols)
        return SimpleNamespace(stdout=linkage)

    monkeypatch.setattr(builder, "_run", run)
    with pytest.raises(builder.BuildError, match="version or reader|KISS FFT"):
        builder._verify_artifact(binary, tmp_path, {})
