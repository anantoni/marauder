"""Enums whose FEnumParams record is only partly constant-initialized.

Unoptimized module builds (for example a DebugGame game module) can leave the scalar tail of the
enum parameter record (object flags, entry count, enum flags, C++ form) to a startup initializer.
The executable's data still holds the outer function, the name and C++ type strings and the
pointer to the constant enumerator array, while the tail reads as zero. The count is recovered
from the enumerator array itself: entries are (name pointer, int64 value) pairs, and an array ends
where the next record's array starts, where a pair no longer names an identifier, or where the
entry qualifier ("EType::") changes.

Properties typed by such an enum reach it through a constructor function. That function is the
one place where this module looks at code: it reads at most WINDOW bytes at the function (after
one jump thunk) and looks for a single RIP-relative address load that points at one of the
recovered records. Nothing else in the code is decoded or interpreted.
"""
import struct

from .genusmap import enum_entries, pick
from .layouts import CLASS, ENUM, PROP, STRUCT
from .scan import Scanner

WINDOW = 64
# REX.W/REX.WR + 8D (lea r64, [rip+disp32]); the ModRM byte has mod=00 and rm=101.
LEA_PREFIXES = (0x48, 0x4C)


def partial_records(im, sc, layout="E5x"):
    """Records with names and an enumerator pointer but an all-zero scalar tail."""
    if ENUM[layout].get("cpp"):
        return []
    complete = sc.enums_raw(layout)
    starts = {im.Q(x["va"] + 0x20) for x in complete if x["entries"]}
    found = []
    for va in sc.cands_enum:
        name, cpp, values = im.ident(im.Q(va + 0x10)), im.ident(im.Q(va + 0x18)), im.Q(va + 0x20)
        if name and cpp and values and im.is_data(values) and im.Q(va + 0x28) == 0:
            found.append((va, name, cpp, values))
    starts.update(values for *_, values in found)
    out = []
    for va, name, cpp, values in found:
        o = im.off(values)
        entries, qualifier = [], None
        for k in range(4096):
            if k and values + 16 * k in starts:
                break
            if o is None or o + 16 * (k + 1) > len(im.d):
                break
            pointer, value = struct.unpack_from("<Qq", im.d, o + 16 * k)
            text = im.ident(pointer)
            if not text:
                break
            head = text.rpartition("::")[0]
            if k == 0:
                qualifier = head
            elif head != qualifier:
                break
            entries.append((text, value))
        # Unqualified entries cannot be bounded by their qualifier; keep them only when the
        # array ends at another record's array or at a non-identifier.
        if entries and (qualifier is None or qualifier in ("", cpp, name)):
            out.append({"va": va, "name": name, "cpp": cpp, "entries": entries, "flags": 0})
    return out


class CodeWindow:
    """Bounded reads of the code section at given addresses (address loads only)."""

    def __init__(self, im):
        self.im = im
        self.lo, self.hi, self.raw = im.pe.sec(".text")
        self.bytes_read = 0
        self.functions = 0

    def read(self, va, size):
        if not self.lo <= va < self.hi:
            return b""
        size = min(size, self.hi - va)
        with open(self.im.pe.path, "rb") as f:
            f.seek(self.raw + va - self.lo)
            data = f.read(size)
        self.bytes_read += len(data)
        return data

    def loaded_addresses(self, fn):
        self.functions += 1
        code = self.read(fn, WINDOW)
        if code[:1] == b"\xE9" and len(code) >= 5:                     # incremental-link thunk
            fn = fn + 5 + struct.unpack_from("<i", code, 1)[0]
            code = self.read(fn, WINDOW)
        out = []
        for p in range(len(code) - 6):
            if code[p] in LEA_PREFIXES and code[p + 1] == 0x8D and code[p + 2] & 0xC7 == 0x05:
                out.append(fn + p + 7 + struct.unpack_from("<i", code, p + 3)[0])
        return out


def _layouts(sc):
    S, _ = pick({s: sc.structs_raw(s) for s in STRUCT})
    C, _ = pick({c: sc.classes_raw(c) for c in CLASS})
    return S, C


def _trees(im, sc):
    """Named struct and class property trees from the reflection tables (UE 5 registration data)."""
    S, C = _layouts(sc)
    structs, classes = sc.structs_raw(S), sc.classes_raw(C)
    arrays = [(x["pa"], x["n"]) for x in structs + classes if x["n"]][:3000]
    best, P = -1, None
    for layout in PROP:
        ok = sum(1 for pa, n in arrays if (lambda p: p and sc.tree(p, layout) is not None)(sc.ptr_array(pa, n)))
        if ok > best:
            best, P = ok, layout
    named = {}
    for x in structs:
        tree = sc.tree(sc.ptr_array(x["pa"], x["n"]), P) if x["n"] else []
        if tree:
            named.setdefault(x["name"], []).append(tree)
    noregs = {x["noreg"]: x for x in classes}
    two, _ = sc.registrations()
    for _, (_, inner, wide) in two:
        x = noregs.get(inner)
        if x and len(wide) > 1 and wide[0] in "AUI" and x["n"]:
            tree = sc.tree(sc.ptr_array(x["pa"], x["n"]), P)
            if tree:
                named.setdefault(wide[1:].removeprefix("DEPRECATED_"), []).append(tree)
    return named


def _fill(ptype, node, names):
    """Set a missing enum reference in a usmap type from the matching property node."""
    changed = 0
    if ptype.get("t") == "EnumProperty" and ptype.get("enum") in (None, "None") and node.get("fn") in names:
        ptype["enum"] = names[node["fn"]]
        changed += 1
    children = node.get("children") or []
    if ptype.get("t") in ("ArrayProperty", "SetProperty", "OptionalProperty") and children:
        changed += _fill(ptype.get("inner", {}), children[0], names)
    if ptype.get("t") == "MapProperty" and len(children) == 2:
        changed += _fill(ptype.get("key", {}), children[0], names)
        changed += _fill(ptype.get("value", {}), children[1], names)
    return changed


def _needs_enum(ptype):
    if ptype.get("t") == "EnumProperty" and ptype.get("enum") in (None, "None"):
        return True
    return any(isinstance(ptype.get(k), dict) and _needs_enum(ptype[k]) for k in ("inner", "key", "value"))


def recover(im, enums, structs, layout=None):
    """Add partly initialized enums and the property references that name them.

    Returns (enums, structs, report). Existing enums and filled references are never changed.
    """
    sc = Scanner(im)
    sc.prepare()
    if layout is None:
        layout, _ = pick({e: sc.enums_raw(e) for e in ENUM if not ENUM[e].get("cpp")})
    records = [x for x in partial_records(im, sc, layout) if x["name"] not in enums]
    report = {"enumLayout": layout, "partialEnumRecords": len(records), "enumsAdded": 0, "referencesFilled": 0,
              "codeFunctions": 0, "codeBytesRead": 0}
    if not records:
        return enums, structs, report
    enums = dict(enums)
    for x in records:
        enums[x["name"]] = enum_entries(x["name"], x["entries"], False)
    report["enumsAdded"] = len(records)
    wanting = {name for name, record in structs.items() if any(_needs_enum(p["type"]) for p in record["props"])}
    if not wanting:
        return enums, structs, report
    by_va = {x["va"]: x["name"] for x in records}
    trees = _trees(im, sc)
    window = CodeWindow(im)
    names = {}

    def visit(node):
        fn = node.get("fn")
        if node.get("kind") in ("Enum", "Byte") and fn and fn not in names:
            hits = {by_va[a] for a in window.loaded_addresses(fn) if a in by_va}
            names[fn] = next(iter(hits)) if len(hits) == 1 else None
        for child in node.get("children") or []:
            visit(child)

    structs = dict(structs)
    for name in sorted(wanting):
        candidates = trees.get(name, [])
        if len(candidates) != 1:
            continue
        nodes = {n["name"].lower(): n for n in candidates[0]}
        record = dict(structs[name], props=[dict(p, type=_copy(p["type"])) for p in structs[name]["props"]])
        for prop in record["props"]:
            node = nodes.get(prop["name"].lower())
            if node and _needs_enum(prop["type"]):
                visit(node)
                report["referencesFilled"] += _fill(prop["type"], node, {k: v for k, v in names.items() if v})
        structs[name] = record
    report["codeFunctions"] = window.functions
    report["codeBytesRead"] = window.bytes_read
    return enums, structs, report


def _copy(ptype):
    return {k: _copy(v) if isinstance(v, dict) else v for k, v in ptype.items()}


def enum_info(im, sc=None, layout=None):
    """Scoping and the bit-flag marker of every enum parameter record in the executable data."""
    if sc is None:
        sc = Scanner(im)
        sc.prepare()
    results = {e: sc.enums_raw(e) for e in ENUM}
    if layout is None:
        layout, _ = pick(results)
    info = {}
    for x in results[layout]:
        if x["entries"]:
            info.setdefault(x["name"], {"qualified": "::" in x["entries"][0][0],
                                        "flags": bool((x.get("flags") or 0) & 1)})
    for x in partial_records(im, sc, layout):
        info.setdefault(x["name"], {"qualified": "::" in x["entries"][0][0], "flags": False})
    return info, layout
