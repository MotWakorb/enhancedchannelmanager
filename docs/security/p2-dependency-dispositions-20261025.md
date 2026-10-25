# Dependency and native robustness dispositions — 2026-10-25

This is a bounded assessment, not a clean-image, release or deployment approval.
Beads: `enhancedchannelmanager-fykpz`, `enhancedchannelmanager-6ayl0`.

## MCP SDK / multidict: repairs already delivered

PR #1030 / merge `f78199eb2182dd146e4002239f80eaead8db88c0` delivered MCP SDK
**1.30.0** and multidict **6.9.1**. Those pins are present in the current protected
baseline. Do not repeat the SDK 2.x migration or claim it is required for these
patch-line advisories.

Fresh `pip-audit 2.10.0` scans of all exact declared pins (`--no-deps --disable-pip`)
report **zero known findings** for 74 backend and 41 MCP dependencies. This scans
the complete declared pinned files, not an installed container or undeclared
package-manager vendors. Reproduction:

```sh
python -m pip_audit -r backend/requirements.txt --no-deps --disable-pip --format json
python -m pip_audit -r mcp-server/requirements.txt --no-deps --disable-pip --format json
```

| Evidence | SHA-256 |
|---|---|
| Backend requirements input | `7fbaf818e6617efc2bbd54e07af7ef5a7eac300712ad7b29fa5c6b4ada17a2bf` |
| MCP requirements input | `de79f8bc7af6839295f4dfcb225e063fec46e841035468d17eaf84f48324833f` |
| Backend raw audit JSON | `453a4a955652728e28a2086c14d5be4c42911e61fef52e4edef9a66c70adbbeb` |
| MCP raw audit JSON | `0dfc9b18987aa897b9dac8d8cc43a9f0fccdbda88f25dbbd3e2b8e3bab5f44c1` |

The source audit's clean result does **not** override image evidence: protected
baseline still pins Mako 1.4.1, and source requirements do not enumerate retained
runtime pip vendors. Remaining Mako and pip delivery is already owned by
**`h1186` and `d7c7f`**, both blocked under draft PR #1040. Its Mako 1.4.2 and
build-only pip 26.2.2 candidates have separate verification, but have not merged or
published. Closing the overlapping source-assessment work must not close those
delivery items or imply the published images were repaired.

## x265: separate unchecked-return concern, not the corrected CVE

The reviewed exact x265 4.1 source includes the corrections for **CVE-2017-13135**.
Separately, `Encoder::encode` ignores `Frame::allocEncodeData` and the frame worker
ignores `Analysis::create` failure in pool/no-pool startup. The owner waits on
worker initialization, so a speculative early return could introduce a deadlock.

Bounded static ECM-local reachability review found no selected x265 encoding:
preview copies video (its transcode mode changes audio), metadata uses ffprobe,
resolution emits Y4M and black-screen detection emits null output. HEVC decoding,
linked encoder availability and CI's x264 fixtures do not establish x265 encoding.

**External boundary:** ECM forwards operator-provided Dispatcharr stream-profile
commands/parameters and can assign them to channels. External x265 encoding,
library identity and failure behavior were not inspected or exercised. Do not
claim the integrated deployment cannot select x265, or that all arbitrary codec
arguments are rejected. Local shell operators can also invoke the retained codec.

Fresh official upstream checks found the discarded returns still present in
release **4.3**, commit `e9b88125dc21393b3fd8d68e98083bdfb89778a8`, and inspected
master `5143a2a00ad0cc600831654a248b96ec7307eb4a`. A version bump alone is not a
demonstrated correction. No allocator-fault test, crash, remote trigger, measured
likelihood or CVSS is established here.

**Disposition:** keep an unscored conditional robustness concern; prefer a
maintainer/distributor-owned correction, not an ECM fork or codec removal. Draft
a separate upstream report identifying those three unchecked call sites and the
owner wakeup requirement, labelled static evidence without a reproducer. Posting
or maintaining a fork needs a separate scope decision. Before claiming a fix,
require owner wakeup/error propagation, safe teardown and allocation-fault tests
for pool/no-pool, supported pixel depths and both architectures. Future ECM-local
HEVC encoding or actual affected Dispatcharr profiles trigger reassessment.

Official sources: [maintainer repository](https://github.com/Multicorewareinc/x265),
[4.3 release](https://github.com/Multicorewareinc/x265/releases/tag/4.3),
[4.3 encoder source](https://github.com/Multicorewareinc/x265/blob/e9b88125dc21393b3fd8d68e98083bdfb89778a8/source/encoder/encoder.cpp),
[4.3 frame worker](https://github.com/Multicorewareinc/x265/blob/e9b88125dc21393b3fd8d68e98083bdfb89778a8/source/encoder/frameencoder.cpp).

Local enforcement context: `backend/routers/stream_preview.py`,
`backend/stream_prober.py`, `backend/routers/profiles.py`,
`backend/dispatcharr_client.py` and `docker/native/ffmpeg.args`. This document
records the inspected source paths, not a fresh binary/ARM/runtime attestation.
