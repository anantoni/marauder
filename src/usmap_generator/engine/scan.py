"""Scan Unreal Engine 4/5 x64 shipping data sections for UHT codegen reflection records.

Reads only .rdata/.data contents. Values that point into the code section are used only as opaque
identifiers (and compared by address); code bytes are never read or decoded.
"""
import struct
import numpy as np
from .pe import PE
from .layouts import PROP, STRUCT, CLASS, ENUM, FUNCTION, GEN, GEN_LEGACY

IDCHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_ :-")

class Image:
    def __init__(self, path):
        self.pe = PE(path)
        self.d = self.pe.d
        tl, th, _ = self.pe.sec(".text")
        self.text = (tl, th)
        self.dsecs = [self.pe.sec(n) for n in (".rdata", ".data") if n in self.pe.secs]
        self.rdata = self.pe.sec(".rdata")
        # Virtual extents (pointer targets may lie in the zero-filled tail of .data, beyond its raw size).
        self.dva = [(self.pe.base + self.pe.secs[n][0], self.pe.base + self.pe.secs[n][0] + self.pe.secs[n][1])
                    for n in (".rdata", ".data") if n in self.pe.secs]
        # u64 views of each data section (section starts are 8-aligned).
        self.arrs = [(lo, ra, np.frombuffer(self.d, dtype="<u8", count=(hi - lo) // 8, offset=ra)) for lo, hi, ra in self.dsecs]
        self._id = {}; self._w = {}

    def off(self, va):
        for lo, hi, ra in self.dsecs:
            if lo <= va < hi: return ra + va - lo
        return None
    def rd(self, va, fmt):
        o = self.off(va)
        if o is None: return None
        try: return struct.unpack_from(fmt, self.d, o)[0]
        except struct.error: return None
    def Q(self, va): return self.rd(va, "<Q")
    def is_text(self, v): return v is not None and self.text[0] <= v < self.text[1]
    def is_data(self, v): return v is not None and any(lo <= v < hi for lo, hi in self.dva)
    def tz(self, v): return v == 0 or self.is_text(v)

    def ident(self, va):
        """ASCII identifier at va (in a data section), else None."""
        if va in self._id: return self._id[va]
        r = None
        o = self.off(va) if va else None
        if o is not None:
            e = self.d.find(b"\0", o, o + 256)
            if e > o:
                try:
                    s = self.d[o:e].decode("ascii")
                    if all(c in IDCHARS for c in s): r = s
                except UnicodeDecodeError: pass
        self._id[va] = r
        return r

    def wident(self, va):
        """UTF-16 identifier (TCHAR) at va, else None."""
        if va in self._w: return self._w[va]
        r = None
        o = self.off(va) if va else None
        if o is not None and o % 2 == 0:
            out = []
            while len(out) < 256:
                c = struct.unpack_from("<H", self.d, o + 2 * len(out))[0]
                if c == 0: break
                ch = chr(c)
                if ch not in IDCHARS or ch in " :-": out = None; break
                out.append(ch)
            if out: r = "".join(out)
        self._w[va] = r
        return r

    def masks(self, a):
        tl, th = self.text
        t = (a >= tl) & (a < th)
        dm = np.zeros(len(a), dtype=bool)
        for lo, hi in self.dva: dm |= (a >= lo) & (a < hi)
        rlo, rhi, _ = self.rdata
        rm = (a >= rlo) & (a < rhi)
        return t, dm, rm

    def candidates(self, pattern):
        """VAs where consecutive qwords match pattern, a list of 'T' text, 't' text-or-0, 'D' data, 'd' data-or-0,
        'R' rdata, 'r' rdata-or-0, '.' anything."""
        out = []
        for lo, ra, a in self.arrs:
            t, dm, rm = self.masks(a)
            z = a == 0
            M = {"T": t, "t": t | z, "D": dm, "d": dm | z, "R": rm, "r": rm | z}
            n = len(a) - len(pattern)
            if n <= 0: continue
            acc = np.ones(n, dtype=bool)
            for k, c in enumerate(pattern):
                if c != ".": acc &= M[c][k:k + n]
            out.extend((lo + 8 * np.nonzero(acc)[0]).tolist())
        return out


class Scanner:
    def __init__(self, img):
        self.img = img
        self._p = {}

    # ---------------- properties ----------------
    def prop(self, va, L):
        key = (va, L)
        if key in self._p: return self._p[key]
        self._p[key] = r = self._prop(va, PROP[L])
        return r

    def _prop(self, va, L):
        im = self.img
        if va % 8: return None
        gen = GEN_LEGACY if L.get("gen") == "legacy" else GEN
        if L.get("type_first"):
            # UE 4.19-4.21: EPropertyClass (one byte, zero padding), then the name.
            g = im.Q(va)
            if g is None or g > 0xFF: return None
            kind = gen.get(g)
            if kind is None: return None
            nm = im.ident(im.Q(va + L["name"]) or 0)
            if not nm: return None
            rep = im.Q(va + L["rep"])
            if rep and not im.ident(rep): return None
            if im.rd(va + 0x14, "<I"): return None          # padding after the 32-bit ObjectFlags
        else:
            nm = im.ident(im.Q(va) or 0)
            if not nm: return None
            rep = im.Q(va + 8)
            if rep and not im.ident(rep): return None
            g = im.rd(va + 0x18, "<B")
            kind = gen.get(g & L.get("kind_mask", 0x3F)) if g is not None else None
            if kind is None: return None
            # The legacy native-bool bit overlaps modern LWCReal. This exclusion belongs
            # only to five-bit layouts: modern upper bits also qualify object properties.
            if L.get('kind_mask')==0x1F and g & 0x20 and kind != 'Bool': return None
            if im.rd(va + 0x19, "<H") or im.rd(va + 0x1B, "<B"): return None
        for a in L["acc"]:
            if not im.tz(im.Q(va + a)): return None
        dim = im.rd(va + L["dim"][0], L["dim"][1])
        if not dim or dim > 4096: return None
        rec = {"name": nm, "kind": kind, "dim": dim, "fn": None, "gen_flags": g}
        if kind != "Bool":
            rec["off"] = im.rd(va + L["off"][0], L["off"][1])
            x1 = im.Q(va + L["x1"])
            if kind in ("Struct", "Enum", "Object", "Interface", "SoftObject", "WeakObject", "LazyObject", "SoftClass",
                        "Delegate", "InlineMulticastDelegate", "SparseMulticastDelegate"):
                if not im.is_text(x1): return None
                rec["fn"] = x1
            elif kind == "Byte":
                rec["fn"] = x1 if im.is_text(x1) else None
        return rec

    READMORE = {"Array": 1, "Set": 1, "Map": 2, "Enum": 1, "Optional": 1}

    def ptr_array(self, va, n):
        im = self.img
        if not (0 < n < 8192) or not im.is_data(va) or va % 8: return None
        o = im.off(va)
        if o is None or o + 8 * n > len(im.d): return None
        arr = list(struct.unpack_from(f"<{n}Q", im.d, o))
        if not all(im.is_data(x) for x in arr): return None
        return arr

    def tree(self, ptrs, L):
        """UHT lists a container's inner properties before the container: rebuild the tree from the end."""
        if ptrs is None: return None
        recs = [self.prop(x, L) for x in ptrs]
        if any(r is None for r in recs): return None
        parent_first = PROP[L].get("parent_first", False)
        if parent_first: recs = recs[::-1]
        i = len(recs) - 1
        out = []
        def build(i):
            r = dict(recs[i]); i -= 1; r["children"] = []
            for _ in range(self.READMORE.get(r["kind"], 0)):
                if i < 0: raise ValueError
                c, i = build(i); r["children"].append(c)
            if r["kind"] == "Enum" and r["children"][0]["kind"] not in (
                "Byte", "Int8", "Int16", "Int", "Int64", "UInt16", "UInt32", "UInt64"):
                raise ValueError
            return r, i
        try:
            while i >= 0:
                r, i = build(i); out.append(r)
        except ValueError:
            return None
        return out[::-1]

    # ---------------- structs ----------------
    def structs_raw(self, S):
        """FStructParams candidates under struct layout S (no property validation)."""
        im = self.img; L = STRUCT[S]
        out = []
        for va in self.cands_struct:
            name = im.ident(im.Q(va + L["name"]))
            if not name: continue
            size = im.rd(va + L["size"][0], L["size"][1]); al = im.rd(va + L["align"][0], L["align"][1])
            n = im.rd(va + L["n"][0], L["n"][1]); pa = im.Q(va + L["props"])
            if al not in (1, 2, 4, 8, 16, 32, 64) or not size or size >= 1 << 24 or size % al: continue
            if not ((n == 0 and pa == 0) or (n and self.ptr_array(pa, n))): continue
            out.append({"va": va, "name": name, "size": size, "align": al, "n": n, "pa": pa,
                        "super_fn": im.Q(va + 8) or None, "ops": im.Q(va + 0x10) or None})
        return out

    def classes_raw(self, C):
        if CLASS[C].get("counts") == "interleaved": return self.classes_raw_interleaved(C)
        im = self.img; out = []
        for va in self.cands_class:
            cfg = im.Q(va + 8)
            if cfg and not im.ident(cfg): continue
            deps, funcs, props, ifaces = (im.Q(va + k) for k in (0x18, 0x20, 0x28, 0x30))
            if C == "C51":
                nd, nf, np_, ni = (im.rd(va + k, "<I") for k in (0x38, 0x3C, 0x40, 0x44))
            else:
                b = im.rd(va + 0x38, "<I")
                if b is None: continue
                nd, nf, np_, ni = b & 0xF, (b >> 4) & 0x7FF, (b >> 15) & 0x7FF, b >> 26
            if None in (nd, nf, np_, ni) or not (1 <= nd < 16 and nf < 4096 and np_ < 4096 and ni < 64): continue
            if (np_ == 0) != (not props) or (nf == 0) != (not funcs) or (ni == 0) != (not ifaces): continue
            o = im.off(deps)
            if o is None: continue
            dl = list(struct.unpack_from(f"<{nd}Q", im.d, o))
            if not all(im.is_text(x) for x in dl): continue
            if np_ and not self.ptr_array(props, np_): continue
            fnames, ffns = [], []
            for k in range(nf):
                ffns.append(im.Q(funcs + 16 * k)); nmv = im.Q(funcs + 16 * k + 8)
                fnames.append(im.ident(nmv) if nmv else None)
            out.append({"va": va, "noreg": im.Q(va), "deps": dl, "pa": props, "n": np_, "funcs": fnames, "ffns": ffns})
        return out

    def classes_raw_interleaved(self, C):
        """UE 4.19-4.21 FClassParams: each array pointer is followed by its own int32 count."""
        im = self.img; L = CLASS[C]; out = []
        for va in im.candidates("TD.d.d"):
            cfg = im.Q(va + L["cfg"])
            if cfg and not im.ident(cfg): continue
            deps, funcs, props, ifaces = (im.Q(va + L[k]) for k in ("deps", "funcs", "props", "ifaces"))
            nd, nf, np_, ni = (im.rd(va + L[k], "<I") for k in ("nd", "nf", "np", "ni"))
            if None in (nd, nf, np_, ni, ifaces) or not (1 <= nd < 16 and nf < 4096 and np_ < 4096 and ni < 64): continue
            if (np_ == 0) != (not props) or (nf == 0) != (not funcs) or (ni == 0) != (not ifaces): continue
            if ifaces and not im.is_data(ifaces): continue
            o = im.off(deps)
            if o is None: continue
            dl = list(struct.unpack_from(f"<{nd}Q", im.d, o))
            if not all(im.is_text(x) for x in dl): continue
            if np_ and not self.ptr_array(props, np_): continue
            fnames, ffns = [], []
            for k in range(nf):
                ffns.append(im.Q(funcs + 16 * k)); nmv = im.Q(funcs + 16 * k + 8)
                fnames.append(im.ident(nmv) if nmv else None)
            if not all(im.is_text(f) for f in ffns): continue
            out.append({"va": va, "noreg": im.Q(va), "deps": dl, "pa": props, "n": np_, "funcs": fnames, "ffns": ffns})
        return out

    def enums_raw_legacy(self, E):
        """UE 4.19-4.21 FEnumParams (see layouts.ENUM)."""
        im = self.img; L = ENUM[E]; out = []
        for va in im.candidates("T.R.t.Rd"):
            if im.Q(va + 8) not in (0, 1): continue                     # EDynamicType
            nm = im.ident(im.Q(va + L["name"])); cpp = im.ident(im.Q(va + L["cpp"])); ev = im.Q(va + L["values"])
            if not nm or not cpp: continue
            form = im.rd(va + L["form"], "<Q")
            if form not in (0, 1, 2): continue
            n = im.rd(va + L["n"][0], L["n"][1])
            if n is None or n > 4096 or bool(n) != bool(ev): continue
            ents = []
            if n:
                o = im.off(ev)
                if o is None or o + 16 * n > len(im.d): continue
                for k in range(n):
                    a, v = struct.unpack_from("<Qq", im.d, o + 16 * k)
                    s = im.ident(a)
                    if not s: ents = None; break
                    ents.append((s, v))
                if not ents: continue
            out.append({"va": va, "outer": im.Q(va), "name": nm, "cpp": cpp, "entries": ents, "flags": 0, "form": form})
        return out

    def enums_raw(self, E):
        if ENUM[E].get("cpp"): return self.enums_raw_legacy(E)
        im = self.img; L = ENUM[E]; out = []
        for va in self.cands_enum:
            nm = im.ident(im.Q(va + 0x10)); cpp = im.ident(im.Q(va + 0x18)); ev = im.Q(va + 0x20)
            if not nm or not cpp: continue
            n = im.rd(va + L["n"][0], L["n"][1])
            if n is None or n > 4096: continue
            if not n:
                flags=im.rd(va+L['object_flags'],'<I');form=im.rd(va+L['form'],'<B')
                if ev or flags is None or flags&0x45!=0x45 or form not in (0,1,2):continue
                out.append({'va':va,'outer':im.Q(va),'name':nm,'cpp':cpp,'entries':[],
                            'flags':im.rd(va+L['flags'],'<B'),'form':form})
                continue
            o = im.off(ev)
            if o is None or o + 16 * n > len(im.d): continue
            ents = []
            for k in range(n):
                a, v = struct.unpack_from("<Qq", im.d, o + 16 * k)
                s = im.ident(a)
                if not s: ents = None; break
                ents.append((s, v))
            if ents: out.append({"va": va, "outer":im.Q(va), "name": nm, "cpp": cpp, "entries": ents,
                                 "flags": im.rd(va + L["flags"], "<B"), "form": im.rd(va + L["form"], "<B")})
        return out

    def functions(self, P, class_outer, classes):
        """Named FFunctionParams, linked to FunctionLinkArray by (owner, function name).

        These give exact data anchors and extra parameter-size evidence without following any
        constructor into code. Unknown owners and ambiguous records are deliberately excluded.
        """
        im = self.img
        # A short class name can occur in more than one package. Verify the selected class's
        # NoRegister identifier against the same outer/inner registration pair before joining.
        registered = {}
        for _, (outer, inner, _) in self.registrations()[0]:
            registered.setdefault(outer, set()).add(inner)
        known = {(outer, name): fn for outer, owner_name in class_outer.items()
                 for x in [classes.get(owner_name)]
                 if x and registered.get(outer) == {x.get("noreg")}
                 for name, fn in zip(x["funcs"], x["ffns"]) if name and fn}
        out = {}; ambiguous = set()
        # Packed builds omit the delegate/implementation name pointers; the 5.1 records retain
        # them (normally null). Validate the property array under the detected generation.
        for va in im.candidates("TtRd"):
            owner = im.Q(va)
            name = im.ident(im.Q(va + 0x10))
            key = (owner, name)
            if key not in known: continue
            # This transition is independent of property packing (5.4 retains name pointers).
            for family, L in FUNCTION.items():
                pa = im.Q(va + L["props"]); n = im.rd(va + L["n"][0], L["n"][1]); size = im.rd(va + L["size"], "<H")
                flags = im.rd(va + L["flags"], "<I")
                # UHT's Public|Transient|MarkAsNative flags also exclude a false empty F5x
                # interpretation of F51's two null name pointers.
                if flags is None or flags & 0x45 != 0x45: continue
                if n is None or size is None or n >= 2048: continue
                if bool(n) != bool(pa): continue
                ptrs = self.ptr_array(pa, n) if n else []
                if n and not ptrs: continue
                tree = self.tree(ptrs, P) if n else []
                if tree is None: continue
                if any(p.get("off") is not None and p["off"] >= size for p in tree): continue
                fn = known[key]
                if fn in out:
                    ambiguous.add(fn)
                else:
                    out[fn] = {"va": va, "name": name, "owner": class_outer[owner], "ownerCtor": owner, "tree": tree, "size": size, "layout": family}
        return {fn: x for fn, x in out.items() if fn not in ambiguous}

    def registrations(self):
        """FClassRegisterCompiledInInfo {OuterRegister, InnerRegister, Name, Info, Version} and
        FStructRegisterCompiledInInfo {OuterRegister, CreateCppStructOps, Name, Info, Version}: both are
        two code pointers (CreateCppStructOps is null for no-export structs such as FVector), a TCHAR name and a .data pointer. FEnumRegisterCompiledInInfo {OuterRegister,
        Name, Info, Version}: one code pointer. Classes and structs are told apart later by linking to
        FClassParams.ClassNoRegisterFunc / FStructParams.StructOpsFunc."""
        im = self.img
        two, one = [], []
        for va in im.candidates("TtDD"):
            w = im.wident(im.Q(va + 16))
            if w: two.append((va, (im.Q(va), im.Q(va + 8), w)))
        for va in im.candidates("TDD"):
            w = im.wident(im.Q(va + 8))
            if w: one.append((va, (im.Q(va), w)))
        return two, one

    def prepare(self):
        im = self.img
        self.cands_struct = im.candidates("Tttr")       # Outer, Super, StructOps, Name
        self.cands_class = im.candidates("TrDD")        # NoRegister, ConfigName, CppClassInfo, Deps
        self.cands_enum = im.candidates("TtRRd")        # Empty enums have a null Values pointer.
