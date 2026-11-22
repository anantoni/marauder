"""Do the packages of an IoStore game (.utoc/.ucas, UE 4.26+ and UE 5) use UNVERSIONED properties?

Unversioned property serialization (PKG_UnversionedProperties, 0x2000) drops property names and
types from cooked data; only then is a usmap needed to read the packages. This probe reads, for
readable containers, the table of contents and the first compression block of each package's
export data chunk, and counts the package flags found in the package header. Encrypted containers
are read only with an AES key the user supplies, otherwise excluded and listed (see key_provider.py).

usage: python3 -I iostore_probe.py [--sample N] FILE.utoc [...]
"""
import collections, json, os, struct, sys, zlib
from .archives import decompress, UnsupportedCompression
from .key_provider import policy_for, key_for, Exclusion, WRONG_KEY
from . import aes

TOC_MAGIC = b"-==--==--==--==-"
FLAG_COMPRESSED, FLAG_ENCRYPTED, FLAG_SIGNED, FLAG_INDEXED = 1, 2, 4, 8
PKG_UNVERSIONED_PROPERTIES = 0x2000
EXPORT_BUNDLE_DATA = 1              # chunk type of a package's header + export data (UE5 numbering)
EXPORT_BUNDLE_DATA_UE4 = 2          # UE 4.26/4.27 numbering (0 Invalid, 1 InstallManifest, 2 ExportBundleData)


def u40(b):
    return int.from_bytes(b[:5], "big")


class Toc:
    def __init__(self, path, key_provider=None):
        self.path = os.fspath(path)
        with open(path, "rb") as f:
            b = f.read()
        self.excluded = []
        if b[:16] != TOC_MAGIC: raise ValueError("not a utoc")
        (self.version,) = struct.unpack_from("<B", b, 16)
        (hsize, n, nblocks, bes, nmeth, mlen, self.block_size, dsize, parts) = struct.unpack_from("<9I", b, 20)
        self.container_id, = struct.unpack_from("<Q", b, 56)
        self.key_guid = b[64:80].hex(); self.flags = b[80]
        o = hsize
        self.ids = [b[o + 12 * k:o + 12 * k + 12] for k in range(n)]; o += 12 * n
        self.offlen = [(u40(b[p:p + 5]), u40(b[p + 5:p + 10])) for p in range(o, o + 10 * n, 10)]
        o += 10 * n
        if self.version >= 4:
            seeds, = struct.unpack_from("<I", b, 84); o += 4 * seeds
        if self.version >= 5:
            nwo, = struct.unpack_from("<I", b, 96); o += 4 * nwo
        self.blocks = []
        for k in range(nblocks):
            e = b[o + 12 * k:o + 12 * k + 12]
            self.blocks.append((int.from_bytes(e[0:5], "little"), int.from_bytes(e[5:8], "little"),
                                int.from_bytes(e[8:11], "little"), e[11]))
        o += 12 * nblocks
        self.methods = ["none"] + [b[o + mlen * k:o + mlen * (k + 1)].split(b"\0")[0].decode("latin-1") for k in range(nmeth)]
        self.partitions = parts or 1
        self.partition_size, = struct.unpack_from("<Q", b, 88)
        self.key = key_for(self.path, self.key_guid, key_provider) if self.flags & FLAG_ENCRYPTED else None
        if self.flags & FLAG_ENCRYPTED and not self.key:
            self.excluded.append(policy_for(path, "container", self.key_guid, key_provider))
        elif self.key and not self._key_works():
            self.key = None
            self.excluded.append(Exclusion(self.path, "container", self.key_guid, WRONG_KEY))

    @property
    def readable(self):
        return not self.flags & FLAG_ENCRYPTED or bool(self.key)

    def _key_works(self):
        """Decrypt and decompress the first compressed block; a wrong key fails to decompress."""
        for bi, (boff, csz, usz, m) in enumerate(self.blocks):
            if not m or self.methods[m].lower() not in ("zlib", "gzip"): continue
            try:
                decompress(self.methods[m], self._block(boff, csz), usz); return True
            except Exception:
                return False
        return True    # nothing verifiable here; a wrong key then shows as unreadable packages

    def _block(self, boff, csz):
        part, boff = divmod(boff, self.partition_size) if self.partitions > 1 and self.partition_size else (0, boff)
        ucas = self.path[:-5] + (f"_s{part}.ucas" if part else ".ucas")
        with open(ucas, "rb") as f:
            f.seek(boff)
            if not self.flags & FLAG_ENCRYPTED: return f.read(csz)
            return aes.decrypt(self.key, f.read(aes.align(csz)))[:csz]

    def chunk_type(self, k):
        return self.ids[k][11]

    def read_head(self, k, nbytes=64):
        """First bytes of chunk k (decompresses only the blocks that cover them)."""
        return self.read_range(k, 0, min(nbytes, self.offlen[k][1]))

    def read_range(self, k, start, size):
        """Read a chunk range (an encrypted container needs the user's AES key)."""
        if not self.readable:
            raise PermissionError("encrypted IoStore container: no AES key supplied; excluded")
        off, length = self.offlen[k]
        if not 0 <= start <= start + size <= length or not self.block_size:
            raise ValueError("invalid IoStore range")
        out = bytearray()
        pos = off + start
        want = size
        while len(out) < want:
            bi = pos // self.block_size; boff, csz, usz, m = self.blocks[bi]
            raw = self._block(boff, csz)
            data = decompress(self.methods[m], raw, usz)
            start = pos - bi * self.block_size
            if len(data) != usz or start >= usz:
                raise ValueError("truncated IoStore block")
            out += data[start:start + want - len(out)]; pos = (bi + 1) * self.block_size
        return bytes(out)


def probe(paths, sample=0, key_provider=None):
    rows = []; excluded = []
    for p in paths:
        t = Toc(p, key_provider); excluded += [x.row() for x in t.excluded]
        row = {"utoc": os.path.basename(p), "version": t.version, "flags": t.flags, "methods": t.methods[1:],
               "chunks": len(t.ids), "encrypted": bool(t.flags & FLAG_ENCRYPTED), "packages": 0,
               "unversioned": 0, "versioned": 0, "unreadable": collections.Counter(), "flagValues": collections.Counter()}
        if not t.readable: rows.append(row); continue
        types = collections.Counter(t.chunk_type(k) for k in range(len(t.ids)))
        bundle = EXPORT_BUNDLE_DATA if types.get(EXPORT_BUNDLE_DATA) else EXPORT_BUNDLE_DATA_UE4
        ks = [k for k in range(len(t.ids)) if t.chunk_type(k) == bundle]
        if sample: ks = ks[:: max(1, len(ks) // sample)][:sample]
        for k in ks:
            try:
                h = t.read_head(k, 32)
            except UnsupportedCompression as ex:
                row["unreadable"][str(ex)] += 1; continue
            except (ValueError, zlib.error, OSError):
                row["unreadable"]["error"] += 1; continue
            flags, = struct.unpack_from("<I", h, 16)       # FPackageSummary / FZenPackageSummary
            row["packages"] += 1; row["flagValues"][hex(flags)] += 1
            row["unversioned" if flags & PKG_UNVERSIONED_PROPERTIES else "versioned"] += 1
        row["unreadable"] = dict(row["unreadable"]); row["flagValues"] = dict(row["flagValues"]); rows.append(row)
    return rows, excluded


if __name__ == "__main__":
    a = sys.argv[1:]; sample = 0
    if a and a[0] == "--sample": sample = int(a[1]); a = a[2:]
    rows, excluded = probe(a, sample)
    print(json.dumps({"containers": rows, "excluded": excluded}, indent=1))
