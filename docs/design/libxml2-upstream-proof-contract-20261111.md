# libxml2 upstream-proof contract correction

Bead `enhancedchannelmanager-m8m42` is now **P2**, remains in progress, and is
not closed by this change. Scope is the existing approved libxml2 provider;
vendor-only decisions for additional providers do not remove this work. No XML
change is implicitly included in the parent's independently tested 0067 app
candidate. New combined source/native/advisory proof is required before delivery.

## What was wrong, and what changed

The source ports at `8dc6d683` are unchanged. Independent review identified two
proof/production warnings, reproduced here rather than dismissed because the
previous native runs were genuine:

1. `native_upstream()` accepted exit-zero commands without checking output and
   learned catalog coverage from a mutable glob. Empty outputs and zero/missing
   catalog pairs could therefore pass.
2. `Makefile.am:184`'s `check-local` does not invoke legacy `Catatests`; the
   shared security builder currently runs only `make check` before installation.
   Registering the shell regression in `Catatests` did not wire it into production.

The helper now checks the terminal `runtest` summary and the complete ordered
suite headings. It records `executed`, `errors` and `leaks` fields. The exact
SHA-bound `runtest.c` emits `no errors` only when **both** error/leak counters are
zero. The observed value **3,809 is a harness counter, not unique input count**.
No 3,809 threshold or unique-fixture assumption is used. Missing, malformed,
truncated, zero-execution or error/leak summaries are rejected even with exit 0.

Expected catalog coverage comes from
`docker/alpine-native/libxml2-upstream-test-contract.json`, whose complete SHA256
is bound in this helper. It records the exact 2.13.9 archive and all 13 patch
identities, the 53 observed suite headings, and hashes for 28 reviewed source
files: `runtest.c`, `testparser.c`, all 19 catalog inputs and seven golden results.
Those inputs were taken from the original verified native source, not invented
for the matcher. The catalog triples are exactly:

* `catal.sgml`, `docbook.xml`, `recursive-duplicate.sgml`, `recursive.sgml`,
  `registry.xml`, `whites.sgml`, `whitex.xml`, each with its corresponding script
  and golden result.

All expected files must exist with their bound bytes; unknown catalog inputs,
missing scripts/catalogs/goldens, symlinks and modified source files are rejected.
Commands use prechecked script/golden bytes and the source is checked again after
execution, so a command cannot redefine its own expected output. An incomplete
run overwrites any old PASS receipt with RUNNING, never leaving a stale success.

`testparser` has no completion-summary protocol. Its reviewed complete combined
stdout/stderr is the expected negative-schema diagnostic, bound by hash together
with its source/configuration; process exit status is still mandatory. `Catatests`
must include start/end banners and all three distinct ordered shell PASS markers.
The three original shell cases and independent seven-pair comparison are retained.

## Tests and evidence scope

Tests were written before the fix: **22 failed, nine passed** against the old
helper. They reproduce empty/fake exit-zero output, truncated/zero/error/leak
summaries, missing/duplicate suite coverage, empty catalog coverage, a missing
pair, and mutable expectations. After the correction **35 focused unit/contract
checks pass**; Ruff and its format check pass. The exact logs, identity checks
and helper/contract digests are retained separately:

* [Follow-up summary](evidence/libxml2-proof-contract-followup-20261111.json)
* [Exact-byte follow-up receipts](evidence/libxml2-proof-contract-followup-20261111.receipts.json.gz)

The recorded complete native captures still validate. A synthetic counter variant
checks that counter parsing does not mistake 3,809 for a unique-fixture count.

All tests use the cached aligned `0c5586b42b0d` Python image, nonroot UID3000,
read-only checkout, no network and private disk-backed scratch, capped at
2 CPUs/3 GiB. Native subprocess capture is stubbed in these **contract tests**;
the recorded real output and exact recorded source files are parsed. No new native
source compilation, hazard probe, image build/pull/prune or operator change ran.

The original 323 exact-byte receipts and six native binary hashes were checked
again, as were the 13 patch assets and 27 distinct patched source-file identities.
The 13 C mode/environment cases × 32 lifecycles, three shell cases (14 aggregated
matrix entries), seven goldens, 1,627 baseline export kind/object-size records,
sole upstream setter addition and SONAME/NEEDED/features remain historical native
evidence for their recorded identities. **This is offline revalidation, not a
fresh native pass under the changed helper.** Neither historical receipt bundle
nor its metadata was rewritten to pretend otherwise.

## Bounded production integration proposal — not applied

The next integration owner must retain `make check` and insert the following
libxml2-only proof phase after the current `make check` and before `make install`
in `scripts/build_alpine_security_libraries.py`. This invokes `Catatests` plus
independent trusted goldens via `upstream`; it does not replace modern tests.
The shared builder already requires disposable root and validates native arch
and stable repositories. Add `subprocess`/`sys` imports for this proposed block:

```python
if item["name"] == "libxml2":
    _require(
        source.is_dir() and not source.is_symlink()
        and source.resolve().is_relative_to(work.resolve()),
        "libxml2 proof must own only its disposable extracted source",
    )
    (source / ".proof-tmp").mkdir()
    # Ownership setup only; no root execution of the proof or native tests.
    for directory, directories, files in os.walk(source, followlinks=False):
        for path in [Path(directory), *(Path(directory) / n for n in directories + files)]:
            os.chown(path, 3000, 3000, follow_symlinks=False)
    helper = Path(__file__).with_name("prove_libxml2_recursion.py")
    proof_env = {
        **env, "ECM_DISPOSABLE_LIBXML2_PROOF": "1",
        "HOME": str(source), "TMPDIR": str(source / ".proof-tmp"),
    }
    proof_env.pop("RNG_INCLUDE_LIMIT", None)
    command = [
        sys.executable, str(helper), "upstream",
        "--repo", str(helper.parent.parent),
        "--assets", str(manifest.parent), "--source", str(source),
    ]
    with (sources / "recipe/libxml2-upstream-command.log").open("wb") as log:
        subprocess.run(
            command, cwd=source, env=proof_env, user=3000, group=3000,
            extra_groups=[], stdout=log, stderr=subprocess.STDOUT, check=True,
        )
    proof = json.loads((source / "proof-upstream.json").read_text())
    _require(
        proof["status"] == "PASS"
        and proof["contract_sha256"]
        == sha256(manifest.parent / "libxml2-upstream-test-contract.json"),
        "libxml2 upstream proof is incomplete or bound to another contract",
    )
    retained = sources / "recipe/libxml2-upstream-proof"
    retained.mkdir()
    for name in ["proof-upstream.json", *(entry["log"] for entry in proof["upstream"])]:
        path = source / name
        _require(
            name == Path(name).name and path.is_file() and not path.is_symlink(),
            "unsafe libxml2 upstream proof member",
        )
        shutil.copy2(path, retained / name)
    # Bind complete retained bytes and this command in the new native receipt.
```

This is a **proposal**, not an applied/tested shared-builder change. The owner
must add integration tests for UID drop, libxml2-only dispatch, refusal of a
non-PASS receipt, exact proof/log retention and unchanged installed payload
UID/GID/modes before accepting it. The helper's
new explicit `--assets` path is contract-tested with the actual `/proof/recipe`
layout; it avoids guessing a checkout-relative path in copied source artifacts.

Additional required integration work:

* Copy `prove_libxml2_recursion.py` into `/proof/scripts/` in the security builder;
  the existing directory COPY brings the contract into `/proof/recipe/`.
* Freeze both helper and contract in the delivered source recipe, alongside the
  existing dependencies. The current source collector's helper COPY list omits
  this helper and contract and must be extended deliberately.
* Run this phase on actual native AMD64 **and ARM64**, retain current helper,
  contract, archive/patch/source/binary identities and counters, and join the new
  receipts to the actual installed provider. The full matrix/mutants/ABI and
  final source/image/advisory acceptance obligations remain separate and pending.
* Refresh current builder, packager, source and SBOM bindings as one reviewed
  transaction. Historical receipts must still be checked against their exact
  original frozen recipe/helper tuple. Do not broadly allow a set of unrelated
  hashes, relabel old receipts as new runs, or regenerate historical fiction.

No shared builder, shared pin, application-source manifest, SBOM, version,
COMPONENTS file, workflow, production source patch or runtime provider is changed
by this follow-up. Fresh native CI using the new helper identity remains required.
