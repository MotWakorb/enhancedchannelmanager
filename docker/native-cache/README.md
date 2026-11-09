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
14 original hashes plus the two exact retained sources and absence of unindexed
files. Native signature/header and final
source/media/image gates still have to execute; Git retention is not their pass.

## Exact e2fsprogs source companion

The same explicit AMD handoff now also selects **only when present in the actual
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
runtime library or partial generic cache. The directory now contains **16 objects
/ 20,668,972 bytes**, plus this README.

`--retained-release-source` still accepts only the original anchor file, not a
directory or caller-defined list. Its adjacent companion filename/hash/size and
exact inventory tuple are fixed in collector code; there is no directory scan.
If selected, missing/corrupt/aliased companion bytes fail before key import with
no network fallback. Without the explicit anchor handoff or selected exact
origin, this companion is not used. ARM gets no handoff. Both copied SRPMs still
undergo native signature/header/full payload authentication. The independent
runtime consumer pins the same canonical e2fsprogs URL/hash and retained
provenance. Generic cache indexes must still cover every selected origin.
