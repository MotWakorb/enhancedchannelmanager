"""Performance / pytest-benchmark suite for the Stats v2 hot read paths.

Bead: ``enhancedchannelmanager-skqln.10``.

These tests do **not** run in the default `pytest tests/` invocation —
they're filtered out by the ``benchmark`` marker (registered in
``pytest.ini``) and the dedicated ``--benchmark-only`` selection done by
``.github/workflows/perf-benchmarks.yml``.

See ``conftest.py`` here for the seeded-database fixture and the design
notes for why we benchmark at the SQL layer rather than the HTTP layer.
"""
