"""
Credential encryption for cloud storage targets.
Uses Fernet symmetric encryption with auto-generated key.
"""
import json
import logging
from pathlib import Path

from cryptography.fernet import Fernet

from config import CONFIG_DIR

logger = logging.getLogger(__name__)

KEY_FILE = CONFIG_DIR / ".export_key"

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = _load_or_generate_key()
    _fernet = Fernet(key)
    return _fernet


def _load_or_generate_key() -> bytes:
    """Load the encryption key from file, or generate a new one."""
    if KEY_FILE.exists():
        key = KEY_FILE.read_bytes().strip()
        logger.debug("[CRYPTO] Loaded encryption key from %s", KEY_FILE)
        return key

    key = Fernet.generate_key()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key)
    # Restrict permissions to owner only
    KEY_FILE.chmod(0o600)
    logger.info("[CRYPTO] Generated new encryption key at %s", KEY_FILE)
    return key


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


def decrypt_credentials(encrypted: str) -> dict:
    """Decrypt an encrypted credentials string back to a dict.

    Args:
        encrypted: Encrypted string from encrypt_credentials().

    Returns:
        Decrypted credentials dict.
    """
    f = _get_fernet()
    plaintext = f.decrypt(encrypted.encode("utf-8"))
    return json.loads(plaintext.decode("utf-8"))


def reset_key_cache() -> None:
    """Reset the cached Fernet instance (for testing)."""
    global _fernet
    _fernet = None
