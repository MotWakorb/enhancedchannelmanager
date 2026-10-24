# Native RPM source delivery

`scripts/collect_native_sources.py` collects **every origin in the actual runtime
RPM inventory**, not just packages selected by license heuristics. Its output is
source bytes to accompany the image, not a promise that moving URLs will remain
available. It does not install packages, run SPECs, call Docker, or publish files.

## Builder contract

Run with Python 3.11+ standard library, `rpm`, `rpmkeys`, and `gpg` in a
**source-builder stage**. `zstd` is additionally required only for zstd SRPM
payloads; gzip and xz use the standard library. None of these source-only tools
needs to be added to the runtime image.

Capture the final runtime graph **after** runtime package installation and before
adding source-builder tools. Either query the actual runtime root:

```sh
python3 scripts/collect_native_sources.py \
  --rpm-root /runtime-root --arch x86_64 --out /delivery \
  --suse-key /trusted/suse.asc --packman-key /trusted/packman.asc
```

Or provide the recorded, unmodified output of this exact query:

```sh
rpm -qa --qf '%{NAME}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{VENDOR}\t%{SOURCERPM}\t%{LICENSE}\n' > runtime-rpms.tsv
python3 scripts/collect_native_sources.py \
  --inventory runtime-rpms.tsv --arch x86_64 --out /delivery \
  --suse-key /trusted/suse.asc --packman-key /trusted/packman.asc
```

The output directory must not already exist. The only exempt inventory records
are strictly validated `gpg-pubkey` rows: eight-hex version/release, `(none)`
architecture/vendor/source, and `pubkey` license. These are retained separately
in the index. Missing fields, duplicates, unsupported vendors, unsafe source
names, `nosrc` sources, or wrong runtime architectures fail closed.

Use `--arch aarch64` with the **actual aarch64 runtime inventory** for ARM64.
The x86 base's 89-origin historical proof is not an ARM or final-runtime proof.
ARM sources use the documented, architecture-specific feed
<https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/>, with
the same SUSE signing-key pin. This comes from the official
[migration documentation](https://en.opensuse.org/openSUSE:Migrate_Leap_to_Tumbleweed),
not a guessed ports `src-oss` path. The separate completed ARM base proof found
24 different source revisions; all 65 filenames shared with x86 had different
whole-SRPM hashes. Even identical filenames and payloads do not justify reusing
x86 archive bytes/hashes for ARM. `arm64-provenance.json` records a compact real
coreutils counterexample. No ARM proof is rerun by this helper's unit tests.
SRPM `header_arch` is recorded but is a source-package build attribute, not a
target-binary architecture check: a common SRPM may serve multiple targets.
The inventory architecture and its exact `SOURCERPM` field establish that binding.
A supplied inventory is a caller-provided observation, not an image attestation;
the parent pipeline must compare it to the final image's RPM DB.

## Exact origins and trust

Only these surveyed literal RPM vendors and documented public source locations
are accepted; there is no configurable arbitrary repository/key fallback:

| Installed `VENDOR` | Runtime architecture | Public source repository | Pinned full fingerprint |
|---|---|---|---|
| `openSUSE` | `x86_64` | <https://download.opensuse.org/tumbleweed/repo/src-oss/> | `AD485664E901B867051AB15F35A2F86E29B700A4` |
| `openSUSE` | `aarch64` | <https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/> | `AD485664E901B867051AB15F35A2F86E29B700A4` |
| `http://packman.links2linux.de` | both | <https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/> | `F8875B880D518B6B8C530D1345A1D0671ABD1AFB` |

The recorded survey found 118 base packages with `openSUSE` and five actual
codec RPMs with `http://packman.links2linux.de`, **not** an invented `Packman`
label. `backend/tests/fixtures/native_sources/` retains a small real inventory
projection, authenticated-primary field projection/provenance, the 10,564-byte
MIT `system-user-root` SRPM, and the pinned public SUSE test key. The base
reference and both archives' upstream URLs, licenses, and SHA hashes are in
`provenance.json`. The public test key was exported from the immutable base RPM
key database; production key files are explicit builder inputs.

For each exact inventory `SOURCERPM`, the collector requests
`<architecture-selected-vendor-repository>/src/<exact-filename>`. It deliberately uses **direct exact
retrieval plus authenticated SRPM header identity**, not repository metadata.
It first validates the supplied public key's full fingerprint with GPG, then
imports only that key into a fresh isolated RPM DB. Native `rpmkeys -Kv` must
verify a signature by that pinned key and the payload SHA256 digest. Only then
are the signed header's name/version/release, source-package marker, vendor,
and license accepted. The exact header name/version/release must reconstruct
the requested filename. No nearest version, private patch, unsigned directory
listing, metadata-only assertion, or alternate vendor is accepted. HTTPS
mirror redirects are allowed; downgrades to non-HTTPS are rejected. Signature
and identity verification, not the mirror's name, authenticate delivered bytes.

The complete SRPM is retained unchanged. Its CPIO payload is streamed, hashed,
and checked for a SPEC and every header-declared Source/Patch file. NoSource,
NoPatch, explicit SPEC omission declarations, missing inputs, duplicate entries,
traversal/nested paths, links, truncated data, unsupported compression, and
nonzero trailing payload data are rejected. SPECs are data and never executed.

Defaults bound origin/download count to 512, aggregate archive bytes (including
cache hits) to 2 GiB, and collection deadline to 900 seconds. Adjust explicitly
with `--max-downloads`, `--max-bytes`, and `--max-seconds`. Network operations
have a socket timeout of at most 30 seconds, native verification commands at
most 120 seconds, and each expanded payload a 2 GiB/120-second limit. Deadline
checks occur between operations and streamed reads; this is not an OS-wide hard
wall-clock kill. Downloads do not retry or substitute another release. A missing
exact rolling-repository SRPM blocks the build; retain verified bytes early.

Optional cache reuse requires **both** `--cache-dir DIR` and
`--expected-sha256 FILE`, where FILE is a per-architecture source index covering
every selected origin. An earlier collector `index.json` works directly, or use
the minimal shape below (hash placeholder shown):

```json
{"architecture":"aarch64","sources":[{"filename":"coreutils-9.12-1.1.src.rpm","url":"https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/src/coreutils-9.12-1.1.src.rpm","sha256":"<64 lowercase hex characters>"}]}
```

Both architecture and each selected source's canonical feed URL must match;
filename-only hash dictionaries are rejected. The caller must supply hashes
obtained for that feed/architecture, not relabel an x86 index. Each reused file is copied
into the new output, hash-checked, signature/key/header-checked, and payload-audited
again. Filenames alone are never trusted. Expected hashes may also constrain
fresh downloads. No cache hit bypasses authentication or completeness checks.

## Output and parent integration

`/delivery` contains:

* `inventory.tsv`: the exact input observation;
* `sources/*.src.rpm`: original archives containing actual SPEC/source/patch bytes;
* `index.json`: installed name/version/release/arch/vendor/source/license rows,
  excluded key rows, archive URL/redirect (or reverified-cache provenance), size,
  SHA256, signature-key fingerprint, signed source license/header architecture,
  and every payload file's name/size/SHA256 plus declared Source/Patch inputs.

The index's `package_set_sha256` hashes canonical sorted package rows;
`source_set_sha256` hashes canonical exact filename/vendor/archive-SHA256/key
and architecture-selected URL bindings. `inventory_sha256` binds the raw query observation. These descriptors
are **not** the final OCI image's own hash, avoiding circular image identity.
Only a completed run publishes `index.json`; partial output without it must
never be copied into a deliverable image.

Parent-owned Docker integration must fail on any nonzero collector exit, compare
the collected package set to the final runtime DB for each architecture, and
copy the complete output alongside each image, e.g. under
`/usr/share/ecm/sources/rpms`. Preserve matching sources for every released
architecture/version. URLs or an index without archive bytes are insufficient.
The full x86 base source set alone was about 596 MB; budget image size honestly.
The parent's newer amd64 prototype mapping has 212 packages / 156 source
origins: base-only success and a complete mapping are not final-graph download
or payload proof. Recollect from the final installed graph.

This helper covers **distribution RPM origins only**. The parent still owns
FFmpeg/gosu/resdet sources and recipes, wheel notices, applicable licenses and
instructions, Docker wiring, canonical validation, and advisory manifests.
Declared-input payload completeness is not semantic rebuildability, a proof
against dynamic fetches in SPEC branches, complete toolchain correspondence,
reproducibility certification, or legal certification.

## Targeted verification

From `backend/`, using the existing project environment:

```sh
../.venv/bin/python -m pytest tests/unit/test_collect_native_sources.py --no-cov -v
```

Unit tests include a real small gzip SRPM and native-process stand-ins. The
separately marked integration test needs real `rpm`/`rpmkeys`/`gpg` and the exact
coreutils archive. In an equipped source-builder, explicitly opt in:

```sh
ECM_NATIVE_SOURCE_INTEGRATION=1 ECM_COREUTILS_SRPM=/cache/coreutils-9.12-1.1.src.rpm \
  python3 -m pytest tests/unit/test_collect_native_sources.py --no-cov -m integration -v
```

Omit `ECM_COREUTILS_SRPM` to permit a bounded exact public download. Without opt-in
the integration test is reported as skipped, not as cryptographic coverage.
Once opted in, missing tools, archive, or network are failures, not empty passes.
The integration checks recorded SHA256, actual pinned-key signature/header and
all coreutils inputs (including 17 patches), then requires tampered-byte rejection.
