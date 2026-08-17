# Pytest Conventions

When running backend tests, use this exact command (from `backend/`):
```bash
../.venv/bin/python -m pytest tests/ --tb=short --no-header -p no:warnings 2>&1 | tail -1
```

Pin the project venv interpreter, not ambient `python`. Ambient `python` commonly resolves an older `cryptography` build and silently self-skips 9 TLS tests instead of failing, so a passing tail line looks identical either way. The only tell is the skip count.

Do NOT use `-q`. It suppresses the summary line (`2147 passed in 50s`) when all tests pass, leaving only dots and `[100%]`. Without `-q`, the summary is always the last line.

**Why:** Agents waste extra test runs trying different grep/tail patterns because `-q` mode hides the pass count and warnings bury everything else.

**How to apply:** Use the exact command above. Never vary it. One run, one `tail -1`, done.

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

A security test that needs a credential-shaped value (a token, a password, a webhook URL) will trip `scripts/check_secrets.py`, the pre-merge secrets ratchet, unless the fixture is built the way `backend/tests/unit/test_check_pii.py` already builds them. That file is the canonical example, and it says so in its own module docstring: fixtures are assembled from repeated or obviously patterned characters, so they carry the right SHAPE for the rule under test without ever resembling a real secret to a human reader or a scanner. It is full of credential shapes (a Telegram bot token, a Discord webhook, hex- and base64-looking values) and appears zero times in `.secrets.baseline`, so this is a proven convention, not a theory.

Two techniques. Pick based on whether the value's exact shape is load-bearing for the test:

- **Split literals**, when the shape matters (length, charset, separators). Concatenate pieces so no single literal in the file matches the pattern a scanner looks for. Example, a Telegram token (`digits:35-char-run`):
  ```python
  FAKE_TELEGRAM_VALUE = "8123456789:" + "Ab3Cd6Ef9Gh2Jk5Lm" + "8Np1Qr4St7Uv0Wx3Yz"
  ```
- **Angle-bracket placeholders**, when any string will do. `is_templated_secret` is one of this repo's required filters, and the `SECRET` regex's `(?=\w+)` lookahead means a value starting with `<` never becomes a scan candidate in the first place:
  ```python
  "password": "<synthetic-dispatcharr-password>"
  ```

**The inline pragma will not save you, on purpose.** `scripts/check_secrets.py` runs `detect-secrets-hook` with `--disable-filter detect_secrets.filters.allowlist.is_line_allowlisted`, so a `# pragma: allowlist secret` comment does nothing, even though that pragma is exactly what detect-secrets' own failure output tells you to add. Disabling it is a deliberate, sound decision: a pragma is exactly what an attacker landing a real secret would also add. Don't spend time on it. Use one of the two techniques above instead.

**A clean-looking failure report can still be incomplete.** The gate prints `FAIL: possible secret finding or ambiguous baseline mutation.` plus the list of changed paths, never the actual finding, because findings must not be echoed into public CI logs. Separately, detect-secrets treats two identical-valued findings in the same file as a single finding, so a second or third occurrence of the same literal can go unlisted. Fixing only the lines the report names does not guarantee a green re-run: grep the file yourself for every occurrence of the value before pushing again. The `KeywordDetector` denylist regex also has no word boundary, so a field like `smtp_password` matches the generic `password` rule; searching for the literal field name will not find every hit either, search for the value instead.
