# Shared current Python notices — x86_64 and expected ARM64 artifacts

**This is the new shared target for the parent release copy.** Earlier candidates remain historical; do not use the old x86-only manifest as the complete new architecture map.

`component-manifest.json` accounts for all 74 current backend locks (cryptography 50.0.2). `artifact-index/linux-amd64.json` preserves the already-proved repaired x86 identities. `artifact-index/linux-arm64.json` independently identifies the 22 eligible public CPython 3.12/AArch64 manylinux wheels for glibc 2.44, their **36** machine-183 ELF files, public archive/native hashes, DT_NEEDED, compiler, metadata, SBOM and notice evidence. x86 has 39 ELF files; no native parity is claimed. All 52 purelib package notices are explicitly byte-agnostic shared files, not newly asserted ARM whole-package parity.

Actual ARM notice text bytes match the shared x86 notice files. Exact ARM SBOM/source-path component versions and 406 retained crate/Rust-library notice hashes were checked before reuse. Distinct ARM SBOMs are kept in `notices/architecture/linux-arm64/`. No new upstream license or Rust standard-library notice payload was needed. All four genuine Rust notice versions remain, including Rust 1.97.1 for hypothesis and 1.98.1 for current cryptography. Conservative foreign-target components and missing named-license-file WASI exceptions are labeled in the component index, not hand-waved into a generic license grant.

**Still unproved:** final ARM native execution/CI, final whole-image artifact selection/content, and recipient source delivery. Match actual final wheels/native files to these expected indexes and run final native ARM checks. No MCP, musllinux, new advisory clearance or architecture-runtime equivalence claim is made. Compiler archives and ARM wheel binaries are not shipped in this notice tree.

The proposed accompanying-image source path is `/usr/share/ecm/sources/python/`; integration and recipient access remain **not implemented or verified here**. Keep the source/notice files for components actually shipped and preserve ECM's separate CC0 root license.

Details: `../../arm64-current/REPORT.md`; new tree integrity: `SHA256SUMS`.
