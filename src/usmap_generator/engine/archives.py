"""Read-only access to the entries of Unreal .pak archives (pak versions 1-12).

Encrypted indexes and entries are read only with an AES key the user supplies; without one they
are excluded and listed (see key_provider.py). Compression:
zlib is built in; Oodle is used only when the caller names an Oodle runtime library
(UE_OODLE_LIB); entries in any other method are skipped and counted.
"""
import ctypes, os, struct, zlib
from .key_provider import policy_for, key_for, Exclusion, WRONG_KEY
from . import aes

PAK_MAGIC = 0x5A6F12E1


class UnsupportedCompression(Exception):
    """Entry compressed with a method this reader does not decode (counted, not read)."""


def fstring(b, o):
    n, = struct.unpack_from("<i", b, o); o += 4
    if n == 0: return "", o
    if n < 0:
        n = -n; s = b[o:o + 2 * n - 2].decode("utf-16-le", "replace"); o += 2 * n
    else:
        s = b[o:o + n - 1].decode("latin-1"); o += n
    return s, o


class _Oodle:
    lib = None

    @classmethod
    def get(cls):
        if cls.lib is None:
            path = os.environ.get("UE_OODLE_LIB")
            cls.lib = False
            if path and os.path.exists(path):
                lib = ctypes.CDLL(path)
                f = lib.OodleLZ_Decompress
                f.restype = ctypes.c_ssize_t
                f.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t] + [ctypes.c_int] * 3 + \
                             [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
                cls.lib = lib
        return cls.lib or None


def decompress(method, raw, size):
    method = (method or "").lower()
    if method in ("", "none"): return raw
    if method == "zlib": return zlib.decompress(raw)
    if method == "gzip": return zlib.decompress(raw, 31)
    if method == "oodle":
        lib = _Oodle.get()
        if not lib: raise UnsupportedCompression("oodle")
        out = ctypes.create_string_buffer(size)
        n = lib.OodleLZ_Decompress(raw, len(raw), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if n != size: raise ValueError("oodle size")
        return out.raw
    raise UnsupportedCompression(method)


class Pak:
    """Index of one .pak. `entries` maps path -> entry dict; `excluded` lists encrypted parts."""

    def __init__(self, path, key_provider=None):
        self.path = path; self.f = open(path, "rb"); self.excluded = []; self.entries = {}
        self.f.seek(0, 2); size = self.f.tell()
        self.f.seek(max(0, size - 2048)); tail = self.f.read()
        i = tail.rfind(struct.pack("<I", PAK_MAGIC))
        if i < 0: raise ValueError("not a pak")
        self.version, self.index_offset, self.index_size = struct.unpack_from("<IQQ", tail, i + 4)
        enc_index = self.version >= 4 and tail[i - 1] == 1
        guid = tail[i - 17:i - 1].hex() if self.version >= 7 else ""
        self.key_guid = "" if not guid or set(guid) == {"0"} else guid
        names = tail[i + 44 + (1 if self.version == 9 else 0):]   # v9 stores a frozen-index byte first
        self.methods = ["none"]
        if self.version >= 8:
            for k in range(0, len(names) - 31, 32):
                nm = names[k:k + 32].split(b"\0")[0].decode("latin-1")
                if nm: self.methods.append(nm)
        else:
            self.methods = {0: "none", 1: "zlib", 2: "gzip", 4: "oodle"}
        self.key = key_for(path, self.key_guid, key_provider)
        self.enc_index = enc_index
        if enc_index and not self.key:
            self.excluded.append(policy_for(path, "index", self.key_guid, key_provider))
            return
        self.f.seek(self.index_offset); idx = self.f.read(self.index_size)
        if enc_index:
            try:
                idx = aes.decrypt(self.key, idx[:len(idx) // 16 * 16])
                self.mount, o = fstring(idx, 0)
                if not 0 < len(self.mount) < 1024 or not self.mount.isprintable(): raise ValueError("mount point")
            except (ValueError, struct.error, UnicodeDecodeError):
                self.key = None
                self.excluded.append(Exclusion(path, "index", self.key_guid, WRONG_KEY))
                return
        self.mount, o = fstring(idx, 0)
        if self.version >= 10: self._read_v10(idx, o)
        else:
            # Pak version 8 written by UE 4.22 stores the compression method index in one byte.
            self.method_byte = False
            for self.method_byte in ((False, True) if self.version == 8 else (False,)):
                try:
                    self.entries = self._legacy_index(idx, o); break
                except (struct.error, UnicodeDecodeError):
                    if self.method_byte or self.version != 8: raise
        n_enc = sum(1 for e in self.entries.values() if e["encrypted"])
        if n_enc and not self.key:
            self.excluded.append(policy_for(path, "entries", self.key_guid, key_provider, entries=n_enc))

    def _legacy_index(self, b, o):
        count, = struct.unpack_from("<i", b, o); o += 4; out = {}
        for _ in range(count):
            name, o = fstring(b, o); e, o = self._legacy_entry(b, o); out[name] = e
        pad = 16 if getattr(self, "enc_index", False) else 1      # encrypted indexes are padded to 16 bytes
        if self.version == 8 and not 0 <= len(b) - o < pad: raise struct.error("index size")
        return out

    def _legacy_entry(self, b, o):
        off, size, usize = struct.unpack_from("<qqq", b, o); o += 24
        if getattr(self, "method_byte", False):
            m = b[o]; o += 1
        elif self.version >= 8:
            m, = struct.unpack_from("<I", b, o); o += 4
        else:
            m, = struct.unpack_from("<i", b, o); o += 4
        if self.version <= 1: o += 8
        o += 20
        blocks = []
        if self.version >= 3:
            if m:
                nb, = struct.unpack_from("<i", b, o); o += 4
                blocks = [struct.unpack_from("<qq", b, o + 16 * k) for k in range(nb)]; o += 16 * nb
            flags = b[o]; o += 1
            bsize, = struct.unpack_from("<I", b, o); o += 4
        else:
            flags = 0; bsize = 0
        return {"offset": off, "size": size, "usize": usize, "method": m, "blocks": blocks,
                "encrypted": bool(flags & 1), "bsize": bsize}, o

    def _read_v10(self, idx, o):
        count, seed = struct.unpack_from("<iQ", idx, o); o += 12
        has_ph, = struct.unpack_from("<i", idx, o); o += 4
        if has_ph: o += 36
        has_fd, = struct.unpack_from("<i", idx, o); o += 4
        fdo = fds = 0
        if has_fd: fdo, fds = struct.unpack_from("<qq", idx, o); o += 36
        es, = struct.unpack_from("<i", idx, o); o += 4
        enc = idx[o:o + es]; o += es
        nfiles, = struct.unpack_from("<i", idx, o); o += 4
        plain = []
        for _ in range(nfiles):
            e, o = self._legacy_entry(idx, o); plain.append(e)
        if not has_fd: return
        self.f.seek(fdo); fd = self.f.read(fds); p = 0
        if self.enc_index: fd = aes.decrypt(self.key, fd[:len(fd) // 16 * 16])
        nd, = struct.unpack_from("<i", fd, p); p += 4
        for _ in range(nd):
            d, p = fstring(fd, p); nf, = struct.unpack_from("<i", fd, p); p += 4
            for _ in range(nf):
                if self.version >= 12:          # UTF-8 file names without terminator
                    ln, = struct.unpack_from("<i", fd, p); n = fd[p + 4:p + 4 + ln].decode("utf-8", "replace"); p += 4 + ln
                else:
                    n, p = fstring(fd, p)
                loc, = struct.unpack_from("<i", fd, p); p += 4
                if loc == -0x80000000: continue
                e = self._encoded(enc, loc) if loc >= 0 else plain[-loc - 1]
                self.entries[(d + n).lstrip("/")] = e

    def _encoded(self, b, o):
        v, = struct.unpack_from("<I", b, o); o += 4
        if v & 0x3F == 0x3F:
            bsize, = struct.unpack_from("<I", b, o); o += 4
        else:
            bsize = (v & 0x3F) << 11
        def rd(small):
            nonlocal o
            if small: x, = struct.unpack_from("<I", b, o); o += 4
            else: x, = struct.unpack_from("<Q", b, o); o += 8
            return x
        m = (v >> 23) & 0x3F; enc = bool((v >> 22) & 1); nb = (v >> 6) & 0xFFFF
        off = rd(v & (1 << 31)); usize = rd(v & (1 << 30)); size = rd(v & (1 << 29)) if m else usize
        blocks = []
        if nb:
            hdr = 8 * 3 + 4 + 20 + 4 + 16 * nb + 1 + 4
            if nb == 1 and not enc: sizes = [size]
            else:
                sizes = list(struct.unpack_from(f"<{nb}I", b, o)); o += 4 * nb
            p = hdr
            for s in sizes:
                blocks.append((p, p + s)); p += (s + 15) & ~15 if enc else s
        return {"offset": off, "size": size, "usize": usize, "method": m, "blocks": blocks,
                "encrypted": enc, "bsize": bsize, "relative": True}

    def method_name(self, m):
        if isinstance(self.methods, dict): return self.methods.get(m, f"legacy{m}")
        return self.methods[m] if m < len(self.methods) else f"index{m}"

    def readable(self, name):
        """False for an encrypted entry when no key was supplied."""
        return not self.entries[name]["encrypted"] or bool(self.key)

    def _raw(self, size, encrypted):
        if not encrypted: return self.f.read(size)
        return aes.decrypt(self.key, self.f.read(aes.align(size)))[:size]

    def read(self, name):
        """Bytes of one entry. Raises PermissionError for an encrypted entry without a key (never read)."""
        e = self.entries[name]
        enc = e["encrypted"]
        if enc and not self.key: raise PermissionError("encrypted entry: no AES key supplied; excluded")
        m = self.method_name(e["method"]) if e["method"] else "none"
        hdr = 8 * 3 + (1 if getattr(self, "method_byte", False) else 4) + (8 if self.version <= 1 else 0) + 20 + \
            ((4 + 16 * len(e["blocks"]) if e["method"] else 0) + 1 + 4 if self.version >= 3 else 0)
        if not e["method"]:
            self.f.seek(e["offset"] + hdr); return self._raw(e["usize"], enc)
        out = bytearray(); rel = e.get("relative") or self.version >= 5
        for k, (s, t) in enumerate(e["blocks"]):
            self.f.seek(e["offset"] + s if rel else s); raw = self._raw(t - s, enc)
            want = min(e["bsize"] or e["usize"], e["usize"] - len(out))
            out += decompress(m, raw, want)
        return bytes(out)
