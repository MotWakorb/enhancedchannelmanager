# Maintained SQLite, coreutils and GLib remedies

**2026-11-11 · evy2p · PO decided: official vendor updates only.** Coordination:
`enhancedchannelmanager-uv80n`. Baseline is published, working 0066 commit
`95fc8e4e88c22744327de0ed75767ce5e8b28af9`, not the older investigation's images.
Calibrated for **small-team ci-release**, with home-lab data/DBAS compatibility.

## Accepted PO decision: vendor-only, remediation still blocked

The PO explicitly chose **official signed stable Alpine 3.24 vendor updates
only** and **rejected maintenance of four additional owned source families:
SQLite, coreutils, GLib and tar**. The coordinating parent acknowledged this
choice. Do not implement or wire those additional owned providers. No automatic
vendor polling or repeated index acquisition is authorized; the retained survey
below is sufficient for this decision record.

The decision-record work is complete; **security remediation and promotion remain
blocked** until equivalent official fixes and complete acceptance actually exist.
The prior proposal/prototypes at `08b3b636` are retained as **historical evidence,
not authorized implementation**. Existing approved libxml2-provider corrections
continue separately with their assigned worker and are not changed here. Tar is
part of the PO's broader rejection, but its three findings remain under `4yxbm`,
not silently added to or discharged by this bead's 13-remedy scope.

### Rejected historical owned-provider proposal

Before the PO's choice, the investigation proposed the following three additional
source families. This list preserves the technical comparison and correction
benchmarks; it is **not the selected delivery route**:

1. **SQLite:** complete corrected **3.54.1 library**, existing features/limits and
   `libsqlite3.so.0`; do not introduce the vulnerable CLI.
2. **coreutils:** retained **9.11** plus `d64e35a8`, `b60a159f`, `4ade9cf7`,
   **and** `52e5694f`, including original tests, GNU capabilities and all four
   installed split outputs.
3. **GLib:** retained **2.88.1** plus `656ad458` and `c9da977c`, including the
   original invalid-nesting regression and its ported registration.

Equivalent **official signed stable 3.24 vendor fixes** were absent from the
recorded inspected feeds. The vendor may ship a corrected release or equivalent
backport: no particular version string substitutes for actual source-fix proof.
The PO's selected route is not a waiver, risk downgrade, remediation delivery or
permission to stop unrelated authorized engineering work.

No stable-branch/cohort expansion, edge/testing feed, distro Python replacement,
74-pin application update, GNU feature removal or new production wiring occurs
in this record. These remain acceptance requirements, **not claims already
enforced by a new provider or a new gate**.

```mermaid
flowchart LR
  A[Fresh signed stable indexes + immutable0066 DB] --> B{Equivalent source fixes?}
  B -->|No in captured feeds| C[PO selected official stable vendor updates only]
  C --> E[Await equivalent official fixes; P1 and promotion blocked]
  C -.-> D[Additional owned providers rejected; historical proofs only]
  E --> F
  F[Complete both-arch final-file, source, ABI, native and advisory acceptance]
```

## Fresh vendor result, not a version-only inference

All four **whole** main/community APK indexes were fetched over HTTPS and passed
`apk verify` using hash-reviewed architecture-specific official keys. A corrupted
index was rejected by the same instrument. ARM signature checking ran on AMD64;
it is **not ARM execution**. Complete tracker JSON-LD and fixed secdb documents
were acquired with the unchanged maintained snapshot helper, starting
`2026-11-11T15:58:42.729696Z`. This is historical decision evidence, not a future
six-hour gate input or proof that a publisher is complete.

| Source | Published0066 and current signed vendor, both architectures | Signed source recipe commit | Current remedy availability |
|---|---|---|---|
| SQLite | `sqlite-libs 3.53.4-r0` | `a9c3ba8b9129ec0f294d0a9bf538c2f1632bb016` | No corrected vendor library |
| coreutils | `9.11-r0`, including env/fmt/sha512sum splits | `8e261cdab4fd1bc77881cbff73b9ea962e9bd49f` | No corrected vendor revision |
| GLib | `2.88.1-r1` | `bd96e2db11c9ba7d7795454d16af620d577614c7` | No corrected vendor revision |

For each of the **six installed target outputs per architecture**, original
query origin/version and installed DB **control checksum, source commit,
architecture and origin** match the freshly verified index. The fixture tests
re-derive this join from recorded real stanzas and complete immutable inventories,
not from the table above. There are no matching replacement outputs hiding in
the inspected community indexes.

The complete field survey, attached to the bead before writing fixture tests:

- Main tracker: 185 items/185 unique CVEs; 292 stable states, all `fixed:false`.
- Community: 1994 items/1639 unique CVEs; 3847 raw/3085 unique stable rows;
  3084 `false`, one `true`. Embedded foreign branches were not mistaken for stable.
- Raw CVSS-vector distributions: main 180 missing, five 3.1; community 1561
  missing, 368 3.1, 53 3.0, **12 4.0** despite the `cvss3` field name.
- Fixed secdb contains 290/489 package rows. Opaque aliases/composites and version
  `0` remain evidence, **not automatic fix exemptions**.
- Whole index row counts: x86 main/community **5961/22708**; ARM **5983/22588**.

## Upstream release evaluation and source proof

| Alternative | Actual examined source result | Historical technical comparison, not delivery authorization |
|---|---|---|
| SQLite 3.54.1 complete release | Canonical source SHA256 `387e7f6c…240c71b`; new official amalgamation SHA3 matches the primary download page. Loaded library source ID matches canonical manifest `555e07ef…e6ca76`. Existing eight straightforward fix contexts replay; authorization follow-up also reverse-matches, while CTE/later edits and initial authorization patch require semantic review. | Benchmark for equivalent official vendor corrections; owned release-build proposal rejected. |
| coreutils 9.12 upstream release | Allocation and wide-blank changes present, but the **blank** branch still adds raw `c32width(g.ch)`. The negative-width patch applies unchanged. A clamp in the sibling nonblank branch does **not** fix it. Downloaded release signature is retained but not verified; no signed-release acceptance claimed. | Blind 9.12 bump is insufficient. Official vendor source must include all four equivalent corrections; owned four-patch proposal rejected. |
| GLib 2.88.4 latest 2.88 release | Official SHA256 matches. Still has the bad `<node>` parent condition and lacks `test_invalid`; the correction code applies, with the same registration-layout conflict. | Bare micro bump is insufficient. Official vendor source must include the paired correction/regression; owned backport proposal rejected. |

Original primary correction URLs and retained source-feature review for each
SQLite correction are in
[`stable-alpine-advisory-feasibility.md`](stable-alpine-advisory-feasibility.md).
This assessment re-executed their source contexts locally against the exact
hash-bound canonical archives. It does not reinterpret patch-context success as
test execution or claim that a source archive is a delivered runtime correction.

## Actual prototype verification

Files under [`proofs/stable-remedies/`](proofs/stable-remedies/) are retained
historical decision/proof artifacts, **not authorized owned providers, installed
recipes or production gate changes**. The results below predate the PO decision;
this decision-only follow-up does not compile or run new provider prototypes.

| Check and layer | Observed result | Boundary |
|---|---|---|
| Native AMD64/musl Python 3.12 SQLite behavior | Old vendor: **2 fail/1 pass**. Private 3.54.1: **3 pass**. FULL JOIN authorizer sees both tables; eleven small CTEs obey depth 11/12. FTS3/4/5, R-Tree, foreign keys and private in-memory backup roundtrip pass. | No attacker SQL payload, filesystem database or operator data. Not WAL/migration/DBAS/concurrency acceptance. |
| Dangerous incomplete SQLite authorization mutant | Recreated initial fix's `a[0]` iteration mistake: **1 fail/2 pass**, specifically missing authorization of `t2.x`. | Proves the test rejects the incomplete fix, not merely the original release. |
| Native AMD64/musl GLib parser behavior | Old exact parser: four original invalid vectors terminate their isolated child processes with SIGSEGV; empty/valid controls pass. Paired correction: **7 pass**. | Compiled parser unit, not rebuilt full GLib or original session-bus C test runner. |
| GLib original C regression port | Full test function retained; registration adapted from upstream slash-separated names to actual 2.88.1 hyphenated harness. | Port exists; full original C harness execution remains pending. |
| coreutils full source/test port | Four original code/test sets apply with **fuzz zero**; original uniq/allocation/wide regressions and THANKS retained; combined NEWS and negative-width musl regression added. | **No utility behavior PASS.** Prototype compile is unfinished. |

The host's **existing GCC 15.2** compiled private shared objects using
signature-verified stable musl/GLib development headers, with two-CPU/3-GiB
bounds. Native execution used cached `0c5586b42b0d`, `/proof/venv/bin/python`,
all 74 application pins verified, UID3000, no network, read-only source/root,
capabilities dropped and private disk-backed `/tmp`. Five GLib support libraries
came from already loaded published0066 AMD image `ca2fb0b9…`; their actual file
checksums match the recorded installed DB. The new SQLite mapping and whole-file
hash were inspected via `/proc/self/maps`, not just an executable version.
The old library loaded in the cached test image is also **whole-file identical**
to the locally loaded published0066 AMD image: SHA256
`4290ff1e86e8c9204789104aed2a2eb562d7292ed9544c5ba235759ea1ff3281`,
with equal loaded path and source ID. That binds the SQLite baseline red to the
actual published library bytes, not merely another package bearing the same name.

These are **not production Alpine-toolchain package builds**, native ARM64
execution, full ABI proofs, scans or final-image/source acceptance. GLib's private
parser configuration disables NLS only for this unit prototype; production must
retain the original feature contract.

The private coreutils build first failed because extraction mtimes unnecessarily
triggered absent Autotools; preserving archive mtimes corrected that without
source changes. The next attempt stopped in retained `lib/hard-locale.c` on
undefined `SETLOCALE_NULL_MAX`, `setlocale_null_r` and `streq`. No invented header,
stub or silent feature deletion was used. That is an **unfinished build-preparation
investigation**, not evidence of an upstream-unfixable vulnerability. Proposed
utility behavior tests are included but **unexecuted**; they are not counted in
the passing SQLite/GLib/recorded-evidence subset.

## Per-finding applicability and quantified risk evidence

The current target tracker has **15 records**: the 13 remedies here plus chroot
and SQLite CLI. Every target tracker CVSS is `0/null`, meaning **UNKNOWN**, not
0.0/Informational or Low. GLib's separately retained primary-vendor CVSS is
**7.5/High**. No effective ECM score is fabricated where attacker reachability
is unproven; such scores remain **unquantified**, not reduced to Low. Quantitative
coverage for the 13 fixes is **0/2 architectures of delivered corrected final
packages**. The bounded AMD64 units above are additional evidence, not clearance.

| CVE | Source-feature applicability / impact prerequisite | Numeric severity and residual risk |
|---|---|---|
| 2026-119371 | CTE parser enabled; depth exhaustion requires crafted SQL/schema path. Safe small-limit regression demonstrates changed behavior. | Tracker UNKNOWN; ECM effective score unknown; P1 |
| 2026-119372 | FTS3/4 enabled; snippet use-after-free requires affected query/database state. | UNKNOWN; path not demonstrated; P1 |
| 2026-119373 | FTS5 enabled; row-list merge overflow requires affected index/query. | UNKNOWN; path not demonstrated; P1 |
| 2026-119374 | FTS5 enabled; secure-delete overflow requires affected index/operation. | UNKNOWN; path not demonstrated; P1 |
| 2026-119375 | WAL supported; page-size disclosure requires affected database/WAL handling. In-memory prototype does not prove WAL safety. | UNKNOWN; final WAL proof pending; P1 |
| 2026-119376 | COLLATE expression parser present; crafted expression depth required. | UNKNOWN; path not demonstrated; P1 |
| 2026-119377 | Joined-column authorization API present; bypass matters to consumers relying on that boundary. Bounded callback regression and incomplete-fix mutant executed. | UNKNOWN; ECM reliance not established; P1 |
| 2026-119378 | R-Tree enabled; use-after-free requires affected index/query state. | UNKNOWN; path not demonstrated; P1 |
| 2026-119379 | Session/preupdate support enabled; changeset overflow requires affected session input/operation. | UNKNOWN; path not demonstrated; P1 |
| 2026-119591 | Count-of-view optimization present; crafted view/expression depth required. | UNKNOWN; path not demonstrated; P1 |
| 2026-56391 | GNU uniq delivered; multibyte `-w` read overrun requires crafted tool input. No direct ECM tool path established. | UNKNOWN; no non-use exemption; P1 |
| 2026-56392 | GNU unexpand delivered; allocation/wide/negative-width whitespace paths apply, including musl. | UNKNOWN; runtime regressions unfinished; P1 |
| 2026-58016 | GLib/GIO delivered; invalid D-Bus introspection nesting triggers parser memory-safety failure in bounded native prototype. Direct ECM D-Bus input path not established. | Primary 7.5 High; effective score unquantified; P1 |

Authenticated restore opens imported SQLite databases and `quick_check` is not a
parser memory-safety boundary. Authentication reduces exposure, not source
applicability. No arbitrary-SQL service endpoint or direct D-Bus parser call is
claimed. `CVE-2016-2781` remains a separate shared-TTY/chroot contract review;
`CVE-2026-119586` is not fixed by 3.54.1, and absent image CLI does not clear
external operator recovery tools. Neither is absorbed into the 13 fixes.

## Exact unresolved tracking: 13 remedies, no paper remediation

Every row below remains **blocked awaiting an equivalent official vendor fix and
complete acceptance**, through `evy2p` → `4izbc` → `g3vd3`. The choice settles the
delivery route, not any CVE disposition. The machine-readable list is checked by
`test_decision_preserves_exact_thirteen_unresolved_remedy_records`.

| Source | Exact unresolved remedy records | Count |
|---|---|---|
| SQLite | CVE-2026-119371, CVE-2026-119372, CVE-2026-119373, CVE-2026-119374, CVE-2026-119375, CVE-2026-119376, CVE-2026-119377, CVE-2026-119378, CVE-2026-119379, CVE-2026-119591 | 10 |
| coreutils | CVE-2026-56391, CVE-2026-56392 | 2 |
| GLib | CVE-2026-58016 | 1 |

The other six original `4izbc` findings are not absorbed: Cairo
CVE-2025-50422 (`eeh2i`), chroot CVE-2016-2781, absent-image SQLite CLI
CVE-2026-119586, and GNU tar CVE-2025-45582/CVE-2026-18477/CVE-2026-18508
(`4yxbm`). Existing source/compiler/static/feature and advisory obligations under
`g3vd3` remain unchanged, including separate existing-provider work.

## Selected route: concrete acceptance trigger and issue disposition

Evaluation resumes when an **actual official stable 3.24 update** is available,
not via an automatic polling loop. Before any of these 13 remedies is accepted:

1. Authenticate official stable 3.24 package/index signatures on the relevant
   architectures and bind exact installed outputs, source recipe commits,
   archives, patches, config and notices to the actual immutable final files.
   Upstream HTTPS/checksum evidence alone is not an official signed vendor APK.
2. Establish **actual source-fix equivalence** for every affected feature and
   correction, including the SQLite authorization follow-up, complete unexpand
   sequence and GLib parser/regression. A tracker `fixed` flag, newer version,
   CVE disappearance, empty Trivy result or fixed-only secdb omission is no waiver.
3. Complete **both native AMD64 and ARM64 ABI and compatibility acceptance**:
   unchanged SQLite ABI/features/limits plus WAL, migrations, concurrency and
   current/legacy DBAS; full GNU capabilities and multibyte/musl regressions;
   GLib symbol/SONAME/dependent-media/introspection and gcompat checks. Preserve
   stable 3.24, Python 3.12 and all 74 application pins. AMD64 unit execution and
   ARM signature verification do not satisfy ARM64 native execution.
4. Complete retained **source and advisory acceptance**, including compiler/static
   dependencies, complete finding reconciliation and required scans against
   actual final outputs on both architectures, with independent review. Preserve
   unknown severity as UNKNOWN rather than Low. Full `g3vd3` acceptance criteria
   are not narrowed by this decision.

These are **future acceptance requirements, not a new gate implementation or an
assertion that they passed**. The existing historical proof limits above remain.

`evy2p`'s choice-only deliverable is fulfilled, but its description also preserves
ABI/features, source/notices, native tests and scans. Therefore its disposition
is **BLOCKED awaiting official updates and complete acceptance**, not closed as
remediated. No issue is closed or acceptance criterion changed by this record;
`4izbc`, dependent `g3vd3` security acceptance and promotion remain unresolved.

### Historical maintenance alternatives retained for traceability

| Route | Maintenance surface / trade-off | Exit |
|---|---|---|
| Official stable backports — **selected** | No additional owned source family; still requires six installed output identities and both-architecture source/ABI checks. Unavailable in the recorded survey. | Normal stable package refresh only once actual fixes and complete acceptance are proven. |
| Proposed three owned families — **rejected** | Historical estimate: **3 recipes, 6 installed package outputs per architecture**, two patch series plus one release build and ongoing source/signing/advisory ownership. | Not authorized to implement; retained comparison only. |
| Blind upstream release bumps | 9.12 and 2.88.4 are demonstrably incomplete; broader compatibility surface without removing ownership. | Rejected as a standalone remedy. |

The former owned-build phases are **not an active implementation plan**. The
selected exit from the blocker is equivalent official signed stable updates
followed by the complete acceptance trigger above, not owned-provider approval.

## Evidence and replay

- Machine-readable decision with rejected historical proposal and exact tracking:
  [`evidence/stable-remedies-20261111.json`](evidence/stable-remedies-20261111.json).
- Committed original-receipt capsule:
  `evidence/stable-remedies-20261111.receipts.json.gz`; includes complete recorded
  0066 inventory texts, selected original vendor stanzas/tracker items, full
  HTTP/hash/signature metadata, source witnesses, build commands and native logs.
  Excerpts are explicitly **not complete signed feeds**.
- Whole raw feeds/indexes/source archives and private outputs remain under
  `/tmp/opencode/ecm-evy2p-20261111`, hash-bound by the capsule. Source canonical
  archive remains at the prior assessment retention path, also hash-bound.
- [`proofs/stable-remedies/README.md`](proofs/stable-remedies/README.md) names the
  bounded replay selection and the unexecuted coreutils selection.

No version, CHANGELOG, SBOM, application, workflow, existing provider/gate helper,
runtime trust store or production source manifest was modified by this work.
