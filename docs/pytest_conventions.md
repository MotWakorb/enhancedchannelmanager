# Pytest Conventions

When running backend tests, use this exact command (from `backend/`):
```bash
../.venv/bin/python -m pytest tests/ --tb=short --no-header -p no:warnings 2>&1 | tail -1
```

Pin the project venv interpreter, not ambient `python`. Ambient `python` commonly resolves an older `cryptography` build and silently self-skips 9 TLS tests instead of failing, so a passing tail line looks identical either way. The only tell is the skip count.

Do NOT use `-q`. It suppresses the summary line (`2147 passed in 50s`) when all tests pass, leaving only dots and `[100%]`. Without `-q`, the summary is always the last line.

**Why:** Agents waste extra test runs trying different grep/tail patterns because `-q` mode hides the pass count and warnings bury everything else.

**How to apply:** Use the exact command above. Never vary it. One run, one `tail -1`, done.
