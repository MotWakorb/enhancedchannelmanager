# Stable Alpine backend regression proof

Tracks `enhancedchannelmanager-63a31` and `enhancedchannelmanager-tikvb` under
the [migration parity matrix](stable-alpine-parity.md). This is a test-only
application environment, not the production media/bootstrap runtime.

## Corrections to the five observed failures

- `backend/date_placeholders.py` preserves GNU formatting on musl instead of
  accepting musl's empty result for unknown directives such as `%Q`. Existing
  GNU-libc execution still uses its native formatter. The musl path preserves
  literals, GNU aliases/flags/padding and legitimate empty `%z`/`%Z` fields;
  both single-date and range expansion use the same formatter. The save-time
  schema/lint gates and real condition evaluator share this expansion.
- `backend/tests/test_cloud_storage.py` controls only DNS answers for the Google
  endpoint tests, not the SSRF validator or adapter. It asserts that **both** API
  and token hosts are validated for explicit and omitted token URIs. Added
  poisoned-DNS cases require denial when either otherwise legitimate Google
  hostname returns a public address alongside the metadata address. Literal
  metadata URI refusals now reach that URI instead of accidentally passing due
  to an earlier failure resolving the fixed API host.
- `docker/alpine-native/Dockerfile.backend-test` installs test dependencies into
  `/proof/venv`, leaving `/opt/venv` absent as the three existing fallback/
  diagnostics/launcher tests intend. Production interpreter/launcher resolution
  and its absolute `/opt/venv` paths are unchanged.

`test_date_strftime_compat.py` parses an actual immutable baseline observation
fixture: **815 formats × three dates × two timezones = 4,890 cases**, covering
unknown codes, escaped percent signs, GNU flags/widths/modifiers, early years,
leap dates, microseconds and legitimate empty timezone output. The observations
use the application's default C time locale and UTC/America-New_York zones.
This bounded corpus is not a claim to have tested every possible format/locale.
The original `%Q` assertion is unchanged, not relaxed to accept empty output.

## Reproduce in an owned disposable environment

Supply the previously downloaded and hash-verified x86 musllinux wheels for all
unchanged `backend/requirements.txt` pins as the `proof-wheels` build context.

```sh
docker buildx build --platform linux/amd64 --progress plain \
  --build-context proof-wheels=/absolute/owned/wheels \
  --file docker/alpine-native/Dockerfile.backend-test \
  --tag ecm-alpine-backend-test:owned --load .
```

Use a fresh results directory owned by the test UID/GID (build defaults 3000:3000).
Inspect the built image and run its immutable image ID, not a floating tag:

```sh
docker run --rm --network none --cpus 2 --memory 4g --pids-limit 1024 \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,src=/absolute/new/results,dst=/proof/results \
  --env TMPDIR=/proof/results IMAGE_ID bash scripts/backend-gate.sh
```

The canonical command has no selection/coverage overrides. Retain its complete
log and completion receipt under the results bind, source commit, immutable image
ID, exact requirement hash, counts/skips/coverage and actual exit code. Verify the
results mount is writable by the non-root test user before starting a long run.
Do not reuse operator config or ports. A subset or build success is not the gate.

The original failed `ad79fd06` run remains retained, including its five failures,
five existing skips and coverage. A passing corrected run supersedes that backend
result only; it cannot clear Codec2 PCM differences, remaining owned security
libraries, complete source/advisory coverage, native ARM, shipped-image bootstrap
or MCP isolation. No production image/CI switch is made by this test recipe.

## Verified result

Source commit `df80c49d96d33f6747d5610e98bea732a8618c30`, image
`sha256:e72ed3f7cf6bf218503d2905d263efdf2a5f28b8716bd2bf133094ee837ea4b5`:

- Full unmodified backend gate: **16,472 passed, zero failed, 5 skipped,
  2 deselected**, **84.20% coverage**, in 1,755.08 seconds.
- All five original failing node IDs pass in a separate explicit subset. The
  broader affected-module subset passes all 120 tests as well.
- Requirement hash, canonical pytest selection/coverage arguments and all five
  skip reasons independently match the earlier failed run. No new exclusions,
  skips, lowered coverage or production security exceptions were introduced.
- Image contents were matched to 1,745 tracked source/recipe file SHA256 values
  before the full run. The one tracked generated frontend test-result artifact
  already excluded by `.dockerignore` is named separately in that binding record;
  it is not a skipped backend test or an omitted application source file.
- In-memory mutation checks on the actual Alpine image make the original `%Q`
  assertion and real evaluator negative case fail when native musl formatting is
  restored. Both poisoned-host tests fail if endpoint validation is bypassed.

Raw evidence lives in `/tmp/opencode/ecm-alpine-backend-proof-20261108/`:
`backend-full-04.log`, `backend-result-04.json`, `backend-source-binding-04.json`,
`backend-results-04/ecm-gate-9xqbcvf7/receipt.json`,
`five-failures-final-verification.json`, `five-original-failures-final.log` and
`failure-regression-mutations.json`. The first `03` attempt stopped in preflight
on the already Docker-ignored frontend test-result artifact; no gate ran or pass
was claimed for that attempt.
