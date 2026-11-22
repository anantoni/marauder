"""Minimal .usmap reader/writer (versions 0-4; payload uncompressed, zstd or brotli).

python3 -I usmap.py <file> <Struct>...   prints structs for checking.
Reading compressed files uses the `zstd` command (zstd payloads) or the `brotli` module.
"""
import struct, subprocess, sys

TYPES = ["ByteProperty","BoolProperty","IntProperty","FloatProperty","ObjectProperty","NameProperty","DelegateProperty","DoubleProperty","ArrayProperty","StructProperty","StrProperty","TextProperty","InterfaceProperty","MulticastDelegateProperty","WeakObjectProperty","LazyObjectProperty","AssetObjectProperty","SoftObjectProperty","UInt64Property","UInt32Property","UInt16Property","Int64Property","Int16Property","Int8Property","MapProperty","SetProperty","EnumProperty","FieldPathProperty","OptionalProperty","Utf8StrProperty","AnsiStrProperty"]
MAGIC = 0x30C4

class R:
    def __init__(s, b): s.b, s.p = b, 0
    def u(s, f):
        v = struct.unpack_from(f, s.b, s.p); s.p += struct.calcsize(f); return v[0]

def _decompress(method, payload, size):
    if method == 0: return payload
    if method == 3:
        out = subprocess.run(["zstd", "-d", "-c", "-q"], input=payload, capture_output=True, check=True).stdout
    elif method == 2:
        import brotli
        out = brotli.decompress(payload)
    else:
        raise ValueError(f"usmap compression {method} (Oodle) not supported")
    assert len(out) == size, (len(out), size)
    return out

def read(path):
    """Returns (enums, structs): enums {name: [(value, entry)]}, structs {name: {super, count, props}}."""
    b = open(path, "rb").read()
    r = R(b)
    assert r.u("<H") == MAGIC
    ver = r.u("<B"); assert 0 <= ver <= 4, ver
    if ver >= 1 and r.u("<i"):
        r.u("<i"); r.u("<i")
        for _ in range(r.u("<i")): r.p += 20      # FCustomVersion: FGuid + int32
        r.u("<I")
    comp = r.u("<B"); csz = r.u("<I"); dsz = r.u("<I")
    r = R(_decompress(comp, b[r.p:r.p + csz], dsz))
    names = []
    for _ in range(r.u("<I")):
        n = r.u("<H" if ver >= 2 else "<B"); names.append(r.b[r.p:r.p + n].decode("utf-8", "replace")); r.p += n
    enums = {}
    for _ in range(r.u("<I")):
        en = names[r.u("<I")]; cnt = r.u("<H" if ver >= 3 else "<B")
        if ver >= 4: enums[en] = [(r.u("<Q"), names[r.u("<I")]) for _ in range(cnt)]
        else: enums[en] = [(i, names[r.u("<I")]) for i in range(cnt)]
    def typ():
        t = TYPES[r.u("<B")]
        if t == "EnumProperty": return {"t": t, "inner": typ(), "enum": names[r.u("<I")]}
        if t == "StructProperty": return {"t": t, "struct": names[r.u("<I")]}
        if t in ("ArrayProperty", "SetProperty", "OptionalProperty"): return {"t": t, "inner": typ()}
        if t == "MapProperty": return {"t": t, "key": typ(), "value": typ()}
        return {"t": t}
    structs = {}
    for _ in range(r.u("<I")):
        nm = names[r.u("<I")]; si = r.u("<I"); sup = None if si == 0xFFFFFFFF else names[si]
        pc = r.u("<H"); sc = r.u("<H")
        props = []
        for _ in range(sc):
            idx = r.u("<H"); ad = r.u("<B"); pn = names[r.u("<I")]
            props.append({"i": idx, "dim": ad, "name": pn, "type": typ()})
        structs[nm] = {"super": sup, "count": pc, "props": props}
    return enums, structs

def write(path, enums, structs):
    """Writes usmap v4 (ExplicitEnumValues), uncompressed, no package versioning."""
    names = []; nidx = {}
    def N(s):
        if s not in nidx: nidx[s] = len(names); names.append(s)
        return nidx[s]
    def wtype(t):
        b = bytes([TYPES.index(t["t"])])
        if t["t"] == "EnumProperty": b += wtype(t["inner"]) + struct.pack("<I", N(t["enum"]))
        elif t["t"] == "StructProperty": b += struct.pack("<I", N(t["struct"]))
        elif t["t"] in ("ArrayProperty", "SetProperty", "OptionalProperty"): b += wtype(t["inner"])
        elif t["t"] == "MapProperty": b += wtype(t["key"]) + wtype(t["value"])
        return b
    eb = bytearray(struct.pack("<I", len(enums)))
    for k, ents in enums.items():
        eb += struct.pack("<IH", N(k), len(ents))
        for v, nm in ents: eb += struct.pack("<QI", v & ((1 << 64) - 1), N(nm))
    sb = bytearray(struct.pack("<I", len(structs)))
    for k, st in structs.items():
        sb += struct.pack("<IIHH", N(k), 0xFFFFFFFF if st["super"] is None else N(st["super"]), st["count"], len(st["props"]))
        for pr in st["props"]: sb += struct.pack("<HBI", pr["i"], pr["dim"], N(pr["name"])) + wtype(pr["type"])
    nb = bytearray(struct.pack("<I", len(names)))
    for s in names:
        e = s.encode(); nb += struct.pack("<H", len(e)) + e
    data = bytes(nb + eb + sb)
    with open(path, "wb") as f:
        f.write(struct.pack("<HBiBII", MAGIC, 4, 0, 0, len(data), len(data)) + data)

def fmt(t):
    if t["t"] == "StructProperty": return f"Struct<{t['struct']}>"
    if t["t"] == "EnumProperty": return f"Enum<{t['enum']}>"
    if t["t"] in ("ArrayProperty", "SetProperty", "OptionalProperty"): return f"{t['t'][:-8]}<{fmt(t['inner'])}>"
    if t["t"] == "MapProperty": return f"Map<{fmt(t['key'])},{fmt(t['value'])}>"
    return t["t"][:-8]

if __name__ == "__main__":
    enums, structs = read(sys.argv[1])
    for s in sys.argv[2:]:
        if s in enums: print(f"== enum {s}", enums[s]); continue
        st = structs[s]
        print(f"== {s} : {st['super']} count={st['count']}")
        for p in st["props"]: print(f"  {p['i']:3} {p['name']} {fmt(p['type'])}" + (f" [{p['dim']}]" if p['dim'] > 1 else ""))
