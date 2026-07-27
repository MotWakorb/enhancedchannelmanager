"""
Credential encryption for cloud storage targets.
Uses Fernet symmetric encryption with auto-generated key.

Key bootstrap integrity (ADR-008 Security Mandatory #1, bead 0i2vt.2):
On every key load the file mode is verified to be 0600 and *repaired* if it
has drifted (e.g. a filesystem regression left it world-readable). Ownership
is checked too, but ownership is a weak control when the process runs as root
(common in the container) because root can read any file regardless of owner —
so an owner mismatch is only fatal when we are NOT root and cannot fix it.

We NEVER regenerate a key that already exists: regenerating silently orphans
every encrypted CloudStorageTarget.credentials blob (unrecoverable data loss).
Regeneration only happens on first boot when no key file exists yet.

Key creation is atomic and never leaves a world-readable window: the key is
written to a temp file, chmod 0600 is applied *before* the bytes are exposed
under the real path, then os.replace() atomically moves it into place.
"""
import json
import logging
import os
import stat
import tempfile

from cryptography.fernet import Fernet

from config import CONFIG_DIR

logger = logging.getLogger(__name__)

KEY_FILE = CONFIG_DIR / ".export_key"

# Permission bits we care about: the key must be readable/writable by the owner
# only (0600). Any group/other access bit set is a violation we repair.
_REQUIRED_MODE = 0o600
_PERM_MASK = 0o777

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = _load_or_generate_key()
    _fernet = Fernet(key)
    return _fernet


def _verify_key_integrity(key_path) -> None:
    """Verify an *existing* key file's mode and ownership.

    Behaviour on violation (per bead 0i2vt.2 PO decision):
      - Wrong MODE              -> REPAIR to 0600 (chmod). Mode is the real control.
      - Wrong OWNER, fixable    -> ignored when running as root (root reads anything;
                                   ownership is a weak control in a root context).
      - Wrong OWNER, unfixable  -> FAIL-CLOSED (raise) only when we are NOT root and
                                   therefore actually exposed by the mismatch.

    Never regenerates the key — regeneration would orphan live ciphertext.
    """
    st = key_path.stat()
    current_mode = stat.S_IMODE(st.st_mode)

    # --- Mode: repair if drifted ----------------------------------------
    if current_mode != _REQUIRED_MODE:
        logger.warning(
            "[CRYPTO] Key file %s has mode %#o, expected %#o — repairing to 0600 "
            "(key_status=mode_repaired)",
            key_path,
            current_mode,
            _REQUIRED_MODE,
        )
        key_path.chmod(_REQUIRED_MODE)

    # --- Ownership ------------------------------------------------------
    process_uid = os.getuid()
    file_uid = st.st_uid

    if file_uid == process_uid:
        # Owned by us — nothing to do.
        return

    if process_uid == 0:
        # Running as root: root can read the key regardless of owner, so the
        # mismatch does not weaken the key. Mode 0600 is the real control and
        # has already been enforced above. Do NOT thrash / fail-close here.
        logger.info(
            "[CRYPTO] Key file %s owned by uid=%s but process is root (uid=0); "
            "ownership check is advisory in a root context (key_status=verified)",
            key_path,
            file_uid,
        )
        return

    # Non-root process and the key is owned by someone else. We cannot chown it
    # (only root may), and another uid owning a key this process must read means
    # the integrity guarantee is broken. Fail closed rather than proceed with a
    # weakened key — and do NOT regenerate (that would orphan live ciphertext).
    logger.error(
        "[CRYPTO] Key file %s owned by uid=%s but process runs as uid=%s and "
        "cannot take ownership; refusing to proceed with a weakened key "
        "(key_status=fail_closed reason=ownership_unfixable)",
        key_path,
        file_uid,
        process_uid,
    )
    raise RuntimeError(
        f"Encryption key {key_path} has wrong ownership (owner uid={file_uid}, "
        f"process uid={process_uid}) and cannot be repaired. Refusing to start "
        f"with a weakened key. Fix ownership of the key file and restart."
    )


def _atomic_write_key(key: bytes) -> None:
    """Write a freshly generated key atomically with no world-readable window.

    Writes to a temp file in the same directory, chmods it 0600 *before* it is
    visible under the real path, then os.replace() moves it into place. This
    fixes the prior TOCTOU where write_bytes() exposed the key world-readable
    for the brief moment before chmod() ran.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    dir_path = KEY_FILE.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".export_key.", dir=str(dir_path))
    try:
        # mkstemp already creates the file 0600; enforce it explicitly anyway.
        os.fchmod(fd, _REQUIRED_MODE)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        # Atomic move into place. The destination inherits the temp file's
        # 0600 mode, so the key is never visible world-readable.
        os.replace(tmp_name, str(KEY_FILE))
    except Exception:
        # Best-effort cleanup of the temp file on any failure.
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_or_generate_key() -> bytes:
    """Load the encryption key from file, or generate a new one (first boot only).

    On load of an existing key, integrity (mode 0600 + ownership) is verified
    and the mode is repaired if it has drifted. An existing key is NEVER
    regenerated. A new key is generated only when no key file exists.
    """
    if KEY_FILE.exists():
        _verify_key_integrity(KEY_FILE)
        key = KEY_FILE.read_bytes().strip()
        logger.info(
            "[CRYPTO] Loaded encryption key from %s (key_status=verified)", KEY_FILE
        )
        return key

    key = Fernet.generate_key()
    _atomic_write_key(key)
    logger.info(
        "[CRYPTO] Generated new encryption key at %s (key_status=generated reason=first_boot)",
        KEY_FILE,
    )
    return key


def verify_key_integrity_at_startup() -> bool:
    """Probe the encryption key's integrity at container startup (bead m40pn).

    Runs the same load path as first crypto use (_get_fernet ->
    _load_or_generate_key): mode-0600 drift is repaired, a missing key is
    generated atomically (first boot), and an unfixable ownership violation
    surfaces as an unmissable ERROR at every container start instead of at
    the first scheduled backup.

    Failure posture — log-loudly-but-boot: ECM's core is channel management,
    and a backup-subsystem key problem must not take down the whole app, so
    this probe NEVER raises. The lazy path stays fail-closed: on failure no
    Fernet instance is cached, so the next real crypto use re-runs the check
    and raises exactly as before.

    Returns:
        True if the key loaded (or was generated) cleanly, False on violation.
    """
    try:
        _get_fernet()
        return True
    except Exception as e:
        logger.error(
            "[CRYPTO] STARTUP KEY INTEGRITY PROBE FAILED — cloud-backup "
            "credential encryption is unavailable and actual crypto use will "
            "fail closed until this is fixed: %s (key_status=startup_probe_failed)",
            e,
        )
        return False


def encrypt_credentials(data: dict) -> str:
    """Encrypt a credentials dict to a string.

    Args:
        data: Dict of credential key-value pairs.

    Returns:
        Encrypted string (base64-encoded).
    """
    f = _get_fernet()
    plaintext = json.dumps(data).encode("utf-8")
    return f.encrypt(plaintext).decode("utf-8")


def decrypt_credentials(encrypted: str) -> dict | None:
    """Decrypt an encrypted credentials string back to a dict.

    Fails soft: if the key is broken/garbled or the ciphertext cannot be
    decrypted (wrong key, corrupted blob, malformed token), this returns
    ``None`` instead of raising, so a single bad row can never crash a list
    or read of CloudStorageTargets. Callers must treat ``None`` as
    "credentials unavailable".

    Args:
        encrypted: Encrypted string from encrypt_credentials().

    Returns:
        Decrypted credentials dict, or ``None`` if decryption failed.
    """
    try:
        f = _get_fernet()
        plaintext = f.decrypt(encrypted.encode("utf-8"))
        return json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        logger.warning(
            "[CRYPTO] Failed to decrypt credentials (fail-soft, returning None): %s", e
        )
        return None


def reset_key_cache() -> None:
    """Reset the cached Fernet instance (for testing)."""
    global _fernet
    _fernet = None
