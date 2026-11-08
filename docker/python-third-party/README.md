# Shared current Python notices — x86_64 and expected ARM64 artifacts

**This is the new shared target for the parent release copy.** Earlier candidates remain historical; do not use the old x86-only manifest as the complete new architecture map.

`component-manifest.json` accounts for all 74 current backend locks (cryptography 50.0.2). `artifact-index/linux-amd64.json` preserves the already-proved repaired x86 identities. `artifact-index/linux-arm64.json` independently identifies the 22 eligible public CPython 3.12/AArch64 manylinux wheels for glibc 2.44, their **36** machine-183 ELF files, public archive/native hashes, DT_NEEDED, compiler, metadata, SBOM and notice evidence. x86 has 39 ELF files; no native parity is claimed. All 52 purelib package notices are explicitly byte-agnostic shared files, not newly asserted ARM whole-package parity.

Actual ARM notice text bytes match the shared x86 notice files. Exact ARM SBOM/source-path component versions and 406 retained crate/Rust-library notice hashes were checked before reuse. Distinct ARM SBOMs are kept in `notices/architecture/linux-arm64/`. No new upstream license or Rust standard-library notice payload was needed. All four genuine Rust notice versions remain, including Rust 1.97.1 for hypothesis and 1.98.1 for current cryptography. Conservative foreign-target components and missing named-license-file WASI exceptions are labeled in the component index, not hand-waved into a generic license grant.

**Still unproved:** final ARM native execution/CI, final whole-image artifact selection/content, and recipient source delivery. Match actual final wheels/native files to these expected indexes and run final native ARM checks. No MCP, musllinux, new advisory clearance or architecture-runtime equivalence claim is made. Compiler archives and ARM wheel binaries are not shipped in this notice tree.

**Targeted dnspython update:** build 0064 uses 2.9.0. Its pure-Python artifact,
metadata and upstream ISC notice are recorded in `artifact-index/dnspython-2.9.0.json`
and the refreshed shared index. The published wheel and sdist omit LICENSE;
the exact release-commit notice was independently retrieved and Git-blob/hash
checked, and its text matches the retained 2.8.0 notice. The old 2.8.0 entry in
the historical x86 snapshot is not evidence for the new package. No native
architecture evidence or final-image verification is silently refreshed.

The proposed accompanying-image source path is `/usr/share/ecm/sources/python/`; integration and recipient access remain **not implemented or verified here**. Keep the source/notice files for components actually shipped and preserve ECM's separate CC0 root license.

Details: `../../arm64-current/REPORT.md`; new tree integrity: `SHA256SUMS`.

## Stable-Alpine GNU correspondence (not source clearance)

`artifact-index/musllinux-gnu.json` is a separate, hash-pinned map for the
unchanged musllinux selection. It does not replace or relabel the historical
manylinux indexes above. It accounts for **14 wheels, 18 GNU member copies and
12 distinct repaired ELF objects** on AMD64 and ARM64, covering GCC 14.2.0 and
12.4.0. `scripts/verify_musllinux_gnu.py` checks all **96** selected whole-wheel
hashes (including non-GNU wheels), complete GNU
member coverage and full-byte replay of bounded SONAME/NEEDED/RPATH operations.
`scripts/prepare_musllinux_gnu_inputs.py` extracts original bytes from hash-pinned
APK/OCI cache blobs without installing packages, extracting filesystem paths or
executing target ELFs. Comparison tools are pinned AMD64 binaries; this verifies
ARM **artifact bytes**, not ARM execution.

The explicit `--binary-only` result is `BINARY_ONLY_NO_SOURCE_CLEARANCE`, never
source/advisory/final-image approval. The default source gate fails: matching
source delivery is not integrated, and the GCC 12.4 OCI build recipe cloned an
unpinned `musl-cross-make` revision then removed its checkout/log. A retrieved
candidate revision and matching upstream archive do not establish that original
revision. Producer metadata remains raw, including cryptography ARM's
`pkg:apk/NotpineForGHA/libgcc@14.2.0-r6`; it is not normalized into Alpine or
used as corresponding-source authority.

See [owned native design](../../docs/design/stable-alpine-owned-native.md) for
verified receipts, offline reproduction and the remaining production gates.
