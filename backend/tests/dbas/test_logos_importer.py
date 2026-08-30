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
    LogoMissDetail,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)

# Reference the symbol so linters don't flag the import as unused; the model is
# exercised via report.logo_miss_details entries (Pydantic instances).
assert LogoMissDetail is not None


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
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _bmp(extra: bytes = b"\x00" * 20) -> bytes:
    """A spec-correct BMP: 'BM' + a 4-byte little-endian file-size dword that
    equals the total byte length (LOW-1: the size dword is validated, so the
    declared size must match the real length for the magic check to pass).
    """
    body = b"BM" + b"\x00\x00\x00\x00" + extra  # placeholder size dword
    total = len(body)
    return b"BM" + total.to_bytes(4, "little") + extra


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


async def _failing_upload(name, filename, content, content_type):
    """An upload the destination refuses: the logo does NOT come back."""
    raise Exception("Logo upload failed: 500 - Server Error")


@pytest.mark.asyncio
async def test_uploaded_logo_records_no_miss():
    """THE invariant, upload path: a logo that comes back is not a loss.

    ``logo_misses`` gates the D9 red banner and the restore summary line, so a
    successful upload counted here tells the operator a logo is missing when it
    is sitting on the destination. Before bead …-xb58a the binary subtree was
    empty on this route and the bug was unreachable; archiving Dispatcharr-hosted
    bytes made this the NORMAL path for every uploaded logo.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=1, name="A")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    client.upload_logo_file.assert_awaited_once()
    assert report.category(EntityType.LOGO).created == 1
    assert report.logo_misses == 0
    assert report.logo_miss_details == []


@pytest.mark.asyncio
async def test_failed_upload_increments_logo_misses_aggregate():
    """A logo whose upload the destination refuses IS a loss (D9 banner)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=1, name="A")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.category(EntityType.LOGO).failed == 1
    assert report.logo_misses == 1


@pytest.mark.asyncio
async def test_rejected_logo_increments_logo_misses_aggregate():
    """A logo the validator refuses is equally lost, and equally reported."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=1, name="A", content=_ELF)],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    client.upload_logo_file.assert_not_awaited()
    assert report.category(EntityType.LOGO).failed == 1
    assert report.logo_misses == 1


@pytest.mark.asyncio
async def test_miss_records_per_channel_detail():
    """qhui4: a miss ALSO records a per-logo detail (id + name) alongside the
    aggregate count, so the banner can drill down into the affected logos.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="ESPN HD")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 1
    assert len(report.logo_miss_details) == 1
    detail = report.logo_miss_details[0]
    assert detail.source_export_id == 42
    assert detail.label == "ESPN HD"


@pytest.mark.asyncio
async def test_miss_detail_count_matches_aggregate_for_multiple():
    """The per-logo detail list stays consistent with the aggregate count."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[
            _logo(src_id=1, name="A"),
            _logo(src_id=2, name="B"),
            _logo(src_id=3, name="C"),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 3
    assert len(report.logo_miss_details) == 3
    assert [d.label for d in report.logo_miss_details] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_mixed_run_reports_only_the_lost_logo():
    """Two logos restore, one fails: the operator is told about exactly one."""
    report, ledger, remap = _ctx()
    uploads = {"n": 0}

    async def _third_upload_fails(name, filename, content, content_type):
        uploads["n"] += 1
        if name == "B":
            raise Exception("Logo upload failed: 500 - Server Error")
        return {"id": 9000 + uploads["n"]}

    client = _client(dest_logos=[], upload_side_effect=_third_upload_fails)
    await import_logos(
        archive_logos=[
            _logo(src_id=1, name="A"),
            _logo(src_id=2, name="B"),
            _logo(src_id=3, name="C"),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    cat = report.category(EntityType.LOGO)
    assert (cat.created, cat.failed) == (2, 1)
    assert report.logo_misses == 1
    assert [d.label for d in report.logo_miss_details] == ["B"]


@pytest.mark.asyncio
async def test_matched_logo_does_not_record_miss_detail():
    """A tier-matched logo is NOT a miss — no detail row, no aggregate bump."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 801, "name": "ESPN"}])
    await import_logos(
        archive_logos=[_logo(src_id=7, name="ESPN")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 0
    assert report.logo_miss_details == []


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
                    (_GIF, "image/gif"), (_WEBP, "image/webp"),
                    (_bmp(), "image/bmp")]:
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
async def test_valid_bmp_with_correct_size_dword_accepted():
    """A spec-correct BMP (LE size dword == byte length) is accepted (LOW-1)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=1, name="Bmp", filename="ok.bmp",
                             content=_bmp(), content_type="image/bmp")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    client.upload_logo_file.assert_awaited_once()
    assert report.category(EntityType.LOGO).created == 1


@pytest.mark.asyncio
async def test_attack_bmp_magic_only_two_bytes_rejected():
    """LOW-1: a non-image blob that merely starts with 'BM' (no valid BMP size
    dword) is REJECTED. The 2-byte 'BM' prefix alone is too weak a signal — a
    real BMP also carries a little-endian file-size dword at offset 2 that must
    match the actual byte length.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    # 'BM' followed by an arbitrary non-BMP payload whose offset-2 dword does
    # NOT equal the total length.
    spoof = b"BM" + b"this is not really a bitmap, just BM-prefixed junk"
    logo = _logo(src_id=1, name="Spoof", filename="evil.bmp",
                 content=spoof, content_type="image/bmp")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_svg_rejected():
    """INFO-3: an SVG (XML, script-executable in a browser) is NOT an allowed
    raster image type — its magic does not match the allowlist, so it is
    rejected even though it is a legitimate image format elsewhere. Locks the
    behaviour that the importer never accepts script-bearing SVG.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Svg", filename="evil.svg",
                 content=_SVG, content_type="image/svg+xml")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_path_traversal_windows_drive_letter_rejected():
    """INFO-3: a Windows drive-letter absolute path (C:\\Windows\\...) is
    rejected by the basename guard before any decode/upload. Locks the
    drive-letter branch of _safe_basename against regression.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="Drive",
                 filename="C:\\Windows\\System32\\evil.png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


@pytest.mark.asyncio
async def test_attack_path_traversal_drive_letter_relative_rejected():
    """INFO-3: a drive-relative path (C:evil.png — no separator, but a drive
    letter prefix) is also rejected. This is the case the bare embedded-
    separator rule would MISS, so the explicit drive-letter rule must catch it.
    """
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=1, name="DriveRel", filename="C:evil.png")
    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )
    _assert_rejected(report, client, source_id=1)


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


# ===========================================================================
# A. FUNCTIONAL — affected-channel context on misses (bead cm9bi)
# ===========================================================================
#
# A logo miss is only actionable if the operator knows WHICH channels were
# restored without their logo. The archive's channel records carry the
# ``logo_id`` FK; the channels importer runs BEFORE the logos importer, so on
# apply the CHANNEL remap namespace already maps source channel ids to live
# destination ids. Each miss detail therefore carries the affected channels
# (destination id where known + operator-facing name).


def _channel(*, src_id, name, logo_id):
    """Build one archive channel record (only the keys the logos importer reads)."""
    return {"id": src_id, "name": name, "logo_id": logo_id}


@pytest.mark.asyncio
async def test_miss_detail_carries_affected_channel_with_dest_id():
    """cm9bi: a miss detail lists the affected channel with its DESTINATION
    Dispatcharr id (resolved through the CHANNEL remap namespace) + name."""
    report, ledger, remap = _ctx()
    remap.add(EntityType.CHANNEL, 5, 505)
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="ESPN HD")],
        archive_channels=[_channel(src_id=5, name="ESPN", logo_id=42)],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 1
    detail = report.logo_miss_details[0]
    assert len(detail.channels) == 1
    assert detail.channels[0].channel_id == 505
    assert detail.channels[0].name == "ESPN"


@pytest.mark.asyncio
async def test_one_miss_lists_every_affected_channel():
    """cm9bi: ONE missed logo referenced by several channels stays ONE detail row
    (the aggregate still counts logos, not channels) but lists EVERY channel."""
    report, ledger, remap = _ctx()
    remap.add(EntityType.CHANNEL, 5, 505)
    remap.add(EntityType.CHANNEL, 6, 606)
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="League Logo")],
        archive_channels=[
            _channel(src_id=5, name="ESPN", logo_id=42),
            _channel(src_id=6, name="ESPN 2", logo_id=42),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 1
    assert len(report.logo_miss_details) == 1
    channels = report.logo_miss_details[0].channels
    assert [(c.channel_id, c.name) for c in channels] == [(505, "ESPN"), (606, "ESPN 2")]


@pytest.mark.asyncio
async def test_miss_detail_channel_without_remap_resolution_has_none_id():
    """cm9bi: an affected channel whose destination id is unknown (not in the
    remap — e.g. its create failed) still lists NAME, with channel_id None."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="ESPN HD")],
        archive_channels=[_channel(src_id=5, name="ESPN", logo_id=42)],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    detail = report.logo_miss_details[0]
    assert len(detail.channels) == 1
    assert detail.channels[0].channel_id is None
    assert detail.channels[0].name == "ESPN"


@pytest.mark.asyncio
async def test_dry_run_miss_detail_never_emits_provisional_channel_ids():
    """cm9bi: on dry-run the CHANNEL remap holds PROVISIONAL ids (src used as
    dest for would-creates) that must never render as real Dispatcharr links —
    the channel is listed by name with channel_id None."""
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    remap.add(EntityType.CHANNEL, 5, 5)  # provisional dry-run mapping
    client = _client(dest_logos=[])
    await import_logos(
        # A logo the validator refuses is a loss the preview must predict; a
        # dry run performs no upload, so this is the reachable failure there.
        archive_logos=[_logo(src_id=42, name="ESPN HD", content=_ELF)],
        archive_channels=[_channel(src_id=5, name="ESPN", logo_id=42)],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        is_dry_run=True,
    )
    detail = report.logo_miss_details[0]
    assert len(detail.channels) == 1
    assert detail.channels[0].channel_id is None
    assert detail.channels[0].name == "ESPN"


@pytest.mark.asyncio
async def test_miss_detail_channels_empty_without_archive_channels():
    """cm9bi back-compat: callers that pass no archive channels still get a
    detail row — with an empty channels list, never a crash."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="ESPN HD")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    assert report.logo_misses == 1
    assert report.logo_miss_details[0].channels == []


@pytest.mark.asyncio
async def test_miss_detail_ignores_channels_referencing_other_logos():
    """cm9bi: only channels whose ``logo_id`` references the MISSED logo are
    listed — unrelated channels (other logos, no logo) never appear."""
    report, ledger, remap = _ctx()
    remap.add(EntityType.CHANNEL, 5, 505)
    remap.add(EntityType.CHANNEL, 6, 606)
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=42, name="ESPN HD")],
        archive_channels=[
            _channel(src_id=5, name="ESPN", logo_id=42),
            _channel(src_id=6, name="CNN", logo_id=99),
            {"id": 7, "name": "NoLogo"},
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )
    channels = report.logo_miss_details[0].channels
    assert [(c.channel_id, c.name) for c in channels] == [(505, "ESPN")]


# ===========================================================================
# D. CONTENT PROVIDER — lazy per-logo hydration for the sync path
#    (bead enhancedchannelmanager-7ipq2.1; ADR-013 S9 exit path / D8 streaming).
#
#    Cross-instance sync assembles LOGO records METADATA-ONLY (no content_b64 in
#    the plan — holding every logo's base64 at once would defeat D8). The
#    importer accepts an optional async ``content_provider`` that is called
#    lazily, ONE MISSED LOGO AT A TIME, to fetch that logo's base64 payload just
#    before validation+upload. The restore path passes no provider and is
#    byte-for-byte unchanged.
# ===========================================================================


def _metadata_logo(*, src_id=None, name="Logo", filename="logo.png", size=None):
    """A metadata-only record (NO content_b64) — the sync plan shape."""
    rec = {"id": src_id, "name": name, "filename": filename}
    if size is not None:
        rec["size"] = size
    return rec


@pytest.mark.asyncio
async def test_provider_hydrates_missed_logo_and_uploads():
    """A metadata-only miss is hydrated via the provider and uploaded."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])

    async def _provider(record):
        return _b64(_PNG)

    await import_logos(
        archive_logos=[_metadata_logo(src_id=7, name="ESPN", filename="espn.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    client.upload_logo_file.assert_awaited_once()
    # The upload received the DECODED bytes the provider supplied.
    upload_args = client.upload_logo_file.await_args.args
    assert upload_args[2] == _PNG
    assert report.category(EntityType.LOGO).created == 1


@pytest.mark.asyncio
async def test_provider_interleaves_one_logo_at_a_time():
    """D8: fetch L0 -> upload L0 -> fetch L1 -> upload L1 — never fetch-all-
    then-upload. The provider and the upload share one event log; a strict
    alternation proves at most ONE hydrated payload is live at a time."""
    report, ledger, remap = _ctx()
    events = []

    async def _provider(record):
        events.append(("fetch", record["name"]))
        return _b64(_PNG)

    async def _upload(name, filename, content, content_type):
        events.append(("upload", name))
        return {"id": 9100 + len(events), "name": name}

    client = _client(dest_logos=[], upload_side_effect=_upload)
    await import_logos(
        archive_logos=[
            _metadata_logo(src_id=1, name="L0", filename="l0.png"),
            _metadata_logo(src_id=2, name="L1", filename="l1.png"),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    assert events == [
        ("fetch", "L0"), ("upload", "L0"), ("fetch", "L1"), ("upload", "L1"),
    ]


@pytest.mark.asyncio
async def test_provider_never_called_for_matched_logo():
    """A tier-matched logo is never hydrated — bytes are fetched ONLY for
    misses (cheaper than restore, which carries bytes for everything)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[{"id": 801, "name": "ESPN"}])
    calls = []

    async def _provider(record):
        calls.append(record["name"])
        return _b64(_PNG)

    await import_logos(
        archive_logos=[_metadata_logo(src_id=7, name="ESPN", filename="espn.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    assert calls == []
    client.upload_logo_file.assert_not_awaited()
    assert report.category(EntityType.LOGO).skipped == 1


@pytest.mark.asyncio
async def test_provider_returning_none_is_per_logo_failure():
    """A provider miss (None) fails THAT logo (VALIDATION_ERROR, path-free
    message) and the loop continues — the next logo still uploads."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])

    async def _provider(record):
        if record["name"] == "Broken":
            return None
        return _b64(_PNG)

    await import_logos(
        archive_logos=[
            _metadata_logo(src_id=1, name="Broken", filename="broken.png"),
            _metadata_logo(src_id=2, name="Good", filename="good.png"),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    cat = report.category(EntityType.LOGO)
    assert cat.failed == 1
    assert cat.created == 1
    detail = cat.failure_details[0]
    assert detail.reason == FailureReason.VALIDATION_ERROR
    assert detail.label == "Broken"
    assert "read" in detail.message
    # Path hygiene: no path/url marker in the message.
    assert "/" not in detail.message and "\\" not in detail.message


@pytest.mark.asyncio
async def test_provider_exception_is_per_logo_failure_not_a_crash():
    """A provider exception is contained per-logo (never crashes the cycle)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])

    async def _provider(record):
        raise OSError("disk read failed under /secret/path/logo.png")

    await import_logos(
        archive_logos=[_metadata_logo(src_id=1, name="Boom", filename="boom.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    cat = report.category(EntityType.LOGO)
    assert cat.failed == 1
    client.upload_logo_file.assert_not_awaited()
    # The raw OSError text (which can carry a path) must NOT leak.
    assert "/secret/path" not in cat.failure_details[0].message


@pytest.mark.asyncio
async def test_provider_not_called_when_declared_size_over_cap():
    """The declared-size pre-check runs BEFORE hydration — we never read a file
    we already know is over the 50MB cap."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    calls = []

    async def _provider(record):
        calls.append(record["name"])
        return _b64(_PNG)

    await import_logos(
        archive_logos=[
            _metadata_logo(src_id=1, name="TooBig", filename="big.png",
                           size=MAX_LOGO_BYTES + 1),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    assert calls == []
    cat = report.category(EntityType.LOGO)
    assert cat.failed == 1
    assert "50MB" in cat.failure_details[0].message


@pytest.mark.asyncio
async def test_provider_not_called_for_unsafe_filename():
    """The basename/traversal guard runs BEFORE hydration."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    calls = []

    async def _provider(record):
        calls.append(record["name"])
        return _b64(_PNG)

    await import_logos(
        archive_logos=[
            _metadata_logo(src_id=1, name="Evil", filename="../../etc/passwd"),
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_provider,
    )
    assert calls == []
    assert report.category(EntityType.LOGO).failed == 1
    client.upload_logo_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_hydration_never_mutates_the_plan_record():
    """D8 no-accumulation pin: the caller's record dicts stay METADATA-ONLY
    after the run — hydrated payloads live on per-iteration copies only."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    records = [
        _metadata_logo(src_id=1, name="L0", filename="l0.png"),
        _metadata_logo(src_id=2, name="L1", filename="l1.png"),
    ]

    async def _provider(record):
        return _b64(_PNG)

    await import_logos(
        archive_logos=records, client=client, selected=True,
        report=report, ledger=ledger, remap=remap, content_provider=_provider,
    )
    assert client.upload_logo_file.await_count == 2
    for rec in records:
        assert "content_b64" not in rec


@pytest.mark.asyncio
async def test_provider_dry_run_validates_but_never_uploads():
    """Dry-run with a provider still hydrates+validates (the preview's counts
    match what apply would do) but uploads nothing."""
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(dest_logos=[])

    async def _provider(record):
        return _b64(_PNG)

    await import_logos(
        archive_logos=[_metadata_logo(src_id=1, name="L0", filename="l0.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        is_dry_run=True, content_provider=_provider,
    )
    client.upload_logo_file.assert_not_awaited()
    cat = report.category(EntityType.LOGO)
    assert cat.would_create == 1
    # A logo the preview says WOULD be created is not a logo the operator has
    # lost, so it must not reach the D9 banner (see record_logo_miss).
    assert report.logo_misses == 0


# ===========================================================================
# Content-type derivation from validated magic bytes (bead 7ipq2.2 —
# live-validation finding): sync-gathered logo records are METADATA-ONLY and
# carry no declared content_type, so the upload fell back to
# application/octet-stream — which a real Dispatcharr 0.28.2 upload endpoint
# REJECTS ("Unsupported file type") even though the bytes were a valid PNG.
# The importer already magic-validates the bytes; the upload content-type is
# derived from that same validated magic when no usable declared type exists.
# ===========================================================================

async def test_missing_declared_content_type_derives_from_magic_bytes():
    """A record with NO content_type key (the sync live-gather shape) uploads
    with the magic-derived image type, never application/octet-stream."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=7, name="SyncLogo")
    del logo["content_type"]

    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )

    client.upload_logo_file.assert_awaited_once()
    sent_content_type = client.upload_logo_file.await_args.args[3]
    assert sent_content_type == "image/png"


async def test_declared_image_content_type_still_wins_over_magic():
    """A usable declared image/* content-type is passed through unchanged
    (advisory declared type honored; magic only fills the gap)."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    logo = _logo(src_id=8, name="DeclaredLogo", content_type="image/x-png")

    await import_logos(
        archive_logos=[logo], client=client, selected=True,
        report=report, ledger=ledger, remap=remap,
    )

    sent_content_type = client.upload_logo_file.await_args.args[3]
    assert sent_content_type == "image/x-png"


# ===========================================================================
# C. DRY-RUN / APPLY PARITY on the URL re-create path
#    (bead enhancedchannelmanager-dgnms)
#
# The URL re-create branch used to be gated on ``not is_dry_run``, so a preview
# never simulated it. Every byte-less record fell through to _validate_logo(),
# which needs a ``filename`` a URL-only record does not carry, and the drill's
# preview reported 11 of 11 logos as ``validation_error: unsafe or empty logo
# filename`` for an artifact whose apply then restored 10 of them. Worse, the
# ONE genuinely unrestorable logo was indistinguishable from the 10 that were
# fine: identical reason, identical message.
# ===========================================================================


def _url_logo(*, src_id, name, url):
    """A URL-only archive record: the shape categories/logos.yaml produces."""
    return {"id": src_id, "name": name, "url": url}


async def test_dry_run_counts_a_remote_url_logo_as_a_would_create():
    """The preview simulates the same decision the apply makes."""
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(dest_logos=[])

    await import_logos(
        archive_logos=[_url_logo(src_id=55, name="FOX", url="https://cdn.example/fox.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        is_dry_run=True,
    )

    cat = report.category(EntityType.LOGO)
    assert cat.would_create == 1
    assert cat.failed == 0
    assert cat.failure_details == []
    # Nothing was written, and no fabricated remap id was registered.
    client.create_logo.assert_not_awaited()
    client.upload_logo_file.assert_not_awaited()
    assert ledger.entries == []
    assert remap.resolve(EntityType.LOGO, 55) is None


async def test_dry_run_still_reports_a_genuinely_unrestorable_logo():
    """A record with neither bytes nor a remote URL is still a failure.

    This is the signal the operator actually needs, and the old preview buried
    it in ten invented failures that read identically.
    """
    report = RestoreReport(is_dry_run=True)
    ledger, remap = RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(dest_logos=[])

    await import_logos(
        archive_logos=[_url_logo(src_id=13, name="Drill Uploaded Logo",
                                 url="/data/logos/drill-logo.png")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        is_dry_run=True,
    )

    cat = report.category(EntityType.LOGO)
    assert cat.would_create == 0
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.VALIDATION_ERROR
    assert cat.failure_details[0].label == "Drill Uploaded Logo"


async def test_dry_run_logo_counts_match_the_apply_for_a_mixed_set():
    """Preview and apply agree, category by category, on the drill's own mix.

    The set is deliberately the drill's shape: remote-CDN logos that restore by
    URL, a byte-bearing logo (what the backup now archives for a
    Dispatcharr-hosted one), a logo the destination already has, and one
    genuinely unrestorable record.
    """
    archive = [
        _url_logo(src_id=51, name="CNN", url="https://cdn.example/cnn.png"),
        _url_logo(src_id=52, name="FOX", url="https://cdn.example/fox.png"),
        _logo(src_id=53, name="Uploaded", filename="drill-logo.png"),
        _url_logo(src_id=54, name="Already There", url="https://cdn.example/there.png"),
        _url_logo(src_id=55, name="Unrestorable", url="/data/logos/gone.png"),
    ]
    dest = [{"id": 900, "name": "Already There", "url": "https://cdn.example/there.png"}]

    dry_report = RestoreReport(is_dry_run=True)
    await import_logos(
        archive_logos=archive, client=_client(dest_logos=dest), selected=True,
        report=dry_report, ledger=RollbackLedger(restore_id="d"),
        remap=IdRemapTable(), is_dry_run=True,
    )

    apply_client = _client(dest_logos=dest)
    apply_client.create_logo = AsyncMock(return_value={"id": 970})
    apply_report = RestoreReport(is_dry_run=False)
    await import_logos(
        archive_logos=archive, client=apply_client, selected=True,
        report=apply_report, ledger=RollbackLedger(restore_id="a"),
        remap=IdRemapTable(), is_dry_run=False,
    )

    dry = dry_report.category(EntityType.LOGO)
    applied = apply_report.category(EntityType.LOGO)
    assert (dry.would_create, dry.would_skip, dry.failed) == (3, 1, 1)
    assert (applied.created, applied.skipped, applied.failed) == (3, 1, 1)
    assert dry.would_create == applied.created
    assert dry.would_skip == applied.skipped
    assert dry.failed == applied.failed
    # The failure the operator must act on is the SAME one in both runs.
    assert [f.label for f in dry.failure_details] == [f.label for f in applied.failure_details]


async def test_dry_run_does_not_count_a_url_recreate_as_a_logo_miss():
    """A logo that comes back by URL is not a miss, in either run.

    logo_misses drives the D9 red banner, so counting a restorable logo there
    would put a "logos were lost" warning on a restore that lost none.
    """
    archive = [_url_logo(src_id=51, name="CNN", url="https://cdn.example/cnn.png")]

    dry_report = RestoreReport(is_dry_run=True)
    await import_logos(
        archive_logos=archive, client=_client(dest_logos=[]), selected=True,
        report=dry_report, ledger=RollbackLedger(restore_id="d"),
        remap=IdRemapTable(), is_dry_run=True,
    )

    apply_client = _client(dest_logos=[])
    apply_client.create_logo = AsyncMock(return_value={"id": 970})
    apply_report = RestoreReport(is_dry_run=False)
    await import_logos(
        archive_logos=archive, client=apply_client, selected=True,
        report=apply_report, ledger=RollbackLedger(restore_id="a"),
        remap=IdRemapTable(), is_dry_run=False,
    )

    assert dry_report.logo_misses == 0
    assert apply_report.logo_misses == 0


@pytest.mark.asyncio
async def test_restored_logo_never_reaches_the_operator_loss_summary():
    """End of the chain: a successful upload produces an EMPTY loss suffix.

    ``logo_misses`` is not an internal number. ``DbasRestoreTask`` turns it into
    "N logo(s) could not be reinstated" on the task-history row, which is the
    ONLY surface an operator who never opens the restore modal sees, and the
    D9 red banner keys off the same field. Asserting the count alone would not
    have shown that the drill's headline defect appeared UNFIXED to the operator.
    """
    from tasks.dbas_restore import DbasRestoreTask

    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])
    await import_logos(
        archive_logos=[_logo(src_id=13, name="Drill Uploaded Logo")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )

    assert report.category(EntityType.LOGO).created == 1
    assert DbasRestoreTask._credential_reentry_suffix(report) == ""


@pytest.mark.asyncio
async def test_lost_logo_does_reach_the_operator_loss_summary():
    """The converse: a logo that did NOT come back is named in that same line."""
    from tasks.dbas_restore import DbasRestoreTask

    report, ledger, remap = _ctx()
    client = _client(dest_logos=[], upload_side_effect=_failing_upload)
    await import_logos(
        archive_logos=[_logo(src_id=13, name="Drill Uploaded Logo")],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
    )

    assert "1 logo(s) could not be reinstated" in DbasRestoreTask._credential_reentry_suffix(report)


# ===========================================================================
# E. A LAZY PROVIDER DOES NOT MAKE A FILE-LESS RECORD HYDRATABLE
#    (bead enhancedchannelmanager-sgrez).
#
#    ``hydratable_bytes`` gates the re-create-BY-URL branch: a record it calls
#    hydratable goes to the upload path instead. The sync path wires a provider
#    for EVERY record, so answering on the provider alone locked every
#    REMOTE-URL logo out of the only branch that could restore it — and sent it
#    to an upload path whose only possible outcome is a VALIDATION_ERROR,
#    because that path cannot run without a ``filename``.
# ===========================================================================


async def _never_called_provider(record):  # pragma: no cover - must not run
    raise AssertionError("a file-less record must never be hydrated")


def test_a_byteless_record_with_no_filename_is_not_hydratable():
    """The invariant, stated on the predicate itself."""
    from dbas.importers.logos import hydratable_bytes

    remote = {"id": 1, "name": "Remote"}
    assert hydratable_bytes(remote, _never_called_provider) is False
    # A record that DOES name a file still is — the sync path's local/hosted
    # records both carry one, so bead …-cfxml's slice is untouched.
    assert hydratable_bytes(
        {"id": 1, "name": "Hosted", "filename": "x.png"}, _never_called_provider
    ) is True
    # Bytes already in hand always win, filename or not.
    assert hydratable_bytes({"content_b64": "AA=="}, None) is True
    # No provider and no bytes: unchanged archive-restore behaviour.
    assert hydratable_bytes(remote, None) is False


@pytest.mark.asyncio
async def test_a_remote_url_record_is_recreated_by_url_even_with_a_provider_wired():
    """End to end through the importer: the URL branch, not the upload branch,
    and the provider is never asked for bytes that do not exist."""
    report, ledger, remap = _ctx()
    client = _client(dest_logos=[])

    await import_logos(
        archive_logos=[
            {"id": 42, "name": "Remote Logo", "url": "http://cdn.example/x.png"}
        ],
        client=client, selected=True, report=report, ledger=ledger, remap=remap,
        content_provider=_never_called_provider,
    )

    client.create_logo.assert_awaited_once_with(
        {"name": "Remote Logo", "url": "http://cdn.example/x.png"}
    )
    client.upload_logo_file.assert_not_awaited()
    assert report.category(EntityType.LOGO).created == 1
    assert report.logo_misses == 0
