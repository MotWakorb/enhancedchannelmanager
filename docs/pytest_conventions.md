# Pytest Conventions

When running backend tests, use this exact command:
```bash
python -m pytest tests/ --tb=short --no-header -p no:warnings 2>&1 | tail -1
```

Do NOT use `-q` — it suppresses the summary line (`2147 passed in 50s`) when all tests pass, leaving only dots and `[100%]`. Without `-q`, the summary is always the last line.

**Why:** Agents waste extra test runs trying different grep/tail patterns because `-q` mode hides the pass count and warnings bury everything else.

**How to apply:** Use the exact command above. Never vary it. One run, one `tail -1`, done.
