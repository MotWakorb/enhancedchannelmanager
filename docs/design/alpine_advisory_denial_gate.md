# Independent Alpine advisory denial seam

Beads: `enhancedchannelmanager-da8kc`, `enhancedchannelmanager-4izbc`, implementation
`enhancedchannelmanager-qoxxi`; coordination `enhancedchannelmanager-uv80n`.

## Implemented scope

`scripts/check_alpine_advisories.py` composes the existing Trivy validator,
live APK query/database source plan and four-response official advisory snapshot
consumer. It does not replace the original runtime/source checks. The current
result is **BLOCKED**, including for both preserved published 0066 inventories.

The producer seals exact original report, scanner/DB identity, image identity,
query/database inventory, snapshot index and trusted helper hashes. The consumer
requires the canonical producer-binding SHA256 independently from its input
directory. Both stages join the manifest, config, platform and rootfs identities;
the existing scan validator also checks every APK source/version/checksum row and
nonempty, structurally valid Python/Go detection rows. **That validator does not
independently prove Python/Go catalogue completeness**: a newly trusted-producer-bound
77-to-one Python / four-to-one Go truncation passes its language checks without
the separate physical capture described below. This is an
inventory-assurance gap, not a current clearance bypass: the verdict remains BLOCKED.
Snapshot files are rechecked, not trusted
because their self-declared hashes match. The six-hour/future-clock rejection is
unchanged. No scan receipt is promoted to signed provenance by this seal.

**Trust boundary:** produce the binding immediately after trusted immutable-image
capture in an isolated job. Carry the binding digest in trusted job output or an
authenticated attestation. Calculating the expected digest from an untrusted
binding next to its files defeats the external anchor. Compromised runner/image
capture is not solved by a local hash. The producer can bind preserved historical
scans for diagnostic replay; that is not a new scan or corrected-image proof.

Execute the consumer/interpreter only from a reviewed, clean, immutable read-only
checkout, with trusted import paths and no untrusted bytecode caches. Never execute
scripts copied into the artifact/input directory. `helper_digests()` now seals
**every local Python module under `scripts/`**, a deliberate superset of the
surveyed import closure, not just two newly discovered missing dependencies.
Dependency-drift tests alter actual module bytes after binding and require
rejection. This does not protect a compromised interpreter or a consumer modified
to disable its own checks; the independent trusted job/code anchor is essential.

### Bounded independent physical language capture

`scripts/alpine_language_inventory.py` takes **no report or package-name list**.
In the trusted capture job, after independently resolving the immutable manifest,
config, platform and rootfs, run it against that image's read-only filesystem:

```bash
language_sha=$(python scripts/alpine_language_inventory.py \
  --root / --identity /trusted-job/identity.json \
  --output /trusted-job-output/physical-language.json)
```

The root must be read-only. The supplied identity is a trusted acquisition-job
input, not identity discovered/authenticated by this script. The job must compare
the actual inspected config/platform/**all** rootfs diff IDs with the resolved
manifest before capture. Mount the reviewed helper checkout read-only outside the
capture roots; do not mount artifact/data volumes over or inside those roots.
Preserve `language_sha` as trusted job output, not as a self-asserted
digest in an untrusted artifact. Supply **both** `--language-capture` and
`--language-capture-sha256` to **both** advisory `bind` and `verify` commands.

The independently derived catalogue consists of:

- Every physical `*.dist-info/METADATA` Name/Version/path in the reviewed CPython
  3.12 venv, official-base site-packages and retained wheel-metadata roots. No
  scanner counts or expected package names are hardcoded. The full trees' code,
  metadata and RECORD bytes, inode/device/mode/time identities are hashed too.
  Unreadable paths, missing installed distribution metadata, symlinked runtime files and
  unreviewed egg-info directories deny rather than silently shrink the catalogue.
  Actual retained wheel-notice directories can be license-only; their bytes are
  still sealed, but their directory names are not interpreted as installations.
- Gosu's actual native ELF64 inline Go buildinfo: main module, every embedded
  dependency and compiler-derived stdlib entry. Its binary hash must match the
  actual build receipt; modules/compiler must match the reviewed pinned recipe
  and receipt. Physical retained gosu/source/toolchain archive bytes are hashed,
  and source/recipe files are sealed. Retained source symlinks bind raw link text
  and are never followed. This is **not** a new signature verification: the
  existing independent signed-provider/runtime-source gate remains required.

The scanner join is exact, including Python metadata path and version, Go target
and root-module role. False extra rows, duplicates, wrong versions, 77→1/76 Python
and 4→1/3 Go truncations deny, including before a newly trusted producer binding.
An altered capture cannot be re-sealed using the original external capture anchor;
an authenticated capture for a different rootfs cannot join this image.

**Scope is deliberately not whole-image discovery.** These are three reviewed
Python locations and gosu, not every possible Python/Go installation or embedded
binary in an arbitrary image. No physical ARM64 capture was performed. Missing
capture retains the legacy denial-only diagnostic mode, explicitly reported as
`MINIMUM_COVERAGE_ONLY`; a joined capture reports
`SCOPED_PHYSICAL_CATALOGUE_JOINED`. Both report `complete_image_catalogue:false`
and remain BLOCKED. Neither is solved P1 acceptance or permission to omit the
original whole-image scans/source checks. The 84 recorded Rust detections are
unchanged; this addition does not claim a new Rust completeness check.

Enforcement tests in `backend/tests/unit/test_alpine_advisory_gate.py` cover:

- Actual complete recorded 0066 AMD64/ARM64 evidence (122 APK, 77 Python, 84 Rust,
  four Go rows each), retaining its original 11 MEDIUM/three LOW findings.
- Known affected sources with **empty vendor findings**, including a deliberately
  LOW-labelled real tracker advisory; score zero/null remains UNKNOWN.
- Nineteen historical source/version/CVE holds and four owned subjects surviving
  nonempty, resealed publisher truncation and nominal version changes.
- Wrong manifest/config/rootfs/architecture, incomplete query/database/scan
  APK inventories, substituted scanner bytes, stale/future snapshots, fabricated
  dispositions, and valid-looking resealed binding timestamps.
- The real CLI's nonzero exit and persisted BLOCKED report, not only its functions.

The existing full recorded decoder tests exercise real ORM duplicates, exact
stable-branch selection and the `cvss3` field's actual CVSS 4.0 vectors. Existing
snapshot tests reject concatenated gzip streams and bounded-body substitutions.

## Deliberately not clearance

**PO remediation policy (2026-11-11): official vendor updates only for the
additional SQLite, coreutils, GLib and GNU tar families.** No new owned providers,
expanded runtime cohort or caller-selected owned-family disposition is authorized.
Future positive acceptance for those records requires actual fixed official
stable-vendor outputs, signed APK/source evidence and native final-image proofs.
Those corrected official images are not currently available in this evidence;
none is fabricated by this helper. Existing separately approved libxml2-provider
backports are outside this branch and still require parent review and native
proofs. The current held 0066 image remains BLOCKED. This denial prototype must
not be wired into production as solved all-acceptance or folded into the parent's
separately prepared application candidate without its outstanding proofs.

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
`95fc8e4e88c22744327de0ed75767ce5e8b28af9` passed the existing APK-complete scan
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

## Independent review remediation evidence (2026-11-11)

Review implementation bead: `enhancedchannelmanager-9tr15`. Original review
reproducer: `/tmp/opencode/ecm-advisory-security-review-3dv1fx34/repro.py`.

The cached AMD64 image is the **actual published 0066 config**
`sha256:ca2fb0b9c99667df56641eaf0427535079f2b8dd66bfbde2e951603a4d4958a8`.
Docker inspection matched its platform and every rootfs diff ID to the original
published AMD64 scan identity. No tag/version guess, image pull, build or prune
was used. The capture ran in that immutable image, UID/GID 3000, network disabled,
read-only root/source, dropped capabilities, two CPUs, 3 GiB, private writable
output. It derived 77 metadata entries (75 venv + one base pip + one retained
cryptography metadata) and four Go entries from physical bytes, not the report.
It hashed 9,358 Python-tree files / 185,714,319 bytes and the retained gosu source
tree; binary SHA256 is
`ce3d453e89416682cc72cb37bfe2ea9df15fb29c21f1c566341f6abe504415d5`.
The capture's canonical digest is
`3e5bdcc1b15c13558651e19a6739d3ed1666048d85ebaa185c7fdd9a67117e8e`.
The committed compressed physical capture and byte-hash-matched gosu ELF are
historical regression fixtures, **not** newly corrected image or source-fix proof.
Corresponding gosu/Go source URLs and SHA256s are the existing reviewed
`docker/alpine-native/gosu.json` pins (gosu commit
`6456aaa0f3c854d199d0f037f068eb97515b7513`, compiler Go 1.26.10); original
Apache-2.0 gosu/module and Go BSD license notices remain in the image's retained
`/usr/share/ecm/sources/owned/gosu/` source tree. The ELF fixture is not executed by
the tests and is not a new runtime dependency.
Private logs/outputs: `/tmp/opencode/ecm-p1-advisory-review-fix-proof-20261111/`.

The final real capture → producer `bind` → `verify` CLI replay used that actual
published AMD64 image and the full fresh official snapshot (capture start
`2026-11-11T15:59:21.587009Z`, not fetched again). It retained 19 exact current
matches, 19 persistent overlapping holds and four owned holds, with zero structural
scan failures and exit 1. Producer binding digests for this reviewed helper tree:
AMD64 `6f14c57a496ed5615cefcf3ac6a427f41d3015a95a4f35289c88068f20910a5f`,
ARM64 `f1901cfc752aa2375fd11a89f3bd87ae66d7975a59771a140eedd5fed710de86`.
The latter is explicitly minimum-language-coverage ARM artifact consumption on
AMD, **not** ARM execution/capture. Four actual CLI producer negatives rejected
77→1/76 and 4→1/3 report truncations before producing any binding. See
`actual-final-02/actual-final-summary.json`. Original 11 MEDIUM/three LOW findings
per architecture were not altered. The old 8d3790fe binding digests above remain
historical receipts only; changed helper bytes/schema require newly trusted
producer bindings, never re-sealing an untrusted artifact to match itself.

Four additional semantic mutants caused the intended regression failures:
omitting the transitive helper closure, ignoring physical catalogue equality,
omitting inode/time identities from the physical tree seal, and silently skipping
unreadable directories. See `mutation-results.json` and the four `mutant-*.log`
files. This demonstrates the negative controls, not source-fix acceptance.

The final aligned native Alpine tooling **subset** passed **481 tests**, receipt
`ecm-gate-s_yeojms/receipt.json`, log `green-final-subset.log`, with all 74 backend
pins verified. This covers the advisory/physical-capture/snapshot/native-scan/APK
inventory modules, not the full backend suite. Ruff check/format and diff whitespace
checks passed. Initial RED evidence is in `red-new-tests.log`; intermediate wrong
test-path invocations and a permission-test targeting correction are retained, not
reported as successful gates. No corrected-image or native ARM acceptance is
implied by this subset.

### Exact physical inputs still needed from other work owners

This is an input handoff, **not an accepted discharge JSON vocabulary**. There is
still no disposition/waiver/fixed-boolean interface. Emit original physical bytes
and existing receipts, with their independently authenticated job digests:

| Owner/input | Required physical subject and binding | Pending consumer obligation |
|---|---|---|
| Each corrected native image | Original per-architecture manifest/config/rootfs identity, full unfiltered report/scanner DB and full live APK query/installed database; physical language capture from the same immutable root | Re-run all original gates; no AMD artifact-consumption claim may stand in for ARM execution |
| Official-vendor-only remediation | Actual fixed stable-vendor SQLite/coreutils/GLib/tar APKs, official signed repository metadata/keys, corresponding official source/patch recipes and native installed-image evidence | No new owned provider or nominal owned-version substitution; until vendor outputs exist, keep every affected hold pending |
| Native APK comparison | Actual shipped `/sbin/apk` bytes/provider/signature identity, exact installed source/full version from the validated query/database, full fresh fixed-secdb bytes; native `apk version -t` stdout/stderr/exit against each exact fixed version | Use native APK ordering, including epochs/revisions/pre-release syntax; never lexical/Python-semver comparison; version `0` is publisher metadata, not a fix |
| Fixed-secdb aliases | Preserve the exact original `pkg.name`, `secfixes` version key and each original advisory array string from all relevant rows | Retain opaque `ALPINE-*`, GHSA and composite aliases; ambiguous or unmatched aliases remain pending, never dropped or inferred to be fixed |
| CPE applicability | Full original tracker vulnerability item (`id`, `cvss3`, `ref`, `state`, `cpeMatch`, context/type/description), with source body hash; actual component/version/features for every reviewed owned subcomponent | Existing exact package-state matching is NOT complete CPE applicability; absent/empty/unknown CPE data is not nonapplicability |
| Per-advisory source fix | Exact original source archive/hash, full ordered patch bytes/hashes and application receipts, reviewed primary correction, recipe/helper bytes, compile flags/features and native regression logs, existing signed provider APK/public key/signature verification output | Independently review each source/patch/feature claim against code; no caller-selected disposition, renamed package or build receipt clears a hold |
| Delivered fix join | Original signed provider package payload, actual installed path/ELF/link/inode/hash inventory and loaded-runtime witnesses in **both** final native images, all joined to reviewed source/recipe digests and immutable image anchors | Prove the corrected source/features reach the actual live bytes; synthetic protocol-positive tests cannot substitute |

The recorded fixed-secdb parser already retains raw aliases and zero-version
markers, but neither it nor this review change implements native version solving,
complete CPE matching or source-proof discharge. Until those proofs and consumers
exist, all nineteen historical holds and four owned holds persist. The nineteen
current matches overlap those historical holds; they are not 38 vulnerabilities
or nineteen demonstrated reachable exploits. Ordinary snapshot freshness remains
six hours, checked again at verification time.

The full recorded CPE field survey (not a fresh HTTPS fetch) found 392 main and
5,427 community match objects, each with exactly `@context`, `cpeUri`, `id`,
`maximumVersion`, `maximumVersionOp`, `minimumVersion`, `minimumVersionOp`,
`package`, `type`, `vuln`. Actual `cpeUri` may be empty, a minimum may be null,
and a community example targets Android, so no universal native applicability or
guessed CPE-version vocabulary is assumed. The complete distribution and example
objects are retained in `cpe-field-survey.log` in the private proof directory.
