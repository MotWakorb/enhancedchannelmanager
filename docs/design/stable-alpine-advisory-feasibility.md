# Stable-Alpine advisory remediation feasibility

Assessment date: **2026-11-10**. Tracks `enhancedchannelmanager-4izbc` and
`enhancedchannelmanager-da8kc`. This is an investigation, **not implemented fixes,
vulnerability clearance, release approval or a change to any gate**.

## Result

All **19** original package findings were investigated. **13 have concrete
upstream code/release remediation paths**: two coreutils findings, GLib and ten
SQLite library findings. They still need maintained Alpine builds/backports and
tests; a source patch is not a verified runtime fix.

The other six need a different disposition:

- One SQLite CLI finding concerns an executable absent from both candidate
  inventories. That is a bounded component-applicability result, not a global
  exemption for SQLite or for operators using external recovery tools.
- `chroot` and three GNU tar findings require usage/security-boundary review;
  they are not resolved by the available stable packages. GNU tar has upstream
  hardening work, but a complete compatible correction series was not established.
- The Cairo reference does **not** establish a fix for the reported assertion.

The four owned package subjects were also investigated. Three are products of
one BusyBox source build; the fourth is an aggregate of six native component
groups. Expanding those groups exposed additional **tracker records**, not a new
claim of additional exploitable vulnerabilities: 11 FFmpeg records already have
corrected code in the pinned source, eight libxml2 records have existing reviewed
backports, and two further libxml2 recursion issues have upstream correction
candidates requiring adaptation. The separate xmlcatalog shell correction also
exists, but its complete upstream patch does not apply to the retained old test
layout. None of the four owned subjects is silently removed from the pending set.

## Evidence and limitations

- Native CI run [42753734801](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/42753734801)
  built both full candidates from merge checkout `c0f042545a721aa75f5bb267cda4ef83d9ca6ceb`.
  The GitHub commit object confirms PR head `e3457bf6` as a parent. Both original
  artifact ZIPs were independently SHA256-checked before reading regular members.
- Both actual 122-row query/database captures still match all 19 original records
  in the retained `2026-11-10T04:02:23Z` advisory snapshot. This is a historical
  snapshot comparison, not a fresh time-valid release gate.
- All four newly fetched stable main/community APK indexes passed native `apk
  verify` with architecture-specific, hash-reviewed official keys. Cross-arch
  signature verification is **not ARM execution**. No runtime trust was changed.
- Both architectures' current stable feeds still offer cairo `1.18.4-r1`,
  coreutils `9.11-r0`, GLib `2.88.1-r1`, sqlite-libs `3.53.4-r0` and tar
  `1.35-r5`. Merely rebuilding with `apk upgrade` does not resolve these findings.
- Local native AMD64 Python loaded `/usr/lib/libsqlite3.so.3.53.4`. Its complete
  library SHA256 matches the newly signature-verified APK, whose compressed
  control checksum and source commit match the current PR candidate's installed
  record and signed index. FTS3/4/5, FTS3 parentheses, R-Tree and session features
  are enabled. This is exact-byte supplementary runtime evidence, **not execution
  inside the final CI image or an ARM feature probe**.
- Only trusted inspection/patch tools and ordinary in-memory SQLite introspection
  were run. No exploit payload, provider binary, downloaded build recipe or
  upstream regression suite was executed for this assessment. Source dry runs
  and source-context checks must not be reported as runtime regression passes.

Exact hashes, observations and retention paths are recorded in
[`evidence/stable-alpine-advisory-feasibility-20261110.json`](evidence/stable-alpine-advisory-feasibility-20261110.json).
The companion `.receipts.json.gz` retains original UTF-8 proof records, including
both actual query/database captures, so the bounded assessment does not depend
solely on transient local logs. Raw upstream bodies/source archives and complete
native artifact ZIPs remain separately hash-bound at the recorded retention root.

## Original 19 findings

### Cairo, coreutils and GLib

| Advisory | Observed code / applicability | Can it be fixed? |
|---|---|---|
| **CVE-2025-50422**, Cairo | Report concerns `_cairo_ft_unscaled_font_fini`. Referenced [MR 621](https://gitlab.freedesktop.org/cairo/cairo/-/merge_requests/621) is open/unmerged and changes rectangular tessellation for Poppler issue **1579**, not the reported font assertion/issue **1591**. Poppler is absent from the candidate, but Cairo itself is present and used transitively. | **Unresolved.** Do not apply this unrelated patch or declare Cairo safe because Poppler is absent. Need the actual assertion's lifetime/root-cause analysis or a matching maintainer correction and regression. |
| **CVE-2016-2781**, coreutils | Requires `chroot --userspec` with a shared controlling terminal/session. ECM's Python/shell callsite survey found no `chroot` invocation; identity switching uses gosu. This is not whole-image non-reachability proof. | **Operational mitigation; no verified universal package fix.** Keep untrusted child execution away from a privileged shared terminal, and review session/TTY isolation. Do not assume the container host disables TIOCSTI, or silently change CLI semantics under the compatibility contract. |
| **CVE-2026-56391**, coreutils | `uniq.c` in the exact 9.11 archive retains the incorrect fixed-pointer scan. | **Yes:** upstream [`d64e35a8`](https://git.savannah.gnu.org/cgit/coreutils.git/commit/?id=d64e35a8a4c0e4608321433e0d84d917e4e36371). Code hunk applies with fuzz zero. Port and run the original tests with real musl multibyte locales; no test skipping as a substitute. |
| **CVE-2026-56392**, coreutils | `unexpand.c` retains overflow-prone allocation. Subsequent upstream corrections also address wide blanks and negative-width whitespace, explicitly including musl. | **Yes:** use the reviewed sequence [`b60a159f`](https://git.savannah.gnu.org/cgit/coreutils.git/commit/?id=b60a159fdc5bfcf9988d3a4cb6f53abe8ad5d35d), [`4ade9cf7`](https://git.savannah.gnu.org/cgit/coreutils.git/commit/?id=4ade9cf77f6c7b39e3fdc5ce97a778f8e294694c), [`52e5694f`](https://git.savannah.gnu.org/cgit/coreutils.git/commit/?id=52e5694f4136b34e12e5e7997949bbc63430cbba), not just the first allocation change. All three code hunks apply sequentially with fuzz zero. Full tests/NEWS integration and native regression proof remain. |
| **CVE-2026-58016**, GLib | Exact 2.88.1 source has the incorrect `<node>` parent condition. No direct D-Bus parser call was found in ECM application code; native/transitive users remain a separate question. | **Yes:** upstream [`c9da977c`](https://gitlab.gnome.org/GNOME/glib/-/commit/c9da977c178fbfc0e4caf99f9fdf5dc433d6fcc2), with companion [`656ad458`](https://gitlab.gnome.org/GNOME/glib/-/commit/656ad4582cb1d7a7fa8bafe3ce8aec6aa3c17da0). Assertions alone are not the fix. Parser code applies, but the complete test patch has a registration-context conflict in this source release. Port the registration rather than dropping the upstream regression. |

The coreutils probes above intentionally applied **code hunks only** to
disposable source files. They do not assert that whole upstream patches/tests
were ported. The GLib probe selected the two exact regular archive targets:
the maintained full-tree extractor rejected unrelated parent-relative test
symlinks and was **not weakened**. No provider source was executed.

### SQLite: one library remediation can address ten findings

The retained 3.53.4 source lacks the corrections below. The official
[3.54.1 source release](https://sqlite.org/releaselog/3_54_1.html) contains the
corrected code examined here; archive `VERSION` and `manifest.uuid` match that
primary release's source ID. Eight corrections pass reverse code-context
checks; CTE depth and joined-column authorization were additionally inspected
where later upstream edits change patch context. The authorization remedy must
include the follow-up correcting the initial incomplete fix.

| Advisory | Issue | Verified upstream correction / disposition |
|---|---|---|
| **CVE-2026-119371** | CTE recursion depth | [`e6ff280c`](https://github.com/sqlite/sqlite/commit/e6ff280c839fdfc7418fd5add12e8f32702a17fb); 3.54.1 has nesting checks plus subsequent cursor-depth hardening. |
| **CVE-2026-119372** | FTS3/4 snippet use-after-free | [`a87bd247`](https://github.com/sqlite/sqlite/commit/a87bd2471a9c0606e78d3c3678bcd9cecaa0e731). |
| **CVE-2026-119373** | FTS5 row-list merge overflow | [`e1c9934f`](https://github.com/sqlite/sqlite/commit/e1c9934f9f11a499029abb020ea715a08970da80). |
| **CVE-2026-119374** | FTS5 secure-delete overflow | [`d9de6eed`](https://github.com/sqlite/sqlite/commit/d9de6eedbe918c074c7556400fa21df5960704d9). |
| **CVE-2026-119375** | WAL page-size memory disclosure | [`e5bf1198`](https://github.com/sqlite/sqlite/commit/e5bf119821f25be1d7f10ef0a2edbe175289166e). |
| **CVE-2026-119376** | COLLATE expression depth | [`d54522a5`](https://github.com/sqlite/sqlite/commit/d54522a5b832422ab893a1e9fa7a99ab314ea4e2). |
| **CVE-2026-119377** | Joined-column authorizer bypass | Both [`7ff247c5`](https://github.com/sqlite/sqlite/commit/7ff247c5b9ead3ad8a69c26d3fd264878e127237) and correcting follow-up [`5694c3bb`](https://github.com/sqlite/sqlite/commit/5694c3bb2a6a40770ecd2f65910eea756734dfd8). Old 3.53.4 context needs adaptation for a backport; prefer validating the complete corrected release rather than copying one hunk. |
| **CVE-2026-119378** | R-Tree use-after-free | [`d21bd37c`](https://github.com/sqlite/sqlite/commit/d21bd37c7cfc1b85b17a6703555f0926b6236e04). |
| **CVE-2026-119379** | Session changeset overflow | [`64459c87`](https://github.com/sqlite/sqlite/commit/64459c87cc702d600704fb77ee40ca1782398a52). |
| **CVE-2026-119591** | Count-of-view optimization depth | [`eff50a06`](https://github.com/sqlite/sqlite/commit/eff50a067c55f972909e7780e506e91382d08de2). |
| **CVE-2026-119586** | C CLI `.recover` command injection | **Not fixed by claiming 3.54.1:** the advisory covers the CLI through that release. Both candidate captures contain `sqlite-libs`, not the `sqlite` CLI package, and owned payload inventories contain no replacement CLI. ECM does contain operator recovery advice in `backend/database.py`; absence in the image is not clearance for an externally installed tool or an unsafe recovery pipeline. |

**Recommended library approach:** a reviewed same-ABI, feature-preserving
Alpine-native SQLite build from the complete corrected release, or an Alpine
maintainer backport delivering equivalent corrections. Stable 3.24 currently has
no such package in the inspected feeds. This would be an **additional owned
provider requiring explicit maintenance approval**, not an authorized change in
this investigation. Do not mix edge repositories or change the 74 Python pins.

Keep the existing FTS3/4/5, session, R-Tree and other compile options. Bind the
library actually loaded by Python, not only an executable's version string.
Native verification must cover database/WAL compatibility, migrations, concurrency,
and both current and legacy backup/restore paths. In particular,
`backend/routers/backup.py:_validate_legacy_journal_db` opens an imported database
and runs `PRAGMA quick_check`; that check is **not** a memory-safety boundary for
the SQLite parser itself. Restore authentication reduces exposure but does not
make an untrusted database harmless. No attacker-controlled SQL endpoint was
demonstrated by this investigation.

### GNU tar

| Advisory | What the primary evidence establishes | Feasible response |
|---|---|---|
| **CVE-2025-45582** | GNU maintainer [states the reported repeated-extraction pattern is documented misuse](https://lists.gnu.org/archive/html/bug-tar/2025-08/msg00012.html). | Enforce independent fresh directories and safe extraction policy for untrusted inputs. Upstream openat2 hardening exists, but no complete compatible backport was validated here. A disputed classification is not an automatic waiver. |
| **CVE-2026-18477** | [Red Hat](https://access.redhat.com/security/cve/CVE-2026-18477) identifies incremental restore's rename race and explicitly says a fresh directory alone is insufficient. | Avoid untrusted incremental restores and attacker-writable restore trees. Source-level repair needs reviewed descriptor-relative rename/confinement handling and the complete relevant upstream chain, not just a path-prefix check. |
| **CVE-2026-18508** | [Red Hat](https://access.redhat.com/security/cve/CVE-2026-18508) limits the reported boundary failure to `--one-top-level`; it is not a sandbox. | Do not rely on that option as confinement. Upstream history contains openat2 work, a later draft and a reverted incremental/top-level change. Do not cherry-pick an unverified draft as a finished fix. |

The application callsite survey found Python `tarfile` use, not a GNU tar
extraction invocation. That distinguishes affected components; it is not proof
that the installed GNU tool is safe for every operator or indirect workflow.
Fetching two identified upstream patch bodies failed due to network errors;
their log titles/IDs alone do not establish a complete correction. Those failures
are retained rather than reported as “no fix exists.” GNU semantics cannot be
silently removed or replaced with BusyBox behavior to make the finding disappear.

## Four owned package subjects

| Subject | Investigation result | Remaining work |
|---|---|---|
| `busybox` | Existing reviewed owned source corrects **CVE-2025-60876**. Paired native receipts bind the source recipe, signed package and existing runtime checks. | Advisory consumer must resolve the real upstream source and correction witnesses instead of comparing the synthetic package version to an official version string. Existing source/compiler/complete-advisory obligations remain. |
| `busybox-binsh` | `/bin/sh` is the signed link into that same BusyBox build, not an independent unknown shell source. | Join its exact signed link/provider and shared source disposition; do not create a blanket name exemption. |
| `ssl_client` | Companion output from the same original BusyBox recipe, separately signed and installed. | Bind the separate payload and its actual crypto dependency versions; a wget correction alone is not complete TLS/advisory clearance. |
| `ecm-owned-native` | Both candidate artifacts bind six component groups and exact raw receipts: codecs, security libraries, JPEG XL, FFmpeg, gosu and resdet. | Evaluate actual component versions/patches and bundled/static dependencies. One aggregate package's synthetic version cannot stand in for all component advisory coverage. |

### FFmpeg records inside the aggregate

All 11 additional stable-tracker records were investigated against the exact
`ffmpeg-8.1.3.tar.xz` used by both native builds (SHA256
`7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3`).

| Records | Source result |
|---|---|
| CVE-2022-3964 | RPZA bounds correction is present, with subsequent width/stride hardening. Whole original reverse patch fails on those later changes; the relevant bounded row/column loops were inspected explicitly. Not mislabeled as a passing whole-patch replay. |
| CVE-2025-25468, CVE-2025-25469 | Referenced [`d5873be5`](https://github.com/FFmpeg/FFmpeg/commit/d5873be583ada9e1fb887e2fe8dcfd4b12e0efcd) is actually an IAMF parse cleanup in `libavformat/iamf_parse.c`; both CVE descriptions misidentify files. The correction is present. |
| CVE-2026-52295, CVE-2026-52297 | [`8439e020`](https://github.com/FFmpeg/FFmpeg/commit/8439e0203744a30d280668fcd086f74ed5001da1) IAMF/MOV padding corrections are present. |
| CVE-2026-52296 | [`23227a44`](https://github.com/FFmpeg/FFmpeg/commit/23227a444de4a8f7696f46660cdd044b460f7e47) WMA padding correction is present. |
| CVE-2026-70628 | DVB subtitle capacity correction is present. |
| CVE-2026-70629 | RSCC short-output validation is present. |
| CVE-2026-70630 | Screenpresso short-output validation is present. |
| CVE-2026-70631 | TIFF short-strip validation is present. |
| CVE-2026-70632 | CFHD output-width validation is present. |

For the last five, original reference lists mix introducing and fixing commits;
the introducing commit was not mistaken for remediation. Ten records have
successful reverse **code-context** checks; RPZA has the explicit later-code
review above. This is evidence for component-aware reconciliation, not permission
to remove a format, replace the source with FFmpeg 9, or install global CVE
suppressions. No new malicious-input regression or complete compiler/linkage
proof is claimed by these source checks.

### libxml2 records inside the aggregate

- **CVE-2026-86137 through CVE-2026-86144:** all eight have existing reviewed
  patches. This investigation replayed them, plus the existing CVE-2026-6732
  patch, against the original 2.13.9 archive and verified every recorded full-file
  pre/post hash. Actual paired candidate receipts reference the same recipe.
  These are not eight reasons to blindly replace the old ABI.
- **CVE-2025-8732:** upstream candidate
  [`ee9fdfcc`](https://gitlab.gnome.org/GNOME/libxml2/-/commit/ee9fdfcc244a5d06b5b087437f5cb3decd05d689)
  deduplicates SGML expansion for a related self-reference issue. It does not
  apply unchanged: the old source lacks the newer depth-parameter infrastructure.
  A faithful backport needs bounded recursion **and** deduplication review; do not
  assume one related 11-line patch settles the whole older advisory.
- **CVE-2026-0989:** upstream
  [`19549c61`](https://gitlab.gnome.org/GNOME/libxml2/-/commit/19549c61590c1873468c53e0026a2fbffae428ef)
  adds an include-depth limit and error propagation. The old source has no such
  limit. Full patch dry run has code/header/test conflicts. **Fixable with an
  adapted backport**, including cleanup/error cases and safe depth limits on
  both native musl platforms, not only the nominal added counter.
- **CVE-2026-11979:** upstream
  [`c2e233fc`](https://gitlab.gnome.org/GNOME/libxml2/-/commit/c2e233fc1b341685fc99621b2768b503f777a72e)
  contains the three shell bounds checks. They fit the old `xmlcatalog.c` with
  offsets, but the complete patch fails because `test/catalogs/test.sh` is absent
  in 2.13.9. Port equivalent tests into the actual old test harness; do not omit
  the test hunk silently. Both candidate owned inventories ship the shared
  library, **not xmlcatalog**. This supports narrow runtime component distinction;
  source delivery/build-tool obligations and explicit gate reconciliation remain.

No matching unfixed stable-tracker rows for the inspected owned source names
were found for the other owned libraries/tools. **That is limited feed coverage,
not proof of no vulnerabilities**, especially for bundled crates, Go modules,
KISS FFT, skcms and compiler/static support. Existing Speex capacity corrections,
Rust/GNU source obligations and all four pending package subjects are retained.

## Risk, priority and recommended sequence

The original Alpine snapshot labels 18 findings with zero score/no vector:
**unknown**, not low. Additional primary metadata supplies GLib CVSS 7.5 and the
two 2026 tar findings CVSS 4.4; the 2016 chroot record has a historical **draft**
8.6 vector for different vendor releases, which is not transferred to ECM.
Effective ECM exploitability was not numerically scored without runtime path
proof. No false numeric precision or “not used” downgrade closes an item here.
Unresolved security/source acceptance remains **P1, pre-promotion blocking**.

1. **SQLite library:** maintain all existing features and ABI; approve the source
   maintenance route, then test the complete corrected library and DBAS paths on
   AMD64/ARM64. This addresses ten findings without changing application pins.
2. **Coreutils and GLib:** approve same-version backports or obtain stable vendor
   updates; preserve the full upstream regression tests, including musl cases.
3. **Existing owned libxml2:** adapt the remaining depth/catalog/shell corrections
   with explicit old-harness tests; retain all nine existing patches and ABI.
4. **Cairo and GNU tar/chroot:** resolve the unmatched Cairo diagnosis and review
   the complete confinement/session contracts. Any compatibility-affecting remedy
   or contextual acceptance needs an explicit decision, not a scanner waiver.
5. **Advisory enforcement:** bind the above source/feature/patch dispositions to
   both actual immutable final images. Preserve known findings across incomplete
   publisher data. Fixed-only secdb/Trivy omission and owned pseudo-versions must
   never stand in for this reconciliation.

The investigation changes documentation/evidence only. No new owned provider,
source version, recipe, production Dockerfile, application lock, test selection,
trust store, repository feed, `OWNED_PENDING` entry or aggregate gate was changed.

Follow-up tracking under the original assessment issue:

- `enhancedchannelmanager-evy2p`: decide maintained SQLite/coreutils/GLib remedies.
- `enhancedchannelmanager-eeh2i`: resolve Cairo's unmatched diagnosis/correction.
- `enhancedchannelmanager-4yxbm`: establish complete GNU tar confinement remedies.
- `enhancedchannelmanager-m8m42`: faithfully adapt remaining libxml2 corrections.
