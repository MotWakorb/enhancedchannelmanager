# Published-image risk remediation — 2026-10-25

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
