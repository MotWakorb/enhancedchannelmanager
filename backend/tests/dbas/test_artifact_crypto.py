"""Tests for the whole-artifact passphrase-encryption primitive
(``backend/dbas/artifact_crypto.py`` — ADR-012 D12, bead
``enhancedchannelmanager-u81kh``; construction from spike ``0zrse``).

Scope under test (the low-level crypto envelope, no app wiring):

1. Round-trip: encrypt -> decrypt recovers the exact bytes (single chunk,
   multi-chunk, and empty input).
2. Structural no-oracle (checklist 33): wrong passphrase and corrupted artifact
   raise the IDENTICAL exception type + message, and release ZERO plaintext
   (the output file is unlinked on any failure). NOT a wall-clock test.
3. Pre-decrypt header version gate (checklist 30): an unsupported
   ``format_version`` / a non-artifact is rejected reading the cleartext header,
   without a passphrase.
4. Tamper detection (checklist 29): chunk reorder, chunk truncation, and header
   param tampering all fail authentication with no partial plaintext.
5. Min-passphrase enforcement (checklist 29): an 11-char passphrase is rejected
   on encrypt; a short passphrase on decrypt fails identically to a wrong one.
"""

from __future__ import annotations

import struct

import pytest

from dbas import artifact_crypto as ac

GOOD_PASS = "correct horse battery staple"  # >= 12 chars
WRONG_PASS = "incorrect horse battery"


def _write(path, data: bytes):
    path.write_bytes(data)
    return path


# --- round-trip ------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 10, ac._CHUNK_SIZE, ac._CHUNK_SIZE * 2 + 7])
def test_round_trip_recovers_exact_bytes(tmp_path, size):
    plain = _write(tmp_path / "plain.bin", b"\xa5" * size if size else b"")
    # Use deterministic-but-varied content so a chunk swap would actually corrupt.
    payload = bytes((i * 31 + 7) % 256 for i in range(size))
    plain.write_bytes(payload)
    enc = tmp_path / "art.enc"
    dec = tmp_path / "out.bin"

    ac.encrypt_file(plain, GOOD_PASS, enc)
    assert ac.is_encrypted_artifact(enc)
    ac.decrypt_file(enc, GOOD_PASS, dec)

    assert dec.read_bytes() == payload


# --- structural no-oracle --------------------------------------------------

def test_wrong_passphrase_raises_decrypt_error_and_releases_no_plaintext(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"super secret M3U password=hunter2")
    enc = tmp_path / "art.enc"
    dec = tmp_path / "out.bin"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(enc, WRONG_PASS, dec)
    assert not dec.exists()  # zero plaintext released


def test_corrupt_artifact_raises_decrypt_error_and_releases_no_plaintext(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"x" * (ac._CHUNK_SIZE + 100))
    enc = tmp_path / "art.enc"
    dec = tmp_path / "out.bin"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    raw = bytearray(enc.read_bytes())
    raw[-1] ^= 0xFF  # flip a byte in the final ciphertext chunk
    enc.write_bytes(raw)

    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(enc, GOOD_PASS, dec)
    assert not dec.exists()


def test_wrong_passphrase_and_corrupt_artifact_identical_exception(tmp_path):
    """Structural (not wall-clock): same type + same message for both failures."""
    plain = _write(tmp_path / "plain.bin", b"y" * (ac._CHUNK_SIZE + 50))
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    with pytest.raises(ac.ArtifactDecryptError) as wrong_pass:
        ac.decrypt_file(enc, WRONG_PASS, tmp_path / "a.bin")

    raw = bytearray(enc.read_bytes())
    raw[-1] ^= 0xFF
    corrupt = tmp_path / "corrupt.enc"
    corrupt.write_bytes(raw)
    with pytest.raises(ac.ArtifactDecryptError) as corrupted:
        ac.decrypt_file(corrupt, GOOD_PASS, tmp_path / "b.bin")

    assert type(wrong_pass.value) is type(corrupted.value)
    assert str(wrong_pass.value) == str(corrupted.value)


# --- pre-decrypt header version gate ---------------------------------------

def test_read_header_exposes_params_without_passphrase(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"data")
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    header = ac.read_header(enc)  # no passphrase
    assert header["format_version"] == ac.FORMAT_VERSION
    assert header["kdf"]["n"] == ac._SCRYPT_N
    assert header["aead"] == ac._AEAD_ID


def test_read_header_rejects_unsupported_format_version(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"data")
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    # Rewrite the header with a future format_version.
    raw = enc.read_bytes()
    (hlen,) = struct.unpack(">I", raw[8:12])
    import json
    header = json.loads(raw[12:12 + hlen])
    header["format_version"] = ac.FORMAT_VERSION + 1
    new_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    tampered = ac.MAGIC + struct.pack(">I", len(new_header)) + new_header + raw[12 + hlen:]
    bad = _write(tmp_path / "future.enc", tampered)

    with pytest.raises(ac.UnsupportedArtifactFormatError):
        ac.read_header(bad)


def test_read_header_rejects_non_artifact(tmp_path):
    plain = _write(tmp_path / "plain.zip", b"PK\x03\x04 not an encrypted artifact")
    assert not ac.is_encrypted_artifact(plain)
    with pytest.raises(ac.UnsupportedArtifactFormatError):
        ac.read_header(plain)


# --- tamper detection ------------------------------------------------------

def _split_chunks(raw: bytes):
    """Return (prefix_bytes, [chunk_bytes...]) for a built artifact."""
    (hlen,) = struct.unpack(">I", raw[8:12])
    off = 12 + hlen
    prefix = raw[:off]
    chunks = []
    while off < len(raw):
        (clen,) = struct.unpack(">I", raw[off:off + 4])
        chunk = raw[off:off + 4 + clen]
        chunks.append(chunk)
        off += 4 + clen
    return prefix, chunks


def test_chunk_reorder_rejected(tmp_path):
    plain = _write(tmp_path / "plain.bin",
                   bytes((i * 17) % 256 for i in range(ac._CHUNK_SIZE * 3)))
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    prefix, chunks = _split_chunks(enc.read_bytes())
    assert len(chunks) >= 3
    chunks[0], chunks[1] = chunks[1], chunks[0]  # swap two interior chunks
    reordered = _write(tmp_path / "reordered.enc", prefix + b"".join(chunks))

    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(reordered, GOOD_PASS, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


def test_chunk_truncation_rejected(tmp_path):
    plain = _write(tmp_path / "plain.bin",
                   bytes((i * 11) % 256 for i in range(ac._CHUNK_SIZE * 3)))
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    prefix, chunks = _split_chunks(enc.read_bytes())
    assert len(chunks) >= 3
    # Drop the real final chunk: the new last chunk was encrypted is_final=0,
    # so the decryptor (which sees it at EOF -> is_final=1) fails auth.
    truncated = _write(tmp_path / "trunc.enc", prefix + b"".join(chunks[:-1]))

    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(truncated, GOOD_PASS, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


def test_header_param_tampering_rejected(tmp_path):
    """Lowering scrypt N in the cleartext header breaks chunk auth (header in AAD)."""
    plain = _write(tmp_path / "plain.bin", b"z" * 4096)
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)

    raw = enc.read_bytes()
    (hlen,) = struct.unpack(">I", raw[8:12])
    import json
    header = json.loads(raw[12:12 + hlen])
    header["kdf"]["n"] = 1024  # weaken the KDF
    new_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    tampered = _write(
        tmp_path / "weak.enc",
        ac.MAGIC + struct.pack(">I", len(new_header)) + new_header + raw[12 + hlen:],
    )

    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(tampered, GOOD_PASS, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


# --- min passphrase enforcement --------------------------------------------

def test_encrypt_rejects_short_passphrase(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"data")
    with pytest.raises(ac.WeakPassphraseError):
        ac.encrypt_file(plain, "short11char", tmp_path / "art.enc")  # 11 chars
    assert not (tmp_path / "art.enc").exists()


def test_decrypt_short_passphrase_fails_identically_to_wrong(tmp_path):
    plain = _write(tmp_path / "plain.bin", b"data")
    enc = tmp_path / "art.enc"
    ac.encrypt_file(plain, GOOD_PASS, enc)
    # A short guess on the decrypt side must NOT leak (no WeakPassphraseError
    # oracle) — it fails identically to a wrong passphrase.
    with pytest.raises(ac.ArtifactDecryptError):
        ac.decrypt_file(enc, "short", tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()
