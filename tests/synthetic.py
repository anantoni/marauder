"""Synthetic archives for tests. Everything here is built from scratch; no game data is used.

The encrypted fixtures are encrypted by the test itself with a key the test generates, so the key
path is tested without any third-party data or key.
"""
import os, struct, zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAK_MAGIC = 0x5A6F12E1


def make_key():
    """A fresh random AES-256 key for one test run."""
    return os.urandom(32)


def encrypt(key, data):
    """AES-256-ECB, zero-padded to 16 bytes (as the engine pads encrypted data)."""
    data = bytes(data) + b"\0" * (-len(data) % 16)
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def fstr(s):
    b = s.encode() + b"\0"
    return struct.pack("<i", len(b)) + b


def _entry(off, size, usize, method, blocks, encrypted, bsize):
    """Pak v8+ entry record (u32 compression index)."""
    out = struct.pack("<qqqI", off, size, usize, method) + b"\0" * 20
    if method:
        out += struct.pack("<i", len(blocks)) + b"".join(struct.pack("<qq", s, t) for s, t in blocks)
    return out + bytes([1 if encrypted else 0]) + struct.pack("<I", bsize)


def make_pak(path, files, *, version=8, key=None, encrypt_index=False, encrypt_entries=(), compress=(),
             key_guid=b"\0" * 16, block_size=64 * 1024):
    """Write a .pak (version 8 legacy index or 11 with a full directory index).
    files: {path: bytes}; compress: names stored zlib-compressed; encrypt_entries: names encrypted with key."""
    out = bytearray(); recs = {}
    for name, data in files.items():
        enc = name in encrypt_entries
        method = 1 if name in compress else 0
        off = len(out)
        if method:
            chunks = [zlib.compress(data[i:i + block_size]) for i in range(0, max(len(data), 1), block_size)]
            hdr_len = len(_entry(0, 0, 0, 1, [(0, 0)] * len(chunks), enc, 0))
            blocks, p, payload = [], hdr_len, bytearray()
            for c in chunks:
                blocks.append((p, p + len(c)))
                stored = encrypt(key, c) if enc else c
                payload += stored; p += len(stored)
            size = sum(len(c) for c in chunks)
            rec = (off, size, len(data), 1, blocks, enc, min(block_size, len(data)))
            out += _entry(*rec) + payload
        else:
            rec = (off, len(data), len(data), 0, [], enc, 0)
            out += _entry(*rec) + (encrypt(key, data) if enc else data)
        recs[name] = rec
    if version >= 10:
        plain = b"".join(_entry(*r) for r in recs.values())
        dirs = {}
        for k, name in enumerate(recs):
            d, _, f = name.rpartition("/")
            dirs.setdefault("/" + (d + "/" if d else ""), []).append((f, -(k + 1)))
        fdi = struct.pack("<i", len(dirs))
        for d, fl in dirs.items():
            fdi += fstr(d) + struct.pack("<i", len(fl)) + b"".join(fstr(f) + struct.pack("<i", loc) for f, loc in fl)
        if encrypt_index: fdi = encrypt(key, fdi)
        fdi_off = len(out); out += fdi
        idx = fstr("../../../") + struct.pack("<iQ", len(recs), 0) + struct.pack("<i", 0)
        idx += struct.pack("<i", 1) + struct.pack("<qq", fdi_off, len(fdi)) + b"\0" * 20
        idx += struct.pack("<i", 0) + struct.pack("<i", len(recs)) + plain
    else:
        idx = fstr("../../../") + struct.pack("<i", len(recs))
        for name, r in recs.items(): idx += fstr(name) + _entry(*r)
    if encrypt_index: idx = encrypt(key, idx)
    io = len(out); out += idx
    names = b"Zlib".ljust(32, b"\0") + b"\0" * 32 * (4 if version >= 9 else 3)
    out += key_guid + bytes([1 if encrypt_index else 0]) + struct.pack("<IIQQ", PAK_MAGIC, version, io, len(idx))
    out += b"\0" * 20 + (b"\0" if version == 9 else b"") + names
    with open(path, "wb") as f: f.write(out)
    return path


def make_utoc(dirpath, chunk, *, key=None, chunk_type=1, name="g"):
    """One-chunk IoStore container (.utoc + .ucas), zlib-compressed, optionally encrypted."""
    FLAG_COMPRESSED, FLAG_ENCRYPTED = 1, 2
    comp = zlib.compress(chunk)
    stored = encrypt(key, comp) if key else comp
    head = bytearray(144); head[:16] = b"-==--==--==--==-"; head[16] = 3
    struct.pack_into("<9I", head, 20, 144, 1, 1, 12, 1, 32, 65536, 0, 1)
    head[80] = FLAG_COMPRESSED | (FLAG_ENCRYPTED if key else 0)
    ids = b"\0" * 11 + bytes([chunk_type])
    offlen = (0).to_bytes(5, "big") + len(chunk).to_bytes(5, "big")
    block = (0).to_bytes(5, "little") + len(comp).to_bytes(3, "little") + len(chunk).to_bytes(3, "little") + b"\1"
    methods = b"Zlib".ljust(32, b"\0")
    p = os.path.join(dirpath, name + ".utoc")
    with open(p, "wb") as f: f.write(bytes(head) + ids + offlen + block + methods)
    with open(os.path.join(dirpath, name + ".ucas"), "wb") as f: f.write(stored)
    return p
