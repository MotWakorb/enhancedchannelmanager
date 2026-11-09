# Owned BusyBox provider — intermediate migration evidence

The PO approved this additional provider after the signed stable package
`busybox-1.37.0-r31` reproduced CVE-2025-60876. This does not change production
Dockerfiles, publish an image, or approve the migration's security gate.

## Retained contract

`manifest.json` seals the source archive, the original aports subtree at
`c3ef5d10e6ef6528852c51f0564963e2f8c1be19`, all 75 vendor input checksums,
44 vendor patch witness sets and 50 final touched-source witnesses. The two
additional patches are:

- The reviewed wget control-character/space fix, retained from Buildroot commit
  `073c6af03e765c930e01da56c78f2770bdfb52a6` with its original attribution.
- A bounded numeric-timezone fix for a reproduced pre-existing musl failure,
  preserving `CONFIG_FEATURE_TIMEZONE` and unchanged upstream test expectations.

`provenance-inputs/` holds the original signed stable indexes and APK witnesses.
They are build/provenance inputs only: vulnerable original APKs are **not** copied
into the runtime proof. Signatures are checked using the architecture-specific
official public keys already supplied by the pinned Python image. The index's
repository architecture and the `busybox-binsh` control architecture (`noarch`)
are recorded separately.

Only `busybox`, `busybox-binsh` and `ssl_client` may enter the runtime. The builder
checks the original 304 applet paths, all 1,084 original configuration settings,
complete package namespaces, nonbinary bytes, symlinks, modes, UID/GID and
unchanged control scripts/triggers. Unused upstream subpackages are retained as
build evidence, not selected as runtime packages.

## Reproduce the bounded proof

On a real AMD64 or ARM64 host:

```sh
python3 scripts/prepare_busybox_cache.py --output-dir /absolute/new-cache
docker buildx build --progress plain \
  --build-context busybox-cache=/absolute/new-cache \
  --file docker/alpine-busybox/Dockerfile \
  --output type=local,dest=/absolute/new-build .
docker buildx build --progress plain \
  --build-context busybox-packages=/absolute/new-build \
  --file docker/alpine-busybox/Dockerfile.runtime \
  --output type=local,dest=/absolute/new-installed-proof .
```

Compilation and signing are offline. The original upstream checks declare
`options=net` and include real HTTP retrievals, so the separate check stage has
network access. No new exclusions are added; the original `SKIP_KNOWN_BUGS=1`
commands remain unchanged. Compilation uses locale C; the Unicode tree goldens
run under C.UTF-8. Raw binary-containing test logs are retained without decoding.

An ephemeral signing key is removed after packaging. Installation first proves
default-trust denial, verifies the three selected APKs under scoped temporary
public-key trust, and requires `BAD signature` for a changed authenticated APKv2
control byte. An unknown-key or unrelated parser error cannot satisfy this
negative control. Runtime keys/repositories must remain unchanged. Installed
payloads and APK ownership are checked again before the runtime probe executes.

The local runtime probe covers 32 representable C0/space URL rejections before
connection, five actual container-local ordinary/encoded HTTP transfers, the
304 applet paths and the timezone case. NUL cannot be passed inside a C argv
string. The original official-base failing result is retained separately.

`test_build_busybox_provider.py`, `test_alpine_apk_v2.py`,
`test_install_busybox_provider.py`, `test_install_alpine_native.py` and
`test_busybox_workflow.py` enforce these bounded contracts. The native workflow
adds real architecture-specific build/install/probe evidence, not publication
authority.

## Independent corresponding-source replay

`source-replay/` is **proof data, not a build input or changed patch stack**.
The original manifest, vendor/owned patches, build recipe and test expectations
remain unchanged. A strict literal replay of that original stack rejects some
historical hunk positions; an independent GNU `--fuzz=0` control also rejects the
original awk test hunk. Those failures are retained, not described as passes.

Independent GNU-default application nevertheless reproduces all 66 recorded full
pre/post file hashes and all 50 final prepared-source witnesses. Four vendor
patches and the owned wget patch need explicit source-only literal diff copies
for replay without offset/search/fuzz. Each copy binds the original patch hash
and **identical full pre/post file witnesses**. They are not applied by the
builder, do not change a source/test byte in the resulting tree, and introduce
no application golden or numerical-tolerance change.

`alpine_source_patch.py` performs exact in-memory line/byte transforms; it never
executes a delivered recipe, source, compiler, target or test. The caller must
explicitly supply the separately hash-sealed `source-replay.json`; there is no
automatic fallback. The complete 2,881-file result must match the independent
GNU source inventory, including modes and hashes. GNU diagnostic backup files
are retained separately and bound to original stage preimage hashes, not silently
accepted by a filename suffix exemption. Original BusyBox GPL-2.0-only source
and notices remain the authority for these corresponding diff snippets.

This proves source transformation correspondence, **not** historical compiler
or static linkage, reproduced runtime binaries, native ARM execution, live final
image acceptance or aggregate source/advisory/MCP clearance.

## Remaining closure

`static-sources.json` now seals both official utmps/skalibs source graphs: the
signed SDK/static APKs on each architecture, source commit, original aports
recipe, upstream SHA512 inputs and original ISC notices. The builder verifies
actual installed SDK headers and archives, including package ownership, before
compilation. These exact source archives, recipes and notices travel in the
source bundle and are rehashed by the installation consumer. This is source
correspondence for those two providers, not independently reproduced archive
bytes or complete compiler-support-source closure.

Native run `42555656082` passed on AMD64 and ARM64: 913 upstream checks per
architecture, exact signed installation/control scripts, all 32 URL rejections
and five local transfers. That run predates the additional static-source graph;
the source-bound build requires a fresh native run.

Compiler-support sources and final rebuild-helper/provenance delivery
integration are still required. The build receipt does not claim complete
source/advisory clearance. Complete unfixed-aware advisory reconciliation,
final ECM/MCP composition and final unsuppressed immutable-image scans remain
required.
