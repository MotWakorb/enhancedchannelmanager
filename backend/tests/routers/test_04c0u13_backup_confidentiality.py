"""Backup confidentiality policy (bead enhancedchannelmanager-04c0u.13).

The first group of tests pins the POLICY MECHANICS: what a plaintext legacy ZIP
may contain, what a legacy restore must still reinstate, and that every locally
persisted artifact is owner-only.

The second group is the CONTENT PROOF the acceptance criteria ask for. It does
not read the producer's intent; it generates a real artifact through the real
producer against seeded identity, TLS and provider-credential state and then
enumerates what is actually inside the bytes on disk. Those assertions are the
executable form of the confidentiality table in
``docs/user_guide/backup-restore/backup-overview.md`` — if the artifact ever
starts carrying account state, TLS private keys or live credentials again, the
doc becomes false and these tests go red together.

READ ``_scannable_bytes`` BEFORE ADDING A CONTENT ASSERTION. Every member of a
produced artifact is DEFLATE-compressed, so a literal substring scan of the
archive FILE cannot see plaintext and is satisfied by any artifact, leaking or
not. An earlier revision of this file scanned the compressed bytes and passed
while a real leak was present. Scan decompressed member bytes, the same way
``test_0i2vt_backup_artifact.py::test_no_secret_in_decompressed_members`` and
``test_gi4zn_standard_artifact_full_redaction.py`` already do.
"""
import datetime
import errno
import io
import json
import sqlite3
import stat
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from dbas import artifact_crypto
from routers import backup as backup_mod
from tasks import yaml_backup
from tests.routers import test_0i2vt_backup_artifact as artifact_harness

# Credential-shaped fixtures, built as angle-bracket placeholders so the
# secrets ratchet never sees a scan candidate. See
# docs/pytest_conventions.md -> "Credential Fixtures in Security Tests".
FAKE_PASSWORD_HASH = "<synthetic-ecm-password-hash-ZZZ111>"
FAKE_SESSION_TOKEN_HASH = "<synthetic-refresh-session-hash-ZZZ222>"
FAKE_RESET_TOKEN_HASH = "<synthetic-password-reset-hash-ZZZ333>"
FAKE_TLS_PRIVATE_KEY = "<synthetic-tls-private-key-ZZZ444>"
FAKE_STORAGE_SECRET = "<synthetic-cloud-storage-secret-ZZZ555>"

# An Xtream-shaped stream URL sitting inside an UPLOADED PLAYLIST FILE. The
# artifact's guarantee here is structural — ``m3u_uploads/`` is not copied at
# all — so this sentinel is enforceable and belongs in the forbidden tuple.
FAKE_PLAYLIST_STREAM_URL = (
    "https://provider.example/live/"
    "<synthetic-playlist-user>/<synthetic-playlist-pass>/1.ts"
)

# The SAME shape sitting in an operator-authored free-text CELL
# (``alert_methods.config`` under a key no denylist covers). DELIBERATELY NOT in
# the forbidden tuple: ``_url_carries_credentials`` documents, with its reasons,
# that it does not classify an Xtream PATH-SEGMENT credential, because no general
# rule separates it from an ordinary path and guessing costs the operator a URL
# the restore needs. That residual is pinned as characterization by
# ``test_xtream_path_credential_in_free_text_is_a_documented_residual`` and
# carried in the docs by backup-overview.md's "inspect operator-authored free
# text before sharing either plaintext format".
FAKE_ALERT_METHOD_URL = (
    "https://provider.example/live/"
    "<synthetic-alert-user>/<synthetic-alert-pass>/2.ts"
)

# A Dispatcharr address whose credential is in RFC 3986 userinfo. Built from
# split literals rather than an angle-bracket placeholder because the SHAPE is
# load-bearing here: ``_find_urls_in_text`` does not recognize a URL whose
# userinfo contains ``<``, so a bracket placeholder would make the scrubber a
# no-op and the test would pass without exercising it. See
# docs/pytest_conventions.md -> "Credential Fixtures in Security Tests".
FAKE_DISPATCHARR_URL_MARKER = "PwZZZ666" + "aaa"
FAKE_DISPATCHARR_URL = (
    "http://admin:" + FAKE_DISPATCHARR_URL_MARKER + "@dispatcharr:9191"
)
FAKE_EPG_URL_MARKER = "EpgPassAaa" + "222"
FAKE_EPG_URL = (
    "https://epg.example/xmltv.php?username=EpgUserAaa111&"
    + "pass" + "word=" + FAKE_EPG_URL_MARKER
)

# Everything the confidentiality policy says a plaintext artifact must not
# carry, in one tuple so a byte scan cannot silently miss one.
FORBIDDEN_IN_PLAINTEXT_ARTIFACT = (
    FAKE_PASSWORD_HASH,
    FAKE_SESSION_TOKEN_HASH,
    FAKE_RESET_TOKEN_HASH,
    FAKE_TLS_PRIVATE_KEY,
    FAKE_PLAYLIST_STREAM_URL,
    FAKE_STORAGE_SECRET,
)

# Tables whose presence in a shipped journal.db would mean ECM account state,
# session state or a credential store rode along. These are the REAL table names
# (models.py) and the ones ``_AUTH_IDENTITY_TABLES`` names: the scrub is an
# allowlist that drops anything not on it, so a made-up table name would be
# absent from the artifact for the trivial reason that no allowlist can contain
# it — passing identically if the real scrub were broken.
IDENTITY_TABLES = ("users", "user_sessions", "password_reset_tokens")


def _empty_db(path):
    sqlite3.connect(path).close()


def _seed_identity_journal(path):
    """Write a real journal.db carrying ECM account state, session/reset hashes,
    a credential store row, and one ALLOWLISTED table so the artifact is not
    trivially empty.

    Table and column names match ``backend/models.py`` (``user_sessions``,
    ``refresh_token_hash``) so a clean result means the production allowlist
    dropped a table it actually recognizes.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO users VALUES (1, 'admin', ?)", (FAKE_PASSWORD_HASH,)
        )
        conn.execute(
            "CREATE TABLE user_sessions (id INTEGER PRIMARY KEY, refresh_token_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO user_sessions VALUES (1, ?)", (FAKE_SESSION_TOKEN_HASH,)
        )
        conn.execute(
            "CREATE TABLE password_reset_tokens (id INTEGER PRIMARY KEY, token_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO password_reset_tokens VALUES (1, ?)", (FAKE_RESET_TOKEN_HASH,)
        )
        conn.execute(
            "CREATE TABLE cloud_storage_targets (id INTEGER PRIMARY KEY, secret TEXT)"
        )
        conn.execute(
            "INSERT INTO cloud_storage_targets VALUES (1, ?)", (FAKE_STORAGE_SECRET,)
        )
        # Allowlisted table whose config JSON carries an operator-authored URL
        # with an Xtream path-segment credential — the documented residual.
        conn.execute("CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, config TEXT)")
        conn.execute(
            "INSERT INTO alert_methods VALUES (1, ?)",
            (json.dumps({"host": "smtp.example.com", "url": FAKE_ALERT_METHOD_URL}),),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_credential_bearing_trees(root):
    """Lay down the two trees a plaintext artifact must not copy: TLS private-key
    material and an uploaded playlist whose stream URL embeds provider
    credentials.

    Separate from :func:`_seed_config_dir` so a test whose harness creates
    ``uploads/logos`` itself can still seed the material whose ABSENCE it
    asserts. An assertion that a tree is missing proves nothing when the tree was
    never there.
    """
    (root / "tls").mkdir()
    (root / "tls" / "server.key").write_text(FAKE_TLS_PRIVATE_KEY)
    (root / "m3u_uploads").mkdir()
    (root / "m3u_uploads" / "provider.m3u").write_text(FAKE_PLAYLIST_STREAM_URL)


def _seed_config_dir(root):
    """:func:`_seed_credential_bearing_trees` plus the logo tree a legacy ZIP
    legitimately carries."""
    _seed_credential_bearing_trees(root)
    (root / "uploads" / "logos").mkdir(parents=True)
    (root / "uploads" / "logos" / "logo.png").write_bytes(b"logo")


def _archive_bytes(archive) -> bytes:
    if hasattr(archive, "getvalue"):
        return archive.getvalue()
    return archive.read_bytes()


def _scannable_bytes(source) -> bytes:
    """Member NAMES plus every member's DECOMPRESSED bytes.

    THE ONLY CORRECT INPUT TO A CONTENT SENTINEL SCAN. Every member a producer
    writes is ``compress_type=8`` (DEFLATE), so ``sentinel not in
    zip_path.read_bytes()`` holds for any archive whatsoever and cannot fail —
    see ``test_a_raw_archive_scan_is_blind_to_a_deflated_secret``, which measures
    exactly that. Names are included so a secret used as a FILENAME is caught
    too.

    Args:
        source: Anything ``zipfile.ZipFile`` accepts — a path or a file object.

    Returns:
        The concatenated scannable bytes of the whole archive.
    """
    out = bytearray()
    with zipfile.ZipFile(source) as zf:
        for info in zf.infolist():
            out += info.filename.encode("utf-8")
            out += zf.read(info)
    return bytes(out)


def _compression_methods(source) -> set[int]:
    with zipfile.ZipFile(source) as zf:
        return {info.compress_type for info in zf.infolist()}


def _tables_in_member(zf, member, tmp_path) -> set[str]:
    """Return the table names of a SQLite member, extracted to disk first so
    sqlite3 reads it exactly as a restore would."""
    extracted = tmp_path / "extracted-journal.db"
    extracted.write_bytes(zf.read(member))
    conn = sqlite3.connect(str(extracted))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _build_legacy_zip(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _seed_identity_journal(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
        patch.object(
            backup_mod,
            "get_settings",
            return_value=artifact_harness._mock_settings_with_secrets(),
        ),
    ):
        return backup_mod._create_backup_zip()


def _write_zip(path, members: dict[str, bytes]):
    """Write a ZIP with ``members`` plus a valid ``ecm_backup.json`` manifest."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        zf.writestr(
            "ecm_backup.json",
            json.dumps(
                {
                    "version": "0.17.0",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": sorted(members),
                },
                indent=2,
            ),
        )


def _run_legacy_restore(zip_path, config_dir):
    """Drive the REAL ``_restore_from_zip`` against a patched ``CONFIG_DIR``.

    Only the database lifecycle and the pre/post row-count probes are stubbed —
    the directory-restore loop under test runs unmodified.
    """
    with zipfile.ZipFile(zip_path) as zf:
        with (
            patch.object(backup_mod, "CONFIG_DIR", config_dir),
            patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"),
            patch.object(backup_mod, "JOURNAL_DB_FILE", config_dir / "journal.db"),
            patch.object(backup_mod, "close_db"),
            patch.object(backup_mod, "init_db"),
            patch.object(backup_mod, "clear_settings_cache"),
            patch.object(backup_mod, "reset_client"),
            patch.object(
                backup_mod, "_capture_existing_alert_method_configs", return_value={}
            ),
            patch.object(backup_mod, "_capture_existing_auth_rows", return_value={}),
            patch.object(backup_mod, "_count_reestablish_rows", return_value={}),
        ):
            plan = backup_mod._validate_backup_zip(zf)
            try:
                return backup_mod._restore_from_zip(zf, plan)
            finally:
                plan.close()


# --- policy mechanics ------------------------------------------------------

def test_plain_legacy_zip_omits_tls_and_uploaded_playlists(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _empty_db(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
    ):
        artifact = backup_mod._create_backup_zip()

    with zipfile.ZipFile(artifact) as zf:
        names = zf.namelist()
    assert "uploads/logos/logo.png" in names
    assert not any(name.startswith("tls/") for name in names)
    assert not any(name.startswith("m3u_uploads/") for name in names)


def test_legacy_restore_reinstates_tls_and_playlists_from_an_older_artifact(tmp_path):
    """BEHAVIOUR, not constants. Producing a plaintext copy of TLS/playlist
    material is the confidentiality question; restoring an operator's own older
    artifact is not, and narrowing ``BACKUP_DIRS`` must not silently drop those
    trees on the way back in.

    Red-proven by reverting the restore loop's ``LEGACY_RESTORE_DIRS`` to
    ``BACKUP_DIRS``: that reintroduces permanent loss of the operator's only copy
    of a TLS private key, and this is the test that catches it. The constant
    comparison this replaced passed that mutant.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    artifact = tmp_path / "legacy.zip"
    _write_zip(
        artifact,
        {
            "tls/server.key": FAKE_TLS_PRIVATE_KEY.encode("utf-8"),
            "m3u_uploads/provider.m3u": FAKE_PLAYLIST_STREAM_URL.encode("utf-8"),
            "uploads/logos/logo.png": b"logo",
        },
    )

    restored = _run_legacy_restore(artifact, config_dir)

    key = config_dir / "tls" / "server.key"
    playlist = config_dir / "m3u_uploads" / "provider.m3u"
    assert key.is_file(), restored
    assert playlist.is_file(), restored
    assert key.read_text() == FAKE_TLS_PRIVATE_KEY
    assert playlist.read_text() == FAKE_PLAYLIST_STREAM_URL
    assert "tls/server.key" in restored
    assert "m3u_uploads/provider.m3u" in restored


def test_legacy_restore_reinstates_tls_material_owner_only(tmp_path):
    """The restore loop rmtree/mkdir/write_bytes at the process umask, so it
    decides the mode of the TLS tree it recreates rather than inheriting one. A
    private key must land the way ``backend/tls/storage.py`` writes it — 0700
    directory, 0600 file — not world-readable."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    artifact = tmp_path / "legacy.zip"
    _write_zip(artifact, {"tls/server.key": FAKE_TLS_PRIVATE_KEY.encode("utf-8")})

    _run_legacy_restore(artifact, config_dir)

    assert stat.S_IMODE((config_dir / "tls").stat().st_mode) == 0o700
    assert stat.S_IMODE((config_dir / "tls" / "server.key").stat().st_mode) == 0o600


def test_restoring_a_new_artifact_does_not_wipe_existing_tls_material(tmp_path):
    """The converse property, and the one that makes the asymmetry safe.

    Now that a newly produced ZIP carries no ``tls/`` member, the restore loop
    must leave an EXISTING ``CONFIG_DIR/tls`` tree alone. Without this, every
    restore of a current artifact would delete the live TLS private key — the
    same data loss the ``LEGACY_RESTORE_DIRS`` half exists to prevent, arriving
    from the other direction.
    """
    config_dir = tmp_path / "config"
    (config_dir / "tls").mkdir(parents=True)
    (config_dir / "tls" / "server.key").write_text(FAKE_TLS_PRIVATE_KEY)
    (config_dir / "m3u_uploads").mkdir()
    (config_dir / "m3u_uploads" / "provider.m3u").write_text(FAKE_PLAYLIST_STREAM_URL)

    artifact = tmp_path / "current.zip"
    _write_zip(artifact, {"uploads/logos/logo.png": b"logo"})

    _run_legacy_restore(artifact, config_dir)

    assert (config_dir / "tls" / "server.key").read_text() == FAKE_TLS_PRIVATE_KEY
    assert (
        config_dir / "m3u_uploads" / "provider.m3u"
    ).read_text() == FAKE_PLAYLIST_STREAM_URL


def test_legacy_restore_dirs_is_a_superset_of_what_the_producer_writes():
    """Constant-level companion to the two behaviour tests above: anything the
    producer can write must be something the restore reads."""
    assert backup_mod.BACKUP_DIRS == ["uploads/logos"]
    assert set(backup_mod.BACKUP_DIRS) <= set(backup_mod.LEGACY_RESTORE_DIRS)


@pytest.mark.asyncio
async def test_saved_plain_backup_is_owner_only(tmp_path):
    payload = io.BytesIO(b"safe redacted archive")
    with (
        patch.object(backup_mod, "BACKUPS_DIR", tmp_path),
        patch.object(backup_mod, "_create_backup_zip", return_value=payload),
        patch.object(
            backup_mod,
            "_get_backup_filename",
            return_value="ecm-backup-2026-08-18_010203.zip",
        ),
    ):
        result = await backup_mod.save_backup()

    saved = tmp_path / result["filename"]
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_scheduled_yaml_backup_is_owner_only(tmp_path):
    task = yaml_backup.YamlBackupTask()
    with (
        patch.object(yaml_backup, "BACKUPS_DIR", tmp_path),
        patch.object(backup_mod, "build_yaml_export", return_value="settings: {}\n"),
    ):
        result = await task.execute()

    assert result.success
    saved = next(tmp_path.glob("ecm-backup-*.yaml"))
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


# --- generated-artifact content proof --------------------------------------

def test_generated_legacy_zip_content_matches_documented_policy(tmp_path):
    """Enumerate a REAL legacy ZIP built from seeded secret-bearing state."""
    archive = _build_legacy_zip(tmp_path)
    archive_file = io.BytesIO(_archive_bytes(archive))
    scannable = _scannable_bytes(archive_file)

    with zipfile.ZipFile(archive_file) as zf:
        names = zf.namelist()
        tables = _tables_in_member(zf, "journal.db", tmp_path)

    # Structure: logos and the scrubbed database ship; the two credential- and
    # key-bearing trees do not. Both were present in the source /config
    # (_seed_config_dir), so their absence here is a producer decision.
    assert "journal.db" in names
    assert "settings.json" in names
    assert "uploads/logos/logo.png" in names
    assert not any(name.startswith("tls/") for name in names), names
    assert not any(name.startswith("m3u_uploads/") for name in names), names

    # Account state, session state and the credential store are gone by
    # construction (allowlist), not merely emptied.
    for table in IDENTITY_TABLES + ("cloud_storage_targets",):
        assert table not in tables, tables

    # Byte scan across every DECOMPRESSED member, not just the members we
    # expected to be risky.
    for sentinel in FORBIDDEN_IN_PLAINTEXT_ARTIFACT:
        assert sentinel.encode() not in scannable, sentinel
    for sentinel in artifact_harness.ALL_SECRET_SENTINELS:
        assert sentinel.encode() not in scannable, sentinel


def test_generated_dbas_artifact_content_matches_documented_policy(tmp_path):
    """Enumerate a REAL DBAS artifact built from seeded secret-bearing state.

    ``_patched_build`` is the production builder driven against a temp
    ``/config``; only its journal seed is swapped for one that carries ECM
    accounts, session/reset hashes and a credential store. The TLS and playlist
    trees are seeded into that same ``/config`` FIRST, so the two
    ``not any(...)`` assertions below describe material that was really there and
    was really not copied.
    """
    _seed_credential_bearing_trees(tmp_path)
    with patch.object(artifact_harness, "_seed_journal_db", _seed_identity_journal):
        art = artifact_harness._patched_build(tmp_path, with_logos=True)

    scannable = _scannable_bytes(art.zip_path)
    with zipfile.ZipFile(art.zip_path) as zf:
        names = zf.namelist()
        tables = _tables_in_member(zf, "journal.db", tmp_path)

    assert not any(name.startswith("tls/") for name in names), names
    assert not any(name.startswith("m3u_uploads/") for name in names), names
    for table in IDENTITY_TABLES + ("cloud_storage_targets",):
        assert table not in tables, tables
    for sentinel in FORBIDDEN_IN_PLAINTEXT_ARTIFACT:
        assert sentinel.encode() not in scannable, sentinel
    for sentinel in artifact_harness.ALL_SECRET_SENTINELS:
        assert sentinel.encode() not in scannable, sentinel

    # The scheduled/unattended recovery story: this is the artifact a schedule
    # produces, it needs no key, and it is owner-only on disk.
    assert not art.encrypted
    assert stat.S_IMODE(art.zip_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(art.sidecar_path.stat().st_mode) == 0o600


def test_legacy_settings_json_no_longer_ships_a_credential_inside_a_url(tmp_path):
    """The legacy ZIP was the ONLY producer reaching the archive without the
    value-aware URL scrubber.

    ``_gather_settings`` masked credential-class fields BY NAME, so a credential
    embedded in a URL VALUE under a name no denylist covers — ``url``,
    ``emby_base_url``, ``jellyfin_base_url``, ``plex_base_url`` — was written as
    the operator entered it. It now runs ``_scrub_credential_urls`` as well.

    The restore side already handles the result: ``_scrub_credential_urls``
    returns the WHOLE-VALUE sentinel when the value IS the credential-bearing URL
    (measured: ``http://admin:<pw>@host:9191``, with or without a trailing
    slash, and a ``?username=&password=`` query URL all return exactly
    ``REDACTED``), and ``_merge_settings_preserving_redacted`` skips exactly that
    value — pinned by
    ``test_a_restore_preserves_the_working_url_the_producer_redacted``.

    What is UNCHANGED and still documented: the name-based mask keeps the
    Dispatcharr ``username``.
    """
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _empty_db(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    stub = MagicMock()
    stub.model_dump.return_value = {
        "url": FAKE_DISPATCHARR_URL,
        "emby_base_url": FAKE_EPG_URL,
        "public_base_url": "https://ecm.example",
        "username": "dispatcharr-operator",
        "password": "<synthetic-dispatcharr-password>",
    }

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
        patch.object(backup_mod, "get_settings", return_value=stub),
    ):
        archive = backup_mod._create_backup_zip()

    with zipfile.ZipFile(io.BytesIO(_archive_bytes(archive))) as zf:
        written = json.loads(zf.read("settings.json"))

    assert written["password"] == backup_mod.REDACTED
    # The whole point: neither URL-embedded credential survives, under EITHER
    # field name, and the sentinel is the exact whole-value one the restore
    # merge recognizes.
    assert written["url"] == backup_mod.REDACTED
    assert written["emby_base_url"] == backup_mod.REDACTED
    assert FAKE_DISPATCHARR_URL_MARKER not in json.dumps(written)
    assert FAKE_EPG_URL_MARKER not in json.dumps(written)
    # A credential-free address is left alone — the restore needs it.
    assert written["public_base_url"] == "https://ecm.example"
    # Documented, and still true: the name-based mask keeps the username.
    assert written["username"] == "dispatcharr-operator"


def test_a_restore_preserves_the_working_url_the_producer_redacted(tmp_path):
    """The other half of the change above: the sentinel the producer now writes
    is exactly the one the ZIP restore merge drops, so a restore does not
    overwrite a working Dispatcharr address with a broken one."""
    existing = tmp_path / "settings.json"
    existing.write_text(json.dumps({"url": FAKE_DISPATCHARR_URL, "username": "old"}))
    zipped = json.dumps(
        {"url": backup_mod.REDACTED, "username": "dispatcharr-operator"}
    ).encode("utf-8")

    with patch.object(backup_mod, "CONFIG_FILE", existing):
        merged = json.loads(backup_mod._merge_settings_preserving_redacted(zipped))

    assert merged["url"] == FAKE_DISPATCHARR_URL
    assert merged["username"] == "dispatcharr-operator"


def test_xtream_path_credential_in_free_text_is_a_documented_residual(tmp_path):
    """Characterization, not approval, and the reason ``FAKE_ALERT_METHOD_URL``
    is NOT in :data:`FORBIDDEN_IN_PLAINTEXT_ARTIFACT`.

    ``_url_carries_credentials`` classifies a URL by RFC 3986 userinfo or a
    credential query key. An Xtream Codes credential lives in PATH SEGMENTS
    (``/live/<user>/<pass>/<id>.ts``), which that function's docstring states,
    with its reasons, it deliberately does not try to detect: no general rule
    separates it from an ordinary path, and a wrong guess costs the operator a
    URL the restore needs. Producers that EMIT stream URLs handle it structurally
    instead (``_STREAM_CREDENTIAL_FIELDS`` / ``_safe_embedded_stream``), and the
    uploaded playlists that contain them are no longer copied at all.

    What is left is this: an operator who types such a URL into a free-text
    configuration field gets it back verbatim in a plaintext artifact. That is
    what backup-overview.md means by "inspect operator-authored free text before
    sharing either plaintext format". If this test ever goes red the residual has
    been closed and the doc sentence should go with it.
    """
    archive = _build_legacy_zip(tmp_path)
    scannable = _scannable_bytes(io.BytesIO(_archive_bytes(archive)))
    assert FAKE_ALERT_METHOD_URL.encode() in scannable


class _FrozenClock(datetime.datetime):
    """Pins ``yaml_backup``'s filename timestamp so a test can plant a symlink at
    the exact path the task is about to open. Subclasses ``datetime`` rather than
    mocking it so ``TaskResult``'s ``completed_at`` still gets a real one."""

    @classmethod
    def now(cls, tz=None):
        return datetime.datetime(2026, 8, 18, 1, 2, 3, tzinfo=tz)


def test_a_symlink_at_a_local_artifact_path_is_not_followed(tmp_path):
    """``_open_private_binary`` opens O_CREAT|O_TRUNC|0600 at an operator-visible
    path. Without ``O_NOFOLLOW`` a symlink planted there is followed — measured:
    the target is truncated, overwritten and fchmod'd to 0600 — so an artifact
    write becomes an arbitrary-file write. It needs prior local write access to
    the backups directory, so this is hardening, but the flag is free.

    All three copies of the helper are exercised, because the defect was
    duplicated with the code.
    """
    victim = tmp_path / "victim.txt"
    victim.write_text("do not truncate me")

    for label, opener in (
        ("routers.backup", backup_mod._open_private_binary),
        ("dbas.artifact_crypto", artifact_crypto._open_private_binary),
    ):
        link = tmp_path / ("artifact-%s.zip" % label.replace(".", "-"))
        link.symlink_to(victim)
        with pytest.raises(OSError) as excinfo:
            opener(link)
        assert excinfo.value.errno == errno.ELOOP, (label, excinfo.value)

    assert victim.read_text() == "do not truncate me"
    assert stat.S_IMODE(victim.stat().st_mode) != 0o600


@pytest.mark.asyncio
async def test_scheduled_yaml_backup_does_not_follow_a_symlink(tmp_path):
    """The third copy, inline in ``tasks/yaml_backup.py``. It is the one with no
    ``resolve()`` + ``relative_to`` check in front of it, unlike
    ``save_backup``."""
    victim = tmp_path / "victim.txt"
    victim.write_text("do not truncate me")
    backups = tmp_path / "backups"
    backups.mkdir()

    (backups / "ecm-backup-2026-08-18_010203.yaml").symlink_to(victim)

    task = yaml_backup.YamlBackupTask()
    with (
        patch.object(yaml_backup, "BACKUPS_DIR", backups),
        patch.object(backup_mod, "build_yaml_export", return_value="settings: {}\n"),
        patch.object(yaml_backup, "datetime", _FrozenClock),
    ):
        result = await task.execute()

    assert not result.success
    assert victim.read_text() == "do not truncate me"


def test_a_passphrase_key_is_redacted_from_a_config_blob():
    """``passphrase`` was absent from :data:`_REDACT_KEYS`, so a value under that
    key in ``alert_methods.config`` or ``task_schedules.parameters`` shipped
    verbatim in both artifacts. No current writer puts one there
    (``DbasBackupTask.get_config`` deliberately omits it), so this closes the
    class against the next writer rather than a measured exposure."""
    assert "passphrase" in backup_mod._REDACT_KEYS
    scrubbed = backup_mod._redact_credentials_deep(
        {"parameters": {"passphrase": "<synthetic-artifact-passphrase-ZZZ888>"}}
    )
    assert scrubbed["parameters"]["passphrase"] == backup_mod.REDACTED


# --- instrument checks -----------------------------------------------------

def test_a_raw_archive_scan_is_blind_to_a_deflated_secret(tmp_path):
    """Why every content assertion above scans DECOMPRESSED bytes.

    A literal substring scan of the archive FILE is satisfied by an archive that
    is leaking. This measures that directly rather than asserting it, and it is
    the check the previous revision of this file was missing: its content proofs
    scanned ``zip_path.read_bytes()`` and passed while a real leak was present.
    """
    secret = "<synthetic-instrument-check-ZZZ777>" * 8
    archive = tmp_path / "probe.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("member.txt", secret)

    assert secret.encode() not in archive.read_bytes()
    assert secret.encode() in _scannable_bytes(archive)


def test_produced_artifacts_are_deflated_so_the_blindness_above_applies(tmp_path):
    """Ties the synthetic measurement above to the REAL producers: every member
    of both artifacts is DEFLATE, so a raw scan of either is a check that cannot
    fail."""
    legacy = io.BytesIO(_archive_bytes(_build_legacy_zip(tmp_path / "legacy")))
    assert _compression_methods(legacy) == {zipfile.ZIP_DEFLATED}

    dbas_root = tmp_path / "dbas"
    dbas_root.mkdir()
    _seed_credential_bearing_trees(dbas_root)
    with patch.object(artifact_harness, "_seed_journal_db", _seed_identity_journal):
        art = artifact_harness._patched_build(dbas_root, with_logos=True)
    assert _compression_methods(art.zip_path) == {zipfile.ZIP_DEFLATED}


def test_content_scan_would_fail_if_the_journal_allowlist_regressed(tmp_path):
    """Fixture-presence check for the two content proofs: the sentinels really
    are in the seeded database, so a clean artifact means the scrub happened
    rather than that the fixtures were never written."""
    journal = tmp_path / "journal.db"
    _seed_identity_journal(journal)
    raw = journal.read_bytes()
    assert FAKE_PASSWORD_HASH.encode() in raw
    assert FAKE_SESSION_TOKEN_HASH.encode() in raw
    assert FAKE_RESET_TOKEN_HASH.encode() in raw
    assert FAKE_STORAGE_SECRET.encode() in raw


def test_seeded_config_dir_really_contains_the_material_the_proofs_deny(tmp_path):
    """Same, for the two file trees: ``not any(name.startswith("tls/"))`` is only
    a check when ``tls/`` was there to be copied."""
    _seed_config_dir(tmp_path)
    assert (tmp_path / "tls" / "server.key").read_text() == FAKE_TLS_PRIVATE_KEY
    assert (
        tmp_path / "m3u_uploads" / "provider.m3u"
    ).read_text() == FAKE_PLAYLIST_STREAM_URL
