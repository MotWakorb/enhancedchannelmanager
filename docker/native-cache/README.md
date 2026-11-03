# Retained exact Packman inputs

These 14 content-addressed files are the **original**, unchanged ten binary RPMs
and four matching source RPMs selected by `docker/native/manifest.json` across
AMD64 and ARM64. Original URLs, versions, architectures and SHA-256 values remain
in that reviewed manifest. The complete set is 9,520,531 bytes. No package has
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

Keeping this small, already-reviewed bootstrap set in Git avoids new registry
write credentials, retention services or untrusted mirror fallback for CI.
This is not implementation of a full native binary/source archive: the SUSE
historical repositories still supply signed packages, and separately pinned
published source layers supply exact corresponding source archives. Their
availability and the ARM security holds remain independent requirements.

`test_retained_packman_cache_is_exact_complete_original_input_set` enforces all
14 hashes and absence of unindexed files. Native signature/header and final
source/media/image gates still have to execute; Git retention is not their pass.
