"""AES-256 decryption for archives encrypted with a key that the USER supplies.

Unreal Engine encrypts pak indexes, pak entries and IoStore container blocks with AES-256 in ECB
mode, in 16-byte units. This module only applies a key it is given. It contains no keys and no
code that finds, derives, extracts or guesses keys.
"""

BLOCK = 16


class MissingBackend(RuntimeError):
    """The optional `cryptography` package is not installed."""


def align(n):
    """Size rounded up to the AES block size (encrypted data is padded to 16 bytes on disk)."""
    return (n + BLOCK - 1) // BLOCK * BLOCK


def decrypt(key, data):
    """Decrypt `data` (a multiple of 16 bytes) with the 32-byte `key`."""
    if len(key) != 32:
        raise ValueError("an AES-256 key is 32 bytes (64 hex digits)")
    if len(data) % BLOCK:
        raise ValueError("encrypted data must be a multiple of 16 bytes")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as ex:  # pragma: no cover - dependency is declared in pyproject.toml
        raise MissingBackend("the 'cryptography' package is required for --aes-key; run: pip install cryptography") from ex
    d = Cipher(algorithms.AES(bytes(key)), modes.ECB()).decryptor()
    return d.update(bytes(data)) + d.finalize()
