# Recorded SUSE scan projection — unit tests only

Source: `ecm-suse-integration-trivy.json`, recorded 2026-10-24 with Trivy
0.76.1 from the SUSE integration candidate; SHA-256:
`99cae833d8bb1056297977a008af129509e9de5e4af753137af63ab3e25aeb92`.
The original evidence lives under `/tmp/opencode/ecm-native-remediation-20261023/`.

`opensuse-tumbleweed.json` projects the real report's schema, OS detection,
scan timestamp, all 212 RPM, 74 Python and 4 Go inventory entries, and both
unsuppressed findings (MEDIUM Python, UNKNOWN Go). Package name, version,
release, architecture, analyzer and Go relationship values are recorded,
not generated. Unused identifiers, file lists, layers, descriptions and image
history are omitted. The six `gpg-pubkey` entries have distinct versions and
the literal string `"None"` as their RPM architecture, not JSON null.

Image config/rootfs digests are replaced with independent `sha256:aaa…` and
`sha256:bbb…` placeholders; tests supply a separate `sha256:ccc…` manifest
placeholder. These and synthetic scanner DB timestamps are **unit-only**,
not candidate evidence or publication attestation. The original arm64 mapping
test is synthetic. `arm64-delta.json` additionally records the actual first native
ARM CI RPM inventory as a lossless delta from this fixture. Its complete projected
package array is checked against the canonical SHA-256 of all five recorded
fields in the real 211-row report. The source report/manifest identities are
retained in that delta; unit-only config/rootfs subjects remain separate.

That real ARM graph has 205 software RPMs and six keys; amd64 has 206 and six.
The only missing package name is the amd64-specific `libnuma1` provider. Required
codec identities/floors remain checked on both architectures. Completeness floors
are therefore 212 for amd64 and 211 for arm64; each rejects a truncated inventory,
and both retain unconditional raw High/Critical failure, including Go findings.
This is not a globally reduced floor or permission to trim supported inputs.

Custom FFmpeg 8 source/build pins and required-owned-artifact/source binding
are checked separately by the parent runtime gate. FFmpeg is absent from
this RPM inventory: accepting this scan neither claims FFmpeg was OS-scanned
nor grants native-advisory clearance or publication approval.
