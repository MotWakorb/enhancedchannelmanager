# Independent Alpine advisory denial seam

Beads: `enhancedchannelmanager-da8kc`, `enhancedchannelmanager-4izbc`, implementation
`enhancedchannelmanager-qoxxi`; coordination `enhancedchannelmanager-uv80n`.

## Implemented scope

`scripts/check_alpine_advisories.py` composes the existing complete Trivy validator,
live APK query/database source plan and four-response official advisory snapshot
consumer. It does not replace the original runtime/source checks. The current
result is **BLOCKED**, including for both preserved published 0066 inventories.

The producer seals exact original report, scanner/DB identity, image identity,
query/database inventory, snapshot index and trusted helper hashes. The consumer
requires the canonical producer-binding SHA256 independently from its input
directory. Both stages join the manifest, config, platform and rootfs identities;
the existing scan validator also checks every APK source/version/checksum row and
the full Python/Go detection inventories. Snapshot files are rechecked, not trusted
because their self-declared hashes match. The six-hour/future-clock rejection is
unchanged. No scan receipt is promoted to signed provenance by this seal.

**Trust boundary:** produce the binding immediately after trusted immutable-image
capture in an isolated job. Carry the binding digest in trusted job output or an
authenticated attestation. Calculating the expected digest from an untrusted
binding next to its files defeats the external anchor. Compromised runner/image
capture is not solved by a local hash. The producer can bind preserved historical
scans for diagnostic replay; that is not a new scan or corrected-image proof.

Enforcement tests in `backend/tests/unit/test_alpine_advisory_gate.py` cover:

- Actual complete recorded 0066 AMD64/ARM64 evidence (122 APK, 77 Python, 84 Rust,
  four Go rows each), retaining its original 11 MEDIUM/three LOW findings.
- Known affected sources with **empty vendor findings**, including a deliberately
  LOW-labelled real tracker advisory; score zero/null remains UNKNOWN.
- Nineteen historical source/version/CVE holds and four owned subjects surviving
  nonempty, resealed publisher truncation and nominal version changes.
- Wrong manifest/config/rootfs/architecture, incomplete query/database/scan
  inventories, substituted scanner bytes, stale/future snapshots, fabricated
  dispositions, and valid-looking resealed binding timestamps.
- The real CLI's nonzero exit and persisted BLOCKED report, not only its functions.

The existing full recorded decoder tests exercise real ORM duplicates, exact
stable-branch selection and the `cvss3` field's actual CVSS 4.0 vectors. Existing
snapshot tests reject concatenated gzip streams and bounded-body substitutions.

## Deliberately not clearance

There is **no discharge format or success verdict** yet. A source archive review,
version name, build receipt or a JSON `fixed` assertion cannot clear an owned
component. Exact source/patch/recipe/compile-feature/signed-provider/live-byte proof,
native regression results and independent review must first be implemented and
tested against the actual corrected final images. Pending applicability stays
pending even when a package disappears; operator workflows have no blanket waiver.

The original 19 findings and four owned subjects remain held pending that explicit
reconciliation. New exact `fixed:false` matches are retained independently of
scanner severity. Fixed-secdb records, including opaque aliases and version-zero
metadata, are preserved as **not clearance**; native APK version solving, CPE and
owned component coverage remain acceptance blockers. The gate does not assert
that the nineteen potential matches are nineteen demonstrated reachable exploits.

## Parent integration contract — not wired by this change

No production workflow, Dockerfile, source pin, SBOM, global version or provider
recipe is modified here. Parent integration must add mandatory paired consumers
and their attestation dependencies, not an optional diagnostic step. Run them after
the original native runtime/source and full scan checks so advisory failure cannot
prevent those checks executing. Preserve existing HIGH/CRITICAL/reviewed-P1 checks
and upload raw acquisition plus reports even on failure. Do not add
`continue-on-error`, a discharge switch or a conditional that omits the new jobs.

Example commands inside the trusted capture job (repeat for each architecture):

```bash
python scripts/alpine_advisory_snapshot.py \
  --output-dir "$PWD/advisories" --evidence-dir "$PWD/advisories.acquisition"

# Same original files supplied to both commands; no scan normalization/filtering.
binding_sha=$(python scripts/check_alpine_advisories.py bind \
  --report "$PWD/native-scan/findings.json" \
  --identity "$PWD/native-scan/identity.json" \
  --scanner "$PWD/native-scan/scanner.json" \
  --runtime-inventory "$PWD/native-scan/runtime-inventory.json" \
  --snapshot "$PWD/advisories" --architecture amd64 \
  --output "$PWD/advisory-binding.json")

# Retain binding_sha through a trusted channel, not from untrusted artifact content.
python scripts/check_alpine_advisories.py verify \
  --report "$PWD/native-scan/findings.json" \
  --identity "$PWD/native-scan/identity.json" \
  --scanner "$PWD/native-scan/scanner.json" \
  --runtime-inventory "$PWD/native-scan/runtime-inventory.json" \
  --snapshot "$PWD/advisories" --architecture amd64 \
  --binding "$PWD/advisory-binding.json" --binding-sha256 "$binding_sha" \
  --output "$PWD/advisory-result.json"
```

The verify command currently exits **1** with a detailed BLOCKED report. Bind's
exit zero means input binding succeeded, not security acceptance. Failed acquisition
or malformed evidence also exits nonzero; existing outputs are not overwritten.

## 2026-11-11 evidence

Ordinary official HTTPS collection started `2026-11-11T15:59:21.587009Z` and
succeeded for all four endpoints, with full bodies and response headers retained at
`/tmp/opencode/ecm-p1-advisory-official-live-20261111{,.acquisition}`. Snapshot index
SHA256: `bb094e368fda14767263798b48be4f3b38f4476e7e7dde8b38d80d753ff70a4e`.
This six-hour input is not reusable as a future fresh survey.

| Actual response | Items / unique CVEs | Stable raw / unique states | CVSS vector distribution |
|---|---:|---:|---|
| main | 185 / 185 | 292 / 292 | null 180; 3.1 five |
| community | 1994 / 1639 | 3847 / 3085 | null 1561; 3.1 368; 3.0 53; 4.0 twelve |

The old recorded community fixture has 1996 items. Its difference from the fresh
body was observed rather than treated as a parser error or ignored.

Both original published 0066 inventories from build-evidence at source
`95fc8e4e88c22744327de0ed75767ce5e8b28af9` passed the existing complete scan
validator. The new native AMD64 read-only/networkless CLI consumed those original
paired evidence files plus the fresh official snapshot and denied **both**:
19 current exact matches (18 UNKNOWN/one MEDIUM), 19 persistent holds, four owned
holds, zero structural scan failures, exit 1. This was ARM64 **artifact consumption
on AMD64**, not new ARM execution or a rebuild. Full command results are retained
under `/tmp/opencode/ecm-p1-advisory-gate-proof-20261111/actual-0066/`.

| Architecture | Actual manifest | Producer binding SHA256 |
|---|---|---|
| AMD64 | `sha256:54d87a26759e8f3fd6086200598a71b823c2414a983908fbfe3d0dd7b25e4b10` | `eb79e74370701f0e5183214555b25209b051a9a276dfb0c049f6cf3ab19049cc` |
| ARM64 | `sha256:a402552e4eaaa26511d8858007043dada96d3d1813c72689a1337348d1c4ffb2` | `7656a10137b07e4ef69922fb370ab1ef17b086d523f0010692d863a23d0e3876` |

Test-first initial run: 16 missing-module errors before implementation. A later
metadata regression was RED (missing original severity). The aligned native Alpine
subset then passed 414 tests, repeated as receipt `ecm-gate-kxw7f8os`, with no network, read-only
source/rootfs, UID/GID 3000, two CPUs, 3 GiB, dropped capabilities and all 74 backend
pins checked. This is a **subset, not the full backend gate**. Four injected
dangerous mutants each caused the intended regression failure: dropping known
holds, emptying independent matches, ignoring snapshot age and ignoring the
external binding anchor. Proof logs and receipts are retained in the same private
proof directory. Source/feature reconciliation and actual paired corrected-image
acceptance remain unfinished; neither owning P1 is closed.
