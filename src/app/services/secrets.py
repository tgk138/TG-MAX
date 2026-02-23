import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def _derive_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary string."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key(settings.SECRET_KEY))
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64-encoded ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt base64-encoded ciphertext and return plaintext."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
