"""Bounded in-memory prototype contracts, not complete SQLite CVE clearance.

Run in the pinned native musl Python 3.12 image, selecting the private library
before interpreter startup. No operator database or SQLite CLI is opened.
"""

import sqlite3

import pytest


def test_join_authorizes_every_coalesced_column():
    """Adapt upstream auth-9.2/9.3, including 5694c3bb's second-column fix."""
    with sqlite3.connect(":memory:") as db:
        db.executescript(
            "CREATE TABLE t1(x,y1); INSERT INTO t1 VALUES(1,'a');"
            "CREATE TABLE t2(x,y2); INSERT INTO t2 VALUES(99,'z');"
        )
        reads = []

        def authorizer(op, table, column, _database, _trigger):
            if op == sqlite3.SQLITE_READ:
                reads.append((table, column))
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorizer)
        assert db.execute("SELECT x,y2 FROM t1 FULL JOIN t2 USING(x)").fetchall() == [
            (1, None),
            (99, "z"),
        ]
        assert ("t1", "x") in reads
        assert ("t2", "x") in reads


def test_small_cte_obeys_configured_depth():
    """Eleven ordinary CTEs and a deliberately small limit, no deep payload."""
    sql = (
        "WITH c0 AS (SELECT 1 AS x),"
        + ",".join(f"c{i} AS (SELECT x FROM c{i - 1})" for i in range(1, 11))
        + " SELECT x FROM c10"
    )
    with sqlite3.connect(":memory:") as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 12)
        assert db.execute(sql).fetchall() == [(1,)]
        db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 11)
        with pytest.raises(sqlite3.OperationalError, match="nested too deep"):
            db.execute(sql + " ")


def test_enabled_features_and_private_backup_roundtrip():
    with sqlite3.connect(":memory:") as db, sqlite3.connect(":memory:") as copy:
        options = {row[0] for row in db.execute("PRAGMA compile_options")}
        assert {
            "ENABLE_FTS3",
            "ENABLE_FTS3_PARENTHESIS",
            "ENABLE_FTS4",
            "ENABLE_FTS5",
            "ENABLE_RTREE",
            "ENABLE_GEOPOLY",
            "ENABLE_SESSION",
            "ENABLE_PREUPDATE_HOOK",
            "ENABLE_COLUMN_METADATA",
            "ENABLE_UNLOCK_NOTIFY",
            "MAX_VARIABLE_NUMBER=250000",
            "THREADSAFE=1",
        } <= options
        db.execute("PRAGMA foreign_keys=ON")
        for module in ("fts3", "fts4", "fts5"):
            db.execute(f"CREATE VIRTUAL TABLE {module}_test USING {module}(body)")
            db.execute(f"INSERT INTO {module}_test VALUES ('ordinary bounded text')")
            assert db.execute(
                f"SELECT count(*) FROM {module}_test WHERE body MATCH 'bounded'"
            ).fetchone() == (1,)
        db.execute("CREATE VIRTUAL TABLE spatial USING rtree(id,x0,x1,y0,y1)")
        db.execute("INSERT INTO spatial VALUES(1,0,1,0,1)")
        db.execute("CREATE TABLE parent(id PRIMARY KEY)")
        db.execute("CREATE TABLE child(parent_id REFERENCES parent(id))")
        db.execute("INSERT INTO parent VALUES(1)")
        db.execute("INSERT INTO child VALUES(1)")
        db.commit()
        db.backup(copy)
        assert copy.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert copy.execute("SELECT * FROM spatial").fetchall() == [
            (1, 0.0, 1.0, 0.0, 1.0)
        ]
        assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
