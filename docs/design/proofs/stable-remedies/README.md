# Bounded stable-remedy proofs

These files are **proposal/evidence only**, not production provider wiring or a
new backend-gate selection. See `../../stable-alpine-maintained-remedies.md`.

## Executed layers

- `test_evidence_contract.py`: original recorded vendor-row/inventory joins,
  signatures/negative-control receipts, unknown-score preservation and source
  witnesses. It does not cryptographically verify excerpted stanzas itself.
- `test_sqlite_compatibility.py`: harmless bounded in-memory behavior, native
  AMD64 musl/Python 3.12. Both old-source and incomplete-authorization mutants
  were demonstrated red. No crafted corrupt database or operator data.
- `test_glib_parser.py`: all five original upstream invalid XML vectors, plus
  two valid nesting controls, each in a bounded isolated child. Original parser
  gives four SIGSEGV failures; paired correction gives seven passes.
- Both `.patch` files: original code **and tests** adapted to retained sources.
  GLib registration port is included; original full C/session-bus harness is
  **not** reported as executed.

**Not executed:** `test_coreutils_compatibility.py` is the proposed next native
utility selection. Source/test patch application succeeded, but the private full
utility build failed in retained gnulib declarations. No fixed utility binary or
native behavior PASS exists. Do not silently include it in a claimed green run,
skip it, or equate its presence with coverage.

## Replay the executed subset

Private inputs/outputs are hash-bound under
`/tmp/opencode/ecm-evy2p-20261111`. The compressed receipt capsule includes exact
compiler commands, source-extraction/backport scripts and old/red logs. The
compiler is existing host GCC15.2 with signed stable musl/GLib headers, **not a
production Alpine packaging toolchain**. Complete package/ABI/ARM/scan/DBAS
acceptance remains pending.

Run from `/tmp/opencode/ecm-p1-stable-remedies-20261111`:

```bash
docker run --rm --pull=never --network=none --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --cpus=2 --memory=3g --pids-limit=128 --ulimit core=0 --user=3000:3000 \
  --mount type=bind,src=/tmp/opencode/ecm-p1-stable-remedies-20261111,dst=/proof/source,readonly \
  --mount type=bind,src=/tmp/opencode/ecm-evy2p-20261111,dst=/research,readonly \
  --mount type=bind,src=/tmp/opencode/ecm-evy2p-20261111/scratch,dst=/tmp \
  --env ECM_PYTHON=/proof/venv/bin/python \
  --env ECM_TEST_CONFIG_ROOT=/tmp/config \
  --env LD_LIBRARY_PATH=/research/3540100:/research/runtime-libs \
  --env ECM_GLIB_PROOF_LIBRARY=/research/glib-fixed/libintrospection-proof.so \
  --workdir=/proof/source 0c5586b42b0d \
  bash /proof/source/scripts/backend-gate.sh --subset -p no:cacheprovider \
  /proof/source/docs/design/proofs/stable-remedies/test_evidence_contract.py \
  /proof/source/docs/design/proofs/stable-remedies/test_sqlite_compatibility.py \
  /proof/source/docs/design/proofs/stable-remedies/test_glib_parser.py
```

For SQLite baseline red, omit the SQLite library directory from
`LD_LIBRARY_PATH`; for the incomplete-fix red, use `/research/sqlite-auth-mutant`.
For GLib source baseline red, use
`ECM_GLIB_PROOF_LIBRARY=/research/glib-old/libintrospection-proof.so` and retain
`/research/runtime-libs`. These expected-red commands must exit nonzero.

For future coreutils execution, supply an actual tested binary pair through
`ECM_COREUTILS_PROOF_PREFIX=/research/fixed` (meaning `fixed-uniq` and
`fixed-unexpand`) and select its test file explicitly. The required full provider
suite must also retain the original multibyte locale tests, both native
architectures, GNU capabilities and no-fix mutants; these bounded cases are not
a substitute.
