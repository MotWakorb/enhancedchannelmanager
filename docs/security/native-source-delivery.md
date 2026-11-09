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

### Fresh cohort with retained native Packman inputs

The refreshed Docker recipe uses all-origin collection rather than
the old recovered 156/155-source carrier indexes. The existing native builder
already retains the two exact Packman source RPMs in its owned source directory;
some of those exact public URLs now return 404. To reuse these original bytes
without inventing a complete new cache index, the source-builder supplies both:

```sh
--native-manifest /tmp/native/manifest.json --native-sources /tmp/native-output/sources
```

This narrow handoff accepts **only** the selected architecture's two reviewed
Packman source inputs. The manifest must retain the existing Packman fingerprint,
the exact canonical vendor URLs, matching codec source set and SHA256s. Only
actual inventory Packman origins can select these files; no SUSE origin is
supplied by this handoff. Files must be regular nonsymlinks and hash-match before
and after copying. Every reused archive still passes the ordinary isolated
`rpmkeys`, exact vendor/source header and complete Source/Patch payload checks.
The index truthfully records `reverified-native-input`, canonical origin URL and
no fabricated download redirect. Apart from the single retained AMD release
source below, other origins use the normal fresh download path. Failed public
requests log the exact allowed URL and fail without retry.

This is not generic partial-cache permission. Supplying `--expected-sha256`
still requires coverage of **every** selected origin, and `--cache-dir` still
requires that complete architecture/URL-bound expected index. Neither native
source reuse nor fresh mode bypasses these existing contracts. Old same-named
ARM/AMD archives and indexes remain noninterchangeable. A failed signature or
missing source still prevents publication of `index.json`.

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

### Retained exact AMD 20261105 release source

The frozen AMD64 binary cohort uses
`openSUSE-release-20261105-4389.1.src.rpm`. The rolling AMD source repository
advanced to signed cohort 20261106 independently and stopped serving that exact
source at the canonical route. The established official GWDG mirror initially
served the same authenticated archive at:

<https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261105-4389.1.src.rpm>

That exact mirror URL also returned **404 in final CI**, after successful local
builds. The product no longer depends on another mirror for this source. Its
unchanged, previously authenticated bytes are retained in `docker/native-cache/`
under their complete 3,391,198-byte archive SHA256:
`93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123`.

The source-builder supplies this location-only option **on x86_64 only**:

```sh
--retained-release-source /tmp/native-cache/93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123
```

It accepts one regular nonsymlink file, never a directory or extra source list,
and binds it to the existing `EXACT_SOURCE_ROUTES` policy for the exact
`(openSUSE, x86_64, openSUSE-release-20261105-4389.1.src.rpm)` tuple and pin.
Missing, corrupt, differently named or noncorresponding inputs fail without
network fallback. The collector copies the archive into its actual output and
rehashes the copied bytes **before `prepare_key`**. It then repeats the unchanged
isolated-key RPM signature/payload-digest, exact source-header and complete
Source/Patch checks. Retention never substitutes for authentication.

The source ledger records `retrieval="reverified-retained-source"`,
`retained_sha256` equal to that pin, and `final_url=null`. Its existing GWDG URL
is the **historical origin**, not a new GET or redirect claim. The final-image
consumer independently checks the tuple, archive pin and retained provenance.
The existing optional fresh/full-cache interfaces remain available without this
flag; the exact product Dockerfile contract requires the AMD handoff and rejects
an ARM handoff. No generic mirror prefix, retry, fallback, source substitution or
second caller-defined trust schema is added.

ARM64 still uses its normal architecture-specific source feed and the distinct
`openSUSE-release-20261105-3521.1.src.rpm`: 4,377,204 bytes, SHA256
`7c31d3e46c3b6bf4fc5d5abba14791d4eb4980580b63d4a4721a23a6e185aba2`.
Both exact SRPMs were previously downloaded and verified as data with the normal
isolated SUSE key, RPM signature/payload digest, exact signed source header and
complete Source/Patch audit. Matching signed binary headers establish the
architecture/source binding; an SRPM header architecture alone does not.
These bounded source checks are not ARM native execution or full-image gates.

Generic `--cache-dir` still needs a complete,
architecture/URL-bound `--expected-sha256` index. Older canonical-URL indexes do
not get silently relabeled, and supplied expected hashes must still cover every
selected origin, including the retained one. The original 14 Packman cache
objects, two-source handoff and all seven no-refresh transactions are unchanged;
the original branding retention added one SUSE source object (15 objects /
12,911,729 bytes). The exact companion below brings that to 16 / 20,668,972 bytes.

### Exact recovered e2fsprogs companion (2026-11-09)

CI subsequently lost the canonical AMD source URL for
`e2fsprogs-1.47.4-1.1.src.rpm`. The identical 7,757,243-byte archive was recovered
from the previously sealed original OCI source layer, matched to its original
source index, and freshly authenticated with the pinned SUSE key and the unchanged
native RPM signature/header/complete-payload checker. All 13 regular members,
including the SPEC, five Source inputs and seven Patch inputs, remain delivered.

The existing explicit AMD branding handoff also selects this **one** adjacent
hash-addressed object, only for the exact installed openSUSE/x86_64 origin:
`4b90c6c666634fe3f97b2045a6556c4ae34709a9f48b53c3c930d32c665b8833`.
`RETAINED_RELEASE_COMPANIONS` is a fixed tuple/hash/size policy, not a directory
scan or partial generic cache. No explicit handoff means no retained companion;
missing/corrupt/noncanonical/aliased selected inputs fail without network
fallback. Copied bytes are rehashed before key import, then normally authenticated.
The canonical URL remains unchanged, `final_url` remains null and retrieval is
truthfully recorded as `reverified-retained-source`. The independent RPM consumer
adds only that exact URL/hash tuple; it keeps all inventory/origin/payload/live-DB
checks and aggregate dispatch. No Alpine source obligation is removed.

The production Dockerfile, runtime graph, package versions, ARM handoff and
generic full-cache contract are unchanged. `test_retained_e2fs_source.py` uses
the real SRPM plus recorded native collector output to exercise selection,
corruption/alias denial, mandatory reauthentication and the independent delivery
consumer (mocking only its live RPM inventory query in unit tests). A separate
networkless native collector integration reauthenticates both actual SRPMs from
three unchanged original inventory rows. That bounded subset is not whole-image
source/security/MCP acceptance. See the retained-source evidence document.

The remaining-source survey subsequently found the exact GCC 16 and SQLite
origins expired too. Both matching originals were recovered and freshly
authenticated from the same sealed baseline. They join the explicit AMD companion
policy with their unchanged canonical URLs and whole archive hashes, not a
general archive fallback. GCC's 108,898,703-byte original exceeds GitHub's 100MiB
blob limit, so two fixed ordered/hash/size-pinned transport parts are concatenated
to the **identical** SRPM. The whole SRPM hash is mandatory before trust import;
ordinary native RPM/header/complete payload verification remains mandatory after
reassembly. No SPEC or source executes. Details and all object identities are in
`docker/native-cache/README.md` and the additional-retention evidence. The current
cache is 19 storage objects / 156,197,839 bytes for the unchanged 14 Packman
objects and four exact SUSE source archives. The earlier two-source subset proof
remains historical; the new networkless integration authenticates all four
sources from their unmodified original inventory rows. Whole fresh image/MCP CI
and Alpine aggregate/security acceptance remain separate requirements.

A bounded actual-collector proof under BuildKit `RUN --network=none` reverified
this retained source with native RPM tools and complete payload checks. Adding
an ordinary fresh zlib origin failed offline without publishing an index, proving
retention does not skip other origins. This is not a cold full-image build, full
runtime source closure, ARM execution or CI-green claim; parent delivery gates
remain required.

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
