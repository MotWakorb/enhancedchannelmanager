# Replacement native source dispositions — 2026-10-24

This is static source/prototype correspondence, **not VEX, an ignore file,
scanner suppression or final-image approval**. Original severity and instance
counts remain in [the historical inventory](native-image-triage-20261023.md).
The 16 original FFmpeg corrections are recorded in the
[signed-release review](ffmpeg-8.1.3-source-review-20261024.md).

## Remaining 26 historical advisories

The observed native-only amd64 prototype is
`sha256:dbf62d0f6b72a25b7205628ce7f1a5a7d2b1ad0a76a3f56daddb2aa88847910b`.
It is not the final application image. Its complete graph has 212 RPM/key rows,
not the smaller base-only inventory. Seventeen rows are source-fixed and nine
are absent in that prototype; all require final per-architecture binding.

| Advisory | Source | Reviewed source/prototype disposition |
|---|---|---|
| CVE-2026-54369 | acl | Exact 2.4.0 source/release correction; safe `_at` APIs do not change every legacy caller's semantics. |
| CVE-2026-16554, -29036, -67215, -67216, -87933 | cJSON | No runtime provider/ELF dependency or owned FFmpeg integration in the reviewed graph. Not a fixed-release claim. |
| CVE-2026-37555 | libsndfile | No runtime provider/dependency. FFmpeg's attributed ADPCM constants are not the affected libsndfile implementation. |
| CVE-2026-88806 | libX11 | Exact 1.8.14 source includes the upstream keycode-range checks; distro patches do not revert them. |
| CVE-2026-6653 (Critical) | libxml2 | Exact 2.15.4 source is outside the CNA's affected interval `[2.9.11, 2.11.0)`. Critical severity is retained. |
| CVE-2026-74860 | libxml2 | Exact Python SAX correction: removed stolen-reference decref matches upstream `f41e1865781f74d1cadfe2fbdfeefed946026f12`. |
| CVE-2026-86138, -86139, -86140, -86142, -86143, -86144 | libxml2 | Six CVE-specific 2.15.4 source corrections match upstream patch postimages. |
| CVE-2026-88807 | libXrender | Exact 0.9.13 numSubpixel/numScreens checks match the upstream correction. |
| CVE-2025-69720 | ncurses | Reconstructed packaged weekly-patch source retains infocmp's checked buffer bound; inspecting only the base tarball is insufficient. |
| CVE-2026-9538 | Archive::Tar | No Perl runtime/provider in the graph. Final module/filesystem and copied-artifact absence must still be checked. |
| CVE-2026-16742 | systemd | Exact 261.4 source contains both 261.2 corrections. Only libraries are present; disposition does not depend solely on homed absence. |
| CVE-2026-36849, -52490 | libtiff | libtiff is builder-only, not a runtime provider. FFmpeg TIFF decoding is not libtiff/tiffcrop. No fixed runtime source hash is invented. |
| CVE-2026-76642, -78408, -78409, -78410 | util-linux | Exact 2.42.4 source has helper/post-hook, pinned mount source and cgroup-FD corrections. Conservatively require 2.42.4 for -78408 despite inconsistent advisory version metadata. |

### Exact reviewed amd64 source inputs

All seven SRPMs authenticate to SUSE fingerprint
`AD485664E901B867051AB15F35A2F86E29B700A4`. Complete archive hashes are below.
ARM sources must be authenticated separately: an identical filename does **not**
prove identical SRPM bytes. Compare the complete signed Source/Patch payload
hashes against the reviewed inputs before reusing a source conclusion.

| Source RPM | SHA-256 |
|---|---|
| `acl-2.4.0-1.2.src.rpm` | `32e06a851df3287ef2e581ce8412c54c6b7494d6db31e3d019849e4417c30186` |
| `libX11-1.8.14-1.1.src.rpm` | `dc46559e5f61ce97bab634f015b6934b6f8da764c6d678bd5462a9471706a565` |
| `libxml2-2.15.4-1.1.src.rpm` | `21284a4bfa8cf27445f224bb12a88670d03d9af1c4d3d0e180560bb7177301a7` |
| `libXrender-0.9.13-1.1.src.rpm` | `c11a1e2e2dee4b57176c5759435dbf6c6fd5719af0eecc580bfbabc5cc351150` |
| `ncurses-6.6.20261017-116.1.src.rpm` | `e81c56190811a968c223c994211449513432c489984a2248446592f2ba2c371f` |
| `systemd-261.4-1.1.src.rpm` | `420925e27773683f0b955dfa9861f40aebdb86fc6cf8339d238cdd37495ce057` |
| `util-linux-2.42.4-1.1.src.rpm` | `80af20dc5ac09277c051bb1d4b8377fae19a0f928445d468ca287983764c9363` |

The detailed 26-row source review, upstream postimage correspondence and exact
patch reconstruction are retained in
`/tmp/opencode/ecm-native-remediation-20261023/suse-historical26/`.
Their final-image selectors are not permission to assume matching artifact bytes.

## Eight additional FFmpeg snapshot advisories

All eight corrections are present in signed FFmpeg 8.1.3 archive SHA-256
`7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3`.
This correspondence does not rely on development-snapshot version ordering.

| Advisory | Correction correspondence |
|---|---|
| CVE-2026-38344 | Initialize chroma filter before returning luma cascade; upstream `dc9bf66796b7c1f4329f127736ddf3258e5ba206`. |
| CVE-2026-38346 | Unsigned accumulation in `yuv2planeX_8_c`; `a59180022af5447d8b2847f539fb99c859175e95`. |
| CVE-2026-38347 | Bound extra row and clamp next column in all relevant endian paths; `b7946098b19af4f06661213d591a5ed9cd3d26ff`. |
| CVE-2026-38348 | Compute increments in int64 and reject outside `[10, INT_MAX]` before narrowing; `946ce12e1c36e0cbfca2145ec66dcbc28350de38`. |
| CVE-2026-38349 | SUINT accumulators/multiplier casts in both alpha paths; `1e63151355342228584e30dab2c53a2cb0d19b42` and `0ab097fb344ab55b207d0431fff8aa32ecdef70f`. |
| CVE-2026-38350 | Both chroma-plane operands cast through SUINT before multiplication; modular result converted before shift. Source-derived correspondence, not a CVE-tagged upstream fix. |
| CVE-2025-52290 | Replacement decoder context uses new codec ID; both update paths close the old codec and H264 helper requires an open codec. Source-derived correspondence to `59a6660625675c8343f3de858b93c8294f05bb52` / `4bb9b46486b3f02a5d826ca93d5192bcbfb84ba5`. |
| CVE-2026-96611 | Reject invalid ispe dimensions and overflowing coded totals before narrowing; `2cc7b87bdb75bcb59bf8bcd5296ca43f89b3a909`. |

SUINT reasoning requires normal non-`CHECKED` production arithmetic. Official
FFmpeg issue pages/API returned Anubis challenges; they were not bypassed.
Public upstream history/source and captured primary records supplied the
correspondence, with limitations explicit in
`/tmp/opencode/ecm-native-remediation-20261023/stable-release-applicability/`.
No adversarial reproduction or general decoder-safety claim is made.

The separately accepted RTSP redirect-limit patch remains an ordered, hash-pinned
recipe input with exact before/after witnesses; these eight rows do not replace
that correction or authorize omitting it.

## Other retained native boundaries

- Both services select cryptography 50.0.2; the actual cryptography backend must
  report released OpenSSL 4.0.x >=4.0.3. System `ssl` alone cannot verify this copy.
- Gosu requires Go 1.26.10, `moby/sys/user v0.4.1` and `x/sys v0.44.0`; inspect
  compiled module/compiler records and the final executable, not just go.mod.
- x265 4.1 contains the two failed-pool/initialize guards for CVE-2017-13135.
  Separate unscored higher-level allocation propagation is `enhancedchannelmanager-6ayl0`.
- libuv CVE-2024-22017's privileged-loop-init then **in-process** identity-drop
  sequence is absent from the reviewed startup: gosu drops identity before exec
  and Python creates the loop afterward. This is not a universal libuv fix claim.
- Reviewed YAML application/SDK consumers use pure-Python SafeLoader, not the
  native parser affected by the unscored maintainer nesting correction. This is
  not general YAML resource safety; composition budgets are `enhancedchannelmanager-10tln`.

## Final binding and acceptance

For **each** final architecture, join the checked source index to actual RPMs,
matching Source/Patch payloads, owned artifacts and approved wheel inventories.
Recheck absence rows against the final application/venv filesystem, not just RPM
names. If selected bytes or build conditions differ, reopen that specific row.
Require the full 28-check/bootstrap matrix and complete unsuppressed scan on the
same verified immutable OCI subject before publication. Neither this document nor
an empty SUSE scan supplies that missing evidence.
