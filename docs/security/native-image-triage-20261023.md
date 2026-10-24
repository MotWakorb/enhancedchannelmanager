# Native image triage — 2026-10-23

## Remediation implementation

[ADR-015](../adr/ADR-015-wolfi-native-runtime.md) records the native-remediation
experiments and their publication boundary. **The Wolfi prototype is rejected for
publication:** its zero High/Critical result does not repair the missing exact
source/patch correspondence or its software-input losses. No format waiver was
approved. A source-built FFmpeg 8.1.3/openSUSE replacement is being tested; it is
not yet an accepted production image.

The three full-backend integration regressions have targeted green evidence
(355 tests), and the runtime gate now requires 26 checks including actual
production probing/cancellation and software-input inventory. Its 80 self-tests
pass, but the earlier 22-check Wolfi image result does not satisfy this expanded
gate. Final-source full tests, native execution on both architectures, complete
source/notices, unsuppressed scans and exact-SHA publication remain required.
The historical Debian inventory below is preserved, not reclassified as fixed or
suppressed.

## Decision and evidence boundary

**Production promotion remains blocked by unresolved applicable Critical/High
native findings. `enhancedchannelmanager-x1xne` is not resolved by this research.**
The complete historical inventory contains **42 unique advisories across 12 Debian
source packages and 34 binary packages: 208 package/advisory instances, comprising
1 Critical and 207 High**. Debian still marks every advisory open in trixie.
Neither a base refresh nor Debian's lower urgency is a vulnerability disposition.

The inventory is `/tmp/opencode/ecm-delivery-audit-20261018/ecm-container-scan.json`:
Trivy 0.76.0, created 2026-10-18, artifact `ecm-delivery-audit:20261018`, image
`sha256:47299705ff6f728bb5798a543dc3c2f7b000c7ccd44e13aa1a0217b39d1f1e66`.
Its configuration identifies source **`95e22bfe`**, build **`0.18.2-0045`**;
scan SHA256 is `67b2aae96f745434ef4110afcb7145de4b010733e5ba3a7aebbef74326699d04`.
This establishes an inventory, **not a delta against today's published image**.

The separate `published-baseline-scan.json` under the current evidence directory
selected a cached local September 20 image/build `0043`, not the current registry
image. It is **invalid as a current published baseline or candidate**. Its ten
additional fixable Debian instances must not be attributed to the current image.

Repository context was inspected through `git show origin/dev:…` at
`b56b87c5d6d99fe6e4d9234f94a61651718d7e2f`, not the dirty working tree. The candidate
refreshes all three production Python stages to
`python:3.12-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`,
retaining `apt-get upgrade`.

### Fresh amd64 candidate result

The rebuilt ECM candidate (Python 3.12.15, build `0.18.2-0046`) was scanned with
Trivy 0.76.1 without `ignore-unfixed` or severity filtering. It still contains the
same **42 unique native advisories / 208 Critical/High instances**; none has an
available Debian `FixedVersion`. Its Python inventory has **zero High/Critical**
findings after the certifi/aiohttp updates (six Medium and one Low remain).
The rebuilt MCP candidate has **zero High/Critical** OS or Python findings
(five Python Medium and one Low remain). These are preliminary local image
results, not exact-merge publication or arm64 evidence.

Evidence under `/tmp/opencode/ecm-p1-security-evidence-20261023/`:
`ecm-candidate-scan.json`, `mcp-candidate-scan.json`, and
`local-image-identities.json`. Real-image FFmpeg/ffprobe, Y4M frame extraction and
resdet execution passed; the disposable API smoke passed its seven checks (its
unconfigured upstream list endpoints explicitly permit HTTP 500, so that smoke
does not prove live Dispatcharr integration). **No native closure is claimed.**

## Complete advisory inventory and official status

The [complete Debian tracker JSON](https://security-tracker.debian.org/tracker/data/json)
was retrieved **2026-10-23 06:58:32 UTC**, with HTTP Last-Modified
**2026-10-23 06:17:36 GMT**. All rows below are **open in trixie**. Fixed Debian
versions below are from **sid/testing**, not available stable fixes. Source links
lead to the official tracker; the saved exact-42 JSON contains individual CVE URLs,
descriptions, release statuses, repository versions and fixed versions.

| Source | Instances | Complete advisory list | Debian status / fix boundary |
| --- | ---: | --- | --- |
| [acl](https://security-tracker.debian.org/tracker/source-package/acl) | 1 | CVE-2026-54369 | No DSA; intended future point update. Fixed in `2.4.0-1`; tracker explicitly advises against individual-patch backporting because of the new ABI. |
| [cjson](https://security-tracker.debian.org/tracker/source-package/cjson) | 5 | CVE-2026-16554, CVE-2026-29036, CVE-2026-67215, CVE-2026-67216, CVE-2026-87933 | All five also open in sid/testing `1.7.19-2`; no Debian fixed version recorded. |
| [ffmpeg](https://security-tracker.debian.org/tracker/source-package/ffmpeg) | 144 | CVE-2026-58049, CVE-2026-64830, CVE-2026-64832, CVE-2026-64833, CVE-2026-64834, CVE-2026-64835, CVE-2026-66036, CVE-2026-66039, CVE-2026-66040, CVE-2026-66041, CVE-2026-70628, CVE-2026-70632, CVE-2026-75142, CVE-2026-75143, CVE-2026-75144, CVE-2026-75146 | All 16 postponed: “Wait until fixed in 7.1.x upstream branch.” Fixed in Debian `7:9.0.2-1`. |
| [libsndfile](https://security-tracker.debian.org/tracker/source-package/libsndfile) | 1 | CVE-2026-37555 | No DSA/minor issue; also open in sid/testing `1.2.2-4`, without a Debian fixed version. |
| [libx11](https://security-tracker.debian.org/tracker/source-package/libx11) | 3 | CVE-2026-88806 | No DSA/minor issue. Description identifies upstream `1.8.14`; sid/testing `2:1.8.13-1` still open. |
| [libxml2](https://security-tracker.debian.org/tracker/source-package/libxml2) | 8 | **CVE-2026-6653 (Critical)**, CVE-2026-74860, CVE-2026-86138, CVE-2026-86139, CVE-2026-86140, CVE-2026-86142, CVE-2026-86143, CVE-2026-86144 | 6653: no DSA/minor issue, Debian fixed `2.14.5+dfsg-0.1`. 74860: fixed `2.15.3+dfsg-1`. Remaining six: fixed `2.15.4+dfsg-1`. |
| [libxrender](https://security-tracker.debian.org/tracker/source-package/libxrender) | 1 | CVE-2026-88807 | No DSA/minor issue. Description identifies upstream `0.9.13`; sid/testing `1:0.9.12-1` still open. |
| [ncurses](https://security-tracker.debian.org/tracker/source-package/ncurses) | 4 | CVE-2025-69720 | No DSA/minor issue. Debian fixed `6.6+20251231-1`; affected component is `infocmp`. |
| [perl](https://security-tracker.debian.org/tracker/source-package/perl) | 1 | CVE-2026-9538 | Postponed pending upstream regression resolution. Debian fixed `5.42.3-1`; affected component is Archive::Tar before `3.10`. |
| [systemd](https://security-tracker.debian.org/tracker/source-package/systemd) | 2 | CVE-2026-16742 | No DSA/minor issue. Debian fixed `261.2-1`; affected component is systemd-homed. |
| [tiff](https://security-tracker.debian.org/tracker/source-package/tiff) | 2 | CVE-2026-36849, CVE-2026-52490 | 36849: ignored/minor issue, Debian fixed `4.7.1-3`. 52490: no DSA/minor issue, fixed `4.7.2-1`. JSON marks both for a next point update, but the retrieved proposed-updates index contains neither a tiff candidate nor a fixed version. |
| [util-linux](https://security-tracker.debian.org/tracker/source-package/util-linux) | 36 | CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 | All no DSA/minor issue. Debian fixed `2.42.3-1`, except 78408 requires **`2.42.4-1`**. |

“No DSA,” “postponed” and “ignored” describe Debian prioritization, **not patched
code or non-applicability**. Preserve scanner severity until an evidence-backed
assessment; do not turn mitigation into a claimed fix.

## Stable/security/backports investigation

Fresh HTTPS downloads were parsed in full, with raw compressed indexes, HTTP
metadata and SHA256 checksums retained. These are repository-availability probes,
**not apt dependency solving or simulated installs**:

- [trixie sources](https://deb.debian.org/debian/dists/trixie/main/source/Sources.xz): all 12 sources present; versions match the historical inventory's source revisions.
- [trixie-security sources](https://security.debian.org/debian-security/dists/trixie-security/main/source/Sources.xz): cjson `1.7.18-3.1+deb13u1`, ffmpeg `7:7.1.5-0+deb13u1`, tiff `4.7.0-3+deb13u3`, util-linux `2.41.5-0+deb13u1`; libxml2 security revision `deb13u1` is older than stable's scanned `deb13u3`.
- [trixie-updates](https://deb.debian.org/debian/dists/trixie-updates/main/source/Sources.xz) and [proposed-updates](https://deb.debian.org/debian/dists/trixie-proposed-updates/main/source/Sources.xz): none of the 12 sources present.
- [trixie-backports sources](https://deb.debian.org/debian/dists/trixie-backports/main/source/Sources.xz), [amd64 packages](https://deb.debian.org/debian/dists/trixie-backports/main/binary-amd64/Packages.xz) and [arm64 packages](https://deb.debian.org/debian/dists/trixie-backports/main/binary-arm64/Packages.xz): none of the 12 sources present.

The initial actual apt policy log is preserved verbatim with provenance. It probes
the old pinned Python base, not the refreshed final candidate. It likewise selects
the affected stable ffmpeg/libxml2/systemd/util-linux/tiff versions. No supported
fix was found for this advisory set. [Backports policy](https://backports.debian.org/Instructions/)
also cautions that support is best-effort and packages should be selected individually.

## Scoped false-positive candidates — not dispositions

These are investigation leads from the historical package/file inventory and
official affected-component descriptions. **None authorizes a scanner exclusion.**
Confirm against the actual rebuilt image digest on each architecture, including
copied virtualenv/native artifacts; retain exact absence/architecture evidence and
an individually reviewed disposition before treating any finding as not affected.

| Advisory | Candidate evidence and remaining proof |
| --- | --- |
| CVE-2026-16742 | Only `libsystemd0`/`libudev1` appear; no systemd/homed package or affected executable. [Upstream advisory](https://github.com/systemd/systemd/security/advisories/GHSA-jm29-p7hh-vjhv) concerns homed/homework. Verify absence of that code in the final filesystem. |
| CVE-2026-74860 | No `python3-libxml2` or libxml Python-binding files recorded; affected path is the Python SAX binding. Verify neither venv nor copied artifacts introduces it. |
| CVE-2026-52490 | Only `libtiff6`; no `libtiff-tools`/`tiffcrop`. Tracker identifies `tools/tiffcrop.c:process_command_opts()`. Verify affected executable/code absent. |
| CVE-2026-9538 | Only `perl-base`; no full perl/modules packages or Archive/Tar files recorded. Verify Archive::Tar is absent, including manually copied modules. |
| CVE-2026-16554 | Official description requires 32-bit `size_t`; historical image is amd64, second supported target arm64. Verify actual ELF class and absence of affected 32-bit components on both targets. |

Do not extrapolate these leads: `libcjson1` ships **libcjson_utils too**;
`/usr/bin/infocmp`, `/usr/bin/mount` and `/usr/bin/nsenter` are present.
ECM feeds provider-controlled media to ffprobe/ffmpeg. Protocol restrictions,
timeouts and dropping privileges are useful mitigations, not decoder patches;
the CFHD advisory explicitly includes exploitation during stream probing.

## Remediation boundary and required verification

The smallest distribution-preserving candidate is the consistent Python
base refresh with stable security upgrades intact. Rebuild and rescan both amd64
and arm64 before reporting its result. **If the 42 advisories persist without
reviewed non-applicability evidence, the native bead remains unresolved.**

A broader approach requires an explicit implementation/compatibility decision:

- [Upstream FFmpeg 9.0.2](https://ffmpeg.org/download.html) is a source-build research
  candidate corresponding to Debian's fixed version for all 16 FFmpeg advisories.
  It is not a tested drop-in replacement. Require verified signed source, pinned
  hashes, SBOM/licenses, both architectures, dependency closure and compatibility
  tests for probing/preview, HTTP/HLS/encrypted streams, required DASH support,
  resdet frame extraction, signalstats and existing CLI timeout options.
- [libxml2 2.15.4 notes](https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.4.news)
  corroborate the overflow/XInclude fixes. However, [2.14 changed SONAME from
  `.so.2` to `.so.16`](https://download.gnome.org/sources/libxml2/2.14/libxml2-2.14.0.news).
  Copying the new library over the old one is unsafe: the image's libavformat,
  libbluray, LLVM and librsvg depend on the old Debian library. The installed
  `2.12.7+dfsg+really2.9.14…` version does not establish the upstream 2.11 fix.
- Rebuilding FFmpeg does not patch retained acl/ncurses/util-linux base packages.
  cjson and libsndfile also lack recorded Debian fixes even in sid/testing.
  Feature/dependency removal must actually remove affected code and preserve
  supported behavior; disabling required demuxers is a product-scope change.
- Bookworm is not a general fix; the tracker still marks the critical libxml2
  advisory and most others open there. Sid/testing mixing or a distribution switch
  is outside the minimal refresh and is not justified as a complete fix by this
  evidence. No such migration or source build was performed in this slice.

No blanket suppression, severity-only downgrade, or mitigation-as-fix is approved.
Production remains blocked until applicable Critical/High findings are remediated
or individually established as false positives, final-image provenance is recorded,
and both-architecture scans and FFmpeg/resdet compatibility checks pass.

## Saved evidence and reproducibility

External evidence is under
`/tmp/opencode/ecm-p1-security-evidence-20261023/native-research/`:

- `collect.py`: reproducible retrieval and complete index enumeration; writes only
  within its evidence directory. It joins scan findings to package `SrcName`.
- `debian-tracker.json`: complete unmodified HTTP response; SHA256
  `6044c3b5c07d71da803ee7a17a1a08078bd8389459196311e17fc13b27c3d1f9`.
- `debian-tracker-exact42.json`: exact 42 source/advisory records, scanner instances
  and official statuses; SHA256
  `ee051edabbf1349ffe3117960d0323fedc8c3a7043ef2362bead796a9293d1a3`.
- `repository-index-probes.json`, seven raw `.xz` indexes and per-response HTTP
  metadata; `parent-debian-current-policy.log` and its separate provenance.
- `inventory-provenance.json`, `native-inventory.json`, affected-source installed
  package/file inventories, upstream release notes and backports instructions.
- `SHA256SUMS`: checksums for all collected evidence and the collector.

Example verification: run `sha256sum -c SHA256SUMS` in that evidence directory.
Hashes identify captured bytes; this collector did **not** verify Debian Release
signatures or resolve installability. Runtime reachability and new image contents
remain unverified. This is complete research for the specified historical advisory
set, not a fresh scan, final security approval, or proof of current registry state.
