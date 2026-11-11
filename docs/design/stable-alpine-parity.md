# Stable Alpine migration: compatibility acceptance

Tracks `enhancedchannelmanager-2ixgf` and
[ADR-017](../adr/ADR-017-stable-alpine-runtime.md).

The PO's requirement is explicit: **"Everything needs to be 100% compatible ...
it still needs to work identically."** Alpine is a platform substitution, not a
feature reduction, application refactor, security exception or permission to
change operator workflows. Any unverified or observable incompatibility remains
a blocker. Do not redefine a failing comparison as acceptable to complete the
migration.

## Baseline and comparison rules

- The isolated migration starts from application commit
  `06cc4bf5a1b9eb1f6ef29f617513d237cda2f531`. This identifies a source baseline,
  **not a claim that this commit is the deployed production image**.
- Compare the same application/lock/test definitions on the existing verified
  runtime and the Alpine candidate. Different application heads, old source
  indexes or earlier image receipts cannot establish platform equivalence.
- Preserve application Python pins and all public routes/tool schemas, error and
  cancellation semantics, on-disk formats, supported media and operator settings.
  Necessary build/tool/platform adaptations require explicit tests and review;
  they are not justification for unrelated application fixes.
- Run each paired scenario with equivalent synthetic inputs, controlled clocks/
  identities where needed, fresh owned volumes and the same upstream fixtures.
  Compare responses, persisted effects and cleanup, not merely exit status.
- Compiled bytes necessarily differ between glibc and musl. That does not excuse
  user-visible differences. Media output, timing bounds, signals, permissions and
  failure behavior need an explicit existing contract/expected result; do not add
  permissive tolerances or normalize away a real discrepancy without PO review.
- Missing evidence is **unverified**, not compatible. Cross-target wheel
  resolution/ELF inspection is not native ARM execution. A scan is not behavior
  proof; successful behavior is not security or source-delivery clearance.

## Required acceptance matrix

| Area | Preserve and exercise | Existing anchors / added proof needed |
|---|---|---|
| Python/runtime | All 74 pinned distributions, native operations, Python 3.12, absolute `/opt/venv` interpreter/healthcheck, default asyncio behavior | Full backend gate plus actual musl imports/operations on both native architectures; old manylinux attribution is not reused |
| Media capabilities | Existing decoder/input-format surface, Codec2/JPEG XL/SVG/game/tracker inputs, all enabled codec integrations, approved FFmpeg source/RTSP patch | Compare actual `ffmpeg` capabilities/configuration and retained synthetic fixtures; do not substitute a vulnerable or narrower stock FFmpeg |
| Probing | HTTP/HTTPS, TS, HLS/AES, DASH support at its existing boundary, direct/transformed modes, SSRF/DNS/redirect controls and time limits | Real production prober against the same controlled media/network fixtures; preserve known external viewer-coordination limits rather than claiming those solved |
| Relay and previews | Video/audio and video-only previews, encrypted HLS relay, black-screen/signalstats, error handling and bounded buffering | Actual native gate and backend consumer checks; compare results and resource cleanup, not just format listings |
| Cancellation/processes | GNU timeout options, TERM/KILL behavior, subprocess-tree reaping, retired-prober draining and truthful all/bulk terminal state | Existing OS-process/lifecycle tests and actual image execution; BusyBox substitution must not change command semantics |
| resdet | Required Y4M reader, current versions/licenses, resolution behavior and pixel ceiling | Build for musl and execute existing bounded image fixtures; no format/limit reduction |
| Identity/bootstrap | Default and custom PUID/PGID, user/group remapping, root-only setup then gosu privilege drop, file ownership and modes | Actual 1000:1000 and 1571:1572 create/restart/recreate cases; preserve shadow/coreutils/bash and reviewed gosu/toolchain behavior |
| TLS and trust | HTTPS/ACME, verification enabled, compromised G3 and Comodo AAA rejected, unaffected G1 and supported administrator anchors retained | Both system-default and certifi contexts, trusted/untrusted HTTPS probes, application TLS consumers; no blanket bundle replacement |
| Persistence/restore | Settings, auth material, journal/schema/WAL/foreign keys, backups and restoration, restart/recreation behavior | Fresh and existing-state synthetic fixtures on owned volumes; no testing against operator data and no on-disk migration solely for the distro change |
| MCP | Same tool schemas/count for the same app source, auth/principal/confirmation/replay boundaries, private credential projection, health/bind behavior, rotation and degraded repair | Full MCP tests, registered-tool→HTTP integration and shipped real-producer Compose isolation on both identity pairs |
| UI/API | Existing request/response shapes, save/progress/error state, responsiveness and frontend behavior | Full frontend/typecheck/lint/build and canonical backend tests; the base migration does not authorize new user-visible behavior |
| Source/notices | Exact installed APK/source/packaging correspondence, owned native sources/patches and actual musl wheel bundled artifacts | New APK/musl attribution and source consumer, including extra wheel-bundled GCC runtimes; do not relabel RPM/manylinux evidence |
| Security/publication | Complete OS/Python/Go inventories, immutable candidate/config/platform/all rootfs binding, unsuppressed findings and reviewed P1 holds | Fresh complete scans and native behavior on both architectures, independent review and protected exact-candidate publication; no lower floor or skipped format to obtain green checks |

## Musl-specific risk cases

Add actual platform checks where unit mocks cannot establish equivalence:
controlled DNS/search/CNAME/IPv4/IPv6 refusal and timeout behavior; locale/timezone
operations; native extension thread/stack behavior; dynamic loader/library
resolution; signal/subprocess behavior; passwd/group/account remapping. Retain
the current GNU command contract where the application depends on it.

GNU/glibc branding assertions and RPM-specific inventory fields must become
equivalent behavioral and APK-specific assertions, not disappear along with the
implementation they name. Keep strict completeness, architecture, source and
tampering controls on the replacement path.

## Current delivery status

The self-contained root ECM build and aligned MCP build now use stable Alpine.
`0.18.2-0066` is prepared for `enhancedchannelmanager-2ixgf.3`, not yet verified
or published. Fresh 0066 CI and independent review must precede protected PR
#1064 integration; exact-SHA ECM and MCP `:dev` publication must then be checked.
No production deployment is authorized by this preparation.

The paired acceptance validated at `84473c966171e1c4c42ef8ca2b417220aae5d33a`
is **0065 evidence**, not 0066 evidence. The retained images themselves carry
`GIT_COMMIT=89ad115a995cdf8aca72c38f122a117171ccca47` and dev-channel metadata;
the validation head must not be substituted for that embedded image marker.

| ECM platform | Manifest digest | Config digest | Recorded result |
|---|---|---|---|
| AMD64 | `sha256:4501f47d2f2738bcc6359118417fea806265155d44115149202617a50a5f21b6` | `sha256:6b73f704c1b761770e3a91ebf978f5ee8866cb8eb149c85dbe8adc0e321e50d1` | Complete scan PASS; 28 runtime checks PASS; both bootstrap identities PASS |
| ARM64 | `sha256:030a5c59d852b347f6b99d2dcc1f598e7b8d505e3420a3ccdbae376b1b427dcf` | `sha256:dd32b7d5e7f432a8a85883f8b162f252349a15b6528698c1a0b8751964c4de8d` | Complete scan PASS; 28 runtime checks PASS; both bootstrap identities PASS |

Each complete ECM scan covers 122 APK, 77 Python, 84 Rust and four Go packages.
Bootstrap covers `1000:1000` and `1571:1572`, including restart and recreation
persistence. The paired summary is retained at
`/tmp/opencode/ecm-alpine-final-image-debug-20261110/paired-final-acceptance-84473c96.json`.
The successful runs at validation head `84473c96` are
[Build 42900152482](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/42900152482),
[Tests 42900155041](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/42900155041),
[native Python 42900152490](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/42900152490)
and [native evidence 42900152724](https://github.com/MotWakorb/enhancedchannelmanager/actions/runs/42900152724).
These are pre-integration results, not evidence of mutable-tag publication.

The [advisory assessment](stable-alpine-advisory-feasibility.md) and follow-ups
`enhancedchannelmanager-evy2p`, `eeh2i`, `4yxbm` and `m8m42` remain in scope but
deferred by the PO. Complete scanner coverage does not establish advisory
clearance; bounded source consumers do not establish full source reconstruction.
Existing HIGH/CRITICAL and reviewed-P1 promotion checks remain unchanged.

## Historical incremental evidence — superseded delivery status

The pending statements below describe earlier trees and remain as a record of
the failures and repairs. Current results are above; original failed receipts
have not been relabelled as passing.

**2026-11-10 final-image repair:** Both full ECM candidates compiled, but paired
Trivy 0.76.1 reports omitted exactly `ecm-owned-native` (121 scanned versus 122
installed APKs): its old `1.0~<digest>-r0` version is rejected by Trivy. The
producer now uses eight decimal uint32 components retaining the full SHA256.
A synthetic database grammar probe detects all 122 packages; this is not proof
of a rebuilt/installed candidate. Recorded paired real reports remain regression
fixtures that must fail complete-inventory validation.

The first corrected-version ARM64 build installed the APK, then exposed two
downstream filename validators still restricted to the old format. Both now
use a shared exact producer/component-derived basename and version check, not
a broader filename regex. A paired-architecture transport regression crosses
the actual retainer and namespace reader with unsigned stand-ins; this verifies
consumer wiring, not native signature or final-image acceptance.

Comprehensive scanning of the retained AMD64 image detects the APK-owned
`gosu` Go metadata (four packages), plus 84 Rust packages, without relaxing the
inventory gate. Both ECM collect/block steps now request that mode. The same
immutable retained image (`sha256:6d21546425a525b44248644731e2121ad0f3097db2d3a301e0203d280e183f04`)
passes both bootstrap identities and all 28 runtime checks with the repaired
gate. Its runtime phase takes 373.764 seconds, including the mandatory full Rust
compiler-source audit; the aggregate deadline is now 900 seconds rather than
240. Individual functional timeouts are unchanged. Historical producer bytes
are retained as exact hash-bound data, never executed, so existing signed
receipts remain verifiable. **Fresh corrected-package builds, scans and runtime
acceptance on both native architectures remain required.** Track this work in
`enhancedchannelmanager-2ixgf.2`; no publication or advisory clearance is claimed.

**2026-11-10 update:** The earlier feasibility bullets below are historical.
Native full candidates have since built on AMD64 and ARM64, with functional
runtime and bootstrap checks passing; their aggregate source check still used
the old RPM layout. The Alpine replacement composes the existing external APK,
four-owned-package, Python-base, installed wheel/GNU and Rust-input consumers.
Its targeted composition/delivery/gate regression subset passes 231 tests; real
paired-image execution of the new composition is still required. It explicitly
does **not** claim full runtime source or advisory acceptance.

The PO has prioritized finishing the containers before further advisory fixes.
See the [ADR priority update](../adr/ADR-017-stable-alpine-runtime.md) and
[completed assessment](stable-alpine-advisory-feasibility.md). Normal build-path
integration, MCP stable-base alignment and APK scanner contracts remain open;
production promotion checks are unchanged. The working implementation now wires
the root `Dockerfile` to the self-contained Alpine graph and aligns MCP's pinned
base. Their build/runtime and scanner verification is in progress; these edits
are not yet completed native acceptance. See the
[container build instructions](../../docker/alpine-native/README.md).

- Stable-only repository policy: 26 direct regressions; 330 policy/SBOM checks
  pass. The guard also runs read-only/offline on actual existing MCP stable
  Alpine files. It is not yet production Docker/CI integration.
- Python feasibility: all unchanged 74 pins resolve to actual verified wheels
  on x86_64 and aarch64. Native x86 installs/imports/selected dependency operations
  pass; native ARM execution and complete application behavior remain unverified.
- Proposed official Python base preserves Python 3.12 on stable Alpine; using
  distro `apk add python3` would instead select Python 3.14 and is not equivalent.
- The [native feasibility result](stable-alpine-feasibility.md) confirms three
  missing libraries, three security-unclosed providers and additional version/
  behavior/source/scanner gaps. A stock-package-only substitution does not meet
  this matrix. No full Alpine ECM image, paired parity run, complete source
  closure, current-head native ARM gate or final security/publication pass is claimed.

The matrix is a delivery checklist, not proof that literally every possible input
has been tested. Keep the migration open until every in-scope contract has actual
evidence and every remaining difference/blocker is explicitly resolved.

The [owned native implementation](stable-alpine-owned-native.md) records the
component builders, exact bounded Codec2 correction and review-driven security
work. Its separate native build workflow is deliberately not final acceptance.
