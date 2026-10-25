# Published-image risk remediation — 2026-10-25

> **Current status: draft, not published.** MCP native candidates pass on both
> architectures with zero all-severity findings. ECM ARM awaits vendor-fixed
> packages; ECM AMD's two missing exact source artifacts have been recovered and
> authenticated, with selected hash-pinned routes implemented and live-tested.
> Complete rebuilt-image/source verification is still required. Earlier receipts below
> are retained as history, not the current MCP result.

## Scope and baseline

The fresh all-severity Trivy 0.76.1 database from **2026-10-25 07:10 UTC**
identified five component groups in published build **0.18.2-0047**, merge
`0bc126006d96c03637cb9e1b707ec04aca2d5804`. Both amd64 and arm64 were scanned by
immutable platform digest; the images were not executed during that assessment.

This change targets **0.18.2-0048** and implements remediation for those five
groups; delivery remains blocked as described below. It does not
authorize a production rollout, a release cut, unrelated dependency upgrades,
supported-format removal, severity filtering, or an exception to existing gates.

## Required fixed boundaries

| Component | Reported finding | Required correction |
|---|---|---|
| ECM Brotli | CVE-2026-111497 / openSUSE-SU-2026:12232-1 | Vendor runtime Brotli libraries at least **1.2.1-1.1** on both architectures. |
| ECM Mozilla CA package | CVE-2026-114406 / openSUSE-SU-2026:12233-1 | Vendor `ca-certificates-mozilla` at least **2.92-2.1**, with both compromised roots distrusted. |
| MCP musl | CVE-2026-107891 | Alpine `musl` and `musl-utils` at least **1.2.6-r3**. |
| MCP pip | Six installer advisories below | Hash-pinned **pip 26.2.2** for dependency installation only; remove its complete payload, wrappers and old ensurepip seed wheel from the final runtime rootfs. |
| ECM Mako | CVE-2026-102991 / GHSA-5639-2j2p-m4mx | **Mako 1.4.2** with matching current dependency/source/notice attribution. |

The pip findings are CVE-2025-8869, CVE-2026-13346, CVE-2026-3219,
CVE-2026-6357, CVE-2026-8643 and CVE-2026-1703. Python 3.12's specific tar
extraction mitigation is not treated as a fix for all six.

## Risk interpretation is not a waiver

- **Brotli:** Trivy/SUSE labeled the package advisory Medium, while the Google CNA
  rates the decoder memory-corruption vulnerability **Critical / CVSS v4 9.2**.
  The lower label is not a reason to leave the affected decoder in the image.
  An application-specific exploit was not demonstrated by the initial scan.
- **CA trust:** the bulletin names both **emSign ECC Root CA–G3** and **Comodo
  AAA Services / AAA Certificate Services**. The baseline system/certifi bundles
  already omitted both, but the RPM was below the vendor fixed boundary. The
  existing exact G3 blocklist alone must not be described as the entire CVE fix.
  Keep G1 and unrelated administrator roots; do not replace system trust wholesale
  with certifi or disable TLS verification.
- **musl:** the distribution patch corrects integer-overflow handling in POSIX
  `regcomp()`, not DNS. The scanner lacked CVE details/scoring; **Unknown is not
  Low**. Direct MCP exposure was not established, and is not needed to justify
  taking the vendor's fixed package.
- **pip:** installer/index/archive conditions differ from the ordinary MCP HTTP
  request path. Bootstrap the corrected installer before dependency installation;
  its complete self-uninstall and obsolete seed removal retire runtime installation.
  Preserve the source records' differing CVSS v3/v4 ratings.
- **Mako:** the named traversal mechanism requires Windows path behavior absent
  from the Linux images. The requested patch is still applied; no ignore rule or
  platform-based scanner suppression is substituted for it.

## Preservation and verification

### Build-only installer boundary

The first retained-pip candidate failed the full scan because its private bundled
dependencies remain vulnerable. Official latest pip **26.2.2** updates its certifi
bundle but still contains affected msgpack, pkg_resources and urllib3 versions.
It is therefore used only for the existing dependency-installation task, followed
by dependency validation and supported RECORD-based self-uninstallation. Remove
the old `ensurepip/_bundled/pip-25.0.1-py3-none-any.whl` too: it is a separate
inherited payload, not owned by pip's installed RECORD. Do not delete only scanner
metadata or replace unrelated top-level application dependencies.

The final runtime must lack pip's module/vendor tree, distribution metadata,
command wrappers and every ensurepip seed wheel. Runtime `pip`/`ensurepip`
bootstrapping is intentionally unavailable; standard-library `importlib.metadata`
and all **41** exact application distributions remain intact. This claims absence
from the merged runtime rootfs, not erasure of bytes from inherited OCI layers.
The source SBOM records the reviewed wheel/hash as a **build dependency**, not
runtime software. The fresh replacement-image scan and native import/health,
crypto/rootless checks must independently prove this boundary.

Wheel SHA-256: `fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70`.
Official PyPI wheel bytes/hash and supplied-certificate signatures were checked;
full Sigstore trust-chain/transparency verification was not performed. No claim
that all build-tool vendor risks are fixed or fully provenance-verified is made.

Keep the signed FFmpeg/gosu recipe, codec/input coverage, resdet, architecture
boundaries, MCP rootless defaults, custom UID/GID startup, and source/notices
delivery. A fixed-version assertion must inspect the actual architecture's image,
not infer arm64 from amd64 or accept package metadata from an unrelated image.

Acceptance requires:

1. Test-first fixed-version/recipe regressions, including rejection of the old
   versions, and preservation of both CA negative controls and G1 positive trust.
2. Exact runtime RPM/source correspondence and unchanged approved native wheel
   bytes except for explicitly reviewed changes; Mako attribution matches its new
   public wheel, while unrelated Python dependency versions remain unchanged.
3. Current source SBOM, canonical backend/frontend/MCP quality gates, all 28 ECM
   native checks and bootstrap cases, and MCP actual-image checks on both platforms.
4. Fresh complete **all-severity** scans of the verified immutable candidates.
   Confirm all five original groups are resolved; do not equate a zero High/Critical
   count with resolution of Medium, Low or Unknown findings.
5. Protected merge followed by exact-merge candidate attestation, successful
   publication and registry manifest/config/version/source-marker verification.

The original scan's raw severities and limits are retained rather than rewritten
as if the old images had already contained the fixes. Trivy package scanning is
not a complete audit of custom/static native code or deployment configuration.

## Tracking and evidence

### Delivery status: blocked on official ARM packages

On **2026-10-25**, the native amd64 repository snapshot `20261023` supplies the
fixed Brotli `1.2.1-1.1` and Mozilla CA `2.92-2.1` packages, with exact signed
sources verified by the existing source collector. The native aarch64 snapshot
`20261020` still supplies Brotli `1.2.0-2.3` and Mozilla CA `2.90-1.1`; the four
fixed ARM binary URLs and two exact source URLs return 404. A second retrieval
confirmed unchanged official repository metadata. The new constraints therefore
intentionally block an ARM image build rather than delivering affected packages.

The source fixes and local checks are not a completed multi-architecture delivery.
Keep the remediation PR draft and all five beads open pending official native ARM
availability, matching signed sources, both native CI lanes, complete scans and
exact-merge publication. Do not mix in another architecture's repository, waive a
floor, or treat synthetic positive-control inventories as actual image evidence.

The first disposable amd64 MCP candidate (`c0a6988cc4b6…`) removes the original
musl/pip findings but fails the complete scan: pip's bundled dependencies produce
**1 Critical, 4 High, 2 Medium and 1 Low** finding (certifi, msgpack, setuptools
and urllib3). That candidate is rejected, not published. A successful replacement
must correct the installer delivery without hiding its bundled dependencies or
altering the unchanged application pins; its full scan must be reviewed again.

The build-only replacement local amd64 MCP config
`sha256:a243ebfec4596e7ee0c94359521e105852ab8640c3706d985e2f1a34c3bc9b04`
passes the production absence/musl/crypto/rootless checks, all 41 exact app pins
and imports, and actual in-process health/auth checks. Its complete unsuppressed
Trivy 0.76.1 scan (same current October 25 database, unchanged bytes before/after)
reports **38 APK packages, 41 Python packages, zero findings at all severities**.
Report SHA-256: `525a310ba9cec55738bf9e7ff5829fba136be96b51c0c901332af0c2137cb9ce`.
This closes the local retained-installer regression, not native ARM verification,
exact-source-commit publication, historical-layer erasure or a complete code audit.

Initial PR #1040 CI also demonstrated a separate AMD build-coherence failure:
cached native provider output pins obsolete Brotli/util-linux/LAME packages, and
a clean native builder cannot resolve the old LAME/Python descriptors. Refresh
only the demonstrated stale official AMD builder descriptors and rebuild exact
provider output. This necessarily changes the native recipe hash; the original
`de91b41e…` receipt remains historical. Preserve all FFmpeg/gosu source/configure/
key/patch/Packman inputs and native wheel objects; do not strip provider versions
or lower the security floors to reuse the cache. Full rebuilt native/source
verification is still required, not established by the descriptor update.

The authenticated AMD descriptor refresh selects LAME **4.0-1.1** (previously
3.100-5.16) and Python **3.12.15-2.1** (previously 3.12.15-1.1). LAME 4.0 is an
upstream version change, not merely an RPM release bump: FFmpeg compilation,
linkage and actual media compatibility remain required native CI evidence.
All 47 AMD descriptors were checked against signed official metadata; only these
two were stale. Both replacement binaries and exact signed SRPM closures passed
the existing verification tools. No source/ABI compatibility is inferred solely
from that result. The new recipe SHA-256 is
`7ae24969cf1636679bdf766242ee2c716683b112241da009fe20899c76aa9a46`;
ARM descriptors and FFmpeg/gosu source/configuration/key/patch/Packman inputs
remain byte-exact.

### GitHub follow-up: MCP green; ECM source availability still blocks

PR #1040 head `1ff46f22652c2531a55107740dbb38dd0888eb3e` was tested in
[build run 40203997657](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/40203997657)
and [test run 40203997677](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/40203997677).
Both native MCP builds, actual crypto/installer-absence checks and High/Critical
container security gates pass. Backend/frontend tests, both CodeQL analyses and
dependency security scans pass. The source-pinned FFmpeg producer compiles on
both architectures, including AMD's refreshed LAME 4.0 dependency; complete final
ECM runtime/media/bootstrap/source checks remain unexecuted, not inferred to pass.

- **ECM ARM:** the vendor-fixed Brotli/CA floors remain unavailable.
- **ECM AMD:** the refreshed runtime builds, then exact source collection fails
  HTTP 404. Signed binary metadata is snapshot **20261023**, while signed source
  metadata is **20261024**. Two required observed runtime source identities return
  404: `harfbuzz-14.6.0-1.1.src.rpm` and
  `openSUSE-release-20261023-4366.1.src.rpm`. Newer source versions are not valid
  substitutes. The historical traceback did not log the failed URL; HarfBuzz is
  the earliest currently failing candidate, not a directly logged request.
- **MCP Server Tests job:** its ECM build for the live isolation test hits the
  same source-collection 404; this is not a newly failing MCP unit test.

An exact cached HarfBuzz SRPM was freshly reverified against the pinned vendor key,
header and complete Source/Patch payload. Recovery of the exact release SRPM is
still required before any source-cache route can be complete. Track the additional
source-delivery blocker as `enhancedchannelmanager-7rsy7`; do not skip a package,
substitute a newer SRPM, relax signatures or bypass this gate. Flushed public exact
source-URL diagnostics make future failures attributable without changing failure
or integrity semantics. They are not themselves a fix for missing source bytes.

Subsequent bounded recovery authenticated the **exact** release SRPM at the source
mirror published by openSUSE's official mirror report:
`https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261023-4366.1.src.rpm`.
SHA-256: `0ca30fbc607834c8c5633ffe977f9d7bc1bd766a430976fec09185ae54d2869e`.
Pinned signer, exact source header and complete Source/Patch payload checks pass.
The earlier “not recovered” status is superseded; production integration is still
subject to complete rebuilt-image verification. The implementation selects only
these two exact `(openSUSE, x86_64, filename)` routes, with the same independent
URL/hash pins in the native-image consumer:

| Exact source filename | Selected requested HTTPS URL | Required complete-file SHA-256 |
|---|---|---|
| `harfbuzz-14.6.0-1.1.src.rpm` | `https://slc-mirror.opensuse.org/source/tumbleweed/repo/oss/src/harfbuzz-14.6.0-1.1.src.rpm` | `53e21e5cb510895c46935f1f6325415846549316e200fb88a69d2a66b3fbc65f` |
| `openSUSE-release-20261023-4366.1.src.rpm` | `https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261023-4366.1.src.rpm` | `0ca30fbc607834c8c5633ffe977f9d7bc1bd766a430976fec09185ae54d2869e` |

The modified producer performed complete real GETs and unchanged signature/header/
payload verification of both exact artifacts. That two-origin sample is **not** a
complete final-image inventory. Hash pins apply to both downloads and existing
cache reuse; a caller-supplied hash index cannot redefine them. Requested/final
URL records remain truthful. Old ledgers naming canonical URLs for these two
origins must be regenerated, not relabeled. Other vendor/architecture/filename
routes, including all ARM and Packman routes, remain unchanged. No generic mirror
prefix, automatic fallback/retry, newer source substitution or weaker key/header/
payload check was added. Mirror loss or a one-byte mismatch remains fatal.

The two immutable MCP candidates from that same CI run were independently scanned
at **all severities**, not just the CI High/Critical threshold: **zero findings,
38 APK / 41 Python packages each**, with unchanged current database bytes.
Archive/config/rootfs IDs and native crypto/absence receipts match the actual PR
test merge `0a5c3085d196b282d306e75c002a05c34132a3f2` (parents are protected base
`0bc12600…` and PR head `1ff46f22…`). They are not published/final-merge subjects.

| MCP platform | Verified OCI manifest | Fresh complete report SHA-256 |
|---|---|---|
| amd64 | `sha256:74eb5d62fd70dec69d385671ffe5a6c665595a815d34aa90b4f99db82cdf51b2` | `dc60a6126c4adb85287056ef987848ce0477f806f96a03909819c09cb2fda5f4` |
| arm64 | `sha256:d54dbbec01104a79735a9a1b0e5a944044d569e533ff6f228e4b11cd4583008c` | `a3a354b99f3907c87f9e2972a5a7834569a002ddc8148ec436f6dc15854837b1` |

- `enhancedchannelmanager-l5z6r` — Brotli.
- `enhancedchannelmanager-97j9u` — Mozilla CA package and trust coverage.
- `enhancedchannelmanager-9sc07` — musl.
- `enhancedchannelmanager-d7c7f` — pip.
- `enhancedchannelmanager-h1186` — Mako.

Assessment, raw scan reports, primary advisory records and hashes:
`/tmp/opencode/ecm-image-risk-scan-20261025/`.
Implementation and verification receipts:
`/tmp/opencode/ecm-image-remediation-20261025/`.
Final CI/publication evidence must identify its actual commit and image digests;
earlier baseline receipts are not relabeled as this delivery's verification.
