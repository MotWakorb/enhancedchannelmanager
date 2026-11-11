# libxml2 production upstream-proof seam — integration preparation only

Bead `enhancedchannelmanager-m8m42` remains **P2, in progress**. This isolated
branch builds on source ports `8dc6d683` and proof-contract correction `53f25f13`.
It is not included in the independently tested Edit Mode 0067 candidate/PR1066.

## Implemented boundary

The security builder retains its existing configure, build, and `make check`
commands. Its libxml2-only autotools path then invokes the exact SHA-bound
`prove_libxml2_recursion.py upstream` phase **before** `make install`. That helper
requires completed `runtest`/`testparser` output, explicit `Catatests` with all
three shell cases, and the seven exact catalog golden pairs. Speex does not
dispatch this phase; the librsvg Meson path is unchanged.

The builder configures the proof subprocess with UID/GID3000, no supplementary
groups, a private source-local TMPDIR, and no inherited `RNG_INCLUDE_LIMIT`.
Ownership changes target only its canonical extracted source below its work
directory, without following symlinks. The parent remains root for installation;
the SDK/runtime copy logic and installation command are unchanged. Actual
installed ownership/mode preservation still needs fresh native verification.

Before installation, the parent validates the exact receipt schema, PASS state,
contract, positive harness counter with zero errors/leaks, all ordered commands,
catalog inventory, and each bounded regular log's digest. It retains the exact
receipt and three log files in `sources/recipe/libxml2-upstream-proof/`; their
complete inventory and the launch-log hash enter the libxml2 component record.
The source recipe collector now also freezes the helper and its contract, and
the security Dockerfile copies the helper into the builder.

Enforcement tests: `backend/tests/unit/test_libxml2_production_upstream.py`.
Helper contracts: `backend/tests/unit/test_prove_libxml2_recursion.py`.

## Verification and explicit limits

- Initial new orchestration tests: **14 RED** before the seam existed.
- Final focused canonical subset: **52 passed** (17 orchestration tests plus
  35 helper contracts); all 74 backend pins verified. This is **not** the full
  backend gate.
- In-memory semantic mutants accept a RUNNING receipt or omit the production
  dispatch; the corresponding tests reject both. Candidate files are read-only
  during these runs.
- Ruff check/format and whitespace checks are required before commit.

These tests mock native subprocesses, `chown`, and installation. They prove
Python orchestration, argument configuration, receipt/log rejection and retention,
not actual privilege dropping, native upstream execution, ABI or final images.
They ran in the cached aligned Alpine Python image with read-only source,
network disabled, nonroot UID3000, and private disk-backed scratch. No native
source build, hazard probe, image pull/prune or operator action was performed.

Evidence directory:
`/tmp/opencode/ecm-p2-libxml2-production-seam-proof-20261111/` (red/green logs,
canonical receipts, and semantic-mutant logs). No historical native receipt,
source port, patch, contract or native binary was rewritten.

## Deliberately pending global transaction

**This branch is not a usable production candidate yet.** The current security
recipe loader still binds `4940090f...f7bfa`, whereas the reviewed source candidate
manifest is `48e2ed4f...7d59`; it continues to fail closed. The complete remaining
integration must coordinate that current recipe identity with packager, source,
runtime metadata and SBOM consumers. It must not allow arbitrary old/new hashes
or relabel historical receipts as fresh evidence.

Historical fixture verification must select an exact reviewed original
recipe/helper/receipt tuple, retaining all signature, architecture, composition,
inventory and live-byte checks. A current producer must not gain permission to
emit an old recipe merely because historical read-only verification exists.
The existing native fixtures and historical receipt bundles are immutable
evidence, not files to regenerate until tests turn green.

After that transaction: independent review, actual AMD64 **and ARM64** execution
under the new helper/builder identities, retained source/log/provider joins,
native matrix/mutant/ABI checks, and both-architecture final source/image/advisory
acceptance remain required. No PR, protected merge, publication, deployment,
release-hold waiver or bead closure is authorized by this preparation.
