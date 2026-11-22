"""Read the self-describing parts of cooked UE4 packages (.uasset/.uexp, legacy summary format):
the package name table, the import and export tables, and TAGGED property data.

Tagged property data (FPropertyTag chains, UE 4.0-4.24 always, later versions unless the package
has PKG_UnversionedProperties) carries each serialized property's name and type name, plus the
struct name for StructProperty, the enum name for Byte/EnumProperty and the inner/key/value type
names for containers. Nothing here reads program code; encrypted data is never touched.
"""
import struct
from .archives import fstring

PKG_UNVERSIONED_PROPERTIES = 0x2000
PROPERTY_TYPES = {
    "ByteProperty", "BoolProperty", "IntProperty", "FloatProperty", "ObjectProperty", "NameProperty",
    "DelegateProperty", "DoubleProperty", "ArrayProperty", "StructProperty", "StrProperty",
    "TextProperty", "InterfaceProperty", "MulticastDelegateProperty", "WeakObjectProperty",
    "LazyObjectProperty", "AssetObjectProperty", "SoftObjectProperty", "UInt64Property",
    "UInt32Property", "UInt16Property", "Int64Property", "Int16Property", "Int8Property",
    "MapProperty", "SetProperty", "EnumProperty", "ClassProperty", "SoftClassProperty",
    "AssetClassProperty", "MulticastInlineDelegateProperty", "MulticastSparseDelegateProperty",
    "FieldPathProperty",
}
# Tag type names that a reflection dump (usmap) stores under another kind.
USMAP_KIND = {"ClassProperty": "ObjectProperty", "SoftClassProperty": "SoftObjectProperty",
              "AssetClassProperty": "AssetObjectProperty",
              "MulticastInlineDelegateProperty": "MulticastDelegateProperty",
              "MulticastSparseDelegateProperty": "MulticastDelegateProperty"}
EXPORT_LAYOUTS = {  # entry size -> (has TemplateIndex, 64-bit serial size/offset)
    72: (False, False), 76: (True, False), 80: (False, True), 84: (True, True),
    92: (False, False), 96: (True, False), 100: (False, True), 104: (True, True), 108: (True, True),
}


class Package:
    """Header tables of one cooked package. `data` is .uasset bytes followed by .uexp bytes."""

    def __init__(self, data, path=""):
        self.path = path; self.d = data
        magic, self.legacy = struct.unpack_from("<Ii", data, 0)
        if magic != 0x9E2A83C1: raise ValueError("not a package")
        if self.legacy < -7: raise ValueError("UE5 summary: use the IoStore probe")
        o = 8
        if self.legacy != -4: o += 4
        self.ue4, self.licensee = struct.unpack_from("<ii", data, o); o += 8
        if self.legacy <= -2:
            n, = struct.unpack_from("<i", data, o); o += 4 + 20 * n
        self.header_size, = struct.unpack_from("<i", data, o); o += 4
        _, o = fstring(data, o)
        self.flags, nc, no = struct.unpack_from("<Iii", data, o); o += 12
        o += 8  # gatherable text count/offset
        ec, eo, ic, io, do = struct.unpack_from("<iiiii", data, o)
        self.names = []
        p = no
        for _ in range(nc):
            s, p = fstring(data, p); p += 4; self.names.append(s)
        self.imports = []
        for k in range(ic):
            cp, cpn, cn, cnn, outer, on, onn = struct.unpack_from("<iiiiiii", data, io + 28 * k)
            self.imports.append({"classPackage": self.names[cp], "class": self.names[cn], "outer": outer,
                                 "name": self.fname(on, onn)})
        self.exports = []
        size = (do - eo) // ec if ec and do > eo and (do - eo) % ec == 0 else 0
        layouts = [size] if size in EXPORT_LAYOUTS else []
        layouts += [s for s in (104, 108, 100, 96, 92, 84, 80, 76, 72) if s not in layouts]
        for size in layouts:
            ex = self._exports(eo, ec, size)
            if ex is not None: self.exports = ex; self.export_entry_size = size; break
        else:
            raise ValueError("export table layout")

    def _exports(self, eo, ec, size):
        """Export entries for one candidate layout, or None when its serial ranges do not tile the
        export data (first export starts at the header end, each next export follows)."""
        tmpl, wide = EXPORT_LAYOUTS[size]; out = []; p = eo
        try:
            for _ in range(ec):
                cls, sup = struct.unpack_from("<ii", self.d, p); q = p + 8
                if tmpl: q += 4
                outer, on, onn, oflags = struct.unpack_from("<iiiI", self.d, q); q += 16
                if wide: ssize, soff = struct.unpack_from("<qq", self.d, q)
                else: ssize, soff = struct.unpack_from("<ii", self.d, q)
                if not 0 <= on < len(self.names) or ssize < 0: return None
                out.append({"class": cls, "super": sup, "outer": outer, "name": self.fname(on, onn),
                            "flags": oflags, "size": ssize, "offset": soff})
                p += size
        except struct.error:
            return None
        if out:
            if out[0]["offset"] != self.header_size: return None
            for a, b in zip(out, out[1:]):
                if b["offset"] != a["offset"] + a["size"]: return None
        return out

    def fname(self, i, n=0):
        if not 0 <= i < len(self.names): raise IndexError(i)
        s = self.names[i]
        return f"{s}_{n - 1}" if n > 0 else s

    @property
    def unversioned(self):
        return bool(self.flags & PKG_UNVERSIONED_PROPERTIES)

    def import_package(self, k):
        """Top-level package name ("/Script/Engine", "/Game/...") of import k (0-based)."""
        seen = 0
        while self.imports[k]["outer"] < 0 and seen < 64:
            k = -self.imports[k]["outer"] - 1; seen += 1
        return self.imports[k]["name"] if self.imports[k]["class"] == "Package" else None

    def ref(self, index):
        """('import', name, package, class) or ('export', name, k) or None for an object index."""
        if index < 0:
            k = -index - 1; im = self.imports[k]
            return ("import", im["name"], self.import_package(k), im["class"])
        if index > 0:
            return ("export", self.exports[index - 1]["name"], index - 1)
        return None


class TagReader:
    """Walks FPropertyTag chains. Version switches: property GUID byte (UE >= 4.12) and set/map
    inner types (UE >= 4.14). Values are skipped by their size; nested tagged structs and the inner
    tag of struct arrays are followed so their field names and types are collected."""

    def __init__(self, pkg, prop_guid=True, set_map=True):
        self.p = pkg; self.d = pkg.d; self.prop_guid = prop_guid; self.set_map = set_map

    def name(self, o):
        i, n = struct.unpack_from("<ii", self.d, o)
        return self.p.fname(i, n)

    def tag(self, o, end):
        """One tag at o: (tag dict, offset of its value) or (None, offset after None) or raises."""
        nm = self.name(o); o += 8
        if nm == "None": return None, o
        ty = self.name(o); o += 8
        if ty not in PROPERTY_TYPES: raise ValueError("type " + ty)
        size, idx = struct.unpack_from("<ii", self.d, o); o += 8
        if size < 0 or idx < 0 or idx > 4096: raise ValueError("size")
        t = {"name": nm, "type": ty, "size": size, "index": idx}
        if ty == "StructProperty":
            t["struct"] = self.name(o); o += 8 + 16
        elif ty == "BoolProperty":
            t["bool"] = self.d[o]; o += 1
        elif ty in ("ByteProperty", "EnumProperty"):
            t["enum"] = self.name(o); o += 8
        elif ty == "ArrayProperty":
            t["inner"] = self.name(o); o += 8
        elif ty in ("SetProperty", "MapProperty") and self.set_map:
            t["inner"] = self.name(o); o += 8
            if ty == "MapProperty": t["value"] = self.name(o); o += 8
        if self.prop_guid:
            g = self.d[o]; o += 1
            if g > 1: raise ValueError("guid flag")
            if g: o += 16
        if o + (0 if ty == "BoolProperty" else size) > end: raise ValueError("overrun")
        return t, o

    def chain(self, o, end, depth=0):
        """Parse a complete chain. Returns (list of (tag, nested) pairs, offset after None)."""
        out = []
        while True:
            if o + 8 > end: raise ValueError("no None")
            t, o = self.tag(o, end)
            if t is None: return out, o
            vend = o + (0 if t["type"] == "BoolProperty" else t["size"])
            out.append((t, self.value(t, o, vend, depth)))
            o = vend

    def value(self, t, o, end, depth):
        """Nested facts from a value: {'fields': chain} for tagged structs, {'inner': tag, 'fields'}
        for struct arrays, {'enum_values': [...]} for enum-valued properties."""
        if depth > 12: return None
        ty = t["type"]
        try:
            if ty == "StructProperty":
                ch, p = self.chain(o, end, depth + 1)
                return {"fields": ch} if p == end else None
            if ty in ("ByteProperty", "EnumProperty") and t.get("enum", "None") != "None" and end - o == 8:
                return {"enum_values": [self.name(o)]}
            if ty == "ArrayProperty":
                n, = struct.unpack_from("<i", self.d, o); p = o + 4
                inner = t.get("inner")
                if inner == "StructProperty" and n > 0:
                    it, p = self.tag(p, end)
                    if not it or it["type"] != "StructProperty": return None
                    fields = []
                    for _ in range(min(n, 64)):
                        try:
                            ch, p = self.chain(p, end, depth + 1); fields.extend(ch)
                        except (ValueError, IndexError, struct.error):
                            break
                    return {"inner": it, "fields": fields}
                if inner in ("ByteProperty", "EnumProperty") and n > 0 and end - p == 8 * n:
                    return {"enum_values": [self.name(p + 8 * k) for k in range(min(n, 256))]}
        except (ValueError, IndexError, struct.error):
            return None
        return None


def export_chains(pkg, prop_guid=True, set_map=True):
    """Yields (export index, chain) for every export whose data starts with a valid tagged chain."""
    r = TagReader(pkg, prop_guid, set_map)
    for k, e in enumerate(pkg.exports):
        o = e["offset"]; end = o + e["size"]
        if e["size"] <= 0 or end > len(pkg.d): continue
        try:
            ch, _ = r.chain(o, end)
        except (ValueError, IndexError, struct.error):
            continue
        yield k, ch


def pick_variant(pkgs):
    """Choose the tag layout (property GUID byte, set/map inner types) that parses the most tags."""
    best = None
    for v in ((True, True), (True, False), (False, False)):
        n = sum(len(ch) for p in pkgs for _, ch in export_chains(p, *v))
        if best is None or n > best[0]: best = (n, v)
    return best[1]
