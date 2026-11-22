"""Static .usmap generator for Unreal Engine 4/5 x64 shipping executables.

Builds the mappings only from the executable's own reflection *data* (UHT codegen FStructParams /
FClassParams / FEnumParams / FPropertyParams records and the Class/Struct/Enum registration tables in
.rdata/.data, plus the .pdata function table). By default, code addresses are opaque identifiers.
Optional --code-links reads ordinary unprotected .text to verify constructor/singleton identities;
protection markers reject code inspection. Neither mode executes or modifies input.
Engine layouts are detected independently per record family. No reference mappings are read;
compare.py measures the result against a reference separately.

usage: python3 -I genusmap.py <exe> <out.usmap> [--report report.json] [--code-links]
"""
import hashlib, json, os, struct, sys, time
from collections import Counter, defaultdict
from .scan import Image, Scanner
from .layouts import PROP, STRUCT, CLASS, ENUM, INTRINSIC, INTRINSIC_FIRST_PROP, version_hint
from .fname_order import enum_prefix
from . import naming
from . import usmap as U

SIMPLE = {"Int8": "Int8Property", "Int16": "Int16Property", "Int": "IntProperty", "Int64": "Int64Property",
          "UInt16": "UInt16Property", "UInt32": "UInt32Property", "UInt64": "UInt64Property", "Float": "FloatProperty",
          "Double": "DoubleProperty", "LWCReal": "DoubleProperty", "Bool": "BoolProperty", "SoftClass": "SoftObjectProperty",
          "WeakObject": "WeakObjectProperty", "LazyObject": "LazyObjectProperty", "SoftObject": "SoftObjectProperty",
          "Class": "ObjectProperty", "Object": "ObjectProperty", "Interface": "InterfaceProperty", "Name": "NameProperty",
          "Str": "StrProperty", "Text": "TextProperty", "Delegate": "DelegateProperty",
          "InlineMulticastDelegate": "MulticastDelegateProperty", "SparseMulticastDelegate": "MulticastDelegateProperty",
          "FieldPath": "FieldPathProperty", "Utf8Str": "Utf8StrProperty", "AnsiStr": "AnsiStrProperty"}

VERBOSE = False   # internal diagnostics (layouts, naming counts); the CLI turns this on with --verbose

def log(*a):
    if VERBOSE: print(*a, file=sys.stderr, flush=True)

def pick(results):
    """Choose the layout with the most validated records; returns (name, {name: count})."""
    counts = {k: len(v) for k, v in results.items()}
    best = max(counts, key=counts.get)
    return best, counts

# v11 FName display order switches (development measurement, see fname_order.registration_order)
STATIC_FIRST = "after types"   # hardcoded and static names before native function names
NATIVE_ORDER = True

DEBUG = {}   # internals of the last run, for measurement harnesses (never written to the output)

def generate(exe, *, code_links=False, config_keys=()):
    """config_keys (v11): startup configuration keys in load order (config_order.startup_files +
    config_order.keys); they follow the static-initializer names in the FName display order."""
    t0 = time.time()
    im = Image(exe); sc = Scanner(im); sc.prepare()
    rep = {"exe": os.path.basename(exe), "layouts": {}, "counts": {}}
    linker = None
    if code_links:
        from .code_links import CodeLinks
        linker = CodeLinks(im, im_functions(im))
    rep["dataSections"] = {name: {"bytes": min(vs, rs),
                                   "sha256": hashlib.sha256(im.d[ra:ra + min(vs, rs)]).hexdigest()}
                           for name, (va, vs, ra, rs) in im.pe.secs.items() if name in im.pe.DATA_SECTIONS}
    # ---- layout detection per record family ----
    sres = {S: sc.structs_raw(S) for S in STRUCT}
    S, rep["layouts"]["structCandidates"] = pick(sres)
    structs = sres[S]
    cres = {C: sc.classes_raw(C) for C in CLASS}
    C, rep["layouts"]["classCandidates"] = pick(cres)
    classes = cres[C]
    eres = {E: sc.enums_raw(E) for E in ENUM}
    E, rep["layouts"]["enumCandidates"] = pick(eres)
    enums = eres[E]
    arrays = [(x["pa"], x["n"]) for x in structs + classes if x["n"]]
    if linker and not linker.blocked:
        pointers=[p for pa,n in arrays for p in (sc.ptr_array(pa,n) or [])]
        initialized=linker.metadata_initializers(sc,pointers)
        for address,value in initialized.items():im.d[im.off(address)]=value
        sc._p.clear()
    pres = {}
    for P in PROP:
        ok = 0
        for pa, n in arrays[:3000]:
            ptrs = sc.ptr_array(pa, n)
            if ptrs and sc.tree(ptrs, P) is not None: ok += 1
        pres[P] = [None] * ok
    P, rep["layouts"]["propArraysValid"] = pick(pres)
    fam = S[1:]                     # families with no records follow the struct layout's generation
    if not classes: C = "C" + fam
    if not enums: E = "E" + fam
    rep["layouts"]["chosen"] = {"prop": P, "struct": S, "class": C, "enum": E}
    rep["versionHint"] = version_hint(rep["layouts"]["chosen"])
    log(f"layouts {rep['layouts']['chosen']} ({rep['versionHint']}) {time.time()-t0:.0f}s")

    # ---- property trees ----
    for x in structs + classes:
        x["tree"] = sc.tree(sc.ptr_array(x["pa"], x["n"]), P) if x["n"] else []
    structs = [x for x in structs if x["tree"] is not None]
    classes = [x for x in classes if x["tree"] is not None]
    # one record per name (largest property list wins; duplicates are rare template instantiations)
    sbyname = {}
    for x in structs:
        if x["name"] not in sbyname or len(sbyname[x["name"]]["tree"]) < len(x["tree"]): sbyname[x["name"]] = x
    ebyname = {}
    for x in enums:
        if x["name"] not in ebyname or len(ebyname[x["name"]]["entries"]) < len(x["entries"]): ebyname[x["name"]] = x

    # ---- registration tables ----
    two, one = sc.registrations()
    noregs = {x["noreg"] for x in classes}
    ops = {x["ops"]: x["name"] for x in sbyname.values() if x["ops"]}
    class_outer, class_inner = {}, {}
    sreg = []
    for va, (a, b, w) in two:
        if b in noregs and len(w) > 1 and w[0] in "AUI":
            name = w[1:].removeprefix("DEPRECATED_")
            class_outer[a] = name; class_inner[b] = name
        elif b in ops or (not b and w in sbyname and not sbyname[w]["ops"]): sreg.append((va, w))
    ereg = [(va, w) for va, (a, w) in one if w in ebyname]
    direct_s, direct_e = {}, {}
    if code_links:
        for fn in sorted(linker.class_candidates()): linker.scan(fn)
        records = {x["va"]: ("struct", x["name"]) for x in structs}
        records.update({x["va"]: ("enum", x["name"]) for x in enums})
        records.update({x["va"]: ("class", x["noreg"]) for x in classes})
        identities = {x["noreg"]: linker.class_name(x["noreg"]) for x in classes}
        linker.class_parameters={x['va']:identities[x['noreg']] for x in classes if identities.get(x['noreg'])}
        conflicts = 0
        for fn, identity in identities.items():
            if identity:
                if fn in class_inner and class_inner[fn] != identity[1]: conflicts += 1
                else: class_inner[fn] = identity[1]
        roots = set()
        def references(node):
            if node["fn"]: roots.add(node["fn"])
            for child in node.get("children", []): references(child)
        for x in structs + classes:
            roots.update(x.get("deps", []))
            if x.get("super_fn"): roots.add(x["super_fn"])
            for node in x["tree"]: references(node)
        for fn in sorted(roots):
            target = linker.constructor(fn, records)
            if target:
                kind, name = target
                if kind == "struct": direct_s[fn] = (name, "code")
                elif kind == "enum": direct_e[fn] = (name, "code")
                elif name in class_inner: class_outer.setdefault(fn, class_inner[name])
        # Class dependencies may call an intrinsic StaticClass directly, without FClassParams.
        for fn in {z for x in classes for z in x["deps"]} - class_outer.keys():
            identity = linker.class_name(fn)
            if identity: class_outer[fn] = identity[1]
        rep["staticCodeLinks"] = {**linker.summary(), "classIdentityConflicts": conflicts,
                                  "classIdentities": sum(bool(v) for v in identities.values()),
                                  "structConstructors": len(direct_s), "enumConstructors": len(direct_e)}
    def groups(regs, stride):
        g = 0; prev = None; out = {}
        for va, w in sorted(regs):
            if prev is None or va - prev != stride: g += 1
            out.setdefault(w, g); prev = va
        return out
    sgroup = groups(sreg, 0x28); egroup = groups(ereg, 0x20)
    rep["counts"].update(structParams=len(sbyname), classParams=len(classes), enumParams=len(ebyname),
                         classRegs=len(class_inner), structRegs=len(sreg), enumRegs=len(ereg))

    # ---- classes: exact names and supers from the registration table ----
    cbyname = {}
    unnamed = 0
    for x in classes:
        nm = class_inner.get(x["noreg"])
        if not nm and not (linker and not linker.blocked) and x["tree"] and x["tree"][0]["name"] in INTRINSIC_FIRST_PROP:
            nm = INTRINSIC_FIRST_PROP[x["tree"][0]["name"]]
            x["name"] = nm; x["super"] = INTRINSIC[nm]; x["intrinsic"] = True
            cbyname.setdefault(nm, x); continue
        if not nm: unnamed += 1; continue
        x["name"] = nm; x["super"] = class_outer.get(x["deps"][0])
        if nm not in cbyname or len(cbyname[nm]["tree"]) < len(x["tree"]): cbyname[nm] = x
    rep["counts"]["classesUnnamed"] = unnamed
    intrinsic_schemas = {}
    if linker and not linker.blocked:
        relationships=[]
        for x in classes:
            child=identities.get(x['noreg'])
            parent=linker.class_name(x['deps'][0]) if x['deps'] else None
            if child and parent:relationships.append((child,parent))
        intrinsic_schemas,calibration=linker.intrinsic_schemas(relationships)
        bootstrap_schemas,bootstrap_calibration=linker.bootstrap_schemas(INTRINSIC)
        for name,schema in bootstrap_schemas.items():intrinsic_schemas.setdefault(name,schema)
        rep['staticCodeLinks'].update(linker.summary())
        rep['staticCodeLinks']['intrinsic_schema_calibration']=calibration
        rep['staticCodeLinks']['legacy_intrinsic_candidates']=len(intrinsic_schemas)
        rep['staticCodeLinks']['bootstrap_schema_calibration']=bootstrap_calibration
        DEBUG['class_link_evidence']={'identities':identities,'class_outer':class_outer,
            'bootstraps':linker.bootstrap_evidence,
            'unmapped_parents':{x['name']:x['deps'][0] for x in cbyname.values()
                                if x['deps'] and x['deps'][0] not in class_outer}}

    # ---- evidence for struct and enum constructors ----
    sev = defaultdict(lambda: {"hi": None, "names": set(), "parents": set(), "subs": set(), "his": Counter()})
    eev = defaultdict(lambda: {"names": set(), "parents": set()})
    def walk(n, hi, parent):
        if n["kind"] == "Struct" and n["fn"]:
            e = sev[n["fn"]]; e["names"].add(n["name"]); e["parents"].add(parent)
            if hi is not None:
                e["hi"] = hi if e["hi"] is None else min(e["hi"], hi); e["his"][hi] += 1
        if n["kind"] in ("Enum", "Byte") and n["fn"]:
            e = eev[n["fn"]]; e["names"].add(n["name"]); e["parents"].add(parent)
        for c in n.get("children", []): walk(c, None, parent)
    def collect(tree, size, parent):
        offs = sorted(set([n["off"] for n in tree if n.get("off") is not None] + ([size] if size else [])))
        for n in tree:
            hi = None
            if n.get("off") is not None:
                nxt = [o for o in offs if o > n["off"]]
                hi = (nxt[0] - n["off"]) // max(1, n["dim"]) if nxt else None
            walk(n, hi, parent)
    for nm, x in sbyname.items():
        collect(x["tree"], x["size"], nm)
        if x["super_fn"]:
            e = sev[x["super_fn"]]; e["subs"].add(nm); e["parents"].add(nm)
            own = [n["off"] for n in x["tree"] if n.get("off") is not None]
            hi = min(own) if own else x["size"]
            e["hi"] = hi if e["hi"] is None else min(e["hi"], hi)
    for nm, x in cbyname.items(): collect(x["tree"], None, nm)

    functions = sc.functions(P, class_outer, cbyname)
    # Only constructors needed by the output participate in assignment. Function-only types
    # supply evidence without consuming names or changing the evaluation population.
    required_s, required_e = set(sev), set(eev)
    hints = naming.signature_hints(functions, sbyname)
    for f in functions.values():
        collect(f["tree"], f["size"], f["owner"] + "_" + f["name"])
    sev = {z: e for z, e in sev.items() if z in required_s}
    eev = {z: e for z, e in eev.items() if z in required_e}
    rep["counts"]["functionParams"] = len(functions)
    rep["layouts"]["functions"] = dict(Counter(f["layout"] for f in functions.values()))

    ssize = {nm: (x["size"], x["align"]) for nm, x in sbyname.items()}
    stok = {nm: naming.toks(nm) for nm in sbyname}
    etok = {nm: naming.toks(nm[1:] if nm[:1] == "E" and nm[1:2].isupper() else nm) for nm in ebyname}
    def par_toks(e): return set().union(*[naming.toks(n) for n in e["parents"]]) if e["parents"] else set()
    sevt = {z: (naming.use_profile(e["names"]), par_toks(e)) for z, e in sev.items()}
    sexact = {z: {frozenset(naming.toks(n)) for n in e["names"] if n != "ReturnValue"} for z, e in sev.items()}
    eevt = {z: (naming.use_profile(e["names"]), par_toks(e)) for z, e in eev.items()}
    def sscore(z, nm):
        e = sev[z]
        if nm in e["subs"]: return None              # a struct is never its own super
        sz, al = ssize[nm]; hi = e["hi"]
        if hi is not None and sz > hi: return None
        v = 0.0
        if hi is not None:
            v += 3.0 if sz == hi else (1.0 if hi - sz < max(al, 8) else -min(2.0, (hi - sz) / 32.0))
        if len(e["his"]) > 1 and sz == e["his"].most_common(1)[0][0]: v += 1.0   # the most common gap
        u, p = sevt[z]
        if len(hints.get(z, ())) == 1:
            v += 12.0 if nm in hints[z] else -12.0
        return v + naming.word_score(u, p, nm, stok[nm], exact_names=sexact[z]) - 0.03 * len(nm)   # weak Occam prior: short (core) names
    # entry names per enum without their common prefix (ECC_WorldStatic -> worldstatic)
    eents = {}
    for nm, x in ebyname.items():
        full = [s_.split("::", 1)[-1] for s_, _ in x["entries"]]
        pre = enum_prefix(nm, full)
        eents[nm] = {(e_[len(pre) + 1:] if e_.startswith(pre + "_") else e_).lower() for e_ in full}
    euse = {z: {n.lower() for n in e["names"]} for z, e in eev.items()}
    def escore(z, nm):
        u, p = eevt[z]; nt = etok[nm]
        v = naming.word_score(u, p, nm, nt)
        # fields named after this enum's entries are indexed by it, not typed by it
        if len(euse[z]) >= 3 and len(euse[z] & eents[nm]) >= 0.5 * len(euse[z]): v -= 3.0
        if nt and all(u[0].get(t) for t in nt): v += 1.0
        elif nt and all(u[0].get(t) or t in p for t in nt): v += 0.75   # nested naming: words from the parent
        return v
    fsig = im_functions(im)
    # exact anchors (code address, registration entry address) from the registration tables
    creg = {}
    for va, (a, b, w) in two:
        if b in class_inner: creg[class_inner[b]] = va
    sregva = {w: va for va, w in sreg}; eregva = {w: va for va, w in ereg}
    anchors = []
    for va, (a, b, w) in two:
        if b in class_inner or b in ops: anchors += [(a, va), (b, va)]
        elif not b and w in sregva and sregva[w] == va: anchors.append((a, va))
    for va, (a, w) in one:
        if w in eregva: anchors.append((a, va))
    for nm, x in cbyname.items():
        if nm in creg: anchors += [(f, creg[nm]) for f in x["ffns"] if f]
    t1 = time.time()
    # data positions: each type's own FStructParams/FEnumParams record (every type has one, including
    # no-export core structs that have no registration entry); anchors map named code addresses
    # (StaticStruct, NewStructOps, StaticEnum) to those records.
    spos = {nm: x["va"] for nm, x in sbyname.items()}
    epos = {nm: x["va"] for nm, x in ebyname.items()}
    sown = [(a, spos[w]) for va, (a, b, w) in two if w in spos and (b in ops or (not b and sregva.get(w) == va))]
    sown += [(b, spos[ops[b]]) for va, (a, b, w) in two if b in ops]
    sown = sorted(set(sown + [(x["ops"], x["va"]) for x in sbyname.values() if x["ops"]]))
    eown = [(a, epos[w]) for va, (a, w) in one if w in eregva and eregva[w] == va]
    sown += [(z, spos[n]) for z, (n, _) in direct_s.items() if n in spos]
    eown += [(z, epos[n]) for z, (n, _) in direct_e.items() if n in epos]
    # constructors of known other kinds (UFunctions from FunctionLinkArray, delegate signatures, classes)
    others = {f for x in classes for f in x["ffns"] if f} | set(class_outer) | delegate_fns(sbyname, cbyname)
    eps = 0.0   # positional reward for unreferenced run members: no measurable gain, off
    fown = [(fn, x["va"]) for fn, x in functions.items()]
    fixed_s = {z: lab for z, lab in direct_s.items() if z in sev}
    fixed_e = {z: lab for z, lab in direct_e.items() if z in eev}
    reserved_s = {n for n, _ in fixed_s.values()}; reserved_e = {n for n, _ in fixed_e.values()}
    if linker and not linker.blocked:
        # Verified-code mode never fills an unsupported constructor with a naming guess.
        snames,enames={},{}
        rep['staticCodeLinks']['unresolved_struct_constructors']=len(set(sev)-fixed_s.keys())
        rep['staticCodeLinks']['unresolved_enum_constructors']=len(set(eev)-fixed_e.keys())
    else:
        snames = naming.assign([z for z in sev if z not in fixed_s], {n:p for n,p in spos.items() if n not in reserved_s}, sown + eown + fown, sscore, fsig, prior_anchors=sown + fown, others=others | set(eev), eps=eps,
                              extra_candidates={z: sorted(ns) for z, ns in hints.items() if len(ns) == 1}, joint=True)
        enames = naming.assign([z for z in eev if z not in fixed_e], {n:p for n,p in epos.items() if n not in reserved_e}, sown + eown + fown, escore, fsig, prior_anchors=eown + fown, others=others | set(sev), eps=eps)
    snames.update(fixed_s); enames.update(fixed_e)
    rep["naming"] = {"structCtors": len(sev), "structNamed": len(snames), "structHow": dict(Counter(h for _, h in snames.values())),
                     "enumCtors": len(eev), "enumNamed": len(enames), "enumHow": dict(Counter(h for _, h in enames.values())),
                     "anchors": len(anchors), "functionAnchors": len(fown),
                     "structFunctionHints": sum(len(hints.get(z, ())) == 1 for z in sev),
                     "seconds": round(time.time() - t1)}
    # Twins: structs with an identical layout whose names extend a common base (e.g. the LWC aliases Rotator3d / Rotator, Transform3d /
    # Transform) cannot be told apart from data; use one canonical name (the shortest) for all of them.
    sig = defaultdict(list)
    for nm, x in sbyname.items():
        if x["tree"]: sig[(x["size"], tuple((n["name"], SIMPLE.get(n["kind"], n["kind"]), n["dim"]) for n in x["tree"]))].append(nm)
    canon = {}
    for names in sig.values():
        if len(names) > 1:
            c = min(names, key=lambda n: (len(n), n))
            for n in names:
                if n.startswith(c): canon[n] = c     # alias pattern: <Base><suffix>
    snames = {z: (n if h == "code" else canon.get(n, n), h) for z, (n, h) in snames.items()}
    rep["naming"]["twins"] = sum(1 for n, c in canon.items() if n != c)
    log(f"naming {rep['naming']}")
    DEBUG.update(class_inner=dict(class_inner), snames=snames, enames=enames, sev=dict(sev), eev=dict(eev), sbyname=sbyname, cbyname=cbyname, ebyname=ebyname,
                 sgroup=sgroup, egroup=egroup, two=two, one=one, sev_hi={z: e['hi'] for z, e in sev.items()}, fsig=fsig, sscore=sscore, escore=escore,
                 functions=functions, hints=dict(hints))

    # ---- convert ----
    unknown = Counter()
    def to_type(n):
        k = n["kind"]
        if k == "Struct":
            lab = snames.get(n["fn"])
            if not lab: unknown["struct"] += 1
            return {"t": "StructProperty", "struct": lab[0] if lab else "None"}
        if k in ("Enum", "Byte"):
            inner = to_type(n["children"][0]) if k == "Enum" and n["children"] else {"t": "ByteProperty"}
            if not n["fn"]: return {"t": "ByteProperty"}
            lab = enames.get(n["fn"])
            if not lab: unknown["enum"] += 1
            if k == "Byte" and not lab: return {"t": "ByteProperty"}
            return {"t": "EnumProperty", "inner": inner, "enum": lab[0] if lab else "None"}
        if k in ("Array", "Set", "Optional"):
            return {"t": {"Array": "ArrayProperty", "Set": "SetProperty", "Optional": "OptionalProperty"}[k], "inner": to_type(n["children"][0])}
        if k == "Map":
            return {"t": "MapProperty", "key": to_type(n["children"][0]), "value": to_type(n["children"][1])}
        return {"t": SIMPLE[k]}
    def props(tree):
        out = []; i = 0
        for n in tree:
            out.append({"i": i, "dim": n["dim"], "name": n["name"], "type": to_type(n)}); i += n["dim"]
        return out, i
    out_s = {}
    for nm, x in sbyname.items():
        pr, cnt = props(x["tree"])
        sup = snames.get(x["super_fn"], (None,))[0] if x["super_fn"] else None
        out_s[nm] = {"super": sup, "count": cnt, "props": pr}
    for nm, x in cbyname.items():
        pr, cnt = props(x["tree"])
        out_s[nm] = {"super": x["super"], "count": cnt, "props": pr}
    for nm, record in intrinsic_schemas.items():
        if nm not in out_s:out_s[nm]={k:record[k] for k in ('super','count','props')}
    DEBUG['intrinsic_evidence']={n:r['evidence'] for n,r in intrinsic_schemas.items()}
    for nm, sup in INTRINSIC.items():     # intrinsic classes without reflected properties
        if nm == "SparseDelegateFunction" and PROP[P].get("legacy"): continue   # added in UE 4.23
        if nm not in out_s: out_s[nm] = {"super": sup, "count": 0, "props": []}
    out_e = {nm: enum_entries(nm, x["entries"], (x.get("flags") or 0) & 1) for nm, x in ebyname.items()}
    if linker and not linker.blocked:
        from .enum_registry import build
        out_e,rep['enumRegistration'],DEBUG['enum_registration_evidence']=build(
            {x['va']:x for x in ebyname.values()},linker,enum_prefix,{x['va']:x for x in sbyname.values()},
            flagless=ENUM[E].get('flags',0) is None)
        rep['staticCodeLinks'].update(linker.summary())
        DEBUG['registration_order']=getattr(linker,'registration_order',[])
    else:
        rep['enumRegistration']={'status':'data-only; package collision order and maximum reducer unverified',
            'unverified_automatic_values':sum(bool(x.get('flags',0)&1) or not x['entries'] for x in ebyname.values())}
    rep["counts"].update(outStructs=len(out_s), outEnums=len(out_e), unknownStructRefs=unknown["struct"], unknownEnumRefs=unknown["enum"])
    kinds = Counter()
    def kw(n):
        kinds[n["kind"]] += 1
        for c in n.get("children", []): kw(c)
    for x in list(sbyname.values()) + list(cbyname.values()):
        for n in x["tree"]: kw(n)
    rep["features"] = {"optionalProps": kinds["Optional"], "utf8StrProps": kinds["Utf8Str"], "ansiStrProps": kinds["AnsiStr"],
                       "lwcRealProps": kinds["LWCReal"], "intrinsicWithProps": sorted(n for n in INTRINSIC if out_s.get(n, {}).get("props"))}
    rep["engineVersionString"] = version_string(im)
    rep["engineVersionEstimate"] = version_estimate(rep["engineVersionString"], rep["layouts"]["chosen"], rep["features"])
    rep["runtimeRecords"] = runtime_records(out_e, out_s, rep["engineVersionString"], rep["layouts"]["chosen"])
    rep["runtimeRecords"] += recover_legacy_types(im, out_s, rep["engineVersionString"])
    # ---- display spelling of case-insensitive FNames (first registration wins) ----
    from . import fname_order
    hard = fname_order.hardcoded(im.d, im, linker)
    # v11: static initializers including tail-called constructors and registry constructors
    static = fname_order.static_names_nested(im, linker) if linker and not linker.blocked else []
    native = fname_order.native_order_table(getattr(linker, "registration_order", None) if linker else None)
    display = fname_order.registration_order(hard, static, sbyname, cbyname, ebyname, snames, class_outer, functions,
                                             static_first=STATIC_FIRST, native_order=native if NATIVE_ORDER else None,
                                             config=config_keys if STATIC_FIRST else ())
    if DEBUG.get("want_inputs") is not None:             # measurement only: registration inputs
        DEBUG["want_inputs"] = dict(hard=hard, static=static, sbyname=sbyname, cbyname=cbyname, ebyname=ebyname,
                                    snames=snames, class_outer=class_outer, functions=functions)
    if DEBUG.get("compare_static_v10") is not None:      # measurement only: the v10 static list
        DEBUG["static_v10"] = fname_order.static_names(im, linker) if linker and not linker.blocked else []
        DEBUG["compare_static_v10"] = fname_order.registration_order(
            hard, DEBUG["static_v10"], sbyname, cbyname, ebyname, snames, class_outer, functions)
    namespaced = {nm for nm, x in ebyname.items() if any("::" in s_ for s_, _ in x["entries"])}
    out_e, out_s, renamed = fname_order.apply(out_e, out_s, display, namespaced)
    rep["fnameDisplay"] = {"hardcoded": len(hard), "staticInitializers": len(static), "renamed": renamed}
    DEBUG["fname_display"] = display
    rep["seconds"] = round(time.time() - t0)
    return out_e, out_s, rep

# Class-default objects of the reflection metaclasses are UStruct/UEnum instances that exist in
# every runtime; reflection dumpers (UE4SS DumpUSMAP) list them as empty records.
RUNTIME_CDOS = {"types": ("Default__Class", "Default__ScriptStruct"), "enums": ("Default__Enum",)}
# Engine plugin event payloads that Niagara registers at module startup. From UE 5.2 Niagara builds
# a simulation copy "<Struct>_SWC" with large-world (double) members narrowed to float.
NIAGARA_STARTUP_SWC = ("ChaosDestructionEvent", "HoudiniEvent")
SWC_STRUCTS = {"Vector": "Vector3f", "Vector2D": "Vector2f", "Vector4": "Vector4f", "Quat": "Quat4f",
               "Rotator": "Rotator3f", "Transform": "Transform3f", "Plane": "Plane4f", "Box": "Box3f"}


def recover_legacy_types(im, structs, version):
    """Recover the legacy AudioCapture component when its property descriptor is partial."""
    name = "AudioCaptureComponent"
    parent = "SynthComponent"
    member = "JitterLatencyFrames"
    if version != "4.19" or name in structs or parent not in structs:
        return []
    if ".data" not in im.pe.secs or ".rdata" not in im.pe.secs:
        return []
    lo, hi, raw = im.pe.sec(".rdata")
    end = raw + hi - lo
    # Both the class and its script package must be present in the retained data.
    class_literal = ("U" + name + "\0").encode("utf-16-le")
    if im.d.find(class_literal, raw, end) < 0 or im.d.find(b"/Script/AudioCapture\0", raw, end) < 0:
        return []
    pos = im.d.find(member.encode() + b"\0", raw, end)
    if pos < 0:
        return []
    # In the legacy type-first property record, the kind and name can be
    # present in the file even when the rest is filled in at startup.
    prop_prefix = struct.pack("<QQ", 3, lo + pos - raw)
    data_lo, data_hi, data_raw = im.pe.sec(".data")
    data_end = data_raw + data_hi - data_lo
    record = im.d.find(prop_prefix, data_raw, data_end)
    if record < 0 or (record - data_raw) % 8 or record + 0x38 > data_end:
        return []
    if any(im.d[record + 0x10:record + 0x38]):
        return []
    structs[name] = {"super": parent, "count": 1,
                     "props": [{"i": 0, "dim": 1, "name": member, "type": {"t": "IntProperty"}}]}
    return [name]


def lwc_minor(vs, chosen):
    """UE5 minor version when it is known: the build string, else the packed 5.3+ layout."""
    if vs and vs.startswith("5."): return int(vs.split(".")[1])
    if not vs and chosen["prop"] == "P5x": return 3
    return None

def runtime_records(out_e, out_s, vs, chosen):
    """Records every native runtime creates beyond the compiled-in reflection data."""
    added = []
    for name in RUNTIME_CDOS["types"]:
        if name not in out_s: out_s[name] = {"super": None, "count": 0, "props": []}; added.append(name)
    for name in RUNTIME_CDOS["enums"]:
        if name not in out_e: out_e[name] = []; added.append(name)
    minor = lwc_minor(vs, chosen)
    if minor is not None and minor >= 2:
        def narrow(t):
            t = dict(t)
            if t["t"] == "DoubleProperty": return {"t": "FloatProperty"}
            if t["t"] == "StructProperty": t["struct"] = SWC_STRUCTS.get(t["struct"], t["struct"])
            for k in ("inner", "key", "value"):
                if k in t: t[k] = narrow(t[k])
            return t
        for name in NIAGARA_STARTUP_SWC:
            src = out_s.get(name)
            if src and name + "_SWC" not in out_s:
                out_s[name + "_SWC"] = {"super": src["super"], "count": src["count"],
                                        "props": [dict(p, type=narrow(p["type"])) for p in src["props"]]}
                added.append(name + "_SWC")
    return added

def version_estimate(vs, chosen, f):
    """Report a retained build string, otherwise only the validated layout family.

    A build can omit all properties of a newer kind. Absence cannot establish a
    minor-version ceiling, and record families change independently.
    """
    if vs: return {"version": vs, "basis": "build string"}
    if chosen["prop"] in ("P422", "P422x"): return {"version": "4.22", "basis": "name-first records with early property kinds"}
    if chosen["prop"] in ("P419", "P419x"): return {"version": "4.19-4.21", "basis": "type-first property records"}
    if chosen["prop"] in ("P4","P4x"): return {"version": "4.x", "basis": "five-bit property kinds; minor version unknown"}
    if chosen["prop"] == "P50": return {"version": "unknown", "basis": "unpacked layout shared by UE4 and UE5.0; no build string"}
    if chosen["prop"] == "P51": return {"version": "5.x", "basis": "accessor layout observed on UE5.1/5.2; minor version unverified"}
    return {"version": "5.x", "basis": "packed layout observed on UE5.3-5.7; minor version unverified"}

def version_string(im):
    """The engine's build string (++UE5+Release-5.x), ASCII or UTF-16, when the build keeps it."""
    import re
    for lo, hi, ra in im.dsecs:
        data = im.d[ra:ra + hi - lo]
        for major in (4, 5):
            prefix = f"++UE{major}+Release-{major}."
            m = re.search(re.escape(prefix.encode()) + rb"(\d+)", data)
            if m: return f"{major}." + m.group(1).decode()
            m = re.search(re.escape(prefix.encode("utf-16-le")) + rb"((?:[0-9]\x00)+)", data)
            if m: return f"{major}." + m.group(1).decode("utf-16-le")
    return None

def delegate_fns(*byname):
    out = set()
    def w(n):
        if n["kind"] in ("Delegate", "InlineMulticastDelegate", "SparseMulticastDelegate") and n["fn"]: out.add(n["fn"])
        for c in n.get("children", []): w(c)
    for b in byname:
        for x in b.values():
            for n in x["tree"]: w(n)
    return out

def enum_entries(name, entries, is_flags, maximum_policy=None):
    """Entries as UEnum holds them after registration (UEnum::SetEnums with bAddMaxKeyIfMissing): the
    generated <prefix>_MAX key is appended unless a case-insensitive match exists; its value is the
    build-matched maximum + 1. Unknown flag/empty reducers omit the synthetic key.
    Names lose their "Enum::" qualifier.
    Package-wide name collisions require registration-order evidence separately.
    """
    full = [s for s, _ in entries]
    qual = full[0].split("::", 1)[0] + "::" if full and "::" in full[0] else ""
    prefix = enum_prefix(name, full)
    mx = ("" if "::" in prefix else qual) + prefix + "_MAX"
    out = [(v, s.split("::", 1)[1] if "::" in s else s) for s, v in entries]
    existing = {s.lower() for s in full}
    if mx.lower() not in existing and not (qual and (qual + "MAX").lower() in existing):
        from .enum_maximum import maximum
        val = maximum(entries,is_flags,maximum_policy)
        if val is not None:out.append((val, mx.split("::", 1)[1] if "::" in mx else mx))
    return out

def im_functions(im):
    """{start VA: (size, unwind RVA)} from the .pdata exception directory (data, not code)."""
    if ".pdata" not in im.pe.secs: return {}
    va, vs, ra, rs = im.pe.secs[".pdata"]
    n = min(vs, rs) // 12
    out = {}
    for b, e, u in struct.iter_unpack("<III", im.d[ra:ra + 12 * n]):
        if b: out[im.pe.base + b] = (e - b, u)
    return out

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe"); ap.add_argument("out"); ap.add_argument("--report")
    ap.add_argument("--code-links", action="store_true", help="Static links from ordinary unprotected code; protected sections remain unread")
    a = ap.parse_args()
    e, s, rep = generate(a.exe, code_links=a.code_links)
    U.write(a.out, e, s)
    log(f"wrote {a.out}: {len(s)} structs/classes, {len(e)} enums")
    if a.report: json.dump(rep, open(a.report, "w"), indent=1)
