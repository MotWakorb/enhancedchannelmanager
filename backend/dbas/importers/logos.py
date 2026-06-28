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
to roll back). A MISS uploads the logo (streaming), ledgers it, remaps it, and
feeds :attr:`RestoreReport.logo_misses` (D9 red banner).

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

from pydantic import BaseModel, Field

from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    LogoMissDetail,
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

        # MISS — this logo must be uploaded. Validate+decode BEFORE upload.
        data, basename, reason = _validate_logo(archive_logo)
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
            # Log only the reason enum-style string + label — never the filename.
            logger.warning(
                "[DBAS-LOGOS] Rejected logo '%s': %s.", label, reason
            )
            continue

        # A valid miss feeds the logo-miss aggregate (D9 red banner) regardless of
        # dry-run vs. apply — it is a logo the destination did not already have.
        # It also records a per-logo detail row (id + name) for the drill-down
        # behind the aggregate count (bead qhui4) — additive to the D9 banner.
        report.logo_misses += 1
        report.logo_miss_details.append(
            LogoMissDetail(source_export_id=source_id, label=label)
        )
        result.misses += 1

        if is_dry_run:
            cat.would_create += 1
            # Release the decoded bytes promptly (no upload in dry-run).
            del data
            continue

        content_type = _safe_content_type(archive_logo)
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


def _safe_content_type(archive_logo: dict) -> str:
    """Return a SAFE content-type for the multipart upload.

    The archive's declared content-type is advisory only (the magic-byte check is
    the real gate). We pass it through when it looks like an image content-type,
    else default to ``application/octet-stream`` so we never forward an arbitrary
    attacker-controlled header value.
    """
    declared = archive_logo.get("content_type")
    if isinstance(declared, str) and declared.lower().startswith("image/"):
        # Strip any parameters / control chars defensively.
        clean = declared.split(";", 1)[0].strip().lower()
        if clean and all(ord(c) >= 0x20 for c in clean):
            return clean
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
