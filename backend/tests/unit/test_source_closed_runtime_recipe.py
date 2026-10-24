"""The native runtime delivers the sources of its actual installed graph."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = (
    "registry.opensuse.org/opensuse/tumbleweed@sha256:"
    "c807eabd03acb6f5dfb6a23fd58ed93700d11283a255cd18184f57815e19fc55"
)


def _stages():
    spec = importlib.util.spec_from_file_location(
        "native_recipe_sbom", ROOT / "scripts/generate_sbom.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._docker_stages((ROOT / "Dockerfile").read_text(), "Dockerfile")


def test_native_runtime_uses_reviewed_shared_graph_and_separate_source_builder():
    stages = _stages()
    assert [stage["alias"] for stage in stages] == [
        "frontend-builder",
        "native-builder",
        "runtime-base",
        "python-builder",
        "resdet-builder",
        "source-builder",
        None,
    ]
    assert [stage["base"] for stage in stages[1:]] == [
        BASE,
        BASE,
        "runtime-base",
        "runtime-base",
        "native-builder",
        "runtime-base",
    ]


def test_source_collection_uses_runtime_inventory_and_final_image_contains_sources():
    stages = _stages()
    source = next(stage for stage in stages if stage["alias"] == "source-builder")
    copies = [args for kind, args, _ in source["instructions"] if kind == "COPY"]
    assert (
        "--from=runtime-base /tmp/runtime-inventory.tsv /tmp/runtime-inventory.tsv"
        in copies
    )
    commands = [args for kind, args, _ in source["instructions"] if kind == "RUN"]
    assert len(commands) == 1
    assert "--inventory /tmp/runtime-inventory.tsv" in commands[0]
    final = [(kind, args) for kind, args, _ in stages[-1]["instructions"]]
    required = [
        "--from=source-builder /tmp/rpm-source-output/ /usr/share/ecm/sources/rpm/",
        "--from=native-builder /tmp/native-output/sources/ /usr/share/ecm/sources/owned/",
        "--from=resdet-builder /tmp/resdet-work/resdet.tar.gz /usr/share/ecm/sources/resdet/resdet.tar.gz",
        "docker/python-third-party/notices/ /usr/share/doc/ecm/python-third-party/",
        "docker/python-third-party/sources/ /usr/share/ecm/sources/python/",
        "docker/python-third-party/artifact-index/ /usr/share/doc/ecm/python-third-party/artifact-index/",
    ]
    tail = final.index(
        (
            "COPY",
            "--from=resdet-builder /tmp/resdet-output/resdet /usr/local/bin/resdet",
        )
    )
    for arguments in required:
        assert final.count(("COPY", arguments)) == 1
        assert final.index(("COPY", arguments)) < tail
