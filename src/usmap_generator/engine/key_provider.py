"""User-supplied AES keys for encrypted archives. OFF by default.

Without a key, every encrypted part of an archive (pak index, pak entries, IoStore container) is
excluded and listed with its reason; nothing is attempted on it. With a key the user supplies for
their own installed copy (`--aes-key` / `--aes-keys-file`), the readers decrypt with it (aes.py).

This package ships no keys and no code that finds, derives, extracts or guesses keys. Keys are
never written to outputs or logs.
"""
import base64, binascii, json, re
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

ZERO_GUID = "0" * 32


class KeyProvider(Protocol):
    """key_for() receives the archive path and the key GUID stored in the archive (32 hex digits,
    or "" for the default key) and returns the 32 key bytes, or None when there is no key for it."""

    def key_for(self, archive: str, key_guid: str) -> Optional[bytes]: ...


class KeyError_(ValueError):
    """A key or key file the user supplied could not be parsed."""


def parse_key(text):
    """32 key bytes from 64 hex digits (optional 0x prefix) or from base64."""
    s = str(text).strip()
    h = s[2:] if s.lower().startswith("0x") else s
    if re.fullmatch(r"[0-9a-fA-F]{64}", h):
        return bytes.fromhex(h)
    try:
        b = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        b = b""
    if len(b) == 32:
        return b
    raise KeyError_("an AES-256 key is 64 hex digits (0x...) or 32 bytes in base64")


def norm_guid(g):
    g = re.sub(r"[^0-9a-fA-F]", "", str(g or "")).lower()
    if g and len(g) != 32:
        raise KeyError_(f"a key GUID is 32 hex digits, got {len(g)}")
    return "" if not g or g == ZERO_GUID else g


@dataclass
class StaticKeys:
    """Keys given on the command line or in a keys file. `default` serves archives whose key GUID
    is zero, and any archive whose GUID has no key of its own."""
    default: Optional[bytes] = None
    by_guid: Dict[str, bytes] = field(default_factory=dict)

    def key_for(self, archive, key_guid):
        g = norm_guid(key_guid)
        return self.by_guid.get(g) or self.default if g else self.default

    def __repr__(self):  # never print key bytes
        return f"StaticKeys(default={'set' if self.default else 'none'}, guids={len(self.by_guid)})"


def load_keys_file(path):
    """Keys file: JSON {"<guid or 'default'>": "<key>"}, or text lines '<guid> <key>' / '<key>'
    (a line with only a key sets the default key). Lines starting with # are ignored."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    keys = StaticKeys()
    pairs = []
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as ex:
            raise KeyError_(f"{path}: not valid JSON ({ex.msg}, line {ex.lineno})") from ex
        pairs = [(k, v) for k, v in data.items()]
    else:
        for n, line in enumerate(text.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line: continue
            parts = re.split(r"[\s:=,]+", line, maxsplit=1)
            pairs.append(("default", parts[0]) if len(parts) == 1 else (parts[0], parts[1]))
    for g, k in pairs:
        try:
            key = parse_key(k)
            guid = "" if str(g).lower() in ("default", "main", "") else norm_guid(g)
        except KeyError_ as ex:
            raise KeyError_(f"{path}: entry '{g}': {ex}") from ex
        if guid: keys.by_guid[guid] = key
        else: keys.default = key
    if not keys.default and not keys.by_guid:
        raise KeyError_(f"{path}: no keys found")
    return keys


@dataclass(frozen=True)
class Exclusion:
    archive: str
    what: str          # "index", "container" or "entries"
    key_guid: str
    reason: str
    entries: int = 0   # number of encrypted entries (for what == "entries")

    def row(self):
        return {"archive": self.archive, "what": self.what, "keyGuid": self.key_guid,
                "reason": self.reason, "entries": self.entries}


NO_PROVIDER = "encrypted: no AES key supplied (default); excluded"
NO_KEY = "encrypted: no AES key supplied for this key GUID; excluded"
WRONG_KEY = "encrypted: the supplied AES key does not open this archive; excluded"


def key_for(archive, key_guid, provider: Optional[KeyProvider] = None):
    """The key for an archive, or None."""
    if provider is None: return None
    return provider.key_for(archive, key_guid) or None


def policy_for(archive, what, key_guid="", provider: Optional[KeyProvider] = None, entries=0):
    """The exclusion row for an encrypted part that will not be read (no key for it)."""
    reason = NO_PROVIDER if provider is None else NO_KEY
    return Exclusion(archive, what, key_guid, reason, entries)
