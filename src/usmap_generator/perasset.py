"""Per-asset mapping: every Anim Blueprint generated struct copy, keyed by the package that exports it.

A .usmap holds one record per name. UE5 Anim Blueprints each compile their own
AnimBlueprintGeneratedConstantData / AnimBlueprintGeneratedMutableData struct, so a game has many
different structs with the same name. The global map cannot hold them all; this file does.

Two encodings of the same content:

JSON (<name>.perasset.json)
  {"format": "usmap-per-asset", "version": 1, "engineVersion": "5.3",
   "packages": [{"package": "/Game/Characters/ABP_Hero",
                 "structs": [{"owner": "ABP_Hero_C", "name": "AnimBlueprintGeneratedConstantData",
                              "super": "AnimBlueprintConstantData", "superPackage": null,
                              "propertyCount": 3,
                              "properties": [{"index": 0, "arrayDim": 1, "name": "X",
                                              "type": {"kind": "IntProperty"}}]}]}]}
  `superPackage` names the package that exports the super when it is another Anim Blueprint's
  struct of the same name (a child Anim Blueprint); look the super up in that package's entry.

Binary (<name>.perasset.bin), little-endian, struct records in .usmap version 4 encoding:
  magic "UPAM" | u8 version (1) | u8 compression (0 = none) | u16 reserved (0) | u32 payload size
  payload:
    names:    u32 count, then per name: u16 byte length + UTF-8 bytes
    packages: u32 count, then per package: u32 package name index, u32 struct count, then per struct:
              u32 owner name index, u32 super package name index (0xFFFFFFFF = none),
              then the .usmap struct record: u32 name index, u32 super name index (0xFFFFFFFF = none),
              u16 property count, u16 serialized property count, and per property:
              u16 index, u8 array dim, u32 name index, property type tree (as in .usmap).
Name indexes refer to the names table. Lookup rule for name-only readers: when a struct is looked up
by name while reading package P, try per_asset[P][name] first, then the global .usmap.
"""
import json, struct
from .engine.usmap import TYPES

MAGIC = b"UPAM"
VERSION = 1
NONE = 0xFFFFFFFF


def records_by_package(scan):
    """[(package, [(owner, name, super, super_package, record)])] from an anim_structs.GameScan, sorted."""
    out = {}
    for pkg, owner, name, rec in scan.records:
        sp = scan.super_packages.get((pkg, owner, name))
        out.setdefault(pkg, []).append((owner, name, rec["super"], sp, rec))
    return sorted(out.items())


def _type_json(t):
    o = {"kind": t["t"]}
    if "struct" in t: o["struct"] = t["struct"]
    if "enum" in t: o["enum"] = t["enum"]
    if "inner" in t: o["inner"] = _type_json(t["inner"])
    if "key" in t: o["key"] = _type_json(t["key"]); o["value"] = _type_json(t["value"])
    return o


def _type_from_json(o):
    t = {"t": o["kind"]}
    if o["kind"] == "EnumProperty": t["inner"] = _type_from_json(o["inner"]); t["enum"] = o["enum"]
    elif o["kind"] == "StructProperty": t["struct"] = o["struct"]
    elif "inner" in o: t["inner"] = _type_from_json(o["inner"])
    elif o["kind"] == "MapProperty": t["key"] = _type_from_json(o["key"]); t["value"] = _type_from_json(o["value"])
    return t


def to_json(packages, engine_version=None):
    return {"format": "usmap-per-asset", "version": VERSION, "engineVersion": engine_version,
            "packages": [{"package": pkg, "structs": [
                {"owner": owner, "name": name, "super": sup, "superPackage": sp, "propertyCount": rec["count"],
                 "properties": [{"index": p["i"], "arrayDim": p["dim"], "name": p["name"], "type": _type_json(p["type"])}
                                for p in rec["props"]]}
                for owner, name, sup, sp, rec in rows]} for pkg, rows in packages]}


def from_json(doc):
    if doc.get("format") != "usmap-per-asset": raise ValueError("not a per-asset mapping")
    return [(p["package"], [(s["owner"], s["name"], s["super"], s["superPackage"],
                             {"super": s["super"], "count": s["propertyCount"],
                              "props": [{"i": q["index"], "dim": q["arrayDim"], "name": q["name"],
                                         "type": _type_from_json(q["type"])} for q in s["properties"]]})
                            for s in p["structs"]]) for p in doc["packages"]]


def to_binary(packages):
    names, idx = [], {}

    def N(s):
        if s is None: return NONE
        if s not in idx: idx[s] = len(names); names.append(s)
        return idx[s]

    def wtype(t):
        b = bytes([TYPES.index(t["t"])])
        if t["t"] == "EnumProperty": b += wtype(t["inner"]) + struct.pack("<I", N(t["enum"]))
        elif t["t"] == "StructProperty": b += struct.pack("<I", N(t["struct"]))
        elif t["t"] in ("ArrayProperty", "SetProperty", "OptionalProperty"): b += wtype(t["inner"])
        elif t["t"] == "MapProperty": b += wtype(t["key"]) + wtype(t["value"])
        return b

    body = bytearray(struct.pack("<I", len(packages)))
    for pkg, rows in packages:
        body += struct.pack("<II", N(pkg), len(rows))
        for owner, name, sup, sp, rec in rows:
            body += struct.pack("<II", N(owner), N(sp))
            body += struct.pack("<IIHH", N(name), N(sup), rec["count"], len(rec["props"]))
            for p in rec["props"]:
                body += struct.pack("<HBI", p["i"], p["dim"], N(p["name"])) + wtype(p["type"])
    nb = bytearray(struct.pack("<I", len(names)))
    for s in names:
        e = s.encode("utf-8"); nb += struct.pack("<H", len(e)) + e
    payload = bytes(nb + body)
    return MAGIC + struct.pack("<BBHI", VERSION, 0, 0, len(payload)) + payload


def from_binary(data):
    if data[:4] != MAGIC: raise ValueError("not a per-asset mapping (bad magic)")
    ver, comp, _, size = struct.unpack_from("<BBHI", data, 4)
    if ver != VERSION or comp != 0: raise ValueError(f"unsupported per-asset mapping version {ver} / compression {comp}")
    b = data[12:12 + size]; o = 0

    def u(f):
        nonlocal o
        v = struct.unpack_from(f, b, o); o += struct.calcsize(f); return v[0]

    names = []
    for _ in range(u("<I")):
        n = u("<H"); names.append(b[o:o + n].decode("utf-8")); o += n
    nm = lambda i: None if i == NONE else names[i]

    def typ():
        t = TYPES[u("<B")]
        if t == "EnumProperty": inner = typ(); return {"t": t, "inner": inner, "enum": nm(u("<I"))}
        if t == "StructProperty": return {"t": t, "struct": nm(u("<I"))}
        if t in ("ArrayProperty", "SetProperty", "OptionalProperty"): return {"t": t, "inner": typ()}
        if t == "MapProperty": k = typ(); return {"t": t, "key": k, "value": typ()}
        return {"t": t}

    out = []
    for _ in range(u("<I")):
        pkg = nm(u("<I")); rows = []
        for _ in range(u("<I")):
            owner = nm(u("<I")); sp = nm(u("<I"))
            name = nm(u("<I")); sup = nm(u("<I")); count = u("<H"); n = u("<H")
            props = []
            for _ in range(n):
                i = u("<H"); dim = u("<B"); pn = nm(u("<I"))
                props.append({"i": i, "dim": dim, "name": pn, "type": typ()})
            rows.append((owner, name, sup, sp, {"super": sup, "count": count, "props": props}))
        out.append((pkg, rows))
    return out


def write(base_path, packages, engine_version=None):
    """Writes <base>.perasset.json and <base>.perasset.bin; returns both paths."""
    j, b = f"{base_path}.perasset.json", f"{base_path}.perasset.bin"
    with open(j, "w", encoding="utf-8") as f:
        json.dump(to_json(packages, engine_version), f, indent=1, ensure_ascii=False)
    with open(b, "wb") as f:
        f.write(to_binary(packages))
    return j, b
