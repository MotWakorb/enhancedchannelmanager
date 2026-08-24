# Pytest Conventions

When running backend tests, run the gate script. It takes no arguments and works from any directory:

```bash
scripts/backend-gate.sh
echo $?          # 0 = green
```

**Do not hand-type a pytest invocation.** This page used to specify one here and instruct "never vary it" — while CI ran a different one. That is precisely how two gate figures ended up in circulation, 72 collected tests apart (bead `enhancedchannelmanager-c9lb9`). The script is now the single invocation, and `backend/tests/unit/test_backend_gate_contract.py` asserts it still matches `.github/workflows/test.yml` flag for flag. Full detail — what it excludes and why, the expected `3 skipped, 2 deselected` shape, the interpreter trap, and the subset-run coverage trap — is in [`docs/testing.md`](testing.md#what-the-backend-gate-runs).

Two things the script handles that a typed command does not:

- **The interpreter.** Ambient `python` commonly resolves an older `cryptography` build and silently self-skips 9 TLS tests instead of failing, so a passing summary line looks identical either way; the only tell is the skip count. The script selects the project interpreter and refuses to fall back.
- **Reading the result.** Check `$?`. Do not pipe the run through `tail` and read that — `cmd | tail` reports `tail`'s exit status, not pytest's, so a failed suite can read as a pass. If you want the summary line, redirect to a file, check `$?`, then read the file.

Do NOT add `-q`. It suppresses the summary line (`2147 passed in 50s`) when all tests pass, leaving only dots and `[100%]`.

## A Fake `journal.db` Must Be a Real SQLite File

Several backup tests used to write a `journal.db` fixture containing only the SQLite magic bytes,
because the only thing reading it was a header check. That stub is not a database: `sqlite3` opens
it happily and then fails on the first query. It passed for as long as it did because the backup's
redaction step failed **open**, so a fixture that could not be queried took the fallback path and
still produced an artifact.

Redaction now fails closed (bead `enhancedchannelmanager-gi4zn`), so an unqueryable `journal.db`
fails the whole backup and therefore the test. Write a real, empty SQLite file instead:

```python
import sqlite3

sqlite3.connect(journal_path).close()
```

The magic-byte stub is still correct in one place, and the difference is the point.
`_make_backup_zip` in `backend/tests/routers/test_backup.py` keeps it for the `journal.db` **ZIP
member**, because `_validate_backup_zip` checks exactly those bytes and never opens the file. The
source database on disk gets a real one, because the producer queries it.

The general trap, which outlives this one fixture: **a stub built to satisfy the check you know
about will pass while the thing it stands in for is broken.** A magic-byte file satisfies "starts
with `SQLite format 3`" and satisfies nothing else. Match the fixture to what the code under test
does with it, not to the cheapest check it currently passes.

## Credential Fixtures in Security Tests

A security test that needs a credential-shaped value (a token, a password, a webhook URL) should build the fixture from repeated or obviously patterned characters, so it carries the right SHAPE for the rule under test without ever resembling a real secret to a human reader or a scanner. `backend/tests/routers/test_9kwzp13_alert_method_masking.py` and `backend/tests/routers/test_gi4zn_standard_artifact_full_redaction.py` follow the convention and are the examples to copy.

**This is now a convention, not a gate.** `scripts/check_secrets.py`, the pre-merge secrets ratchet, and its committed `.secrets.baseline` were removed in the CI gate reduction, along with `scripts/check_pii.py`. Nothing scans added lines for credential shapes any more; a real secret in a fixture will merge.

Two techniques. Pick based on whether the value's exact shape is load-bearing for the test:

- **Split literals**, when the shape matters (length, charset, separators). Concatenate pieces so no single literal in the file matches the pattern a scanner looks for. Example, a Telegram token (`digits:35-char-run`):
  ```python
  FAKE_TELEGRAM_VALUE = "8123456789:" + "Ab3Cd6Ef9Gh2Jk5Lm" + "8Np1Qr4St7Uv0Wx3Yz"
  ```
- **Angle-bracket placeholders**, when any string will do. `is_templated_secret` is one of this repo's required filters, and the `SECRET` regex's `(?=\w+)` lookahead means a value starting with `<` never becomes a scan candidate in the first place:
  ```python
  "password": "<synthetic-dispatcharr-password>"
  ```

**Historical note on the inline pragma.** While the ratchet existed it ran `detect-secrets-hook` with `--disable-filter detect_secrets.filters.allowlist.is_line_allowlisted`, so a `# pragma: allowlist secret` comment did nothing, even though that pragma is exactly what detect-secrets' own failure output tells you to add. That was deliberate: a pragma is exactly what an attacker landing a real secret would also add. Existing fixtures still carry no pragmas for that reason. Use one of the two techniques above rather than adding one.

**A clean-looking failure report can still be incomplete.** The gate prints `FAIL: possible secret finding or ambiguous baseline mutation.` plus the list of changed paths, never the actual finding, because findings must not be echoed into public CI logs. Separately, detect-secrets treats two identical-valued findings in the same file as a single finding, so a second or third occurrence of the same literal can go unlisted. Fixing only the lines the report names does not guarantee a green re-run: grep the file yourself for every occurrence of the value before pushing again. The `KeywordDetector` denylist regex also has no word boundary, so a field like `smtp_password` matches the generic `password` rule; searching for the literal field name will not find every hit either, search for the value instead.
