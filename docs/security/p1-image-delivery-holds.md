# Reviewed P1 image delivery holds

Build 0.18.2-0065 adds enforcement of the existing P1 advisory assessments to
`scripts/check_native_image_scan.py`. It does **not** repair the affected RPMs,
clear their beads, or authorize promotion of the combined application candidate.

## Why the severity-only gate was insufficient

The actual AMD64 and ARM64 PR #1060 scans from Build run `41579029152`, source
`dbe84402b822ff9cd5c9f1b35804136e3542f40e`, report Brotli, librsvg and Speex as
**Medium**. The recorded primary-advisory review treats them as P1s. Previously,
removing the separate High CA finding left these records eligible to pass the
severity-only validator. That would not satisfy the recorded delivery holds.

The validator now denies each of these advisory IDs and its CVE alias, at any
reported severity and regardless of fix-availability status. It preserves the raw
scanner labels and reports the hold separately. `Status: fixed` in a finding
means the vendor has a fix, not that the scanned package contains it. Other
High/Critical findings still deny promotion; unrelated Medium/Low/Unknown
records retain their existing treatment. No ignore, VEX, or suppression is added.

| Bead | Advisory / CVE | Recorded primary scores | Vendor fixed boundary |
|---|---|---|---|
| `l5z6r` | `openSUSE-SU-2026:12232-1` / [CVE-2026-111497](https://www.suse.com/security/cve/CVE-2026-111497.html) | Google CNA v4 **9.2** | Brotli runtime libraries **1.2.1-1.1** |
| `97j9u` | `openSUSE-SU-2026:12233-1` / [CVE-2026-114406](https://www.suse.com/security/cve/CVE-2026-114406.html) | CNA v3.1 **6.8**, SUSE v3.1 **7.4** / v4 **9.1** | Mozilla CA **2.92-2.1** |
| `6wx8d` | `openSUSE-SU-2026:12364-1` / [CVE-2026-96889](https://www.suse.com/security/cve/CVE-2026-96889.html) | CNA/SUSE v3.1 **7.8**, SUSE v4 **8.4** | librsvg **2.63.4-1.1** |
| `82cy4` | `openSUSE-SU-2026:12336-1` / [CVE-2026-116741](https://www.suse.com/security/cve/CVE-2026-116741.html) | CNA v3.1 **6.5** / v4 **8.3**; SUSE v3.1 **8.1** / v4 **9.2** | Speex **1.2.1-3.1** |

The fixed boundaries above are review inputs, not an implemented RPM EVR solver.
The new check recognizes findings; it cannot prove a package safe when a scanner
omits an advisory, or verify an unreviewed backport. Complete scan inventories,
exact native provider/source binding and the separate native gates remain
required. A scan passing this check is not a vulnerability-free assertion.

## Risk and scope

- **Brotli:** Critical primary score retained. A crafted decoder input can corrupt
  memory; an ECM-specific exploit was not demonstrated.
- **librsvg:** High provisional effective risk. The shipped FFmpeg calls librsvg
  on packet bytes. The precise malicious inclusion path was not exercised.
- **Speex:** High provisional effective risk with the Critical primary score
  retained. The bounded FFmpeg source search did not find direct calls to the
  named non-owned-buffer APIs, but is not a full non-exploitability proof.
- **CA:** historical bundle inspection reduced effective risk only in the
  demonstrated system/certifi contexts. It did not fix the vendor RPM or prove
  all consumers, administrator stores or later candidate images safe. Both
  compromised roots and unrelated positive trust controls remain in scope.

No media support is removed, trust store replaced, vendor key changed, native
fork introduced, or application/probe routing policy selected by this guard.
Actual remediation still needs architecture-correct vendor-signed binaries,
exact corresponding signed sources, rebuilt compatible images and complete
fresh unsuppressed scans on **both** architectures. Removing a resolved finding
from the scanner output does not require deleting the defensive advisory list.

## Vendor availability recheck, November 7

The old ARM availability diagnosis is superseded for these four fixes. Fresh
official binary/source metadata advertises **20261105** on both architectures,
and the selected fixed binary/devel and source RPMs were completely retrieved:
24 HTTP 200 responses, 70,565,551 total bytes. Detached metadata signatures,
embedded header signatures, payload hashes and eight Source/Patch audits pass
under the existing SUSE fingerprint
`AD485664E901B867051AB15F35A2F86E29B700A4`.

| Official feed | Signed revision | Retrieved repomd SHA-256 |
|---|---|---|
| AMD64 binary | `1793948236` | `36db62d4ce6887faa6ec09802a5b1311c1e14e075e5d7011fc590ceccbdabbe6` |
| ARM64 binary | `1793957306` | `374a893f64518ff85a66f866d88e0aab77d0fbeb78f83eaa5fe140947a0495ce` |
| AMD64 source | `1793948447` | `d55452409baa0089c46986f502f9f7db827c46f2f4e6ea36b6731572c5eb20e8` |
| ARM64 source | `1793957689` | `b37919a3d16d81ad5aa9e6413916d8e29f36792bb62da424c501e94a6fca8484` |

The official roots are `https://download.opensuse.org/tumbleweed/repo/oss/`,
`https://download.opensuse.org/tumbleweed/repo/src-oss/`,
`https://download.opensuse.org/ports/aarch64/tumbleweed/repo/oss/` and
`https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/`.
Canonical and officially listed GWDG ARM metadata agree. The two noarch CA
archives, and each same-filename source pair, have different architecture-feed
hashes; a filename/noarch match does not authorize cross-feed substitution.

This is artifact-availability and research authentication evidence, **not an
installed fix**. Host research used supplemental GPG header verification because
`rpmkeys` was unavailable. The normal native builder/collector's independent
`rpmkeys -Kv`, header and payload replay remains required before product use.
Evidence and exact download receipts are retained under
`/tmp/opencode/ecm-p1-native-check-20261107/`; the implementation must regenerate
the complete provider/source closure rather than relabel the old source carriers.

## Regression evidence

`backend/tests/fixtures/native_scan/p1-advisories.json` preserves six-field
projections of three real findings from **each** architecture, with the original
report hashes and config identities. These projections are test fixtures, not
complete replacement scan reports or proof of newly built image health.

`backend/tests/unit/test_native_image_scan.py` covers:

- Denial of both recorded projections with no High CA finding present.
- Both SUSE and CVE identifiers, every supported severity and all four tested
  vendor status labels, including findings from an additional analyzer.
- Unchanged report bytes/values, existing non-P1 positive controls, and malformed
  non-string finding identities rejected without a traceback.
- The actual CLI exiting 1 for the recorded P1 findings rather than printing its
  success message.

The first three new test groups failed against the previous validator before
the repair. This is gate-policy verification, not native vulnerability repair
or deployed application acceptance.
