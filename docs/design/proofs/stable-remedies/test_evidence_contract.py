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
    assert "recommendation" not in proposal
    assert {
        item["source"] for item in proposal["historical_owned_provider_proposal"]
    } == {
        "sqlite",
        "glib",
        "coreutils",
    }
    decision = proposal["po_decision"]
    assert (
        decision["selected_route"] == "official_signed_stable_3.24_vendor_updates_only"
    )
    assert set(decision["additional_owned_source_families_rejected"]) == {
        "sqlite",
        "coreutils",
        "glib",
        "tar",
    }
    assert decision["decision_record_complete"] is True
    assert decision["remediation_complete"] is False
    assert decision["automatic_vendor_polling_authorized"] is False
    trigger = proposal["vendor_acceptance_trigger"]
    assert trigger["official_branch"] == "v3.24"
    assert trigger["official_signature_required"] is True
    assert trigger["actual_source_fix_equivalence_required"] is True
    assert trigger["final_image_file_binding_required"] is True
    assert trigger["required_native_architectures"] == ["amd64", "arm64"]
    assert set(trigger["complete_acceptance_required"]) == {
        "abi",
        "native_compatibility",
        "source",
        "advisory",
    }
    assert trigger["version_or_tracker_disappearance_is_clearance"] is False


def test_decision_preserves_exact_thirteen_unresolved_remedy_records():
    proposal = json.loads(
        EVIDENCE.with_name("stable-remedies-20261111.json").read_text()
    )
    expected = {
        "sqlite": {
            *(f"CVE-2026-{suffix}" for suffix in range(119371, 119380)),
            "CVE-2026-119591",
        },
        "coreutils": {"CVE-2026-56391", "CVE-2026-56392"},
        "glib": {"CVE-2026-58016"},
    }
    coverage = proposal["unresolved_remedy_tracking"]
    assert len(coverage) == 3
    assert {item["source"] for item in coverage} == set(expected)
    for item in coverage:
        assert set(item["cves"]) == expected[item["source"]]
        assert len(item["cves"]) == len(expected[item["source"]])
        assert (
            item["disposition"]
            == "BLOCKED_AWAITING_OFFICIAL_FIX_AND_COMPLETE_ACCEPTANCE"
        )
        assert item["issue_path"] == [
            "enhancedchannelmanager-evy2p",
            "enhancedchannelmanager-4izbc",
            "enhancedchannelmanager-g3vd3",
        ]
    assert sum(len(item["cves"]) for item in coverage) == 13
