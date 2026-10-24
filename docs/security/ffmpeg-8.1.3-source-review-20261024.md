# FFmpeg 8.1.3 source review — 2026-10-24

**Scope: verified upstream source, not a built-image approval.** This records the
fixed source line for `enhancedchannelmanager-x1xne`. The current Wolfi prototype
is rejected for publication. Both-architecture build, compatibility, source and
notice delivery, complete scans, and publication evidence remain required.

## Signed release identity

- Archive: <https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz>
- Detached signature: <https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz.asc>
- Release key: <https://ffmpeg.org/ffmpeg-devel.asc>
- Verified signing fingerprint: `FCF986EA15E6E293A5644F10B4322F04D67658D8`.
- Archive SHA256: `7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3`.
- Signature SHA256: `5f2880c7fea01f8694d30cb9d06362f77241ee67ce5926b157a226b18f08a538`.
- Key-file SHA256: `397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5`.

The archive signature verified with that exact key. Seventeen affected files
extracted from this signed archive were byte-identical to the upstream `n8.1.3`
files inspected for the patches below. This is stronger than inferring fixes from
a version range, but does not prove which source an unrelated binary contains.

## Original 16 FFmpeg advisories

The [upstream security page](https://ffmpeg.org/security.html), release source,
stable backports and individual
[CVE Program records](https://cveawg.mitre.org/api/cve/CVE-2026-58049) were reviewed.
The table identifies the actual stable fix commits in
<https://github.com/FFmpeg/FFmpeg>; append `/commit/<commit>` to inspect a change.
All resulting security hunks were present in the inspected release source.

| Advisory | Stable fix commit | Correction |
| --- | --- | --- |
| CVE-2026-58049 | `cf148db1a82c8a4dc3ef13407962f901956c10b8` | RASC row-bound checks |
| CVE-2026-64830 | `4fe710047d633ddd7a126352ce0f5f505a0fe843` | VobSub stream-count bounds |
| CVE-2026-64832 | `9cbcf979a587a66b280496657d00fabe301bf20f` | NVDEC error-path double free |
| CVE-2026-64833 | `9a92fb0034bc56045e3211a04b08c145a4594eda` | S/PDIF DTS packet-size validation |
| CVE-2026-64834 | `c26e3cd2f879dc7bea3d0ee00dc5353f7bc9842d` | RTP/ASF minimum object size |
| CVE-2026-64835 | `852b0552f072826723749db6891928d839f536d8` | ADX channel/extradata synchronization |
| CVE-2026-66036 | `c8bfe73e26cbb16a99d252186acb66f717a06f7c` | hqdn3d changed-size/format rejection |
| CVE-2026-66039 | `228fd8f0caca7d698ec8e06497ac6e2df436dadc` | MACE sample-count overflow |
| CVE-2026-66040 | `45f1910444f34b02621f9f0426ea1a538a613c41` | PNG/APNG parsed EXIF sizing |
| CVE-2026-66041 | `6757c200cdd0e80cabde7a19e1261a3ffd118c54` | quirc buffer resizing |
| CVE-2026-70628 | `46ef25c9c2d2bd470681e47f6a015b5e8e1312e7` | DVB subtitle overflow-safe capacity |
| CVE-2026-70632 | `ea721086022772d88ef5dd262d7cff319d4c29f5` | CFHD output-width validation |
| CVE-2026-75142 | `3193e032015543806e6b24d2f1eea2db76a0c527` | MPEG-PS system-header bounds |
| CVE-2026-75143 | `4e1b9a41bab1dfd210ad53654e9dfab3cf78884f` | RIST caller-buffer bounds |
| CVE-2026-75144 | `be8a4974e8fd41c4936546f8ef6ac9e8a873bb33` | VC-2 RTP payload bounds |
| CVE-2026-75146 | `62d4ca1f3ead52ac45c9ceae761e0139f42bfd4b` | DASH negative-index/delta checks |

For **CVE-2026-66036**, the CNA references dynamic-size enhancement
`5d7112c60e6f0f0742ce47d448e6da0718a70f4c`. Its preimage already contains the
security rejection introduced by the immediately preceding
`f0f634b6585fdc7bbb43ab3ae461499bfca9ad2e`. The stable backport has the same
security changed lines; the later enhancement is not claimed to be backported.
For the other 15 rows, upstream and stable security changed lines are identical.
Some CNA release ceilings omit later 8.1 backports or model patch commits as
affected revisions; source inspection, not arbitrary commit/version ordering,
resolves those ambiguities. The MPEG-PS companion buffer-size correction is also
present in 8.1.3.

## Compatibility boundary

8.1.3 retains Codec2, JPEG XL, SVG, libgme and libopenmpt integrations when built
with their explicit configure switches. Installing those libraries beside an
unconfigured FFmpeg does not enable them. Five original synthetic inputs for
these integrations were successfully probed and decoded with the exact published
Debian baseline `8ac2b20f9f85416d088d3adf8479a7c3b6c55236b282dad0a91a63b2bb4319f9`
(build `0.18.2-0046`, source `bdef595bafc40128e7df49b8dcb55dde98e9aee2`).
The replacement must exercise those same bytes, not merely advertise codec names.

Sonic registration remains in 8.1.3; its decoder is experimental in both baseline
7.1.5 and 8.1.3. No default successful Sonic decoding is claimed, and this repair
does not enable experimental strictness in ECM. FFmpeg 9 removes the implementation.
The old `hls:` protocol handler is removed in 8.1, but HLS demuxing remains; ECM's
approved provider scheme lists do not admit that legacy scheme. Other capability
differences still require comparison against actual application consumers.

Latest released 7.1 remains 7.1.5 in the checked upstream evidence and lacks
multiple required fixes. It is not an all-16 repair without owned backports.
No source-level result here downgrades the historical High severity or substitutes
for final image/source correspondence and both-architecture verification.

## Local evidence receipt

The working evidence root is
`/tmp/opencode/ecm-native-remediation-20261023/`:

- `ffmpeg-stable-recovery/patch-correspondence.json`: full commits, messages,
  per-hunk presence and upstream/stable comparisons; raw CVE records and patches
  are retained alongside its retrieval manifest and checksums.
- `ffmpeg-8.1.3-release/{retrieval.json,signature-verification.log}`: exact
  release bytes, retrieval identities and successful signature verification.
- `ffmpeg-8.1.3-release/verified-patch-source-correspondence.json`: the 17
  signed-archive file hashes and byte-equality checks.
- `compatibility-fixtures/`: owned generators, five fixture bytes, baseline
  image identity, probe metadata and nonempty decoded-frame receipts.

These local receipts are not public source delivery. Corresponding source must
accompany, or be durably available with, the actual distributed binary image.
