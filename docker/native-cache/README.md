# Retained exact native inputs

The original 14 content-addressed Packman files are the unchanged ten binary RPMs
and four matching source RPMs selected by `docker/native/manifest.json` across
AMD64 and ARM64. Original URLs, versions, architectures and SHA-256 values remain
in that reviewed manifest. That original set is 9,520,531 bytes. No package has
been rebuilt, re-signed, updated, or selected by a floating repository solve.

The native builder's existing `--cache-dir` path hashes each cached object and
then independently checks its Packman signature and exact RPM header before
use. Cache corruption fails; it does not authorize replacement bytes. The
existing key fingerprint remains `F8875B880D518B6B8C530D1345A1D0671ABD1AFB`.
Corresponding source RPMs accompany the binaries in this same directory; the
manifest maps their hash filenames back to the original package filenames.
Unpack only using the existing validated source-delivery tooling, never execute
SPECs to inspect them. Third-party licenses remain those of the RPMs and sources,
not ECM's root license.

One additional object is the exact **AMD64-only SUSE source RPM**
`openSUSE-release-20261105-4389.1.src.rpm`, not a new binary/package revision:

* Content-addressed filename / SHA256:
  `93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123`
* Size: **3,391,198 bytes**; license: **BSD-3-Clause**.
* Original selected public origin (now expired):
  <https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261105-4389.1.src.rpm>
* Unchanged SUSE key:
  `AD485664E901B867051AB15F35A2F86E29B700A4`.

These bytes match the earlier authenticated complete source-builder output.
They are retained unchanged because the
canonical source and later exact mirror route expired independently. The complete
directory previously held **15 objects / 12,911,729 bytes**, plus this README.

Only the exact `(openSUSE, x86_64, filename, SHA256)` policy in the existing
collector selects this source. Its explicit location-only
`--retained-release-source` input rejects missing/corrupt/nonregular or differently
named bytes and wrong vendor/architecture/origin. The copied bytes are rehashed
before isolated key import and the ordinary RPM signature, exact source header
and complete Source/Patch verification. Provenance is
`reverified-retained-source`, **not a new network download**. ARM does not receive
that handoff or reuse these bytes/hash; its own source/feed remains unchanged.

Keeping this small, already-reviewed bootstrap set in Git avoids new registry
write credentials, retention services or untrusted mirror fallback for CI.
This is not implementation of a full native binary/source archive. Other SUSE
origins except the exact companion below still use ordinary fresh retrieval, and the existing two
per-architecture Packman source inputs retain their separate reviewed handoff.
Their availability/authentication and complete runtime source closure remain
independent requirements. There is no generic partial-cache permission or
automatic mirror fallback.

`test_retained_packman_cache_is_exact_complete_original_input_set` enforces all
14 original hashes plus the five exact retained source objects and absence of unindexed
files. Native signature/header and final
source/media/image gates still have to execute; Git retention is not their pass.

## Exact e2fsprogs source companion

The same explicit AMD handoff also selects **only when present in the actual
inventory** the exact `(openSUSE, x86_64, e2fsprogs-1.47.4-1.1.src.rpm)` companion:

* SHA256 / filename: `4b90c6c666634fe3f97b2045a6556c4ae34709a9f48b53c3c930d32c665b8833`
* Size: **7,757,243 bytes**; source license: **GPL-2.0-only**.
* Unchanged canonical origin:
  <https://download.opensuse.org/tumbleweed/repo/src-oss/src/e2fsprogs-1.47.4-1.1.src.rpm>
* Same pinned SUSE fingerprint as above; all 13 payload members retained.

These identical bytes were recovered as data from the previously sealed original
AMD64 OCI source layer and freshly verified by the collector's ordinary native
RPM signature, exact header and complete payload checks. Neither source nor SPEC
was executed. This is not an upgraded package, replacement binary, new mirror,
runtime library or partial generic cache. This first recovery brought the directory
to 16 objects / 20,668,972 bytes; see the two additional exact sources below.

`--retained-release-source` still accepts only the original anchor file, not a
directory or caller-defined list. Adjacent companion filenames/hashes/sizes and
exact inventory tuples are fixed in collector code; there is no directory scan.
If selected, missing/corrupt/aliased companion bytes fail before key import with
no network fallback. Without the explicit anchor handoff or selected exact
origin, a companion is not used. ARM gets no handoff. All copied SRPMs still
undergo native signature/header/full payload authentication. The independent
runtime consumer pins the same canonical e2fsprogs URL/hash and retained
provenance. Generic cache indexes must still cover every selected origin.

## Exact GCC 16 and SQLite companions

A complete survey of the remaining 151 original SUSE source URLs found just two
additional HTTP 404 responses, matching runtime source origins recorded by the
failed build. Both identical originals were recovered from the same sealed OCI
source layer and freshly authenticated with the same pinned SUSE key, exact RPM
headers and complete Source/Patch payload checks:

| Source | Original SHA256 | Bytes | Source license |
| --- | --- | ---: | --- |
| `gcc16-16.2.1+git9713-1.1.src.rpm` | `e0eb6f15a65dec740af17424092d643806a00f40d9da227fea8825066efe87fa` | 108,898,703 | GPL-3.0-or-later |
| `sqlite3-3.54.1-1.1.src.rpm` | `f42c07f564efc29fa6b7f8edbbfaa2636d00682c50bc2d3893984d211374e629` | 26,630,164 | LicenseRef-SUSE-Public-Domain |

SQLite is retained directly under its hash filename. GCC exceeds GitHub's
100MiB single-blob limit; its **unchanged original bytes** are stored as two
ordered parts (no recompression or content transformation):

1. `1fb48b10a9fad6e9ce3da8571c3278ca1d53ea5e614e8a0f321061b6f39a0289` — 67,108,864 bytes.
2. `f5fc9ab689b3e5069bcc23cf19c498d90b6c0f523b60cfd6594b595d92aa6339` — 41,789,839 bytes.

The collector pins their order, separate hashes and sizes, joins bytes without
executing/decompressing them, then verifies the **whole original SRPM hash before
key import** and repeats normal RPM signature/header/full payload authentication.
The delivered filename/archive is still the identical original source RPM, not
these transport parts. Reordered, missing, corrupt or symlinked parts and corrupt
joined output fail closed. All three companions require the explicit branding
anchor and exact selected AMD origins. No generic partial-cache permission,
network fallback or ARM source reuse is introduced.

The complete directory now has **19 content-addressed objects / 156,197,839 bytes**
plus this README: 14 unchanged Packman objects and five storage objects for four
exact SUSE sources (branding, e2fsprogs, SQLite, and two parts of GCC).
