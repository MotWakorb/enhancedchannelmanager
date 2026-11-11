"""Reconcile recorded real vendor rows, never infer clearance from a version."""

import gzip
import hashlib
import json
from pathlib import Path

EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "evidence/stable-remedies-20261111.receipts.json.gz"
)


def capsule():
    blob = EVIDENCE.read_bytes()
    proposal = json.loads(
        EVIDENCE.with_name("stable-remedies-20261111.json").read_text()
    )
    assert hashlib.sha256(blob).hexdigest() == proposal["receipts_sha256"]
    assert len(blob) == proposal["receipts_bytes"]
    return json.loads(gzip.decompress(blob))


def stanzas(text):
    """Read original APK stanza bytes; repeated file-list fields are irrelevant."""
    return [
        dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        for block in text.split("\n\n")
        if block.strip()
    ]


def test_recorded_0066_origins_and_control_identities_match_signed_vendor():
    data = capsule()
    expected_inventories = {
        "amd64": "554d7074c502ae355c96e142181a1fed61a7947f58a6ea57b4f22d81070da013",
        "arm64": "298e8aca99d8c23fed97bd77976b94168da49e04fd534ee49e7ec314a817bec0",
    }
    for arch, native in [("amd64", "x86_64"), ("arm64", "aarch64")]:
        record = data["inventories"][arch]
        assert (
            hashlib.sha256(record["text"].encode()).hexdigest()
            == (expected_inventories[arch])
        )
        inventory = json.loads(record["text"])
        query = inventory["capture"]["packages"]
        installed = stanzas(inventory["capture"]["installed_database"])
        index_record = data["raw_target_indexes"][native + "-main-APKINDEX.tar.gz"]
        vendor = stanzas(index_record["target_stanzas"])
        selected = [
            row for row in installed if row.get("o") in {"sqlite", "glib", "coreutils"}
        ]
        assert {row["P"] for row in selected} == {
            "sqlite-libs",
            "glib",
            "coreutils",
            "coreutils-env",
            "coreutils-fmt",
            "coreutils-sha512sum",
        }
        for row in selected:
            matches = [item for item in vendor if item["P"] == row["P"]]
            assert len(matches) == 1
            # Control checksum and source commit, not a newer version string.
            assert {key: row[key] for key in ("P", "V", "C", "c", "o", "A")} == {
                key: matches[0][key] for key in ("P", "V", "C", "c", "o", "A")
            }
            queried = [item for item in query if item["name"] == row["P"]]
            assert len(queried) == 1
            assert queried[0]["origin"] == row["o"]
            assert queried[0]["version"] == row["V"]


def test_signature_instrument_positive_and_corrupt_negative_receipts():
    data = capsule()
    signatures = json.loads(data["records"]["index-signatures.json"]["text"])
    assert len(signatures["records"]) == 4
    for record in signatures["records"]:
        assert record["exit_code"] == 0
        assert (
            record["sha256"]
            == data["raw_target_indexes"][record["file"]]["signed_archive_sha256"]
        )
    assert signatures["negative_control"]["exit_code"] != 0


def test_recorded_unknown_scores_are_not_low_or_fixed():
    data = capsule()
    items = data["tracker_items"]["main"]["selected_original_items"]
    assert len(items) == 15  # 13 remedy findings + chroot + absent SQLite CLI
    for item in items:
        assert item["cvss3"] == {"score": 0.0, "vector": None}
        for state in item["state"]:
            if state["repo"] == "3.24-main":
                assert state["fixed"] is False
    # Complete acquisition field distributions are separate from these excerpts.
    survey = json.loads(data["records"]["field-survey.json"]["text"])
    assert (
        survey["trackers"]["3.24-community"]["cvss_vector_versions"]["CVSS:4.0"] == 12
    )
    assert survey["secdb"]["main"]["package_count"] == 290
    assert survey["secdb"]["community"]["package_count"] == 489


def test_source_port_retains_upstream_regression_and_all_corrections():
    data = capsule()
    baseline = json.loads(data["records"]["sqlite-loaded-baseline.json"]["text"])
    published = json.loads(data["records"]["sqlite-loaded-published0066.json"]["text"])
    assert baseline == published
    assert set(baseline["loaded_files"].values()) == {
        "4290ff1e86e8c9204789104aed2a2eb562d7292ed9544c5ba235759ea1ff3281"
    }
    proposal = json.loads(
        EVIDENCE.with_name("stable-remedies-20261111.json").read_text()
    )
    for name, digest in proposal["proposal_patch_sha256"].items():
        assert (
            hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            == digest
        )
    aggregate = json.loads(data["records"]["aggregate-port-verification.json"]["text"])
    assert len(aggregate) == 2
    for port in aggregate:
        assert port["exit_code"] == 0
        assert port["reverse_dryrun"]["exit_code"] == 0
        assert port["all_targets_byte_identical_to_previously_probed_sources"] is True
    ports = json.loads(data["records"]["coreutils-source-test-port.json"]["text"])
    assert len(ports["records"]) == 4
    assert all(record["exit_code"] == 0 for record in ports["records"])
    for filename in [
        "src/uniq.c",
        "src/unexpand.c",
        "tests/uniq/uniq.pl",
        "tests/unexpand/mb.sh",
    ]:
        source = data["source_files"]["coreutils-port/" + filename]
        assert (
            hashlib.sha256(source["text"].encode()).hexdigest()
            == ports["witnesses"][filename]["after_sha256"]
        )
    for source in data["source_files"].values():
        assert hashlib.sha256(source["text"].encode()).hexdigest() == source["sha256"]


def test_no_aggregate_clearance_or_maintenance_approval_is_implied():
    data = capsule()
    snapshot = json.loads(data["records"]["advisories/index.json"]["text"])
    assert snapshot["full_runtime_advisory_acceptance"] is False
    proposal = json.loads(
        EVIDENCE.with_name("stable-remedies-20261111.json").read_text()
    )
    assert proposal["additional_provider_maintenance_approved"] is False
    assert proposal["production_wiring_changed"] is False
    assert proposal["arm64_prototype_execution"] is False
    assert proposal["source_advisory_clearance"] is False
    assert {item["source"] for item in proposal["recommendation"]} == {
        "sqlite",
        "glib",
        "coreutils",
    }
