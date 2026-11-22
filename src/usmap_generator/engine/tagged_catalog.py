"""Type catalog of a UE 4.13-4.24 game from the TAGGED property data of its unencrypted packages.

Sources, all self-describing data in cooked packages (no program code is read):
  - import tables: names of native classes, structs and enums (/Script/... packages);
  - tagged property chains of every export: property name, type name, struct name, enum name,
    container inner/key/value type names, static-array index;
  - nested tagged structs and the inner tag of struct arrays: field names and types of native structs;
  - Blueprint class exports: their super class and the properties they declare, so properties of a
    Blueprint instance can be split into Blueprint-declared ones and native ones.

What tagged data cannot give (stated in the report): properties never serialized (defaults,
transient, editor-only, function parameters), native super classes, the position of a property in
its class, enum numeric values, the underlying type of EnumProperty, struct names inside map/set
values, and the layout of structs that serialize natively (Vector, Color, ...).

usage: python3 -I tagged_catalog.py OUT.usmap REPORT.json ARCHIVE.pak [...]
"""
import collections, json, os, re, struct, sys
from .archives import Pak, UnsupportedCompression
from . import uasset_tags as T
from . import usmap as U

BP_CLASS_KINDS = {"BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass", "AnimBlueprintGeneratedClass"}
USER_TYPES = {"UserDefinedStruct", "UserDefinedEnum"}
GUID_FIELD = re.compile(r"_[0-9A-F]{32}$")


def package_path(entry, mount=""):
    p = (mount.replace("../../../", "") + entry).lstrip("/")
    p = p.rsplit(".", 1)[0]
    parts = p.split("/")
    if "Content" in parts:
        i = parts.index("Content")
        root = parts[i - 1] if i else ""
        if root == "Engine": root = "Engine"
        elif "Plugins" not in parts[:i]: root = "Game"
        return "/" + root + "/" + "/".join(parts[i + 1:])
    return "/" + p


def usmap_type(t, nested=None):
    ty = T.USMAP_KIND.get(t["type"], t["type"])
    if ty == "StructProperty": return {"t": ty, "struct": t.get("struct") or "?"}
    if ty == "ByteProperty" and t.get("enum", "None") != "None":
        return {"t": "EnumProperty", "inner": {"t": "ByteProperty"}, "enum": t["enum"]}
    if ty == "EnumProperty":
        return {"t": ty, "inner": {"t": "ByteProperty"}, "enum": t.get("enum") or "?"}
    if ty in ("ArrayProperty", "SetProperty"):
        inner = T.USMAP_KIND.get(t.get("inner") or "?", t.get("inner") or "?")
        if inner == "StructProperty":
            st = nested and nested.get("inner") and nested["inner"].get("struct")
            return {"t": ty, "inner": {"t": inner, "struct": st or "?"}}
        if inner in ("ByteProperty", "EnumProperty"):
            vals = (nested or {}).get("enum_values") or []
            en = {v.split("::")[0] for v in vals if "::" in v}
            if len(en) == 1: return {"t": ty, "inner": {"t": "EnumProperty", "inner": {"t": "ByteProperty"}, "enum": en.pop()}}
            return {"t": ty, "inner": {"t": inner} if inner == "ByteProperty" else {"t": inner, "inner": {"t": "ByteProperty"}, "enum": "?"}}
        return {"t": ty, "inner": {"t": inner}}
    if ty == "MapProperty":
        def k(n):
            n = T.USMAP_KIND.get(n or "?", n or "?")
            if n == "StructProperty": return {"t": n, "struct": "?"}
            if n == "EnumProperty": return {"t": n, "inner": {"t": "ByteProperty"}, "enum": "?"}
            return {"t": n}
        return {"t": ty, "key": k(t.get("inner")), "value": k(t.get("value"))}
    return {"t": ty}


def known(tt):
    """True when a type has no unknown parts."""
    return "?" not in json.dumps(tt) and all(x in U.TYPES for x in re.findall(r'"t": "(\w+)"', json.dumps(tt)))


class Catalog:
    def __init__(self):
        self.native = {"Class": set(), "ScriptStruct": set(), "Enum": set()}
        self.user = set()
        self.props = collections.defaultdict(dict)      # owner -> name -> {"type", "dim"}
        self.orders = collections.defaultdict(list)     # owner -> list of observed name sequences
        self.enums = collections.defaultdict(list)      # enum -> entries in first-seen order
        self.bp = {}                                    # (pkg, name) -> (super ref, declared names)
        self.instances = []                             # (class ref, chain summary)
        self.stats = collections.Counter()

    def add_prop(self, owner, t, nested):
        tt = usmap_type(t, nested); cur = self.props[owner].get(t["name"])
        dim = t["index"] + 1
        if cur is None or (not known(cur["type"]) and known(tt)):
            self.props[owner][t["name"]] = {"type": tt, "dim": max(dim, cur["dim"] if cur else 1)}
        else:
            cur["dim"] = max(cur["dim"], dim)
        vals = (nested or {}).get("enum_values") or []
        en = tt.get("enum") or tt.get("inner", {}).get("enum")
        if en and en != "?":
            for v in vals:
                if v == "None": continue
                e = v.split("::", 1)[1] if v.startswith(en + "::") else v
                if e not in self.enums[en]: self.enums[en].append(e)

    def add_struct_fields(self, t, nested, depth=0):
        if not nested or depth > 12: return
        st = None; fields = nested.get("fields")
        if t["type"] == "StructProperty": st = t.get("struct")
        elif nested.get("inner"): st = nested["inner"].get("struct")
        if not st or not fields or st in self.user: return
        seq = []
        for ft, fn in fields:
            if GUID_FIELD.search(ft["name"]): return            # Blueprint (user-defined) struct
            self.add_prop(st, ft, fn); self.add_struct_fields(ft, fn, depth + 1)
            if ft["index"] == 0 and ft["name"] not in seq: seq.append(ft["name"])
        self.orders[st].append(seq)

    def add_package(self, pkg, pkgpath, variant):
        self.stats["packages"] += 1
        if pkg.unversioned: self.stats["unversioned_packages"] += 1; return
        for k, im in enumerate(pkg.imports):
            if im["class"] in self.native and (pkg.import_package(k) or "").startswith("/Script/"):
                self.native[im["class"]].add(im["name"])
            if im["class"] in USER_TYPES: self.user.add(im["name"])
        for k, e in enumerate(pkg.exports):
            r = pkg.ref(e["class"]); cls = r[1] if r else None
            if cls in USER_TYPES: self.user.add(e["name"])
            if cls in BP_CLASS_KINDS:
                declared = {x["name"] for x in pkg.exports if x["outer"] == k + 1 and (pkg.ref(x["class"]) or ("", ""))[1].endswith("Property")}
                self.bp[(pkgpath, e["name"])] = (self.classref(pkg, pkgpath, e["super"]), declared)
        for k, ch in T.export_chains(pkg, *variant):
            e = pkg.exports[k]; self.stats["chains"] += 1; self.stats["tags"] += len(ch)
            self.instances.append((self.classref(pkg, pkgpath, e["class"]), ch))
            for t, nested in ch: self.add_struct_fields(t, nested)

    def classref(self, pkg, pkgpath, index):
        r = pkg.ref(index)
        if r is None: return None
        if r[0] == "import":
            if (r[2] or "").startswith("/Script/"): return ("native", r[1])
            return ("bp", (r[2], r[1]))
        return ("bp", (pkgpath, r[1]))

    def resolve(self):
        """Attribute instance properties to the most-derived native class of each instance."""
        for ref, ch in self.instances:
            declared = set(); seen = 0
            while ref and ref[0] == "bp" and seen < 32:
                info = self.bp.get(ref[1])
                if info is None: ref = None; break
                ref, d = info; declared |= d; seen += 1
            if not ref or ref[0] != "native":
                self.stats["instances_unresolved"] += 1; continue
            owner = ref[1]; seq = []
            for t, nested in ch:
                if t["name"] in declared or GUID_FIELD.search(t["name"]): continue
                self.add_prop(owner, t, nested)
                if t["index"] == 0 and t["name"] not in seq: seq.append(t["name"])
            if seq: self.orders[owner].append(seq)
            self.stats["instances_resolved"] += 1
        self.instances = []

    def ordered(self, owner):
        """Merge observed sequences into one order (tags follow declaration order)."""
        names = list(self.props[owner]); after = collections.defaultdict(set); indeg = collections.Counter()
        for seq in self.orders.get(owner, []):
            for a, b in zip(seq, seq[1:]):
                if b not in after[a]: after[a].add(b); indeg[b] += 1
        first = {n: i for i, n in enumerate(names)}; out = []; ready = sorted((n for n in names if not indeg[n]), key=first.get)
        while ready:
            n = ready.pop(0); out.append(n)
            for m in sorted(after[n], key=first.get):
                indeg[m] -= 1
                if not indeg[m]: ready.append(m)
            ready.sort(key=first.get)
        return out + [n for n in names if n not in out]              # cycles keep first-seen order

    def mapping(self):
        types = (self.native["Class"] | self.native["ScriptStruct"] | set(self.props)) - self.user
        structs = {}
        for n in sorted(types):
            props = []; i = 0
            for pn in (self.ordered(n) if n in self.props else []):
                p = self.props[n][pn]
                props.append({"i": i, "dim": p["dim"], "name": pn, "type": p["type"]}); i += p["dim"]
            structs[n] = {"super": None, "count": i, "props": props}
        enums = {}
        for n in sorted((self.native["Enum"] | set(self.enums)) - self.user):
            enums[n] = [(k, e) for k, e in enumerate(self.enums.get(n, []))]   # values are NOT known
        return enums, structs


def build(archives, key_provider=None, log=None):
    cat = Catalog(); excluded = []; variant = None; skipped = collections.Counter(); pending = []
    for a in archives:
        pak = Pak(a, key_provider); excluded += [x.row() for x in pak.excluded]
        names = sorted(n for n in pak.entries if n.endswith((".uasset", ".umap")))
        for n in names:
            if not pak.readable(n): skipped["encrypted"] += 1; continue
            try:
                b = pak.read(n); ux = n.rsplit(".", 1)[0] + ".uexp"
                if ux in pak.entries: b += pak.read(ux)
                pkg = T.Package(b, n)
            except UnsupportedCompression as ex:
                skipped["compression " + str(ex)] += 1; continue
            except (ValueError, IndexError, struct.error, PermissionError):
                skipped["unreadable"] += 1; continue
            pending.append((pkg, package_path(n, getattr(pak, "mount", ""))))
            if variant is None and len(pending) >= 24: variant = T.pick_variant([p for p, _ in pending])
            if variant is not None:
                for p, path in pending: cat.add_package(p, path, variant)
                pending = []
    if pending:
        variant = variant or T.pick_variant([p for p, _ in pending])
        for p, path in pending: cat.add_package(p, path, variant)
    cat.resolve()
    rep = {"variant": {"propertyGuid": variant and variant[0], "setMapInner": variant and variant[1]},
           "stats": dict(cat.stats), "skipped": dict(skipped), "excluded": excluded,
           "nativeNames": {k: len(v) for k, v in cat.native.items()}, "userTypes": len(cat.user)}
    return cat, rep


if __name__ == "__main__":
    out, report, *archives = sys.argv[1:]
    cat, rep = build(archives)
    e, s = cat.mapping(); U.write(out, e, s)
    rep["types"] = len(s); rep["enums"] = len(e); rep["properties"] = sum(len(x["props"]) for x in s.values())
    json.dump(rep, open(report, "w"), indent=1)
    print(json.dumps(rep, indent=1))
