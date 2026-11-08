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

## Evidence so far — deliberately incomplete

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
