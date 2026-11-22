"""Anim Blueprint generated structs from cooked UE5 packages (data only, unencrypted only).

UE5 Anim Blueprints compile two per-asset script structs, `AnimBlueprintGeneratedConstantData`
and `AnimBlueprintGeneratedMutableData`, whose fields are numbered node properties
(`__FloatProperty_12`, `AnimSequenceBase_543`, ...). They are not in the executable: each Anim
Blueprint package holds its own copy as an export of class `AnimBlueprintGeneratedStruct`, and the
runtime keeps one record per loaded copy. A saved runtime mapping keeps several records with the
same name, so a reader that keys by name sees the copy written last.

This module reads those exports from IoStore (zen) packages and from .pak packages (.uasset +
.uexp). What it reads, per export: the struct header (super struct, child fields) and the
serialized field list (type name, field name, flags, array size, then the per-type references:
struct, enum, inner/key/value fields). Object references are resolved through the package import
map and the global script object table. Encrypted containers and entries are read only with the
user's own AES key, otherwise excluded and reported. No program code is read.

The serialized layout was learned from the engine source (5.1) and checked on cooked data of
5.0-5.7: every export parsed here must end exactly at its cooked size, otherwise it is dropped.
"""
import os, struct

from .name_batch import read_name_batch

TARGETS = ("AnimBlueprintGeneratedConstantData", "AnimBlueprintGeneratedMutableData")
STRUCT_CLASSES = ("AnimBlueprintGeneratedStruct", "ScriptStruct")
EXPORT_BUNDLE_DATA = 1
SCRIPT_OBJECTS = 5
CONTAINER_HEADER = 6
ANIM_CLASS = "AnimBlueprintGeneratedClass"
NULL_INDEX = (1 << 64) - 1
PKG_UNVERSIONED_PROPERTIES = 0x2000

# field type name -> usmap type (None: needs per-type handling below)
_SIMPLE = {
    "Int8Property": "Int8Property", "Int16Property": "Int16Property", "IntProperty": "IntProperty",
    "Int64Property": "Int64Property", "UInt16Property": "UInt16Property", "UInt32Property": "UInt32Property",
    "UInt64Property": "UInt64Property", "FloatProperty": "FloatProperty", "DoubleProperty": "DoubleProperty",
    "NameProperty": "NameProperty", "StrProperty": "StrProperty", "TextProperty": "TextProperty",
    "Utf8StrProperty": "Utf8StrProperty", "AnsiStrProperty": "AnsiStrProperty",
    "LargeWorldCoordinatesRealProperty": "DoubleProperty",
}
_ONE_REF = {  # one object reference after the common part
    "ObjectProperty": "ObjectProperty", "ObjectPtrProperty": "ObjectProperty",
    "WeakObjectProperty": "WeakObjectProperty", "LazyObjectProperty": "LazyObjectProperty",
    "SoftObjectProperty": "SoftObjectProperty", "InterfaceProperty": "InterfaceProperty",
    "DelegateProperty": "DelegateProperty", "MulticastDelegateProperty": "MulticastDelegateProperty",
    "MulticastInlineDelegateProperty": "MulticastDelegateProperty",
    "MulticastSparseDelegateProperty": "MulticastDelegateProperty",
}
_TWO_REFS = {"ClassProperty": "ObjectProperty", "ClassPtrProperty": "ObjectProperty",
             "SoftClassProperty": "SoftObjectProperty"}


class Unreadable(Exception):
    pass


class _Cursor:
    def __init__(self, b, p, end):
        self.b, self.p, self.end = b, p, end

    def take(self, fmt):
        n = struct.calcsize(fmt)
        if self.p + n > self.end: raise Unreadable("past export end")
        v = struct.unpack_from(fmt, self.b, self.p); self.p += n
        return v if len(v) > 1 else v[0]


def _skip_unversioned_header(c):
    """Skips an unversioned property header that announces no values (the struct object's own
    properties). Returns False when values follow (not handled)."""
    values = 0; zeroes = False
    while True:
        frag = c.take("<H")
        values += frag >> 9; zeroes |= bool(frag & 0x80)
        if frag & 0x100: break
    if values and zeroes:
        width = 1 if values <= 8 else 2 if values <= 16 else 4 * ((values + 31) // 32)
        mask = int.from_bytes(c.b[c.p:c.p + width], "little"); c.p += width
        if bin(mask).count("1") == values: values = 0  # every announced value is zero
    return values == 0


class _Fields:
    """Field list reader for one export. `name(i, n)` gives a package name; `ref(i)` resolves a
    package index to an object name."""

    def __init__(self, c, name, ref):
        self.c, self.name, self.ref = c, name, ref

    def fname(self):
        i, n = self.c.take("<II")
        return self.name(i, n)

    def field(self, typ):
        c = self.c
        nm = self.fname(); c.take("<I")                  # name, object flags
        dim, _size, _flags, _rep = c.take("<iiQH")
        self.fname(); c.take("<B")                       # rep-notify function, replication condition
        if not 0 < dim < 4096: raise Unreadable("array size")
        if typ in _SIMPLE: t = {"t": _SIMPLE[typ]}
        elif typ in _ONE_REF: c.take("<i"); t = {"t": _ONE_REF[typ]}
        elif typ in _TWO_REFS: c.take("<ii"); t = {"t": _TWO_REFS[typ]}
        elif typ == "BoolProperty": c.take("<6B"); t = {"t": "BoolProperty"}
        elif typ == "StructProperty": t = {"t": "StructProperty", "struct": self.ref(c.take("<i"))}
        elif typ == "ByteProperty":
            e = c.take("<i")
            t = {"t": "EnumProperty", "inner": {"t": "ByteProperty"}, "enum": self.ref(e)} if e else {"t": "ByteProperty"}
        elif typ == "EnumProperty":
            e = self.ref(c.take("<i")); inner = self.single()
            t = {"t": "EnumProperty", "inner": inner or {"t": "ByteProperty"}, "enum": e}
        elif typ in ("ArrayProperty", "SetProperty", "OptionalProperty"):
            t = {"t": typ, "inner": self.single()}
        elif typ == "MapProperty":
            t = {"t": "MapProperty", "key": self.single(), "value": self.single()}
        elif typ == "FieldPathProperty": self.fname(); t = {"t": "FieldPathProperty"}
        else: raise Unreadable("field type " + typ)
        if any(v is None for k, v in t.items() if k != "t"): raise Unreadable("missing inner field")
        return nm, dim, t

    def single(self):
        typ = self.fname()
        if typ == "None": return None
        return self.field(typ)[2]

    def struct_body(self):
        """UStruct + UScriptStruct body after the object header. Returns (super, props)."""
        c = self.c
        sup = self.super_index = c.take("<i")
        n, = (c.take("<i"),)
        if not 0 <= n < 100000: raise Unreadable("children")
        c.p += 4 * n
        count = c.take("<i")
        if not 0 <= count < 100000: raise Unreadable("field count")
        props = []; i = 0
        for _ in range(count):
            nm, dim, t = self.field(self.fname())
            props.append({"i": i, "dim": dim, "name": nm, "type": t}); i += dim
        code, storage = c.take("<ii")
        if code < 0 or storage < 0: raise Unreadable("script size")
        c.p += storage
        c.take("<I")                                     # struct flags
        if c.p != c.end: raise Unreadable("size mismatch")
        return {"super": self.ref(sup) if sup else None, "count": i, "props": props}


def _object_body(b, start, end, name, ref, unversioned=True, meta=None):
    """Struct record of one export. `meta`, when given, receives the super's package index."""
    c = _Cursor(b, start, end)
    if unversioned:
        if not _skip_unversioned_header(c): raise Unreadable("struct object has property values")
    elif name(*c.take("<II")) != "None":               # tagged properties: only the terminator
        raise Unreadable("struct object has property values")
    if c.take("<I"): c.p += 16                          # optional object guid
    f = _Fields(c, name, ref); rec = f.struct_body()
    if meta is not None: meta["super_index"] = f.super_index
    return rec


# ---------------------------------------------------------------- IoStore (zen) packages

def script_objects(global_toc):
    """{global index: object name} from the global script object table."""
    k = next(k for k in range(len(global_toc.ids)) if global_toc.chunk_type(k) == SCRIPT_OBJECTS)
    b = global_toc.read_range(k, 0, global_toc.offlen[k][1])
    names, o = read_name_batch(b, 0)
    n, = struct.unpack_from("<i", b, o); o += 4
    out = {}
    for j in range(n):
        ni, nn, gi = struct.unpack_from("<IIQ", b, o + 32 * j)
        nm = names[ni & 0x3FFFFFFF]
        out[gi] = f"{nm}_{nn - 1}" if nn else nm
    return out


class ZenPackage:
    """Header of one zen package. `new_layout` is the 5.3+ summary (dependency bundles)."""

    def __init__(self, b, new_layout):
        self.b = b
        (self.versioned, self.header_size, ni, nn, self.flags, self.cooked_header_size,
         hashes_off, self.import_off, self.export_off, self.bundle_off) = struct.unpack_from("<IIIIIIiiii", b, 0)
        if self.versioned: raise Unreadable("versioned package")
        # the summary grew over versions (44 bytes up to 5.2, 52 from 5.3, 60 from 5.5); the name
        # batch follows it and ends before the imported export hashes
        for o in ((52, 60, 44) if new_layout else (44, 52, 60)):
            try:
                self.names, end = read_name_batch(b, o)
            except (ValueError, struct.error):
                continue
            if end <= hashes_off: break
        else:
            raise Unreadable("summary layout")
        self.name = self.names[ni & 0x3FFFFFFF] if self.names else ""
        nimp = (self.export_off - self.import_off) // 8
        self.imports = list(struct.unpack_from(f"<{nimp}Q", b, self.import_off))
        nh = max(self.import_off - hashes_off, 0) // 8   # public export hashes of package imports
        self.import_hashes = list(struct.unpack_from(f"<{nh}Q", b, hashes_off))
        self.exports = []
        for p in range(self.export_off, self.bundle_off, 72):
            off, size, oi, on, outer, cls, sup, tmpl, h, flags, filt = struct.unpack_from("<QQIIQQQQQIB", b, p)
            self.exports.append({"offset": off, "size": size, "name": self.fname(oi, on), "outer": outer,
                                 "class": cls, "super": sup, "flags": flags, "filter": filt, "hash": h})
        self.bundle_entries = []
        end, = struct.unpack_from("<i", b, 40)           # graph data (5.0-5.2) or dependency headers (5.3+)
        for p in range(self.bundle_off, end, 8):
            self.bundle_entries.append(struct.unpack_from("<II", b, p))

    def fname(self, i, n):
        i &= 0x3FFFFFFF
        if i >= len(self.names): raise Unreadable("name index")
        s = self.names[i]
        return f"{s}_{n - 1}" if n else s

    def export_ranges(self):
        """Candidate data ranges per export: serialization order of the bundle entries, and the
        cooked offset relative to the cooked header."""
        seq = {}; pos = self.header_size
        for idx, cmd in self.bundle_entries:
            if cmd == 1 and idx < len(self.exports):
                seq[idx] = pos; pos += self.exports[idx]["size"]
        out = {}
        for k, e in enumerate(self.exports):
            cands = []
            if k in seq: cands.append(seq[k])
            for alt in (self.header_size + e["offset"] - self.cooked_header_size, self.header_size + e["offset"]):
                if alt not in cands and 0 <= alt: cands.append(alt)
            out[k] = cands
        return out


def public_exports(pkg):
    """{public export hash: export name} of a zen package (what other packages import)."""
    return {e["hash"]: e["name"] for e in pkg.exports if e["hash"]}


def read_zen(b, script, new_layout, imported=None, errors=None, super_packages=None):
    """Anim generated struct records of one zen package: [(owner name, struct name, record)].
    `imported(k)` gives (package name, public exports {hash: name}) of the k-th imported package;
    it resolves references to objects of other packages (a child Anim Blueprint's struct has the
    parent's struct, of the same name, as its super). Struct exports that cannot be read are added
    to `errors`; a super exported by another package is noted in `super_packages[(owner, name)]`."""
    pkg = ZenPackage(b, new_layout)
    if not any(t in pkg.names for t in TARGETS): return []  # the name map, not a fixed-size prefix

    def resolve(idx):
        if idx == NULL_INDEX: return None
        typ = idx >> 62
        if typ == 0: return pkg.exports[idx & ((1 << 62) - 1)]["name"]
        if typ == 1: return script.get(idx)
        if typ == 2 and imported is not None:          # package import: (package, export hash) pair
            k, j = (idx >> 32) & 0x3FFFFFFF, idx & 0xFFFFFFFF
            if j >= len(pkg.import_hashes): return None
            return (imported(k) or (None, {}))[1].get(pkg.import_hashes[j])
        return None

    def ref(i):
        if i < 0:
            if -i - 1 >= len(pkg.imports): raise Unreadable("import index")
            r = resolve(pkg.imports[-i - 1])
        elif i > 0:
            if i - 1 >= len(pkg.exports): raise Unreadable("export index")
            r = pkg.exports[i - 1]["name"]
        else:
            return None
        if r is None: raise Unreadable("unresolved reference")
        return r

    out = []
    ranges = pkg.export_ranges()
    for k, e in enumerate(pkg.exports):
        if e["name"] not in TARGETS or resolve(e["class"]) not in STRUCT_CLASSES: continue
        owner = resolve(e["outer"])
        rec = None; why = []; meta = {}
        for start in ranges[k]:
            try:
                rec = _object_body(b, start, start + e["size"], pkg.fname, ref, meta=meta); break
            except (Unreadable, struct.error, IndexError) as ex:
                why.append(str(ex))
        if rec is not None:
            out.append((owner, e["name"], rec))
            si = meta.get("super_index", 0)
            if super_packages is not None and si < 0 and -si - 1 < len(pkg.imports):
                v = pkg.imports[-si - 1]
                if v != NULL_INDEX and v >> 62 == 2 and imported is not None:
                    super_packages[(owner, e["name"])] = (imported((v >> 32) & 0x3FFFFFFF) or (None,))[0]
        elif errors is not None:
            errors.append((owner, e["name"], "; ".join(why)))
    return out


# ---------------------------------------------------------------- .pak packages (.uasset + .uexp)

def _fstring(b, o):
    n, = struct.unpack_from("<i", b, o); o += 4
    if n < 0: return b[o:o - 2 * n - 2].decode("utf-16-le", "replace"), o - 2 * n
    return b[o:o + max(n - 1, 0)].decode("latin-1"), o + n


class LegacyPackage:
    """Header tables of one UE5 .uasset (+ .uexp) package. Cooked packages are unversioned, so
    the version-dependent summary fields are tried in order and checked against the tables."""

    def __init__(self, b):
        self.b = b
        tag, legacy = struct.unpack_from("<Ii", b, 0)
        if tag != 0x9E2A83C1 or legacy > -8: raise Unreadable("not a UE5 package")
        o = 8 + 4 + 12                                   # legacy UE3, UE4, UE5, licensee versions
        n, = struct.unpack_from("<i", b, o); o += 4 + 20 * n
        self.header_size, = struct.unpack_from("<i", b, o); o += 4
        _, o = _fstring(b, o)
        self.flags, nc, no = struct.unpack_from("<Iii", b, o); o += 12
        self.names = []; p = no
        for _ in range(nc):
            s, p = _fstring(b, p); p += 4; self.names.append(s)
        for soft in (True, False):
            q = o + (8 if soft else 0) + 8               # soft object paths, gatherable text
            ec, eo, ic, io_, _do = struct.unpack_from("<iiiii", b, q)
            if 0 < ec < 100000 and 0 <= ic < 100000 and no < io_ <= eo < self.header_size: break
        else:
            raise Unreadable("summary layout")
        isz = (eo - io_) // ic if ic else 32
        self.imports = []
        for k in range(ic):
            _cp, _cpn, cn, cnn, outer, on, onn = struct.unpack_from("<iiiiiii", b, io_ + isz * k)
            self.imports.append({"class": self.fname(cn, cnn), "outer": outer, "name": self.fname(on, onn)})
        for size in (96, 112, 100, 104, 108, 92, 88, 80):
            ex = self._exports(eo, ec, size)
            if ex: self.exports = ex; break
        else:
            raise Unreadable("export table layout")

    def _exports(self, eo, ec, size):
        out = []
        try:
            for k in range(ec):
                cls, sup, tmpl, outer, on, onn, fl, ssize, soff = struct.unpack_from("<iiiiiiIqq", self.b, eo + size * k)
                out.append({"class": cls, "super": sup, "outer": outer, "name": self.fname(on, onn), "size": ssize,
                            "offset": soff})
        except (struct.error, Unreadable):
            return None
        if not out or out[0]["offset"] != self.header_size: return None
        if any(b2["offset"] != a["offset"] + a["size"] for a, b2 in zip(out, out[1:])): return None
        return out

    def fname(self, i, n):
        if not 0 <= i < len(self.names): raise Unreadable("name index")
        s = self.names[i]
        return f"{s}_{n - 1}" if n else s

    def ref(self, i):
        if i < 0:
            if -i - 1 >= len(self.imports): raise Unreadable("import index")
            return self.imports[-i - 1]["name"]
        if i > 0:
            if i - 1 >= len(self.exports): raise Unreadable("export index")
            return self.exports[i - 1]["name"]
        return None

    def import_package(self, i):
        """("script", object name) or ("package", package name) of an import index (< 0)."""
        k = -i - 1; im = self.imports[k]; seen = 0
        while im["outer"] < 0 and seen < 64:
            im = self.imports[-im["outer"] - 1]; seen += 1
        pkg = im["name"]
        return ("script", self.imports[k]["name"]) if pkg.startswith("/Script/") else ("package", pkg)

    def class_super(self, e):
        """("script", name) or ("package", package name) for a class export's super, else None."""
        return self.import_package(e["super"]) if e["super"] < 0 else None

    def ref_class(self, i):
        if i < 0: return self.imports[-i - 1]["name"]
        if i > 0: return self.exports[i - 1]["name"]
        return None


def read_legacy(b, errors=None, super_packages=None):
    """Anim generated struct records of one .uasset+.uexp package: [(owner, name, record)]."""
    pkg = LegacyPackage(b)
    if not any(t in pkg.names for t in TARGETS): return []  # the name map, not a fixed-size prefix
    out = []
    for e in pkg.exports:
        if e["name"] not in TARGETS or pkg.ref_class(e["class"]) not in STRUCT_CLASSES: continue
        meta = {}
        try:
            rec = _object_body(b, e["offset"], e["offset"] + e["size"], pkg.fname, pkg.ref,
                               bool(pkg.flags & PKG_UNVERSIONED_PROPERTIES), meta)
        except (Unreadable, struct.error, IndexError) as ex:
            if errors is not None: errors.append((pkg.ref(e["outer"]), e["name"], str(ex)))
            continue
        out.append((pkg.ref(e["outer"]), e["name"], rec))
        si = meta.get("super_index", 0)
        if super_packages is not None and si < 0:
            kind, sp = pkg.import_package(si)
            if kind == "package": super_packages[(pkg.ref(e["outer"]), e["name"])] = sp
    return out


# ---------------------------------------------------------------- driver

def game_containers(paks_dir):
    out = []
    for root, _, files in os.walk(paks_dir):
        for f in sorted(files):
            if f.endswith((".utoc", ".pak")): out.append(os.path.join(root, f))
    return sorted(out)


def container_imports(toc):
    """{package id: [imported package ids]} from the container header (5.0-5.2 store entries)."""
    k = next((k for k in range(len(toc.ids)) if toc.chunk_type(k) == CONTAINER_HEADER), None)
    if k is None: return {}
    b = toc.read_range(k, 0, toc.offlen[k][1])
    if b[:4] != b"nCoI": return {}
    version, = struct.unpack_from("<I", b, 4)
    o = 20 if version == 1 else 16                   # version 1 (5.0) also stores a package count
    n, = struct.unpack_from("<i", b, o)
    ids = struct.unpack_from(f"<{n}Q", b, o + 4)
    entries = o + 4 + 8 * n + 4; out = {}
    for j, pid in enumerate(ids):
        p = entries + 24 * j
        ni, rel = struct.unpack_from("<II", b, p + 8)
        out[pid] = list(struct.unpack_from(f"<{ni}Q", b, p + 8 + rel)) if ni else []
    return out


def pak_package_name(entry):
    """Long package name of a .pak entry path (Project/Content/X -> /Game/X, Engine/Content/X ->
    /Engine/X, .../Plugins/P/Content/X -> /P/X)."""
    parts = entry.rsplit(".", 1)[0].split("/")
    if "Content" not in parts: return "/" + "/".join(parts)
    i = len(parts) - 1 - parts[::-1].index("Content")
    if i >= 2 and parts[i - 2] == "Plugins" or "Plugins" in parts[:i]: root = parts[i - 1]
    elif parts[0] == "Engine": root = "Engine"
    else: root = "Game"
    return "/" + "/".join([root] + parts[i + 1:])


def _zen_head(b):
    """(package name, imported package names or None) from a zen package header."""
    hashes_off, = struct.unpack_from("<i", b, 24)
    for o in (44, 52, 60):
        try:
            names, end = read_name_batch(b, o)
        except (ValueError, struct.error):
            continue
        if end <= hashes_off: break
    else:
        raise Unreadable("summary layout")
    ni, = struct.unpack_from("<I", b, 8)
    name = names[ni & 0x3FFFFFFF] if names else ""
    imported = None
    if o >= 52:
        off, = struct.unpack_from("<i", b, 48)
        if 0 < off < len(b):
            try:
                imported, _ = read_name_batch(b, off)
            except (ValueError, struct.error):
                imported = None
    return name, o, imported


CLASS_KINDS = ("BlueprintGeneratedClass", ANIM_CLASS, "WidgetBlueprintGeneratedClass")


def _classes(h, summary, script):
    """(has an Anim Blueprint class, [super of each Blueprint class export]) of a zen package.
    A super is ("script", name) or ("import", imported package position)."""
    try:
        pkg = ZenPackage(h, summary >= 52)
    except (Unreadable, ValueError, struct.error):
        return False, []
    anim = False; supers = []
    for e in pkg.exports:
        kind = script.get(e["class"]) if e["class"] >> 62 == 1 else None
        if kind not in CLASS_KINDS: continue
        anim |= kind == ANIM_CLASS
        t = e["super"] >> 62
        if t == 1: supers.append(("script", script.get(e["super"])))
        elif t == 2: supers.append(("import", (e["super"] >> 32) & 0x3FFFFFFF))
    return anim, supers


class GameScan:
    """Anim generated struct records of a game plus the package graph that decides which copy a
    runtime dump keeps (see choose.py). Records: [(package, owner, struct name, record)]."""

    def __init__(self):
        self.records = []; self.packages = {}; self.excluded = []; self.failed = []; self.referencing = []
        self.anim_classes = set()                       # packages with an Anim Blueprint class
        self.class_supers = {}                          # package -> supers of its Blueprint classes
        self.super_packages = {}                        # (package, owner, struct) -> package of its super

    def add_records(self, package, rows, super_packages=None):
        self.records += [(package,) + r for r in rows]
        for (owner, nm), sp in (super_packages or {}).items():
            self.super_packages[(package, owner, nm)] = sp


def scan(paks_dir, key_provider=None, head_bytes=64 * 1024):
    """Reads every readable container under `paks_dir` (encrypted ones need the user's key). Packages: {name: [imported names]}."""
    from .archives import Pak
    from .iostore_probe import Toc, FLAG_ENCRYPTED
    out = GameScan()
    paths = game_containers(paks_dir)
    utocs = [p for p in paths if p.endswith(".utoc")]
    glob_ = [p for p in utocs if os.path.basename(p) == "global.utoc"]
    script = {}
    if glob_:
        try: script = script_objects(Toc(glob_[0]))
        except (StopIteration, ValueError, struct.error, OSError) as ex:
            script = {}; out.failed.append((glob_[0], "", "script objects: " + type(ex).__name__))
    by_id = {}; name_id = {}; id_deps = {}; raw_supers = {}; exported = {}; pending = []; header_failed = []
    for u in utocs:
        if u in glob_: continue
        try: t = Toc(u, key_provider)
        except (ValueError, struct.error, OSError) as ex:
            out.failed.append((u, "", "table of contents: " + type(ex).__name__)); continue
        out.excluded += [x.row() for x in t.excluded]
        if not t.readable: continue
        try: id_deps.update(container_imports(t))
        except (ValueError, struct.error) as ex: header_failed.append((u, "", "container header: " + type(ex).__name__))
        for k in range(len(t.ids)):
            if t.chunk_type(k) != EXPORT_BUNDLE_DATA: continue
            pid = int.from_bytes(t.ids[k][:8], "little"); ln = t.offlen[k][1]
            try:
                h = t.read_range(k, 0, min(ln, head_bytes))
                hs, = struct.unpack_from("<I", h, 4)
                if hs > len(h) and hs <= ln: h = t.read_range(k, 0, hs)
                name, summary, imported = _zen_head(h)
            except Exception as ex:                    # unreadable package: counted, skipped
                out.failed.append((u, k, "head: " + type(ex).__name__)); continue
            by_id[pid] = name; name_id[name] = pid
            out.packages[name] = imported
            try: exported[name] = public_exports(ZenPackage(h, summary >= 52))
            except (Unreadable, ValueError, struct.error): pass
            if script:
                anim, supers = _classes(h, summary, script)
                if anim: out.anim_classes.add(name)
                if supers: raw_supers[name] = supers
            if any(x.encode() in h for x in TARGETS): pending.append((t, u, k, ln, name, summary))
    if any(v is None for v in out.packages.values()):  # the container header is needed only by 5.0-5.2 packages
        out.failed += header_failed
    for name in list(out.packages):                   # imported package names, in import order
        if out.packages[name] is None:
            out.packages[name] = [by_id.get(d) for d in id_deps.get(name_id.get(name), [])]
    for t, u, k, ln, name, summary in pending:        # bodies after all headers: imports resolve
        imp = out.packages.get(name) or []
        lookup = lambda j, imp=imp: (imp[j], exported.get(imp[j]) or {}) if j < len(imp) and imp[j] else None
        errs = []; sups = {}
        try:
            rows = read_zen(t.read_range(k, 0, ln), script, summary >= 52, lookup, errs, sups)
        except Exception as ex:
            out.failed.append((u, k, "body: " + type(ex).__name__)); continue
        out.failed += [(u, k, f"export {o}.{n}: {w}") for o, n, w in errs]
        if not rows: out.referencing.append(name)     # names the structs, exports none (child class)
        out.add_records(name, rows, sups)
    for name, supers in raw_supers.items():           # imported positions -> package names
        imp = out.packages[name]
        out.class_supers[name] = [s if s[0] == "script" else ("package", imp[s[1]] if s[1] < len(imp) else None)
                                  for s in supers]
    for name in out.packages:                        # unknown (script or other container) ids dropped
        out.packages[name] = [d for d in out.packages[name] if d]
    if not paths and os.path.isdir(paks_dir):          # loose cooked files (no archives)
        for root, _, files in os.walk(paks_dir):
            for f in sorted(files):
                if not f.endswith((".uasset", ".umap")): continue
                full = os.path.join(root, f); x = full.rsplit(".", 1)[0] + ".uexp"
                rel = os.path.relpath(full, paks_dir).replace(os.sep, "/")
                try:
                    with open(full, "rb") as fh: b = fh.read()
                    pkg = LegacyPackage(b)
                    name = pak_package_name(rel)
                    out.packages[name] = [i["name"] for i in pkg.imports if i["class"] == "Package"]
                    if any(pkg.ref_class(e["class"]) == ANIM_CLASS for e in pkg.exports): out.anim_classes.add(name)
                    sup = [pkg.class_super(e) for e in pkg.exports if pkg.ref_class(e["class"]) in CLASS_KINDS]
                    if sup: out.class_supers[name] = [y for y in sup if y]
                    if not any(t.encode() in b for t in TARGETS) or not os.path.isfile(x): continue
                    errs = []; sups = {}
                    with open(x, "rb") as fh: rows = read_legacy(b + fh.read(), errs, sups)
                    out.failed += [(full, "", f"export {o}.{n}: {w}") for o, n, w in errs]
                except Exception as ex:
                    out.failed.append((full, "", type(ex).__name__)); continue
                if not rows: out.referencing.append(name)
                out.add_records(name, rows, sups)
    for pk in (p for p in paths if p.endswith(".pak")):
        try: pak = Pak(pk, key_provider)
        except (ValueError, struct.error, OSError) as ex:
            out.failed.append((pk, "", "archive: " + type(ex).__name__)); continue
        out.excluded += [x.row() for x in pak.excluded]
        for n, e in pak.entries.items():
            if not n.endswith((".uasset", ".umap")) or not pak.readable(n): continue
            x = n.rsplit(".", 1)[0] + ".uexp"
            if x not in pak.entries or not pak.readable(x): continue
            try:
                b = pak.read(n)
                pkg = LegacyPackage(b + b"")
                name = pak_package_name(n)
                out.packages[name] = [i["name"] for i in pkg.imports if i["class"] == "Package"]
                if any(pkg.ref_class(e["class"]) == ANIM_CLASS for e in pkg.exports): out.anim_classes.add(name)
                sup = [pkg.class_super(e) for e in pkg.exports if pkg.ref_class(e["class"]) in CLASS_KINDS]
                if sup: out.class_supers[name] = [x for x in sup if x]
                if not any(t.encode() in b for t in TARGETS): continue
                errs = []; sups = {}
                rows = read_legacy(b + pak.read(x), errs, sups)
                out.failed += [(pk, n, f"export {o}.{nm}: {w}") for o, nm, w in errs]
            except Exception as ex:
                out.failed.append((pk, n, type(ex).__name__)); continue
            if not rows: out.referencing.append(name)
            out.add_records(name, rows, sups)
    return out
