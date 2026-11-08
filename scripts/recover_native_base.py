"""Recover original SUSE base bytes and separately pinned source archives.

The reviewed vendor index binds the retained child manifests/configs. Public
GHCR is only a carrier for exact original compressed layers. No application
rootfs is substituted for the base, and no RPM payload/SPEC is extracted or
executed. No output image is published or selected by a floating tag.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import stat
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

INDEX_DIGEST = "sha256:c807eabd03acb6f5dfb6a23fd58ed93700d11283a255cd18184f57815e19fc55"
BASE = "registry.opensuse.org/opensuse/tumbleweed@" + INDEX_DIGEST
REPOSITORY = "motwakorb/enhancedchannelmanager"
MAX_LAYER = 64 * 1024**2
MAX_EXPANDED = 512 * 1024**2
CHUNK = 1024**2
SOURCE_DONORS = {
    "amd64": {
        "manifest": "sha256:13cbcfffff8d5091f857583656b91ed8fe40ad3b472b54f7a67ab50e22846c01",
        "digest": "sha256:d3a9ad4e24c03b7ec0db6b60bc9d15a2e49d01d0cac170fd16ee78b7d650ba11",
        "size": 951201446,
        "index_sha256": "e2d58d8cdc0436c3e118b82192817f8caee105dae78241e76cc2e481bc5ded1b",
        "rpm_arch": "x86_64", "source_count": 156,
    },
    "arm64": {
        "manifest": "sha256:98722bea0ddb8f7c52398151751b89d4173b775f6612afc5bb75aa133d091d48",
        "digest": "sha256:11a6121cda7cb49ac6dbc4b46da7dd3dc040a752706cc92c11bcca110367aaf0",
        "size": 950517590,
        "index_sha256": "e8bbf1d7381b76dd9f45f2078be6609ca79c63333becd92651b3d67ebae2f013",
        "rpm_arch": "aarch64", "source_count": 155,
    },
}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify_bytes(data, descriptor):
    check(
        type(descriptor["size"]) is int
        and len(data) == descriptor["size"]
        and digest(data) == descriptor["digest"],
        "recorded base identity mismatch",
    )


def load_metadata(path, architecture):
    check(architecture in ("amd64", "arm64"), "unsupported base architecture")
    check(path.stat().st_size <= 64 * 1024, "oversized base metadata")
    data = json.loads(path.read_text())
    index_bytes = data["index"].encode()
    check(digest(index_bytes) == INDEX_DIGEST, "original base index identity mismatch")
    index = json.loads(index_bytes)
    selected = [
        item for item in index["manifests"]
        if item["platform"]["architecture"] == architecture
        and item["platform"]["os"] == "linux"
    ]
    check(len(selected) == 1, "missing/ambiguous base architecture")
    descriptor = selected[0]
    manifest_bytes = data[architecture + "_manifest"].encode()
    config_bytes = data[architecture + "_config"].encode()
    verify_bytes(manifest_bytes, descriptor)
    manifest = json.loads(manifest_bytes)
    verify_bytes(config_bytes, manifest["config"])
    config = json.loads(config_bytes)
    check(config["os"] == "linux" and config["architecture"] == architecture,
          "base config architecture mismatch")
    check(len(manifest["layers"]) == len(config["rootfs"]["diff_ids"]) == 1,
          "unexpected original base layer graph")
    return descriptor, manifest_bytes, config_bytes


def verify_layer(path, descriptor, diff_id):
    check(stat.S_ISREG(path.lstat().st_mode), "base layer must be a regular file")
    check(type(descriptor["size"]) is int and 0 < descriptor["size"] <= MAX_LAYER,
          "invalid base layer size")
    check(path.stat().st_size == descriptor["size"], "base layer size mismatch")
    with path.open("rb") as stream:
        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    check(actual == descriptor["digest"], "compressed base layer identity mismatch")
    expanded, size = hashlib.sha256(), 0
    with gzip.open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            size += len(chunk)
            check(size <= MAX_EXPANDED, "expanded base layer exceeds bound")
            expanded.update(chunk)
    check("sha256:" + expanded.hexdigest() == diff_id, "base layer diff ID mismatch")


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check(newurl.startswith("https://"), "non-HTTPS base layer redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def public_registry_request(suffix):
    # Anonymous read-only public pull token, never an ambient GitHub/user secret.
    opener = urllib.request.build_opener(PublicRedirects())
    url = "https://ghcr.io/token?service=ghcr.io&scope=repository:" + REPOSITORY + ":pull"
    with opener.open(url, timeout=60) as response:
        token_bytes = response.read(64 * 1024 + 1)
    check(len(token_bytes) <= 64 * 1024, "oversized public registry token response")
    token = json.loads(token_bytes)["token"]
    url = "https://ghcr.io/v2/" + REPOSITORY + "/" + suffix
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.oci.image.manifest.v1+json",
    })
    return opener, request


def download_layer(descriptor, destination):
    opener, request = public_registry_request("blobs/" + descriptor["digest"])
    total, deadline, next_report = 0, time.monotonic() + 1200, 128 * 1024**2
    with opener.open(request, timeout=60) as response, destination.open("xb") as stream:
        while chunk := response.read(CHUNK):
            total += len(chunk)
            check(total <= descriptor["size"] and time.monotonic() <= deadline,
                  "base layer transfer exceeds size/time bound")
            stream.write(chunk)
            if descriptor["size"] > MAX_LAYER and total >= next_report:
                print("Recovered " + str(total) + "/" + str(descriptor["size"]) + " carrier bytes", flush=True)
                next_report += 128 * 1024**2
    check(total == descriptor["size"], "incomplete base layer transfer")


def blob_path(directory, identity):
    check(re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is not None,
          "invalid base blob identity")
    return directory / "blobs/sha256" / identity.split(":")[1]


def recover_layout(metadata, architecture, output, *, fetch=download_layer):
    descriptor, manifest_bytes, config_bytes = load_metadata(metadata, architecture)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("refusing to overwrite base recovery output")
    check(not any(c in str(output) for c in "\n\r"), "unsafe output path")
    manifest, config = json.loads(manifest_bytes), json.loads(config_bytes)
    layer = manifest["layers"][0]
    with tempfile.TemporaryDirectory(prefix=".base-recovery-", dir=output.parent) as name:
        staging = Path(name) / "layout"
        (staging / "blobs/sha256").mkdir(parents=True)
        target = blob_path(staging, layer["digest"])
        fetch(layer, target)
        verify_layer(target, layer, config["rootfs"]["diff_ids"][0])
        blob_path(staging, descriptor["digest"]).write_bytes(manifest_bytes)
        blob_path(staging, manifest["config"]["digest"]).write_bytes(config_bytes)
        (staging / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
        # Complete one-architecture layout. Do not advertise the six other
        # vendor-index platforms whose blobs were not recovered by this command.
        (staging / "index.json").write_text(json.dumps({
            "schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [descriptor],
        }) + "\n")
        receipt = {
            "original_base": BASE, "architecture": architecture,
            "child": descriptor["digest"], "config": manifest["config"]["digest"],
            "layer": layer["digest"], "diff_id": config["rootfs"]["diff_ids"][0],
            "layer_carrier": "https://ghcr.io/v2/" + REPOSITORY + "/blobs/" + layer["digest"],
            "scope": "Exact original base bytes only; not package/source closure or security clearance.",
        }
        (staging / "recovery.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if output.exists() or output.is_symlink():
            raise FileExistsError("refusing to overwrite base recovery output")
        staging.rename(output)
    return BASE + "=oci-layout://" + str(output) + "@" + descriptor["digest"]


def unpack_source_layer(layer, output, specification):
    """Copy only pinned regular RPM archive files; never extract their payloads."""
    prefix = "usr/share/ecm/sources/rpm/"
    records, seen, total = None, set(), 0
    with tarfile.open(layer, "r|gz") as archive:
        for member in archive:
            if member.name == prefix + "index.json":
                check(records is None and member.isfile() and member.size <= 8 * 1024**2,
                      "invalid/duplicate source index")
                data = archive.extractfile(member).read()
                check(hashlib.sha256(data).hexdigest() == specification["index_sha256"],
                      "source index identity mismatch")
                index = json.loads(data)
                check(index["architecture"] == specification["rpm_arch"],
                      "source index architecture mismatch")
                records = {}
                for row in index["sources"]:
                    name = row["filename"]
                    check(re.fullmatch(r"[A-Za-z0-9_+~.-]+\.src\.rpm", name) is not None
                          and name not in records and row["path"] == "sources/" + name,
                          "unsafe/duplicate source name")
                    check(type(row["size"]) is int and 0 < row["size"] <= 256 * 1024**2
                          and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None,
                          "invalid source archive descriptor")
                    records[name] = row
                check(len(records) == specification["source_count"], "incomplete source index")
                (output / "index.json").write_bytes(data)
                (output / "sources").mkdir()
            elif member.name.startswith(prefix + "sources/") and not member.isdir():
                check(records is not None, "source archive precedes pinned index")
                name = member.name[len(prefix + "sources/"):]
                check(name in records and name not in seen and member.isfile(),
                      "unindexed/duplicate/nonregular source archive")
                expected = records[name]
                check(member.size == expected["size"], "source archive size mismatch")
                total += member.size
                check(total <= 2 * 1024**3, "source set exceeds byte bound")
                hashed = hashlib.sha256()
                with archive.extractfile(member) as inp, (output / "sources" / name).open("xb") as dest:
                    while chunk := inp.read(CHUNK):
                        hashed.update(chunk)
                        dest.write(chunk)
                check(hashed.hexdigest() == expected["sha256"], "source archive identity mismatch")
                seen.add(name)
    check(records is not None and seen == set(records), "incomplete source archive set")


def recover_sources(architecture, output):
    check(architecture in SOURCE_DONORS, "unsupported source architecture")
    specification = SOURCE_DONORS[architecture]
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("refusing to overwrite source recovery output")
    check(not any(c in str(output) for c in "\n\r"), "unsafe output path")
    opener, request = public_registry_request("manifests/" + specification["manifest"])
    with opener.open(request, timeout=60) as response:
        body = response.read(64 * 1024 + 1)
    check(len(body) <= 64 * 1024 and digest(body) == specification["manifest"],
          "source carrier manifest identity mismatch")
    matches = [row for row in json.loads(body)["layers"] if row["digest"] == specification["digest"]]
    check(len(matches) == 1 and matches[0]["size"] == specification["size"],
          "source carrier layer binding mismatch")
    with tempfile.TemporaryDirectory(prefix=".source-recovery-", dir=output.parent) as name:
        staging = Path(name) / "sources"
        staging.mkdir()
        layer = Path(name) / "source-layer.tar.gz"
        print("Recovering exact " + architecture + " source layer (" + str(specification["size"]) + " bytes)", flush=True)
        download_layer(specification, layer)
        with layer.open("rb") as stream:
            actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        check(actual == specification["digest"], "compressed source carrier identity mismatch")
        unpack_source_layer(layer, staging, specification)
        (staging / "recovery.json").write_text(json.dumps({
            "carrier_repository": REPOSITORY, "carrier_source_commit": "0bc126006d96c03637cb9e1b707ec04aca2d5804",
            "architecture": architecture, **specification,
            "scope": "Transport/hash recovery only; current collector must repeat vendor signature/header/payload verification.",
        }, indent=2) + "\n")
        if output.exists() or output.is_symlink():
            raise FileExistsError("refusing to overwrite source recovery output")
        staging.rename(output)
    return "ecm-native-sources=" + str(output)


def write_compose_context(context, path, sources_context=None):
    path.write_text(json.dumps({
        "services": {"ecm": {"build": {"additional_contexts": [
            context, *([sources_context] if sources_context else []),
        ]}}}
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--compose-output", type=Path)
    parser.add_argument("--with-sources", action="store_true")
    args = parser.parse_args()
    metadata = Path(__file__).resolve().parents[1] / "docker/native/base-recovery.json"
    context = recover_layout(metadata, args.arch, args.output)
    sources_context = None
    if args.with_sources:
        sources_context = recover_sources(args.arch, args.output.with_name(args.output.name + "-sources"))
    if args.compose_output is not None:
        write_compose_context(context, args.compose_output, sources_context)
    if args.github_output is not None:
        with args.github_output.open("a") as stream:
            stream.write("context=" + context + "\n")
            if sources_context:
                stream.write("sources_context=" + sources_context + "\n")
    print(json.dumps({"context": context, "sources_context": sources_context, "original_base": BASE}))


if __name__ == "__main__":
    main()
