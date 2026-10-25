"""Retain exact, pinned-signature SUSE/Packman SRPMs for a runtime RPM inventory.

Run in a source-builder, not the runtime image. No Docker, package installation,
SPEC execution, license heuristics, repository fallback, or third-party Python.
See docs/security/native-source-delivery.md for the trust and delivery contract.
"""

import argparse
import gzip
import hashlib
import json
import lzma
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

FIELDS = ("name", "version", "release", "arch", "vendor", "sourcerpm", "license")
QUERY = "\\t".join("%{" + field.upper() + "}" for field in FIELDS) + "\\n"
REPOSITORIES = {
    "openSUSE": {
        "urls": {
            "x86_64": "https://download.opensuse.org/tumbleweed/repo/src-oss/",
            "aarch64": "https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/",
        },
        "fingerprint": "AD485664E901B867051AB15F35A2F86E29B700A4",
    },
    "http://packman.links2linux.de": {
        "urls": dict.fromkeys(
            ("x86_64", "aarch64"),
            "https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/",
        ),
        "fingerprint": "F8875B880D518B6B8C530D1345A1D0671ABD1AFB",
    },
}
# Two authenticated historical SRPMs absent from the rolling source feed.
# Exact routing (not fallback); scope/hash enforced by test_collect_native_sources.
EXACT_SOURCE_ROUTES = {
    ("openSUSE", "x86_64", "harfbuzz-14.6.0-1.1.src.rpm"): {
        "url": "https://slc-mirror.opensuse.org/source/tumbleweed/repo/oss/src/harfbuzz-14.6.0-1.1.src.rpm",
        "sha256": "53e21e5cb510895c46935f1f6325415846549316e200fb88a69d2a66b3fbc65f",
    },
    ("openSUSE", "x86_64", "openSUSE-release-20261023-4366.1.src.rpm"): {
        "url": "https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261023-4366.1.src.rpm",
        "sha256": "0ca30fbc607834c8c5633ffe977f9d7bc1bd766a430976fec09185ae54d2869e",
    },
}
TOKEN = re.compile(r"[A-Za-z0-9_+~.-]+\Z")
SOURCE_NAME = re.compile(r"[A-Za-z0-9_+~.-]+\.src\.rpm\Z")
MAX_PAYLOAD = 2 * 1024**3


def source_url(vendor: str, architecture: str, filename: str) -> str:
    route = EXACT_SOURCE_ROUTES.get((vendor, architecture, filename))
    if route:
        return route["url"]
    return REPOSITORIES[vendor]["urls"][architecture] + "src/" + filename


def parse_inventory(text: str, architecture: str) -> tuple[list[dict], list[dict]]:
    """Validate the seven literal RPM query columns, including key exemptions."""
    if architecture not in ("x86_64", "aarch64"):
        raise ValueError("unsupported runtime architecture")
    rows, excluded, seen = [], [], set()
    for line in text.splitlines():
        values = line.split("\t")
        if len(values) != len(FIELDS) or any(
            not v or any(ord(c) < 32 for c in v) for v in values
        ):
            raise ValueError("inventory requires seven nonempty RPM query columns")
        row = dict(zip(FIELDS, values))
        identity = tuple(values[:4])
        if identity in seen:
            raise ValueError("duplicate installed RPM identity")
        seen.add(identity)
        if row["name"] == "gpg-pubkey":
            if not all(
                re.fullmatch(r"[0-9a-f]{8}", row[f]) for f in ("version", "release")
            ) or [row[f] for f in ("arch", "vendor", "sourcerpm", "license")] != [
                "(none)",
                "(none)",
                "(none)",
                "pubkey",
            ]:
                raise ValueError("unexpected public-key inventory row")
            excluded.append(row)
            continue
        if any(
            not TOKEN.fullmatch(row[f]) or row[f] in (".", "..")
            for f in ("name", "version", "release")
        ):
            raise ValueError("missing or unsafe installed identity")
        if row["arch"] not in (architecture, "noarch"):
            raise ValueError(
                "installed RPM architecture disagrees with requested architecture"
            )
        if row["vendor"] not in REPOSITORIES:
            raise ValueError("unapproved actual RPM vendor: " + row["vendor"])
        if not SOURCE_NAME.fullmatch(row["sourcerpm"]):
            raise ValueError("missing or unsafe exact SOURCERPM")
        if row["license"] == "(none)":
            raise ValueError("missing installed license")
        rows.append(row)
    if not rows:
        raise ValueError("empty package inventory")
    return sorted(rows, key=lambda row: tuple(row[f] for f in FIELDS)), excluded


def run(command: list[str], timeout: float = 60) -> str:
    """Native tools run without a shell, scriptlets, or ambient trust database."""
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout


def prepare_key(key: Path, vendor: str, workspace: Path) -> Path:
    """Import exactly one full-fingerprint-pinned key into an isolated RPM DB."""
    workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
    home = workspace / "gnupg"
    home.mkdir(mode=0o700)
    key = key.resolve(strict=True)
    if key.stat().st_size > 1024 * 1024:
        raise ValueError("oversized public key")
    status = run(
        [
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--no-auto-key-retrieve",
            "--with-colons",
            "--show-keys",
            str(key),
        ]
    )
    fingerprints = [
        line.split(":")[9] for line in status.splitlines() if line.startswith("fpr:")
    ]
    if fingerprints != [REPOSITORIES[vendor]["fingerprint"]]:
        raise ValueError("public key fingerprint does not match the vendor pin")
    database = workspace / "rpmdb"
    database.mkdir(mode=0o700)
    run(["rpm", "--dbpath", str(database), "--initdb"])
    run(["rpmkeys", "--dbpath", str(database), "--import", str(key)])
    return database


def check_rpmkeys(status: str, fingerprint: str) -> None:
    if re.search(
        r"\b(NOKEY|NOT OK|BAD|NOTTRUSTED|UNSIGNED|UNKNOWN|FAILED)\b",
        status,
        re.IGNORECASE,
    ):
        raise ValueError("RPM signature/digest failure")
    signatures = re.findall(r"Signature, key ID ([0-9a-f]+): OK", status, re.IGNORECASE)
    if not signatures or any(
        value.lower() not in (fingerprint[-8:].lower(), fingerprint[-16:].lower())
        for value in signatures
    ):
        raise ValueError("missing or wrong pinned RPM signature")
    if not re.search(r"Payload SHA256 digest: OK", status):
        raise ValueError("missing verified payload digest")


def read_exact(file, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise ValueError("truncated RPM/CPIO data")
    return data


def rpm_header(file) -> dict:
    header = read_exact(file, 16)
    if header[:8] != b"\x8e\xad\xe8\x01\0\0\0\0":
        raise ValueError("bad RPM header")
    count, size = struct.unpack_from(">II", header, 8)
    if count > 100_000 or size > 32 * 1024**2:
        raise ValueError("oversized RPM header")
    indices, store = read_exact(file, count * 16), read_exact(file, size)
    tags = {}
    for index in range(count):
        tag, kind, offset, number = struct.unpack_from(">IIII", indices, index * 16)
        if str(tag) in tags or offset >= size or number > 100_000:
            raise ValueError("duplicate tag or invalid RPM header offset/count")
        # Keep unknown types represented so duplicate tags cannot hide in them.
        tags[str(tag)] = None
        if kind in (6, 8, 9):
            values, position = [], offset
            for _ in range(number):
                end = store.find(b"\0", position)
                if end < 0:
                    raise ValueError("unterminated RPM header string")
                values.append(store[position:end].decode("utf-8"))
                position = end + 1
            tags[str(tag)] = values
        elif kind == 4:
            if offset + number * 4 > size:
                raise ValueError("truncated RPM header integers")
            tags[str(tag)] = list(struct.unpack_from(">" + "I" * number, store, offset))
    return tags


def payload_offset(file) -> dict:
    lead = read_exact(file, 96)
    if lead[:4] != b"\xed\xab\xee\xdb" or struct.unpack_from(">H", lead, 6)[0] != 1:
        raise ValueError("not a source RPM")
    rpm_header(file)
    file.seek((file.tell() + 7) // 8 * 8)
    return rpm_header(file)


def source_header(path: Path) -> dict:
    with path.open("rb") as file:
        return payload_offset(file)


def check_identity(tags: dict, filename: str, vendor: str) -> None:
    def field(tag):
        value = tags.get(tag)
        if not isinstance(value, list) or len(value) != 1:
            raise ValueError("missing exact source header identity")
        return value[0]

    if (
        tags.get("1106") != [1]
        or field("1011") != vendor
        or f"{field('1000')}-{field('1001')}-{field('1002')}.src.rpm" != filename
        or not field("1014")
        or field("1014") == "(none)"
    ):
        raise ValueError(
            "signed source header disagrees with exact filename/vendor/license"
        )


def complete_payload(tags: dict, filenames: set[str]) -> None:
    if tags.get("1051") or tags.get("1052"):
        raise ValueError("NoSource/NoPatch source omission")
    if not any(name.endswith(".spec") for name in filenames):
        raise ValueError("missing SPEC")
    listed = tags.get("1018", []) + tags.get("1019", [])
    if not listed:
        raise ValueError("source RPM has no declared Source/Patch inputs")
    # These are labels in authenticated RPM headers, not fetch instructions.
    names = {urllib.parse.urlsplit(name).path.rsplit("/", 1)[-1] for name in listed}
    if not names <= filenames:
        raise ValueError(
            "missing declared Source/Patch payload files: "
            + ", ".join(sorted(names - filenames))
        )


def read_payload(archive, max_bytes: int, seconds: float) -> dict:
    """Hash CPIO contents without extracting or executing anything."""
    deadline, consumed = time.monotonic() + seconds, 0
    files, specs, seen = [], [], set()

    def read(size):
        nonlocal consumed
        consumed += size
        if consumed > max_bytes or time.monotonic() >= deadline:
            raise ValueError("source payload byte/time limit exceeded")
        return read_exact(archive, size)

    while True:
        header = read(110)
        if header[:6] not in (b"070701", b"070702"):
            raise ValueError("not a newc CPIO payload")
        fields = [int(header[6 + i * 8 : 14 + i * 8], 16) for i in range(13)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        if not 1 <= namesize <= 4096:
            raise ValueError("invalid payload filename size")
        raw_name = read(namesize)
        if not raw_name.endswith(b"\0") or b"\0" in raw_name[:-1]:
            raise ValueError("invalid payload filename terminator")
        name = raw_name[:-1].decode("utf-8")
        read(-(110 + namesize) % 4)
        if name == "TRAILER!!!":
            if size:
                raise ValueError("invalid CPIO trailer")
            break
        name = name.removeprefix("./")
        if (
            "/" in name
            or "\\" in name
            or name in ("", ".", "..")
            or any(ord(c) < 32 for c in name)
            or mode & 0o170000 != 0o100000
            or fields[4] != 1
            or name in seen
        ):
            raise ValueError("unsafe, duplicate, or nonregular source payload entry")
        seen.add(name)
        if len(seen) > 100_000 or (name.endswith(".spec") and size > 8 * 1024**2):
            raise ValueError("source payload file count/SPEC size limit exceeded")
        digest, remaining, spec, crc = hashlib.sha256(), size, bytearray(), 0
        while remaining:
            block = read(min(remaining, 1024**2))
            remaining -= len(block)
            digest.update(block)
            if header[:6] == b"070702":
                crc = (crc + sum(block)) & 0xFFFFFFFF
            if name.endswith(".spec"):
                spec.extend(block)
        if header[:6] == b"070702" and crc != fields[12]:
            raise ValueError("CPIO CRC mismatch")
        read(-size % 4)
        files.append({"name": name, "size": size, "sha256": digest.hexdigest()})
        if name.endswith(".spec"):
            if re.search(
                rb"^\s*No(?:Source|Patch)\s*:", spec, re.IGNORECASE | re.MULTILINE
            ):
                raise ValueError("SPEC declares NoSource/NoPatch")
            specs.append(name)
    # Read through compressor EOF/checksum, rejecting hidden trailing contents.
    while True:
        if time.monotonic() >= deadline:
            raise ValueError("source payload time limit exceeded")
        padding = archive.read(min(1024**2, max_bytes - consumed + 1))
        if not padding:
            break
        consumed += len(padding)
        if consumed > max_bytes or padding.strip(b"\0"):
            raise ValueError("excess or nonzero data after CPIO trailer")
    return {"files": files, "spec_names": sorted(specs)}


@contextmanager
def decompressed(file, compressor: str, seconds: float):
    if compressor in ("gzip", "xz"):
        with (
            gzip.GzipFile(fileobj=file, mode="rb")
            if compressor == "gzip"
            else lzma.LZMAFile(file, "rb")
        ) as archive:
            yield archive
        return
    if compressor != "zstd":
        raise ValueError("unsupported source payload compressor")
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["zstd", "-dc", "--memory=256MB"],
            stdin=file,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        timer = threading.Timer(seconds, process.kill)
        timer.start()
        try:
            yield process.stdout
            if process.wait(timeout=seconds) != 0:
                raise ValueError("source payload decompression failed")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def audit_payload(
    path: Path, max_bytes: int = MAX_PAYLOAD, seconds: float = 120
) -> dict:
    # Unbuffered: a zstd child must inherit the exact payload offset.
    with path.open("rb", buffering=0) as file:
        tags = payload_offset(file)
        with decompressed(file, tags.get("1125", [""])[0], seconds) as archive:
            result = read_payload(archive, max_bytes, seconds)
    complete_payload(tags, {item["name"] for item in result["files"]})
    return {
        **result,
        "source": tags.get("1018", []),
        "patch": tags.get("1019", []),
        "nosource": tags.get("1051", []),
        "nopatch": tags.get("1052", []),
        "declared_input_payload_complete": True,
    }


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify_source(
    path: Path, filename: str, vendor: str, database: Path, seconds: float = 120
) -> dict:
    fingerprint = REPOSITORIES[vendor]["fingerprint"]
    check_rpmkeys(
        run(["rpmkeys", "--dbpath", str(database), "-Kv", str(path)], timeout=seconds),
        fingerprint,
    )
    tags = source_header(path)
    check_identity(tags, filename, vendor)
    return {
        "filename": filename,
        "vendor": vendor,
        "sha256": sha256(path),
        "size": path.stat().st_size,
        "fingerprint": fingerprint,
        "license": tags["1014"][0],
        "header_arch": tags.get("1022", []),
        "authentication": "pinned-rpm-signature-and-exact-header",
        "payload": audit_payload(path, seconds=seconds),
    }


class Budget:
    def __init__(self, count: int, size: int, seconds: float):
        if count <= 0 or size <= 0 or seconds <= 0 or not math.isfinite(seconds):
            raise ValueError("download limits must be positive")
        self.count, self.size = count, size
        self.deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("collection time limit exceeded")
        return remaining

    def consume(self, size: int) -> None:
        self.remaining()
        self.size -= size
        if self.size < 0:
            raise ValueError("collection byte limit exceeded")


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("source redirect must remain HTTPS")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url: str, destination: Path, budget: Budget) -> dict:
    pinned_hash = next(
        (
            route["sha256"]
            for route in EXACT_SOURCE_ROUTES.values()
            if route["url"] == url
        ),
        None,
    )
    if pinned_hash is None and not any(
        url.startswith(base + "src/")
        and SOURCE_NAME.fullmatch(url[len(base + "src/") :])
        for repo in REPOSITORIES.values()
        for base in repo["urls"].values()
    ):
        raise ValueError("only documented exact public source URLs are allowed")
    budget.count -= 1
    if budget.count < 0:
        raise ValueError("download count limit exceeded")
    # No retry/fallback: failed exact-source availability is a build failure.
    opener = urllib.request.build_opener(HTTPSRedirect())
    with opener.open(url, timeout=min(30, budget.remaining())) as response:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise ValueError("source redirect must remain HTTPS")
        with destination.open("xb") as output:
            reader = getattr(response, "read1", response.read)
            while block := reader(64 * 1024):
                budget.consume(len(block))
                output.write(block)
        budget.remaining()
        if pinned_hash is not None and sha256(destination) != pinned_hash:
            raise ValueError("pinned source checksum mismatch: " + url)
        return {"url": url, "final_url": response.geturl()}


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def expected_hashes(path: Path | None, architecture: str, origins: list[tuple]) -> dict:
    """An existing collector index also serves as a per-architecture cache pin."""
    if path is None:
        return {}
    if path.stat().st_size > 32 * 1024**2:
        raise ValueError("oversized expected source index")
    index = json.loads(path.read_text())
    if not isinstance(index, dict) or index.get("architecture") != architecture:
        raise ValueError("expected source index architecture mismatch")
    sources = index.get("sources")
    if not isinstance(sources, list):
        raise TypeError("expected source index requires source records")
    hashes, selected = {}, dict(origins)
    for record in sources:
        if not isinstance(record, dict):
            raise TypeError("invalid expected source record")
        name, digest = record.get("filename"), record.get("sha256")
        if (
            not isinstance(name, str)
            or not SOURCE_NAME.fullmatch(name)
            or name in hashes
            or not isinstance(digest, str)
            or not re.fullmatch("[0-9a-f]{64}", digest)
        ):
            raise ValueError("invalid/duplicate expected source hash")
        if name in selected and record.get("url") != source_url(
            selected[name], architecture, name
        ):
            raise ValueError(
                "expected source repository disagrees with architecture/vendor"
            )
        hashes[name] = digest
    if any(name not in hashes for name, _ in origins):
        raise ValueError("expected source index does not cover every selected origin")
    return hashes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inventory_input = parser.add_mutually_exclusive_group()
    inventory_input.add_argument(
        "--inventory",
        type=Path,
        help="recorded seven-column RPM inventory; otherwise query rpm",
    )
    inventory_input.add_argument(
        "--rpm-root",
        type=Path,
        default=Path("/"),
        help="runtime root for live RPM query only",
    )
    parser.add_argument("--arch", required=True, choices=("x86_64", "aarch64"))
    parser.add_argument(
        "--out", type=Path, required=True, help="new output directory (must not exist)"
    )
    parser.add_argument("--suse-key", type=Path)
    parser.add_argument("--packman-key", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--expected-sha256",
        type=Path,
        help="per-architecture JSON source index with exact filename/URL/SHA256; mandatory for cache",
    )
    parser.add_argument("--max-downloads", type=int, default=512)
    parser.add_argument("--max-bytes", type=int, default=2 * 1024**3)
    parser.add_argument("--max-seconds", type=float, default=900)
    options = parser.parse_args(argv)
    budget = Budget(options.max_downloads, options.max_bytes, options.max_seconds)
    if options.inventory:
        if options.inventory.stat().st_size > 16 * 1024**2:
            raise ValueError("oversized inventory")
        inventory = options.inventory.read_bytes()
    else:
        inventory = run(
            ["rpm", "--root", str(options.rpm_root.resolve()), "-qa", "--qf", QUERY]
        ).encode()
    rows, excluded = parse_inventory(inventory.decode("utf-8"), options.arch)
    origins = sorted({(row["sourcerpm"], row["vendor"]) for row in rows})
    if len({name for name, _ in origins}) != len(origins):
        raise ValueError("conflicting vendors for the same source filename")
    if len(origins) > options.max_downloads:
        raise ValueError("source count limit exceeded")
    expected = expected_hashes(options.expected_sha256, options.arch, origins)
    if options.cache_dir and any(name not in expected for name, _ in origins):
        raise ValueError("cache requires expected SHA256 for every selected source")
    keys = {
        "openSUSE": options.suse_key,
        "http://packman.links2linux.de": options.packman_key,
    }
    if any(keys[vendor] is None for _, vendor in origins):
        raise ValueError(
            "each installed origin requires its vendor's pinned public key file"
        )
    options.out.mkdir(parents=True, exist_ok=False)
    directory = options.out / "sources"
    directory.mkdir()
    sources = []
    with tempfile.TemporaryDirectory(prefix="ecm-source-trust-") as temporary:
        databases = {
            vendor: prepare_key(keys[vendor], vendor, Path(temporary) / str(index))
            for index, vendor in enumerate(sorted({vendor for _, vendor in origins}))
        }
        for filename, vendor in origins:
            budget.remaining()
            path = directory / (filename + ".partial")
            url = source_url(vendor, options.arch, filename)
            print(f"Collecting exact source: {url}", flush=True)
            cached = options.cache_dir / filename if options.cache_dir else None
            if cached is not None and cached.exists():
                if not cached.is_file() or cached.is_symlink():
                    raise ValueError("cache source must be a regular nonsymlink file")
                budget.consume(cached.stat().st_size)
                shutil.copyfile(cached, path)
                retrieval = {
                    "url": url,
                    "final_url": None,
                    "retrieval": "reverified-cache",
                }
            else:
                retrieval = download(url, path, budget)
            if filename in expected and sha256(path) != expected[filename]:
                raise ValueError("expected source checksum mismatch: " + filename)
            route = EXACT_SOURCE_ROUTES.get((vendor, options.arch, filename))
            if route and sha256(path) != route["sha256"]:
                raise ValueError("pinned source checksum mismatch: " + url)
            record = verify_source(
                path, filename, vendor, databases[vendor], min(120, budget.remaining())
            )
            budget.remaining()
            path.rename(directory / filename)
            sources.append({**record, **retrieval, "path": "sources/" + filename})
    index = {
        "schema": 1,
        "architecture": options.arch,
        "inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "package_set_sha256": canonical_hash(rows),
        "source_set_sha256": canonical_hash(
            [
                {
                    key: item[key]
                    for key in ("filename", "vendor", "url", "sha256", "fingerprint")
                }
                for item in sources
            ]
        ),
        "packages": rows,
        "excluded_public_keys": excluded,
        "sources": sources,
    }
    (options.out / "inventory.tsv").write_bytes(inventory)
    pending = options.out / "index.json.partial"
    pending.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    pending.rename(options.out / "index.json")


if __name__ == "__main__":
    main()
