"""The logos restore importer — streaming upload + 3-tier match + hardened validation.

Bead ``enhancedchannelmanager-0i2vt.15``. Logos are the LAST entity in the hard
Phase-2 ordering (``M3U → EPG → Channels → Logos``; ADR-012) and carry both the
largest memory/bandwidth profile AND the only untrusted-file-upload surface in
the restore. This module restores the LOGO entity category from a Dispatcharr
export archive.

It mirrors the established Phase-2 importer pattern (``importers/channels.py`` /
``importers/m3u_accounts.py``): opt-in, consumes the shared restore contracts
(:class:`~dbas.restore_contracts.IdRemapTable`,
:class:`~dbas.restore_contracts.RollbackLedger`,
:class:`~dbas.restore_contracts.RestoreReport`, the Skip/Failure taxonomy),
per-entity results, dry-run support, and credential-clean logging.

----------------------------------------------------------------------------
STREAMING UPLOAD (ADR-008 D8 — the OOM-avoidance headline requirement)
----------------------------------------------------------------------------

A 50MB-per-logo cap × 2000 logos is a 100GB policy ceiling. The container is a
single process; it CANNOT hold that in memory. So this importer processes ONE
logo at a time: read its base64 string → decode to bytes → validate → upload →
let the bytes go out of scope BEFORE the next logo is read. There is never more
than a single decoded logo payload live at once — NOT a CumulativeMemoryTracker
port (rejected by D8). The decode is isolated in :func:`_decode_logo_bytes` so a
test can prove the read/upload interleave (one decode, one upload, repeat).

----------------------------------------------------------------------------
3-TIER MATCH (the logoMap contract — pure resolver :func:`resolve_logo_match`)
----------------------------------------------------------------------------

An archived logo is reconciled against the destination instance's logos by THREE
strategies, in strict priority order — the first that hits wins:

* **(1) src** — ``src:{source_id}``: the archived logo's source id resolves
  through the :class:`IdRemapTable` ``LOGO`` namespace. Strongest signal.
* **(2) name** — ``name:{lower(original_name)}``: case-insensitive name equality
  against a destination logo. Tie-break = lowest destination id (deterministic).
* **(3) file** — ``file:{sanitized_basename}``: the sanitized filename basename
  equals a destination logo's basename (derived from its ``url``/``filename``).
  Tie-break = lowest destination id.

A match means the logo already exists on the destination: it is NOT re-uploaded
(``ALREADY_EXISTS_IDENTICAL``), its ``source->dest`` id is recorded in the remap
so channel ``logo_id`` references can resolve, and nothing is ledgered (nothing
to roll back). A MISS uploads the logo (streaming), ledgers it and remaps it, and
increments the INTERNAL :attr:`LogoImportResult.misses` counter.

----------------------------------------------------------------------------
WHAT COUNTS AS A LOGO MISS (the operator-facing loss report)
----------------------------------------------------------------------------

``LogoImportResult.misses`` above and :attr:`RestoreReport.logo_misses` are NOT
the same counter and must never be wired to each other:

* ``LogoImportResult.misses`` answers "did the destination already have this
  logo?". Internal bookkeeping. Never rendered to an operator.
* :attr:`RestoreReport.logo_misses` answers "did the operator LOSE this logo?".
  It gates the D9 red banner and the restore summary line.

The canonical definition, the rule for all three producers, and the history that
produced it live in ONE place: :meth:`RestoreReport.record_logo_miss`. Read it
before touching any miss-recording site. The short form for this module: record
a miss ONLY where a logo failed to come back (unreadable bytes, failed
validation, failed upload, failed URL re-create), NEVER on a path that restored
it, and record it through ``report.record_logo_miss()`` rather than by
incrementing the field.

----------------------------------------------------------------------------
SECURITY VALIDATION (untrusted file upload — the security-review surface)
----------------------------------------------------------------------------

Every logo passes FOUR validations BEFORE any upload; a failure is a per-entity
``VALIDATION_ERROR`` (sanitized message, no raw bytes/path), never a crash, and
never writes/uploads anything:

1. **Filename basename** — strip every directory component (POSIX ``/`` AND
   Windows ``\\``); reject empty / ``.`` / ``..`` / control-char names. The
   stripped basename is also a path-traversal guard: a ``../`` or absolute path
   collapses to nothing usable and is rejected (see :func:`_safe_basename`).
2. **Declared-size cap** — if the archive declares a ``size`` over 50MB, reject
   BEFORE decoding (don't buffer a blob we already know is over cap).
3. **Decoded-size cap** — after decode, the actual byte length is hard-capped at
   50MB (the declared size is advisory; the decoded length is authoritative).
4. **MIME magic-byte validation** — the decoded bytes' leading magic number must
   match a known image type (PNG/JPEG/GIF/WebP/BMP). A ``.png`` file whose bytes
   are ELF/PE/script is a disguised binary and is REJECTED — the extension and
   the declared content-type are NEVER trusted. Magic checks are byte
   comparisons / membership tests (no regex on dynamic input — Semgrep clean).
   BMP is validated more strictly than a 2-byte prefix: it must carry the "BM"
   signature AND a little-endian file-size dword (offset 2) that equals the
   decoded byte length (LOW-1 hardening — a bare "BM…" blob is rejected). SVG is
   NOT an allowed type: it is XML/script-bearing, has no raster magic number, and
   does not match the allowlist, so it is rejected.

----------------------------------------------------------------------------
BULK-DELETE PRE-STEP (destructive — opt-in, never silent, never in dry-run)
----------------------------------------------------------------------------

When ``clear_existing=True`` AND not a dry-run, the importer clears the
destination's existing logos via ``client.bulk_delete_logos`` before uploading
the archive's logos. This is destructive, so it is OFF by default and never runs
in dry-run (grooming guard: a mid-delete failure must not leave Dispatcharr with
no logos and no replacements — so the delete is best-effort-logged and the upload
proceeds regardless; the operator opts into this explicitly).

----------------------------------------------------------------------------
CREDENTIAL / PATH HYGIENE (the .8 / .13 clear-text-logging lesson)
----------------------------------------------------------------------------

Logo bytes, raw filenames/paths, and upstream SDK exception bodies (which can
echo a logo url/path) NEVER reach a log line, a report label/message, or a
ledger entry. We log/report ONLY safe fields: the logo's display name, the match
tier, counts, and a failure-reason enum. :func:`_sanitize_failure` scrubs any
upstream message that smells like a URL/path before it lands in the report.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from typing import Awaitable, Callable, Optional

from pydantic import BaseModel, Field

from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    LogoMissChannel,
    RestoreReport,
    RollbackLedger,
    SkipDetail,
    SkipReason,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Hard per-logo size cap: 50MB (ADR-008 D8 / STRIDE). The decoded byte length is
# authoritative; a declared size over this is rejected before decode.
MAX_LOGO_BYTES = 50 * 1024 * 1024

# Allowed image magic-byte signatures. Each entry is a (prefix_bytes, offset)
# pair the decoded payload must start with at the given offset. These are plain
# byte comparisons — NO regex on dynamic input (Semgrep-clean magic check).
# WebP and (some) AVIF carry their tag at offset 8 inside a RIFF/ISO-BMFF box.
#
# BMP is NOT in this table: a 2-byte "BM" prefix is too weakly discriminating
# (LOW-1 from the .15 security review — any non-image "BM…" blob would pass). It
# is validated separately in :func:`_bmp_ok`, which also checks the BMP header's
# little-endian file-size dword, so a real BMP (size dword == byte length) is
# accepted while a "BM"-prefixed junk blob is rejected.
_MAGIC_PREFIXES: tuple[tuple[bytes, int], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0),   # PNG
    (b"\xff\xd8\xff", 0),         # JPEG (JFIF/EXIF)
    (b"GIF87a", 0),              # GIF87a
    (b"GIF89a", 0),              # GIF89a
    (b"WEBP", 8),               # WebP (RIFF....WEBP)
)

# The BMP header is: 2-byte "BM" signature + a 4-byte little-endian file-size
# dword at offset 2 that equals the TOTAL file length. A genuine BMP writer fills
# this in; a non-image blob that merely starts with "BM" almost never has a size
# dword that matches its own length, so this is a far stronger discriminator than
# the 2-byte prefix alone (LOW-1). 14 bytes is the smallest valid BMP file
# header (BITMAPFILEHEADER), so anything shorter cannot be a BMP.
_BMP_SIGNATURE = b"BM"
_BMP_MIN_HEADER_LEN = 14
_BMP_SIZE_DWORD_OFFSET = 2

# The smallest leading slice we must inspect to decide every magic above. Kept
# small so we never read more than necessary to classify.
_MAGIC_INSPECT_LEN = 16

# Control bytes (NUL + C0 controls + DEL) are never valid in a logo filename.
_CONTROL_BYTES = frozenset(range(0x00, 0x20)) | {0x7F}

# Markers that, if present in an upstream message, mean it may carry a url/path —
# we replace the whole message rather than risk leaking it into the report.
_PATH_LEAK_MARKERS = ("http", "://", "/", "\\", "url", "token", "passw")


class LogoImportResult(BaseModel):
    """The logos importer's return value.

    Carries safe aggregate counters for the orchestrator/UX. No per-logo bytes,
    paths, or upstream error bodies — only counts and the logo-miss aggregate
    (which is also reflected into :attr:`RestoreReport.logo_misses`).
    """

    uploaded: int = Field(default=0)
    matched: int = Field(default=0)
    misses: int = Field(default=0)
    rejected: int = Field(default=0)
    deleted_existing: int = Field(default=0)
    # SOURCE (archive) ids of the logos a DRY RUN determined it would create —
    # by upload or by URL re-create (bead …-dgnms). Empty on an apply, where a
    # created logo has a real destination id in the LOGO remap instead.
    #
    # A source id, never a fabricated destination id: this importer deliberately
    # registers NO dry-run remap entry for a would-create (the id an apply mints
    # is not knowable, and inventing one would corrupt every FK resolved through
    # it). But ``channel_reattach.reattach_channel_logos`` still needs to know
    # the logo WILL exist, or its preview split reads 0 for a restore that
    # reattaches N — drill run 4 measured preview 0 against apply 11 on a fresh
    # target, because a fresh target matches nothing and every logo is a
    # would-create. This set is the missing fact, carried without faking an id.
    would_create_source_ids: set[int] = Field(default_factory=set)


def _logo_label(archive_logo: dict) -> str:
    """Operator-facing identifier for a logo — its display name, never a path."""
    name = archive_logo.get("name")
    return str(name) if name else "<unknown>"


def _norm_name(value) -> str | None:
    """Case-insensitive, trimmed key for a logo name; None when absent/blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def _safe_basename(raw) -> str | None:
    """Return a validated filename BASENAME, or ``None`` if the name is unsafe.

    The single chokepoint for the filename-basename + path-traversal (Zip-Slip)
    guards. This is intentionally STRICT — it REJECTS (does not silently strip) a
    name that carries any directory structure, so a crafted archive entry never
    has a chance to resolve outside the upload target:

    * rejects any control byte (NUL-injection / log-forging);
    * rejects a leading ``/`` (absolute POSIX path) or a Windows drive letter
      (``C:``) / leading backslash (absolute / UNC path);
    * rejects ANY embedded path separator (``/`` or ``\\``) — a legitimate logo
      basename has none; a name with one is a path, not a basename, and we refuse
      it rather than strip-and-trust;
    * rejects a ``..`` traversal component anywhere;
    * rejects empty / ``.`` / ``..`` (no usable basename).

    A name that survives is a bare, separator-free basename safe to hand to the
    multipart upload. Returns it on success, or ``None`` (caller ->
    VALIDATION_ERROR — never logs the raw name).
    """
    if not isinstance(raw, str) or not raw:
        return None
    # Reject control characters outright (NUL-injection / log-forging).
    if any(ord(ch) in _CONTROL_BYTES for ch in raw):
        return None
    # Reject a Windows drive-letter prefix (e.g. ``C:\\...`` or ``C:rel``).
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        return None
    # Reject ANY embedded separator — a valid basename has none. This catches a
    # leading ``/`` (absolute), a leading ``\\`` (UNC/absolute), and every
    # ``../`` / ``..\\`` traversal in one rule.
    if "/" in raw or "\\" in raw:
        return None
    name = raw.strip()
    if not name or name in (".", ".."):
        return None
    # Belt-and-braces: the value must equal its own basename on this platform.
    if os.path.basename(name) != name:
        return None
    return name


def _basename_key(value) -> str | None:
    """Lowercased basename of a destination logo's url/filename, for tier-3 match."""
    if not isinstance(value, str) or not value:
        return None
    last = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    last = last.strip().lower()
    return last or None


def resolve_logo_match(
    archive_logo: dict,
    *,
    dest_logos: list[dict],
    remap: IdRemapTable,
) -> tuple[int | None, str]:
    """Resolve an archived logo to a DESTINATION logo id via the 3-tier match.

    Strategies, in strict priority order — the first that hits wins:

    (1) ``src``  — source id through the :class:`IdRemapTable` ``LOGO`` namespace.
    (2) ``name`` — case-insensitive name equality; tie-break = lowest dest id.
    (3) ``file`` — sanitized basename equals a destination logo's basename;
        tie-break = lowest dest id.

    Args:
        archive_logo: The archived logo record (``id`` / ``name`` / ``filename``).
        dest_logos: The destination instance's logos (``id`` / ``name`` / ``url``).
        remap: The shared :class:`IdRemapTable` (read-only here).

    Returns:
        ``(destination_id, tier)`` where ``tier`` is one of ``"src"`` / ``"name"``
        / ``"file"`` on a hit, or ``(None, "miss")`` when no strategy resolved it.
    """
    # (1) src — explicit source->dest mapping.
    source_id = archive_logo.get("id")
    if source_id is not None:
        mapped = remap.resolve(EntityType.LOGO, int(source_id))
        if mapped is not None:
            return mapped, "src"

    # (2) name — case-insensitive, trimmed; lowest dest id wins.
    name_key = _norm_name(archive_logo.get("name"))
    if name_key is not None:
        matches = [
            d.get("id")
            for d in dest_logos
            if _norm_name(d.get("name")) == name_key and d.get("id") is not None
        ]
        if matches:
            return min(int(m) for m in matches), "name"

    # (3) file — sanitized basename equality; lowest dest id wins.
    file_key = _basename_key(archive_logo.get("filename"))
    if file_key is not None:
        matches = []
        for d in dest_logos:
            if d.get("id") is None:
                continue
            dest_basename = _basename_key(d.get("url")) or _basename_key(d.get("filename"))
            if dest_basename == file_key:
                matches.append(d.get("id"))
        if matches:
            return min(int(m) for m in matches), "file"

    return None, "miss"


def _decode_logo_bytes(archive_logo: dict) -> bytes:
    """Decode ONE logo's base64 payload to bytes (the streaming-read seam).

    Isolated so the streaming pattern is observable in tests (one decode, one
    upload, repeat — never decode-all-then-upload). Raises ``ValueError`` on a
    non-decodable payload; the caller turns that into a VALIDATION_ERROR.
    """
    payload = archive_logo.get("content_b64")
    if not isinstance(payload, str) or not payload:
        raise ValueError("missing logo content")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("undecodable logo content") from exc


def _bmp_ok(data: bytes) -> bool:
    """True if ``data`` is a plausible BMP: "BM" signature AND a little-endian
    file-size dword (offset 2) that equals the total byte length (LOW-1).

    The 2-byte "BM" prefix on its own is too weak — a non-image blob can start
    with it. A genuine BMP also stores its total file size as a 4-byte
    little-endian dword at offset 2, so we require that dword to match the actual
    decoded length. Pure byte/int comparison — no regex on dynamic input.
    """
    if len(data) < _BMP_MIN_HEADER_LEN or data[:2] != _BMP_SIGNATURE:
        return False
    declared_size = int.from_bytes(
        data[_BMP_SIZE_DWORD_OFFSET:_BMP_SIZE_DWORD_OFFSET + 4], "little"
    )
    return declared_size == len(data)


def _magic_ok(data: bytes) -> bool:
    """True if the decoded bytes start with a known IMAGE magic number.

    Pure byte-prefix membership test (no regex). Rejects disguised binaries
    (ELF/PE/script) whose extension or declared content-type claims an image.
    BMP is validated by :func:`_bmp_ok` (signature + size dword), not by the
    prefix table, because a bare "BM" prefix is too weakly discriminating.
    """
    head = data[:_MAGIC_INSPECT_LEN]
    for prefix, offset in _MAGIC_PREFIXES:
        if head[offset:offset + len(prefix)] == prefix:
            return True
    return _bmp_ok(data)


def _sanitize_failure(exc: Exception | str) -> str:
    """Sanitized, operator-facing failure message — NO url/path/byte leak.

    An upstream upload error body can echo the logo url/cache path. If the text
    carries any path/url marker, replace it wholesale with a generic message
    rather than risk leaking a path or token into the report.
    """
    text = (str(exc) or "").strip()
    lowered = text.lower()
    if not text or any(m in lowered for m in _PATH_LEAK_MARKERS):
        return "Upstream rejected the logo upload request."
    return text


def _upstream_reason_for(exc: Exception) -> FailureReason:
    """Classify an upload failure: timeout vs. generic upstream API error."""
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return FailureReason.UPSTREAM_TIMEOUT
    return FailureReason.UPSTREAM_API_ERROR


def _validate_logo(archive_logo: dict) -> tuple[bytes | None, str | None, str | None]:
    """Run all four validations + decode for ONE logo (the security chokepoint).

    Order is cheapest-first and decode-late so an over-declared-size or bad-name
    logo is rejected BEFORE we decode/buffer it:

    1. filename basename + path-traversal guard (:func:`_safe_basename`);
    2. declared-size cap (pre-decode);
    3. decode (base64 -> bytes);
    4. decoded-size cap;
    5. MIME magic-byte validation.

    Returns ``(decoded_bytes, safe_basename, None)`` when the logo is safe to
    upload, or ``(None, None, reason)`` with a SHORT, path-free reason string when
    it must be rejected. The reason never contains the raw filename or bytes.
    """
    # 1. Filename basename + traversal guard.
    basename = _safe_basename(archive_logo.get("filename"))
    if basename is None:
        return None, None, "unsafe or empty logo filename"

    # 2. Declared-size cap — reject before decoding a blob we know is over cap.
    declared = archive_logo.get("size")
    if isinstance(declared, int) and declared > MAX_LOGO_BYTES:
        return None, None, "declared logo size exceeds the 50MB cap"

    # 3. Decode (base64 -> bytes).
    try:
        data = _decode_logo_bytes(archive_logo)
    except ValueError:
        return None, None, "logo content could not be decoded"

    # 4. Decoded-size cap (authoritative).
    if len(data) > MAX_LOGO_BYTES:
        return None, None, "logo exceeds the 50MB cap"

    # 5. MIME magic-byte validation (disguised-binary rejection).
    if not _magic_ok(data):
        return None, None, "logo bytes are not a recognised image type"

    return data, basename, None


# Schemes a logo URL may carry for the re-create-by-URL path. Anything else (a
# Dispatcharr-local ``/data/logos/...`` path, a ``file:`` URI, a bare basename)
# names a location only the SOURCE instance could read, so it must not be turned
# into a dangling destination row. Those logos are restored from their ARCHIVED
# BYTES instead (the backup fetches them from Dispatcharr at gather time, bead
# …-xb58a); when an older artifact carries no bytes for one, an honest miss is
# the correct outcome.
_REMOTE_URL_SCHEMES = ("http://", "https://")


def hydratable_bytes(archive_logo: dict, content_provider) -> bool:
    """True when this record can supply image BYTES for an upload.

    Either the record already carries its base64 payload (the archive-restore
    path) or a lazy ``content_provider`` is wired that can fetch it (the
    cross-instance sync path). When neither holds, the only way to restore the
    logo is by URL.
    """
    if archive_logo.get("content_b64"):
        return True
    return content_provider is not None


def _remote_logo_url(archive_logo: dict) -> str | None:
    """The archived logo's REMOTE url, or ``None`` when it has no usable one.

    Only absolute ``http(s)`` URLs qualify — see the call site for why a
    Dispatcharr-local path is deliberately excluded. Control characters are
    rejected outright (log-forging / header-injection hygiene, mirroring
    :func:`_safe_basename`).
    """
    url = archive_logo.get("url")
    if not isinstance(url, str):
        return None
    trimmed = url.strip()
    if not trimmed or any(ord(ch) in _CONTROL_BYTES for ch in trimmed):
        return None
    lowered = trimmed.lower()
    if not lowered.startswith(_REMOTE_URL_SCHEMES):
        return None
    return trimmed


# Public aliases for the PRODUCER side (``routers.backup``). The backup builder
# has to make the SAME two judgements this importer makes, and it must make them
# identically or the artifact it writes is one the restore cannot consume:
#
#   * which logos are remotely hosted (those restore from their archived URL and
#     their bytes are deliberately NOT archived), and
#   * whether a filename is safe (a record whose ``filename`` this validator
#     rejects is a guaranteed VALIDATION_ERROR on the way back in).
#
# The producer therefore calls THESE functions rather than re-implementing them.
# The drill (bead …-dgnms) is the cautionary tale: the producer emitted logo
# records with no ``filename`` key at all, and every one of them failed the
# consumer's first check.
remote_logo_url = _remote_logo_url
safe_logo_basename = _safe_basename


async def _create_logo_from_url(
    *,
    archive_logo: dict,
    remote_url: str,
    label: str,
    source_id,
    client: DispatcharrClient,
    cat,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    result: "LogoImportResult",
    channels_by_logo: dict[int, list[dict]],
) -> None:
    """Re-create a byte-less, remotely-hosted logo from its archived URL.

    Mirrors the upload path's bookkeeping exactly — ledger, remap, per-category
    counts — so a URL-restored logo is indistinguishable downstream from an
    uploaded one and the channel-logo reattach
    (:func:`dbas.channel_reattach.reattach_channel_logos`) can resolve it.

    A failure is a per-entity ``UPSTREAM_API_ERROR`` plus a COUNTED logo miss:
    the operator must be told which logos did not come back, which is the whole
    point of the bead. A SUCCESS records no miss, because the logo came back.
    """
    try:
        created = await client.create_logo({"name": label, "url": remote_url})
    except Exception as exc:  # noqa: BLE001 - per-entity failure, never a crash
        reason_enum = _upstream_reason_for(exc)
        cat.failed += 1
        result.rejected += 1
        cat.failure_details.append(
            FailureDetail(
                reason=reason_enum,
                label=label,
                message=_sanitize_failure(exc),
                source_export_id=source_id,
            )
        )
        _record_logo_loss(
            report=report, label=label, source_id=source_id,
            channels_by_logo=channels_by_logo, remap=remap,
            is_dry_run=False, result=result,
        )
        logger.warning(
            "[DBAS-LOGOS] Failed to re-create remote logo '%s': %s",
            label, reason_enum.value,
        )
        return

    new_id = created.get("id") if isinstance(created, dict) else None
    cat.created += 1
    result.uploaded += 1
    if new_id is not None:
        new_id = int(new_id)
        if source_id is not None:
            remap.add(EntityType.LOGO, int(source_id), new_id)
        ledger.record_created(EntityType.LOGO, new_id, label)
    logger.info(
        "[DBAS-LOGOS] Re-created remotely-hosted logo '%s' from its archived URL "
        "(id=%s).",
        label, new_id,
    )


def _simulate_create_logo_from_url(
    *,
    label: str,
    source_id,
    cat,
    result: "LogoImportResult",
) -> None:
    """Dry-run twin of :func:`_create_logo_from_url` (bead …-dgnms).

    The preview must predict what the apply will DO, so it simulates the same
    decision: a byte-less logo with a usable absolute ``http(s)`` URL is a
    ``would_create``, not a failure. Before this existed the URL branch was
    gated on ``not is_dry_run``, so every byte-less record fell through to
    :func:`_validate_logo`, which needs a ``filename`` a URL-only record does
    not carry. The drill's preview reported 11 of 11 logos as
    ``validation_error`` for an artifact whose apply then restored 10 of them,
    burying the ONE genuinely unrestorable logo in invented noise.

    Nothing is written: no ledger entry, no remap registration (a dry-run remap
    id would be a fabrication), and no logo miss (the apply's URL path does not
    record one either, because the logo does come back). The SOURCE id is
    recorded on the result instead, so the logo-reattach pass can count a channel
    whose logo this preview knows will exist without inventing its id.
    """
    cat.would_create += 1
    result.uploaded += 1
    _note_would_create(result, source_id)
    logger.info(
        "[DBAS-LOGOS] Dry run: logo '%s' would be re-created from its archived "
        "URL (source id=%s).",
        label, source_id,
    )


def _note_would_create(result: "LogoImportResult", source_id) -> None:
    """Record that a DRY RUN would create the logo with this SOURCE id.

    The one place :attr:`LogoImportResult.would_create_source_ids` is written, so
    the two dry-run create branches (URL re-create and validated upload) cannot
    diverge. A record with no usable integer id is skipped rather than guessed —
    the reattach pass keys on the archived ``logo_id`` FK, which is an int or
    nothing.
    """
    if isinstance(source_id, int) and not isinstance(source_id, bool):
        result.would_create_source_ids.add(source_id)


def _channels_by_logo_id(archive_channels: list[dict] | None) -> dict[int, list[dict]]:
    """Index the archive channels by their ``logo_id`` FK (bead cm9bi).

    The logo-miss drill-down lists the AFFECTED CHANNELS per missed logo. The
    channel context lives in the archive's channel records (``logo_id`` points at
    the SOURCE logo id); this index makes the per-miss lookup O(1) instead of a
    rescan per miss.
    """
    index: dict[int, list[dict]] = {}
    for channel in archive_channels or []:
        logo_ref = channel.get("logo_id")
        if isinstance(logo_ref, int) and not isinstance(logo_ref, bool):
            index.setdefault(logo_ref, []).append(channel)
    return index


def _affected_channels(
    source_logo_id: object,
    channels_by_logo: dict[int, list[dict]],
    remap: IdRemapTable,
    is_dry_run: bool,
) -> list[LogoMissChannel]:
    """The affected-channel rows for ONE missed logo (bead cm9bi).

    ``channel_id`` is the DESTINATION id resolved through the CHANNEL remap
    namespace — the channels importer runs before this one, so on apply the
    mapping is already populated. On dry-run the CHANNEL remap holds PROVISIONAL
    ids (source id reused as a stand-in for would-creates, indistinguishable
    from real matches), so dry-run NEVER emits a channel id — name only.
    """
    if not isinstance(source_logo_id, int) or isinstance(source_logo_id, bool):
        return []
    affected: list[LogoMissChannel] = []
    for channel in channels_by_logo.get(source_logo_id, []):
        name = channel.get("name")
        dest_id = None
        source_channel_id = channel.get("id")
        if not is_dry_run and isinstance(source_channel_id, int):
            dest_id = remap.resolve(EntityType.CHANNEL, source_channel_id)
        affected.append(
            LogoMissChannel(
                channel_id=dest_id,
                name=str(name) if name else "<unknown>",
            )
        )
    return affected


def _record_logo_loss(
    *,
    report: RestoreReport,
    label: str,
    source_id,
    channels_by_logo: dict[int, list[dict]],
    remap: IdRemapTable,
    is_dry_run: bool,
    result: "LogoImportResult",
) -> None:
    """Report ONE logo this importer could not bring back (the D9 loss signal).

    The single call site pattern for every failure path in :func:`import_logos`.
    See :meth:`RestoreReport.record_logo_miss` for the invariant this enforces:
    a miss is a logo the operator LOST, so this is called only after a path has
    already decided the logo is not coming back, and never on a success path.

    Also attaches the AFFECTED CHANNELS (bead cm9bi) so the drill-down behind
    the aggregate names which channels lost their artwork, and bumps the
    importer's own ``misses`` counter, which tracks the same events for the
    caller's summary.
    """
    report.record_logo_miss(
        label=label,
        source_export_id=source_id,
        channels=_affected_channels(source_id, channels_by_logo, remap, is_dry_run),
    )
    result.misses += 1


async def import_logos(
    *,
    archive_logos: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
    clear_existing: bool = False,
    archive_channels: list[dict] | None = None,
    content_provider: Optional[Callable[[dict], Awaitable[Optional[str]]]] = None,
) -> LogoImportResult:
    """Restore the LOGO category: 3-tier match + streaming upload of the misses.

    Args:
        archive_logos: The logo records from the export archive. Each carries the
            source ``id``, display ``name``, ``filename``, ``content_type``, and
            base64 ``content_b64`` (the D1 binary subtree decoded into records).
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag. ``False`` -> the whole category is
            skipped (no deletes, no uploads); each logo recorded
            EXCLUDED_BY_OPERATOR.
        report: The shared :class:`RestoreReport`; results land in the
            ``EntityType.LOGO`` category and feed :attr:`RestoreReport.logo_misses`.
        ledger: The shared :class:`RollbackLedger`; each UPLOADED logo is recorded
            for compensating deletes (a matched existing logo is NOT ledgered).
        remap: The shared :class:`IdRemapTable`. WRITTEN with each matched/uploaded
            logo's ``source->dest`` id under ``EntityType.LOGO`` so channel
            ``logo_id`` references resolve.
        is_dry_run: ``True`` -> nothing is deleted or uploaded; the importer only
            reports ``would_create`` / ``would_skip``.
        clear_existing: ``True`` -> run the DESTRUCTIVE bulk-delete pre-step
            (clear all destination logos before upload). OFF by default; NEVER
            runs in dry-run.
        archive_channels: The CHANNEL records from the export archive (bead
            cm9bi). Read-only channel context for the logo-miss drill-down:
            each miss lists the channels whose ``logo_id`` referenced it, with
            the destination channel id resolved through the CHANNEL remap
            namespace (populated by the channels importer, which runs earlier).
            ``None``/empty -> misses carry an empty channel list.
        content_provider: OPTIONAL lazy per-logo payload loader (bead
            ``7ipq2.1`` — the cross-instance sync path). When set, a MISSED
            logo whose record carries no ``content_b64`` is hydrated by
            awaiting ``content_provider(record)`` (must return the base64
            string, or ``None`` on failure) IMMEDIATELY before validation +
            upload — one logo at a time, so the D8 streaming guarantee holds
            for plans that are assembled METADATA-ONLY (holding every logo's
            base64 in the plan at once would defeat D8). The hydrated payload
            lives on a per-iteration COPY; the caller's record is never
            mutated (no accumulation). Cheap pre-checks (safe basename,
            declared-size cap) run BEFORE hydration so a logo that is already
            rejectable is never read. A provider failure (``None`` / raise) is
            a per-logo ``VALIDATION_ERROR`` with a path-free message, never a
            crash. ``None`` (the default, the archive-restore path) leaves
            behaviour byte-for-byte unchanged.

    Returns:
        A :class:`LogoImportResult` with safe aggregate counters.
    """
    cat = report.category(EntityType.LOGO)
    result = LogoImportResult()

    # OPT-IN. Off unless the operator selected the logos category.
    if not selected:
        logger.info("[DBAS-LOGOS] Category not selected; skipping logos.")
        for archive_logo in archive_logos:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _logo_label(archive_logo),
                archive_logo.get("id"),
                is_dry_run,
            )
        return result

    logger.info(
        "[DBAS-LOGOS] Restoring logos (dry_run=%s, clear_existing=%s); %d archived logo(s).",
        is_dry_run,
        clear_existing,
        len(archive_logos),
    )

    # Index the archive channels by logo_id ONCE (bead cm9bi) so each miss can
    # list its affected channels without rescanning the channel records.
    channels_by_logo = _channels_by_logo_id(archive_channels)

    # Enumerate destination logos ONCE for the 3-tier match (safe: id/name/url).
    try:
        dest_logos = await client.get_all_logos_paginated()
    except Exception as exc:  # noqa: BLE001 - best-effort; match degrades to misses
        logger.warning("[DBAS-LOGOS] Could not list destination logos: %s", _safe_failure_log(exc))
        dest_logos = []

    # DESTRUCTIVE bulk-delete pre-step — opt-in, never in dry-run.
    if clear_existing and not is_dry_run:
        existing_ids = [d.get("id") for d in dest_logos if isinstance(d.get("id"), int)]
        if existing_ids:
            try:
                await client.bulk_delete_logos(existing_ids)
                result.deleted_existing = len(existing_ids)
                logger.info(
                    "[DBAS-LOGOS] Bulk-delete pre-step cleared %d existing logo(s).",
                    len(existing_ids),
                )
            except Exception as exc:  # noqa: BLE001 - log + proceed (don't leave no logos)
                logger.warning(
                    "[DBAS-LOGOS] Bulk-delete pre-step failed (%s); proceeding with upload.",
                    _safe_failure_log(exc),
                )
        # After a clear, nothing remains to match against.
        dest_logos = []

    # STREAMING loop — process ONE logo at a time: match, validate+decode, upload,
    # release. Never holds more than a single decoded payload in memory (D8).
    for archive_logo in archive_logos:
        label = _logo_label(archive_logo)
        source_id = archive_logo.get("id")

        # 3-tier match FIRST — a matched logo already exists; never re-uploaded.
        dest_id, tier = resolve_logo_match(
            archive_logo, dest_logos=dest_logos, remap=remap
        )
        if dest_id is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            if source_id is not None:
                remap.add(EntityType.LOGO, int(source_id), int(dest_id))
            result.matched += 1
            logger.info(
                "[DBAS-LOGOS] Logo '%s' matched existing (tier=%s, dest id=%s); skipped.",
                label,
                tier,
                dest_id,
            )
            continue

        # MISS with a REMOTE URL and no bytes: re-create it BY URL rather than
        # by upload (bead …-dfkbn item 1). A logo whose image lives on a CDN has
        # an archived URL and no bytes, because the backup does not fetch what
        # it does not host. The drill lost 12 such logos: their URLs WERE in the
        # archive and nothing re-resolved them. Dispatcharr's Logo model is
        # exactly ``{name, url}`` (0.28.2 apps/channels/models.py), so
        # re-creating the row from the archived URL restores it byte-identically.
        # The image was never ours to hold.
        #
        # ONLY an absolute http(s) URL qualifies. A Dispatcharr-LOCAL path
        # (``/data/logos/x.png``) names bytes on the SOURCE instance's volume,
        # not a fetchable location; re-creating that row would give the operator
        # a channel whose logo silently 404s, which is strictly worse than an
        # honest miss. Those logos are archived WITH their bytes at backup time
        # (bead …-xb58a, ``routers.backup._gather_dispatcharr_logo_payloads``),
        # so on a current artifact they arrive here hydratable and take the
        # upload path below. An OLDER artifact built before that fix carries no
        # bytes for them, and they still fall through to the validate/upload
        # path and are reported as an honest miss.
        #
        # The dry run takes the SAME branch (bead …-dgnms) so the preview's
        # counts match the apply's.
        if not hydratable_bytes(archive_logo, content_provider):
            remote_url = _remote_logo_url(archive_logo)
            if remote_url is not None:
                if is_dry_run:
                    _simulate_create_logo_from_url(
                        label=label,
                        source_id=source_id,
                        cat=cat,
                        result=result,
                    )
                else:
                    await _create_logo_from_url(
                        archive_logo=archive_logo,
                        remote_url=remote_url,
                        label=label,
                        source_id=source_id,
                        client=client,
                        cat=cat,
                        report=report,
                        ledger=ledger,
                        remap=remap,
                        result=result,
                        channels_by_logo=channels_by_logo,
                    )
                continue

        # MISS — this logo must be uploaded. Hydrate lazily when the record is
        # metadata-only (sync path), then validate+decode BEFORE upload. The
        # cheap pre-checks (basename, declared-size cap) run FIRST so a logo
        # that is already rejectable is never read from the source; when they
        # fail, hydration is skipped and _validate_logo below produces the
        # canonical rejection reason for the un-hydrated record.
        hydrated = archive_logo
        if content_provider is not None and not archive_logo.get("content_b64"):
            declared_size = archive_logo.get("size")
            precheck_ok = _safe_basename(archive_logo.get("filename")) is not None and not (
                isinstance(declared_size, int) and declared_size > MAX_LOGO_BYTES
            )
            if precheck_ok:
                try:
                    payload_b64 = await content_provider(archive_logo)
                except Exception:  # noqa: BLE001 - per-logo containment, path-free
                    payload_b64 = None
                if not payload_b64:
                    cat.failed += 1
                    result.rejected += 1
                    cat.failure_details.append(
                        FailureDetail(
                            reason=FailureReason.VALIDATION_ERROR,
                            label=label,
                            # Path-free by construction — never the provider's
                            # exception text (it can echo a filesystem path).
                            message="logo content could not be read from the source",
                            source_export_id=source_id,
                        )
                    )
                    # The bytes could not be read, so this logo does NOT come
                    # back: an operator-facing loss.
                    _record_logo_loss(
                        report=report, label=label, source_id=source_id,
                        channels_by_logo=channels_by_logo, remap=remap,
                        is_dry_run=is_dry_run, result=result,
                    )
                    logger.warning(
                        "[DBAS-LOGOS] Rejected logo '%s': source content unavailable.",
                        label,
                    )
                    continue
                # Per-iteration COPY: the caller's plan record stays
                # metadata-only (no accumulation across the loop — D8).
                hydrated = {**archive_logo, "content_b64": payload_b64}

        data, basename, reason = _validate_logo(hydrated)
        if reason is not None:
            cat.failed += 1
            result.rejected += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.VALIDATION_ERROR,
                    label=label,
                    message=reason,
                    source_export_id=source_id,
                )
            )
            # A record we refuse does not come back: an operator-facing loss.
            _record_logo_loss(
                report=report, label=label, source_id=source_id,
                channels_by_logo=channels_by_logo, remap=remap,
                is_dry_run=is_dry_run, result=result,
            )
            # Log only the reason enum-style string + label — never the filename.
            logger.warning(
                "[DBAS-LOGOS] Rejected logo '%s': %s.", label, reason
            )
            continue

        # From here the logo is VALIDATED and will be uploaded. It is NOT a logo
        # miss: a miss is a logo the operator lost, and this one is coming back.
        # It was one until bead …-xb58a made archived bytes the normal case for a
        # Dispatcharr-hosted logo, at which point the pre-upload increment here
        # started reporting every successfully restored logo as lost. See
        # :meth:`RestoreReport.record_logo_miss` for the invariant.
        if is_dry_run:
            cat.would_create += 1
            # The apply WILL upload this logo, so a channel pointing at it will
            # get its logo back. Record the source id for the reattach pass's
            # preview split (bead …-dgnms) — see would_create_source_ids.
            _note_would_create(result, source_id)
            # Release the decoded bytes promptly (no upload in dry-run).
            del data
            continue

        content_type = _safe_content_type(archive_logo, data)
        try:
            created = await client.upload_logo_file(
                label, basename, data, content_type
            )
        except Exception as exc:  # noqa: BLE001 - per-entity failure, never a crash
            reason_enum = _upstream_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason_enum,
                    label=label,
                    message=_sanitize_failure(exc),
                    source_export_id=source_id,
                )
            )
            # The upload failed, so this logo does NOT come back: an
            # operator-facing loss. This is the residual case bead …-d0agi is
            # the safety net for (an upstream 5xx on a logo the backup DID
            # archive), and it must reach the D9 banner.
            _record_logo_loss(
                report=report, label=label, source_id=source_id,
                channels_by_logo=channels_by_logo, remap=remap,
                is_dry_run=is_dry_run, result=result,
            )
            logger.warning(
                "[DBAS-LOGOS] Failed to upload logo '%s': %s", label, reason_enum.value
            )
            # Release the decoded bytes on the failure path too.
            del data
            continue
        finally:
            # Drop the decoded payload reference ASAP so it is collectable before
            # the next logo is read (the D8 release-before-next guarantee).
            data = None  # noqa: F841 - intentional release of the decoded payload

        new_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        result.uploaded += 1
        if new_id is not None:
            new_id = int(new_id)
            if source_id is not None:
                remap.add(EntityType.LOGO, int(source_id), new_id)
            ledger.record_created(EntityType.LOGO, new_id, label)
        logger.info("[DBAS-LOGOS] Uploaded logo '%s' (id=%s).", label, new_id)

    return result


# Validated-magic -> content-type map for _safe_content_type's fallback. Keyed
# by the SAME signatures _MAGIC_PREFIXES accepts (plus the BMP path), so the
# derived type can only ever describe bytes the magic gate already validated.
_MAGIC_CONTENT_TYPES: tuple[tuple[bytes, int, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"GIF87a", 0, "image/gif"),
    (b"GIF89a", 0, "image/gif"),
    (b"WEBP", 8, "image/webp"),
)


def _content_type_from_magic(data: bytes) -> Optional[str]:
    """Derive the image content-type from ALREADY-VALIDATED magic bytes.

    Called only after :func:`_validate_logo` accepted ``data``, so this is a
    classification of known-image bytes, not a validation step. Returns ``None``
    for a magic outside the map (BMP falls back to its own type below).
    """
    head = data[:_MAGIC_INSPECT_LEN]
    for prefix, offset, content_type in _MAGIC_CONTENT_TYPES:
        if head[offset:offset + len(prefix)] == prefix:
            return content_type
    if _bmp_ok(data):
        return "image/bmp"
    return None


def _safe_content_type(archive_logo: dict, data: bytes = b"") -> str:
    """Return a SAFE content-type for the multipart upload.

    The archive's declared content-type is advisory only (the magic-byte check
    is the real gate). We pass it through when it looks like an image
    content-type. When the record declares no usable type — the sync
    live-gather records are METADATA-ONLY and carry none (bead 7ipq2.2) — the
    type is derived from the already-validated magic bytes: a real Dispatcharr
    0.28.2 upload endpoint REJECTS ``application/octet-stream`` outright
    ("Unsupported file type"), so the octet-stream fallback is a last resort
    only when even the magic is outside the map.
    """
    declared = archive_logo.get("content_type")
    if isinstance(declared, str) and declared.lower().startswith("image/"):
        # Strip any parameters / control chars defensively.
        clean = declared.split(";", 1)[0].strip().lower()
        if clean and all(ord(c) >= 0x20 for c in clean):
            return clean
    derived = _content_type_from_magic(data) if data else None
    if derived:
        return derived
    return "application/octet-stream"


def _safe_failure_log(exc: Exception) -> str:
    """A path-free string for a best-effort WARN log (list/delete failures)."""
    return _sanitize_failure(exc)


def _skip(
    cat,
    reason: SkipReason,
    label: str,
    source_export_id,
    is_dry_run: bool,
) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )
