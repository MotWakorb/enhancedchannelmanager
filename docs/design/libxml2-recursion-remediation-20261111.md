# libxml2 recursion/catalog corrections — m8m42

**Native AMD64 component proof passes; parent integration and ARM/full-image
acceptance remain pending. This is not a release or advisory clearance.**
The native results below belong to source-port commit `8dc6d683`; the later
[proof-contract correction](libxml2-upstream-proof-contract-20261111.md) changes
the helper identity and has fixture-based contract tests, **not a new native
execution claim**. Fresh native CI must exercise that new helper on both arches.
Worker branch `fix/p1-libxml2-20261111` starts at published dev
`95fc8e4e88c22744327de0ed75767ce5e8b28af9`; its merge-base matches that commit.
Only the manifest's libxml2 section changed: four appended patches (SGML depth
prerequisite plus the three remaining corrections) and retained original patches.
The original nine patch records, other providers, source version, SONAME,
configure flags, application pins, and global version/hash bindings are unchanged.

## Primary source findings

Original assessment and bead/comments were read in full. Read-only upstream
refresh on **2026-11-11** retained complete HTTP bodies and headers at
`/tmp/opencode/libxml2-primary-20261111/receipts.json` (SHA256
`d3909b9f45a2e450e47b606f552fc707839241c7b5270c93143fbe0d05ed1c7c`).

Upstream patch URLs use
`https://gitlab.gnome.org/GNOME/libxml2/-/commit/<commit>.patch`:

| Purpose | Commit | Full downloaded patch SHA256 |
|---|---|---|
| SGML depth prerequisite | `eae9291aa73907694dd3a4274d306e31217e746e` | `be4d7a524074869750a74896c1c41025e87b2e28e2606b4b70f2223c8c0a7c0d` |
| SGML deduplication | `ee9fdfcc244a5d06b5b087437f5cb3decd05d689` | `d427b474acee40696a00ead9c2f89af487f8ba38013bb487a2a18e47a4e53ab1` |
| RelaxNG include depth | `19549c61590c1873468c53e0026a2fbffae428ef` | `5cd5c77d9d6045664053443cd878d2ff512bfa98469f5d9cf1ba9f018ef317a3` |
| Shell bounds | `c2e233fc1b341685fc99621b2768b503f777a72e` | `a8aaa97f64f7c186087bff6959c00a5504cae7778f908ccd1563a2d760b109cc` |

Upstream blame identifies the depth prerequisite positively; its title alone
(“Prevent infinite recursion in xmlCatalogListXMLResolve”) obscures its SGML
changes. Its `recursive.script`, `recursive.sgml` and expected result are retained;
a duplicate-self-reference old-harness fixture is added as well. Depth alone
leaves exponential work. The shell patch's three regression cases enter the old
`Catatests` harness in `Makefile.am` **and generated `Makefile.in`**. The old fixture is
`test/catalogs/docbook.xml`, not the newer patch's `dockbook.xml` spelling.

The exact original archive was freshly downloaded from its unchanged manifest
URL, checking size **2,426,164** and SHA256
`a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a`.
All nine original reviewed patches replayed in the baseline with full existing
before/after witnesses.
Receipt: `baseline-patch-proof/source.json`, SHA256
`39c130ae22efb61d029768457fe7443d09a3a5b42cdc8645859b2aa8b71fcbde`.
This is source replay, not compiled validation.

## Actual native evidence

The initial missing-toolchain blocker was resolved by explicit parent approval.
One disposable container used exact cached image config digest
`sha256:0c5586b42b0da040b974f5e87b80c6075374216186f28ad3833eb4cab704fc2f`.
The stable repository-policy helper passed before signed `apk add --no-cache
build-base zlib-dev xz-dev`. GCC/g++ `15.2.0-r5`, musl-dev `1.2.6-r3`, make
`4.4.1-r4`, zlib-dev `1.4-r0`, xz-dev `5.8.4-r0`, the complete before/after
package inventories and tool versions are retained. The writable layer was
266,505,323 bytes. Bridge networking was disconnected before source execution;
native commands ran as UID3000 with a read-only checkout and private disk-backed
scratch, capped at 2 CPUs/3 GiB. No image build/pull/prune, host compilation,
operator-container change or downloaded exploit attachment was used.

`scripts/prove_libxml2_recursion.py` enforces disposable opt-in, Alpine 3.24,
supported native architecture and UID3000 for execution. It reuses existing safe
extraction and full patch witnesses. The exact fresh source replay applied all
13 patches with fuzz zero. All final source witnesses and new fixture hashes
were checked again after mutation restoration.

| Layer | Actual result |
|---|---|
| Original-source RED | 12 expected failures in a 14-case matrix; default/environment-zero positive controls pass |
| Patched behavior | All 14 cases pass: 13 C mode/environment cases, each with 32 lifecycles, plus all three shell cases |
| Resource cleanup | Tracked libxml allocations return to zero after each lifecycle; RelaxNG input opens/closes balance, including 32 default-1,000-include-limit cycles |
| Dangerous mutants | Seven separately rebuilt mutants fail: combined SGML depth checks, SGML dedup, RNG limit, RNG error propagation, and each shell guard; restored source is GREEN again |
| Selected upstream targets | `runtest` reports a **3,809 harness counter**, 0 errors and 0 leaks (not 3,809 unique inputs); `testparser` and `make Catatests` pass |
| Old catalog harness | Seven actual script/catalog pairs independently equal their existing golden bytes, avoiding the old Makefile's ignored-loop-exit behavior |
| ABI/features | All 1,627 original exported symbol/type/object-size records retained; one additive upstream setter; same SONAME/NEEDED, identical generated feature configuration; old-header consumer loads the new library |
| Python helper at `8dc6d683` | Nine aligned backend subset tests passed; Ruff passed; the later upstream-output/catalog-coverage warning and its separate correction are documented in the follow-up |

The baseline and patched C smoke executables are byte-identical; the changed
shared library, not a different smoke binary, determines RED versus GREEN.
An additional fresh patched build uses the recipe's exact `CFLAGS='-O2 -fPIC'`:
all 14 matrix cases, seven mutants, restored GREEN, upstream tests/goldens, ABI
comparison and old-header consumer pass again. Both build configurations and
their exact binary digests are retained separately in the portable evidence.
The include default follows upstream (1,000), with its original setter spelling,
zero-reset and environment/error semantics. The tested native main-thread stack
limit is **8 MiB**; this is not a claim about arbitrary smaller thread stacks.
All four upstream RNG fixture files are retained and their default/limit-2/
limit-3 test is registered in the actual 2.13 `runtest` harness.

The shell port preserves the upstream 470-character command, 470-character
argument and 80-argument cases in POSIX syntax. It additionally checks the guard's
bounded-rejection output: the upstream exit-status-only assertion proved too weak
on an ordinary old native build, which exited zero despite overwritten command
data. That initial non-detecting run, corrected RED, initial SGML callback
instrumentation error (old catalog loading uses direct file IO), and a missing
environment-variable invocation are retained, not counted as successful proofs.
The final SGML test uses small local scratch fixtures and a failing-allocation
budget to stop the old recursive parser safely; RNG uses memory-only IO fixtures.

Portable summary and exact-byte receipts:

* `docs/design/evidence/libxml2-recursion-native-amd64-20261111.json`
* `docs/design/evidence/libxml2-recursion-native-amd64-20261111.receipts.json.gz`

## Reproduction and integration

Within an approved network-disconnected disposable builder, prepare separate
`baseline` (`--baseline`) and `patched` source directories with the helper's
`prepare` action. Use the original archive above and a read-only checkout.
Run `build` with `ECM_DISPOSABLE_LIBXML2_PROOF=1` and
`CFLAGS='-O2 -g -fstack-protector-all -D_FORTIFY_SOURCE=2'`, then `run`
(`--expect-red` for baseline). Against patched source run `mutants`, `run` again,
`upstream`, and `abi --baseline-source <baseline>`. Each action takes explicit
`--repo` and `--source` absolute paths. Only the two small libxml2 source trees,
their tests and intermediate objects are built; no full application image.
Repeat the patched-source actions in a fresh directory with the production
recipe flags `CFLAGS='-O2 -fPIC'` for the second configuration.

Parent-owned integration must refresh global recipe/source hash consumers for
manifest SHA256
`48e2ed4fbd287889eb3b6edaef1974208b47965bd7a18f10e109ad9fc46c7d59`.
Those bindings intentionally remain unchanged here, so the existing production
recipe loader will reject this new manifest until that central integration is
done. The existing recipe test's expected libxml2 patch set was extended, not
weakened. Full source delivery/SBOM/advisory binding, native ARM execution and
fresh both-architecture final-image CI remain mandatory. No new provider,
waiver, publication, merge or deployment is authorized; the bead stays open.
Production wiring must also explicitly call `Catatests` and the trusted seven
golden comparisons: old `make check-local` does **not** invoke `Catatests`.
The follow-up gives a bounded integration proposal without editing the shared
builder, its pins, or historical source receipts.
