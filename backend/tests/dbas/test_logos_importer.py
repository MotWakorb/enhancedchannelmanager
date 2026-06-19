"""Tests for the logos restore importer
(enhancedchannelmanager-0i2vt.15 — Phase 2 LAST entity; ADR-008 D8).

Logos carry the largest memory/bandwidth profile in the restore AND the only
untrusted-file-upload surface, so this suite is split into two halves:

A. FUNCTIONAL
   1. 3-tier match (logoMap contract) in strict priority order:
        src:{source_id}       -> IdRemapTable LOGO namespace (primary)
        name:{lower(name)}     -> destination logo by lowercased name (fallback 1)
        file:{sanitized_file}  -> destination logo by sanitized basename (fallback 2)
      First tier that hits wins; a miss is reported (feeds RestoreReport.logo_misses).
   2. Streaming upload (D8 OOM avoidance): the importer reads + decodes + uploads
      ONE logo at a time and releases its bytes before reading the next. Proven by
      a read/upload INTERLEAVE assertion — no all-in-memory accumulation.
   3. Bulk-delete pre-step (destructive, opt-in): clears existing logos via
      ``client.bulk_delete_logos`` ONLY when ``clear_existing=True`` AND not dry-run.
   4. Opt-in off -> no-op (no deletes, no uploads). Dry-run -> no deletes, no uploads.

B. SECURITY (ATTACK CORPUS — each entry REJECTED as a per-entity FAILURE, with
   NO upload call, NO disk write, and a sanitized message that leaks no raw bytes
   or path):
   - 60MB logo over the 50MB cap.
   - ``.png`` extension but ELF / PE / shell-script magic bytes (MIME spoof).
   - archive entry paths ``../../etc/passwd``, ``/abs/path``, ``..\\win`` (traversal).
   - filename with directory components / control chars / empty / ``.`` / ``..``.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.logos``); the importer is exercised with an AsyncMock client.
NOTHING touches the real filesystem — logo bytes arrive base64-encoded in the
archive records (the D1 binary subtree is decoded into these records upstream).
"""
import base64

import pytest
from unittest.mock import AsyncMock

from dbas.importers.logos import (
    MAX_LOGO_BYTES,
    import_logos,
    resolve_logo_match,
)
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A 1x1 PNG (valid magic bytes \x89PNG\r\n\x1a\n).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_GIF = b"GIF89a" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
_ELF = b"\x7fELF" + b"\x00" * 16          # Linux executable
_PE = b"MZ\x90\x00" + b"\x00" * 16        # Windows executable
_SHELL = b"#!/bin/sh\nrm -rf /\n"          # shell script


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _logo(
    *,
    src_id=None,
    name="Logo",
    filename="logo.png",
    content=_PNG,
    content_type="image/png",
    content_b64=None,
):
    """Build one archive logo record."""
    rec = {
        "id": src_id,
        "name": name,
        "filename": filename,
        "content_type": content_type,
        "content_b64": _b64(content) if content_b64 is None else content_b64,
    }
    return rec


def _client(*, dest_logos=None, upload_side_effect=None):
    """AsyncMock Dispatcharr client with the methods the logos importer uses."""
    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=dest_logos or [])
    client.bulk_delete_logos = AsyncMock(return_value={"deleted": len(dest_logos or [])})

    counter = {"n": 9000}

    async def _upload(name, filename, content, content_type):
        counter["n"] += 1
        return {"id": counter["n"], "name": name}

    client.upload_logo_file = AsyncMock(
        side_effect=upload_side_effect or _upload
    )
    return client


def _ctx():
    return RestoreReport(is_dry_run=False), RollbackLedger(restore_id="t"), IdRemapTable()


# ===========================================================================
# A. FUNCTIONAL — 3-tier match
# ===========================================================================


def test_resolve_tier1_src_id_primary():
    """Tier 1: source id resolves through the IdRemapTable LOGO namespace."""
    remap = IdRemapTable()
    remap.add(EntityType.LOGO, 7, 700)
    logo = _logo(src_id=7, name="ESPN", filename="espn.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=[], remap=remap)
    assert dest_id == 700
    assert tier == "src"


def test_resolve_tier2_name_fallback():
    """Tier 2: lowercased name equality when src id is unmapped."""
    remap = IdRemapTable()
    dest = [{"id": 801, "name": "ESPN"}]
    logo = _logo(src_id=7, name="espn", filename="nomatch.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=dest, remap=remap)
    assert dest_id == 801
    assert tier == "name"


def test_resolve_tier3_file_fallback():
    """Tier 3: sanitized basename equality when src id + name both miss."""
    remap = IdRemapTable()
    dest = [{"id": 802, "name": "Other", "url": "http://x/cache/espn-hd.png"}]
    logo = _logo(src_id=7, name="NoNameMatch", filename="espn-hd.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=dest, remap=remap)
    assert dest_id == 802
    assert tier == "file"


def test_resolve_priority_src_beats_name_and_file():
    """Priority: src id wins even when name + file would also match."""
    remap = IdRemapTable()
    remap.add(EntityType.LOGO, 7, 700)
    dest = [
        {"id": 801, "name": "ESPN"},
        {"id": 802, "name": "Other", "url": "http://x/espn.png"},
    ]
    logo = _logo(src_id=7, name="ESPN", filename="espn.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=dest, remap=remap)
    assert dest_id == 700
    assert tier == "src"


def test_resolve_priority_name_beats_file():
    """Priority: name match wins over a file match when src id misses."""
    remap = IdRemapTable()
    dest = [
        {"id": 801, "name": "ESPN"},
        {"id": 802, "name": "Other", "url": "http://x/espn.png"},
    ]
    logo = _logo(src_id=7, name="ESPN", filename="espn.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=dest, remap=remap)
    assert dest_id == 801
    assert tier == "name"


def test_resolve_miss_returns_none():
    """A miss on all three tiers returns (None, miss)."""
    remap = IdRemapTable()
    logo = _logo(src_id=7, name="Nope", filename="nope.png")
    dest_id, tier = resolve_logo_match(logo, dest_logos=[], remap=remap)
    assert dest_id is None
    assert tier == "miss"


@pytest.mark.asyncio
async def test_matched_logo_not_reuploaded_registered_in_remap():
    """A tier-matched logo is NOT uploaded; src->dest is recorded in the remap."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 801, "name": "ESPN"}])
    logo = _logo(src_id=7, name="ESPN")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    client.upload_logo_file.assert_not_awaited()
    assert remap.resolve(EntityType.LOGO, 7) == 801
    cat = report.category(EntityType.LOGO)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL


@pytest.mark.asyncio
async def test_unmatched_logo_uploaded_ledgered_remapped():
    """A miss uploads the logo, ledgers it, and records src->dest in the remap."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=7, name="NewLogo")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    client.upload_logo_file.assert_awaited_once()
    cat = report.category(EntityType.LOGO)
    assert cat.created == 1
    assert remap.resolve(EntityType.LOGO, 7) is not None
    assert ledger.entries and ledger.entries[0].entity_type == EntityType.LOGO


@pytest.mark.asyncio
async def test_miss_increments_logo_misses_aggregate():
    """An upload-eligible miss feeds RestoreReport.logo_misses (D9 banner)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=1, name="A")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 1


# ===========================================================================
# A. FUNCTIONAL — streaming (one logo at a time, released before the next)
# ===========================================================================


@pytest.mark.asyncio
async def test_streaming_reads_one_uploads_then_reads_next():
    """D8: bytes are decoded just-in-time and released before the next logo.

    Proven by an INTERLEAVE: the importer decodes logo N, uploads it, and only
    then decodes logo N+1 — never decoding all logos up front. We observe the
    order of (decode, upload) events through a decode hook + the upload mock.
    """
    report, ledger, remap = _ctx()
    events: list[str] = []

    async def _upload(name, filename, content, content_type):
        events.append(f"upload:{name}")
        return {"id": 9000 + len(events), "name": name}

    client = _client(dest_logos=[])
    client.upload_logo_file = AsyncMock(side_effect=_upload)

    logos = [_logo(src_id=i, name=f"L{i}") for i in range(5)]

    # Hook the importer's decode seam so we can observe decode ordering.
    import dbas.importers.logos as mod
    real_decode = mod._decode_logo_bytes

    def _decode_hook(rec):
        events.append(f"decode:{rec.get('name')}")
        return real_decode(rec)

    mod._decode_logo_bytes = _decode_hook
    try:
        await import_logos(
            archive_logos=logos, client=client, selected=True,
            report=report, ledger=ledger, remap=remap,
        )
    finally:
        mod._decode_logo_bytes = real_decode

    # Strict interleave: decode L0, upload L0, decode L1, upload L1, ...
    expected = []
    for i in range(5):
        expected.append(f"decode:L{i}")
        expected.append(f"upload:L{i}")
    assert events == expected


@pytest.mark.asyncio
async def test_streaming_does_not_decode_all_before_first_upload():
    """No all-in-memory accumulation: at most ONE decoded payload is live.

    We assert that the FIRST upload happens before the SECOND decode — i.e. the
    importer never front-loads every decode (which is the OOM failure shape).
    """
    report, ledger, remap = _ctx()
    events: list[str] = []

    async def _upload(name, filename, content, content_type):
        events.append("upload")
        return {"id": 9000 + len(events), "name": name}

    client = _client(dest_logos=[])
    client.upload_logo_file = AsyncMock(side_effect=_upload)

    import dbas.importers.logos as mod
    real_decode = mod._decode_logo_bytes
    decode_count = {"n": 0}

    def _decode_hook(rec):
        decode_count["n"] += 1
        events.append("decode")
        return real_decode(rec)

    mod._decode_logo_bytes = _decode_hook
    try:
        await import_logos(
            archive_logos=[_logo(src_id=i, name=f"L{i}") for i in range(3)],
            client=client, selected=True, report=report, ledger=ledger, remap=remap,
        )
    finally:
        mod._decode_logo_bytes = real_decode

    # The first upload must precede the second decode.
    first_upload = events.index("upload")
    second_decode = [i for i, e in enumerate(events) if e == "decode"][1]
    assert first_upload < second_decode


# ===========================================================================
# A. FUNCTIONAL — bulk-delete pre-step
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_delete_invoked_when_clear_existing_enabled():
    """clear_existing=True -> bulk_delete_logos is called with destination ids."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
    await import_logos(
        archive_logos=[_logo(src_id=5, name="New")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        clear_existing=True,
    )
    client.bulk_delete_logos.assert_awaited_once()
    sent_ids = client.bulk_delete_logos.await_args.args[0]
    assert sorted(sent_ids) == [1, 2]


@pytest.mark.asyncio
async def test_bulk_delete_not_invoked_when_clear_existing_disabled():
    """Default (clear_existing=False) -> NO destructive bulk-delete."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 1, "name": "A"}])
    await import_logos(
        archive_logos=[_logo(src_id=5, name="New")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        clear_existing=False,
    )
    client.bulk_delete_logos.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_delete_not_invoked_in_dry_run_even_when_enabled():
    """Destructive pre-step NEVER runs in dry-run (grooming guard)."""
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(dest_logos=[{"id": 1, "name": "A"}])
    await import_logos(
        archive_logos=[_logo(src_id=5, name="New")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        clear_existing=True, is_dry_run=True,
    )
    client.bulk_delete_logos.assert_not_awaited()
    client.upload_logo_file.assert_not_awaited()


# ===========================================================================
# A. FUNCTIONAL — opt-in / dry-run
# ===========================================================================


@pytest.mark.asyncio
async def test_opt_in_off_is_noop():
    """selected=False -> no deletes, no uploads, all EXCLUDED_BY_OPERATOR."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 1, "name": "A"}])
    await import_logos(
        archive_logos=[_logo(src_id=5, name="New")],
        client=client, selected=False, report=report, ledger=ledger, remap=remap,
        clear_existing=True,
    )
    client.bulk_delete_logos.assert_not_awaited()
    client.upload_logo_file.assert_not_awaited()
    cat = report.category(EntityType.LOGO)
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR


@pytest.mark.asyncio
async def test_dry_run_no_uploads_reports_would_create():
    """Dry-run uploads nothing; reports would_create for a miss."""
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=5, name="New")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        is_dry_run=True,
    )
    client.upload_logo_file.assert_not_awaited()
    cat = report.category(EntityType.LOGO)
    assert cat.would_create == 1
    assert ledger.entries == []


# ===========================================================================
# B. SECURITY — ATTACK CORPUS (each REJECTED; no upload, no write, sanitized msg)
# ===========================================================================


def _assert_rejected(report, client, *, source_id=None):
    """Common assertions: per-entity FAILURE, no upload, sanitized message."""
    client.upload_logo_file.assert_not_awaited()
    cat = report.category(EntityType.LOGO)
    assert cat.created == 0
    assert cat.failed == 1
    detail = cat.failure_details[0]
    assert detail.reason == FailureReason.VALIDATION_ERROR
    # Sanitized: no raw path/byte leak.
    assert "/etc/passwd" not in detail.message
    assert "\\" not in detail.message
    assert "\x00" not in detail.message
    if source_id is not None:
        assert detail.source_export_id == source_id


@pytest.mark.asyncio
async def test_attack_oversize_60mb_rejected_before_upload():
    """A 60MB logo (over the 50MB cap) is rejected; nothing uploads."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_LOGO_BYTES + 10)
    logo = _logo(src_id=1, name="Huge", content=oversize)
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_declared_size_oversize_rejected():
    """An over-cap declared size is rejected without decoding the whole blob."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Huge", content=_PNG)
    logo["size"] = MAX_LOGO_BYTES + 1  # declared size over cap
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_mime_spoof_elf_magic_rejected():
    """.png extension but ELF magic bytes -> rejected (disguised binary)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Spoof", filename="evil.png",
                 content=_ELF, content_type="image/png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_mime_spoof_pe_magic_rejected():
    """.png extension but PE (MZ) magic bytes -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=2, name="Spoof", filename="evil.png", content=_PE)
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=2)


@pytest.mark.asyncio
async def test_attack_mime_spoof_shell_script_rejected():
    """.png extension but a shell script -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=3, name="Spoof", filename="evil.png", content=_SHELL)
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=3)


@pytest.mark.asyncio
async def test_valid_image_types_accepted():
    """PNG / JPEG / GIF / WebP magic bytes are all accepted and uploaded."""
    for raw, ct in [(_PNG, "image/png"), (_JPEG, "image/jpeg"),
                    (_GIF, "image/gif"), (_WEBP, "image/webp")]:
        report, ledger, remap = _ctx()
        client = _client(dest_logos=[])
        await import_logos(
            archive_logos=[_logo(src_id=1, name="Ok", filename="ok.img", content=raw,
                                 content_type=ct)],
            client=client, selected=True, report=report, ledger=ledger, remap=remap,
        )
        client.upload_logo_file.assert_awaited_once()
        assert report.category(EntityType.LOGO).created == 1


@pytest.mark.asyncio
async def test_attack_path_traversal_dotdot_rejected():
    """Archive entry path ../../etc/passwd -> rejected, no leak of the path."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Trav", filename="../../etc/passwd")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_path_traversal_absolute_rejected():
    """An absolute archive path /etc/cron.d/evil -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=2, name="Trav", filename="/etc/cron.d/evil")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=2)


@pytest.mark.asyncio
async def test_attack_path_traversal_windows_backslash_rejected():
    """A Windows traversal ..\\win\\system32 -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=3, name="Trav", filename="..\\win\\system32\\evil.png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=3)


@pytest.mark.asyncio
async def test_attack_filename_empty_rejected():
    """An empty filename -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Bad", filename="")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_filename_dot_and_dotdot_rejected():
    """Filenames '.' and '..' -> rejected (no usable basename)."""
    for bad in (".", ".."):
        report, ledger, remap = _ctx()
        client = _client(dest_logos=[])
        logo = _logo(src_id=1, name="Bad", filename=bad)
        await import_logos(
            archive_logos=[logo], client=client, selected=True,
            report=report, ledger=ledger, remap=remap,
        )
        _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_filename_control_chars_rejected():
    """A filename with a NUL / control char -> rejected."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Bad", filename="lo\x00go.png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_invalid_base64_rejected():
    """A non-decodable base64 payload -> rejected, no crash."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Bad", content_b64="!!!not base64!!!")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_directory_component_stripped_to_basename_then_validated():
    """A traversal-laden filename never reaches upload even if it has a basename."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Trav", filename="../../evil/logo.png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_upload_failure_recorded_as_per_entity_failure():
    """An upstream upload error is a sanitized per-entity FAILURE, not a crash."""
    report, ledger, remap = _ctx()

    async def _boom(name, filename, content, content_type):
        raise Exception("Logo upload failed: 500 - http://secret/url?token=abc")

    client = _client(dest_logos=[])
    client.upload_logo_file = AsyncMock(side_effect=_boom)
    await import_logos(
        archive_logos=[_logo(src_id=1, name="Boom")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    cat = report.category(EntityType.LOGO)
    assert cat.failed == 1
    detail = cat.failure_details[0]
    assert detail.reason == FailureReason.UPSTREAM_API_ERROR
    # The upstream URL/token must NOT leak into the operator-facing message.
    assert "token" not in detail.message
    assert "http" not in detail.message.lower()


@pytest.mark.asyncio
async def test_one_bad_logo_does_not_block_the_rest():
    """A rejected logo is isolated — sibling valid logos still upload."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logos = [
        _logo(src_id=1, name="Bad", filename="evil.png", content=_ELF),
        _logo(src_id=2, name="Good"),
    ]
    await import_logos(
        archive_logos=logos, client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    cat = report.category(EntityType.LOGO)
    assert cat.failed == 1
    assert cat.created == 1
    client.upload_logo_file.assert_awaited_once()
