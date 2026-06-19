"""Whole-artifact passphrase encryption for DBAS backups (ADR-012 D12, bead
``enhancedchannelmanager-u81kh``; construction from spike ``0zrse``).

This is a **new crypto primitive, intentionally parallel to** the Fernet
primitive in :mod:`cloud_storage.crypto` (which is static-key, whole-artifact
in-RAM, and cannot stream). Per ADR-012, **D3 governs at-rest credential
columns** (Fernet); **D12 governs this opt-in whole-artifact path**. The two
coexist; ``cloud_storage.crypto`` is **not** reused here.

Construction (spike ``0zrse`` — build-ready, ``cryptography`` only, 0 new deps)
------------------------------------------------------------------------------
* **KDF:** scrypt, ``N >= 2**15`` (floor), ``r=8``, ``p=1``, 32-byte key; a
  per-artifact random 16-byte salt. KDF params + salt live in a **cleartext,
  authenticated header** so a version check can read them before decrypting.
* **Cleartext authenticated header:** ``magic`` + a JSON blob carrying
  ``format_version`` (the *encryption-envelope* version, **distinct from** the
  backup ``schema_version``), the KDF params, the salt, a random base nonce, the
  AEAD id, and the plaintext chunk size. The header JSON bytes are folded into
  **every** chunk's AAD, so any header edit (e.g. lowering ``N``) breaks
  authentication on the first chunk.
* **AEAD:** ChaCha20-Poly1305, **chunked streaming**. Per-chunk nonce =
  ``base_nonce XOR counter``. Each chunk's **AAD binds the header + chunk index
  + an is_final flag**, so chunk swap, reorder, and stream truncation all fail
  authentication (no forged/partial plaintext).
* **Structural no-oracle (checklist 33):** a wrong passphrase and a corrupted
  artifact raise an **identical** :class:`ArtifactDecryptError` and release
  **zero plaintext** on any failure (the partial output file is unlinked before
  the exception propagates). This is a *structural* property, not a wall-clock
  one — spike ``0zrse`` accepted a ~15 ms size-dependent timing residual for an
  offline artifact. Do not assert wall-clock equivalence anywhere.

Streaming / off-event-loop
--------------------------
:func:`encrypt_file` and :func:`decrypt_file` are **synchronous**, stream their
input one chunk at a time (never whole-artifact-in-RAM), and are meant to be
driven from an executor (``loop.run_in_executor``) by the async backup/restore
seams so the scrypt derivation + AEAD work never blocks the event loop.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log line (a passphrase or
derived key is NEVER logged).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger(__name__)

# --- Envelope format constants ---------------------------------------------
# 8-byte file magic; a sniff (is_encrypted_artifact) and a header read both use
# it to distinguish an encrypted artifact from a plain ZIP without a passphrase.
MAGIC = b"ECMBKENC"

# The encryption-envelope version. SEPARATE from the backup schema_version
# (which lives in the manifest INSIDE the ciphertext). This lets a restore-time
# version gate (.17-style) refuse an unsupported envelope BEFORE asking for a
# passphrase. Bump only on a breaking envelope-format change.
FORMAT_VERSION = 1

# scrypt parameters. N is the CPU/memory cost (2**15 is the spike floor); r/p
# per spike 0zrse. 32-byte key for ChaCha20-Poly1305. N=2**15,r=8 ~= 32 MiB per
# derivation — acceptable for a once-per-backup / once-per-restore operation.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_LEN = 16

# ChaCha20-Poly1305 nonce width. The per-chunk nonce is base_nonce XOR counter.
_NONCE_LEN = 12
_AEAD_ID = "chacha20poly1305"

# Plaintext chunk size. 1 MiB keeps per-chunk RAM bounded while amortising the
# AEAD per-call overhead. A 16-byte Poly1305 tag is appended per chunk by the
# AEAD, so ciphertext chunks are chunk_size + 16.
_CHUNK_SIZE = 1024 * 1024

# Minimum operator passphrase length, API-enforced (checklist 29). Also enforced
# here as defence-in-depth so the primitive can never derive a key from a
# trivially short passphrase even if a caller forgets the boundary check.
MIN_PASSPHRASE_LENGTH = 12

# Sanity ceiling on a single ciphertext chunk length read from an untrusted
# artifact, so a corrupt/hostile length prefix cannot drive an enormous
# allocation before authentication. chunk_size + tag + generous slack.
_MAX_CT_CHUNK = _CHUNK_SIZE + 1024


class ArtifactCryptoError(Exception):
    """Base class for artifact-crypto errors."""


class WeakPassphraseError(ArtifactCryptoError):
    """Raised when a passphrase is shorter than :data:`MIN_PASSPHRASE_LENGTH`."""


class UnsupportedArtifactFormatError(ArtifactCryptoError):
    """Raised reading the cleartext header for a non-artifact or a
    newer/unsupported ``format_version`` — WITHOUT needing a passphrase."""


class ArtifactDecryptError(ArtifactCryptoError):
    """The SINGLE failure raised for ANY decrypt failure.

    A wrong passphrase, a corrupted/truncated artifact, a reordered or swapped
    chunk, and a tampered header all raise this exact type with this exact
    message — the structural no-oracle property (checklist 33). Zero plaintext
    is released on any failure (the partial output is unlinked first).
    """

    # One fixed message so wrong-passphrase and corruption are indistinguishable
    # to the caller (no oracle in the exception text).
    _MESSAGE = "Could not decrypt artifact: wrong passphrase or corrupted artifact"

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)


def _validate_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise WeakPassphraseError(
            "Passphrase must be at least %d characters" % MIN_PASSPHRASE_LENGTH
        )


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Derive the 32-byte AEAD key from the passphrase via scrypt.

    The passphrase and the derived key are NEVER logged.
    """
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def _chunk_nonce(base_nonce: bytes, index: int) -> bytes:
    """Per-chunk nonce = base_nonce XOR (index as a big-endian counter).

    ``index`` is bounded by the artifact size / chunk size, far below 2**96, so
    the XOR counter never wraps and no nonce repeats under the per-artifact key.
    """
    counter = index.to_bytes(_NONCE_LEN, "big")
    return bytes(a ^ b for a, b in zip(base_nonce, counter))


def _build_header_bytes(salt: bytes, base_nonce: bytes) -> bytes:
    """Serialise the cleartext authenticated header to its exact on-disk bytes.

    The returned bytes are what goes on disk AND what is folded into every
    chunk's AAD, so the two can never drift.
    """
    header = {
        "format_version": FORMAT_VERSION,
        "kdf": {"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
                "length": _KEY_LEN},
        "salt": base64.b64encode(salt).decode("ascii"),
        "base_nonce": base64.b64encode(base_nonce).decode("ascii"),
        "aead": _AEAD_ID,
        "chunk_size": _CHUNK_SIZE,
    }
    # Canonical, stable serialisation (sorted keys, no spaces) so the exact
    # bytes are reproducible and AAD-stable across reads.
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _chunk_aad(header_bytes: bytes, index: int, is_final: bool) -> bytes:
    """AAD for a chunk: header bytes + chunk index + is_final flag.

    Binding the header makes a param edit fail auth; binding the index defeats
    reorder/swap; binding is_final defeats truncation/append.
    """
    return header_bytes + struct.pack(">Q", index) + struct.pack(">B", 1 if is_final else 0)


def is_encrypted_artifact(path: Path) -> bool:
    """True if ``path`` begins with the artifact :data:`MAGIC`.

    A cheap sniff used by the restore decrypt-at-ingest gate to decide whether a
    plain ZIP or an encrypted envelope was uploaded. Never raises on a
    short/unreadable file — returns False.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_header(path: Path) -> dict:
    """Read and validate the cleartext envelope header WITHOUT a passphrase.

    Returns the parsed header dict (``format_version``, ``kdf``, ``salt``,
    ``aead``, ``chunk_size``, ...). Raises :class:`UnsupportedArtifactFormatError`
    if the file is not an artifact, the header is malformed, or
    ``format_version`` exceeds :data:`FORMAT_VERSION` (the pre-decrypt version
    gate, checklist 30). Does NOT derive a key or touch ciphertext.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(len(MAGIC))
            if magic != MAGIC:
                raise UnsupportedArtifactFormatError("Not an ECM encrypted artifact")
            raw_len = fh.read(4)
            if len(raw_len) != 4:
                raise UnsupportedArtifactFormatError("Truncated artifact header")
            (header_len,) = struct.unpack(">I", raw_len)
            if header_len <= 0 or header_len > 64 * 1024:
                raise UnsupportedArtifactFormatError("Invalid artifact header length")
            header_bytes = fh.read(header_len)
            if len(header_bytes) != header_len:
                raise UnsupportedArtifactFormatError("Truncated artifact header")
    except OSError as exc:
        raise UnsupportedArtifactFormatError("Artifact header unreadable") from exc

    try:
        header = json.loads(header_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UnsupportedArtifactFormatError("Malformed artifact header") from exc
    if not isinstance(header, dict):
        raise UnsupportedArtifactFormatError("Malformed artifact header")

    version = header.get("format_version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise UnsupportedArtifactFormatError(
            "Unsupported artifact format version"
        )
    return header


def encrypt_file(plaintext_path: Path, passphrase: str, dest_path: Path) -> None:
    """Encrypt ``plaintext_path`` to ``dest_path`` (chunked streaming AEAD).

    Synchronous + streaming; drive from an executor off the event loop. Writes
    the magic + authenticated header, then one length-prefixed AEAD chunk per
    plaintext chunk (the last carries ``is_final=1`` in its AAD). On ANY failure
    the partial ``dest_path`` is removed before the exception propagates.

    Raises:
        WeakPassphraseError: passphrase shorter than the minimum.
    """
    _validate_passphrase(passphrase)
    plaintext_path = Path(plaintext_path)
    dest_path = Path(dest_path)

    salt = os.urandom(_SALT_LEN)
    base_nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    aead = ChaCha20Poly1305(key)
    header_bytes = _build_header_bytes(salt, base_nonce)

    try:
        with open(plaintext_path, "rb") as src, open(dest_path, "wb") as dst:
            dst.write(MAGIC)
            dst.write(struct.pack(">I", len(header_bytes)))
            dst.write(header_bytes)

            index = 0
            # Read one chunk ahead so we know which chunk is the LAST and can
            # stamp is_final in its AAD (truncation/append detection).
            current = src.read(_CHUNK_SIZE)
            while True:
                nxt = src.read(_CHUNK_SIZE)
                is_final = (nxt == b"")
                nonce = _chunk_nonce(base_nonce, index)
                aad = _chunk_aad(header_bytes, index, is_final)
                ct = aead.encrypt(nonce, current, aad)
                dst.write(struct.pack(">I", len(ct)))
                dst.write(ct)
                if is_final:
                    break
                current = nxt
                index += 1
        logger.info(
            "[ARTIFACT-CRYPTO] Encrypted artifact -> %s (%d chunks)",
            dest_path.name, index + 1,
        )
    except Exception:
        _unlink_quiet(dest_path)
        raise


def decrypt_file(ciphertext_path: Path, passphrase: str, dest_path: Path) -> None:
    """Decrypt ``ciphertext_path`` to ``dest_path`` (chunked streaming AEAD).

    Synchronous + streaming; drive from an executor off the event loop.

    STRUCTURAL no-oracle (checklist 33): a wrong passphrase, a corrupted or
    truncated artifact, a reordered/swapped chunk, and a tampered header ALL
    raise the identical :class:`ArtifactDecryptError`, and ``dest_path`` is
    unlinked before the exception propagates so ZERO plaintext is released on
    any failure. Plaintext chunks are written as they authenticate, but the
    whole output is destroyed the instant any chunk fails — the caller never
    receives a verified prefix.

    A passphrase shorter than the minimum still raises the identical
    :class:`ArtifactDecryptError` here (not :class:`WeakPassphraseError`): on the
    DECRYPT side, a short guess must be indistinguishable from a wrong guess (no
    oracle). The API boundary enforces the min length on the backup/produce side.
    """
    ciphertext_path = Path(ciphertext_path)
    dest_path = Path(dest_path)
    try:
        if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
            # No oracle: a too-short passphrase fails identically to a wrong one.
            raise ArtifactDecryptError()

        header = read_header(ciphertext_path)
        salt = base64.b64decode(header["salt"])
        base_nonce = base64.b64decode(header["base_nonce"])
        kdf = header["kdf"]
        key = _derive_key(passphrase, salt, int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
        aead = ChaCha20Poly1305(key)

        with open(ciphertext_path, "rb") as src:
            # Read past magic + length, then read the LITERAL on-disk header
            # bytes. The AAD must be the EXACT bytes the encryptor authenticated
            # — NOT a reconstruction from current constants (which would silently
            # diverge if a future version changed chunk_size / param ordering).
            src.read(len(MAGIC))
            (header_len,) = struct.unpack(">I", src.read(4))
            header_bytes = src.read(header_len)
            if len(header_bytes) != header_len:
                raise ArtifactDecryptError()

            with open(dest_path, "wb") as dst:
                index = 0
                current = _read_chunk(src)
                if current is None:
                    # No chunks at all — not a well-formed artifact.
                    raise ArtifactDecryptError()
                while True:
                    nxt = _read_chunk(src)
                    is_final = (nxt is None)
                    nonce = _chunk_nonce(base_nonce, index)
                    aad = _chunk_aad(header_bytes, index, is_final)
                    pt = aead.decrypt(nonce, current, aad)
                    dst.write(pt)
                    if is_final:
                        break
                    current = nxt
                    index += 1
    except ArtifactDecryptError:
        _unlink_quiet(dest_path)
        raise
    except (InvalidTag, UnsupportedArtifactFormatError, struct.error, OSError,
            ValueError, KeyError, TypeError):
        # EVERY failure mode collapses to the one identical exception with zero
        # plaintext released — the structural no-oracle property.
        _unlink_quiet(dest_path)
        raise ArtifactDecryptError() from None


def _read_chunk(src) -> bytes | None:
    """Read one length-prefixed ciphertext chunk.

    Returns the ciphertext+tag bytes, or ``None`` at a clean end-of-stream (the
    4-byte length prefix is absent). A partial length prefix, a length over the
    sane ceiling, or a short ciphertext read are all corruption -> raise (the
    caller maps it to the identical :class:`ArtifactDecryptError`).
    """
    raw_len = src.read(4)
    if raw_len == b"":
        return None
    if len(raw_len) != 4:
        raise ArtifactDecryptError()
    (ct_len,) = struct.unpack(">I", raw_len)
    if ct_len <= 0 or ct_len > _MAX_CT_CHUNK:
        raise ArtifactDecryptError()
    ct = src.read(ct_len)
    if len(ct) != ct_len:
        raise ArtifactDecryptError()
    return ct


def _unlink_quiet(path: Path) -> None:
    """Best-effort unlink of a partial output; never raises."""
    try:
        if Path(path).exists():
            Path(path).unlink()
    except OSError as exc:
        logger.warning("[ARTIFACT-CRYPTO] Could not remove partial output %s: %s",
                       path, exc)
