"""Display spelling of case-insensitive FNames, from the order in which a native runtime first creates them.

FName comparison ignores case and the name pool keeps the spelling of the first registration, so a
reflection dump shows e.g. "Tooltip" for every property spelled "ToolTip" if "Tooltip" was created
earlier. The order is reconstructed statically:

1. compiled-in type names (created at registration), which also bridge gaps in the hardcoded list:
   structs with C++ ops, then each class with its native function names;
2. hardcoded names, stored when the name pool is constructed (the ANSI literal block that starts
   with ByteProperty, merged with its UTF-16 copy), then FNames built by CRT static initializers;
3. compiled-in reflection: structs, then classes (with their UFunctions), each construction recursing
   into its super type and the structs its properties reference; properties are constructed from the
   end of their parameter array; then unqualified enum entries.

Between the hardcoded names and reflection come the FNames that CRT static initializers construct
(static_names(): global FNames and startup registries such as script type names).
Record names and type references keep their own spelling: renaming them measured regressions.

Names created later (configuration, assets, constructors) cannot be seen; they surface as spellings
that occur nowhere in the executable. Nothing is executed or modified.
"""
import collections, difflib, os, re

IDENT = re.compile(rb"[A-Za-z0-9_]+")
NUMBERED = re.compile(r"(.+)(_0|_[1-9][0-9]{0,9})\Z")


def name_parts(name):
    """Split the comparison string from the decimal suffix stored separately by FName."""
    match = NUMBERED.fullmatch(name)
    if match and int(match[2][1:]) < 0x7FFFFFFF:
        return match[1], match[2]
    return name, ""


def enum_prefix(name, entries):
    """Prefix used by the runtime when deriving an automatic enum maximum."""
    prefix = os.path.commonprefix(entries)
    cut = prefix.rfind("_")
    return prefix[:cut] if cut > 0 else name


def respell_enum_maximum(name, before, after, known_names):
    """Update a synthesized maximum's spelling after the enumerators are respelled."""
    if not before or before == after:
        return after
    _, last = before[-1]
    if not last.endswith("_MAX") or after[-1][1] != last:
        return after
    if last.lower() in known_names or f"{name}::{last}".lower() in known_names:
        return after
    old = enum_prefix(name, [entry for _, entry in before[:-1]]) + "_MAX"
    new = enum_prefix(name, [entry for _, entry in after[:-1]]) + "_MAX"
    if last != old or old.lower() != new.lower() or old == new:
        return after
    return [*after[:-1], (after[-1][0], new)]

def _ansi_block(d):
    m = re.search(rb"(?<=\0)ByteProperty\0+IntProperty\0", d)
    if not m: return []
    o = m.start(); out = ["None"]
    while True:
        e = d.find(b"\0", o, o + 81); s = d[o:e] if e > 0 else b""
        if not s or len(s) > 80 or not IDENT.fullmatch(s): break
        if d[e + 1:e + 2] != b"\0" and d[e + 2:e + 3] == b"\0": break      # UTF-16 text follows
        out.append(s.decode()); o = e
        while o < len(d) and d[o] == 0 and o - e < 16: o += 1
        if o >= len(d) or d[o] == 0: break
    return out

def _wide_block(d):
    best = []
    for m in re.finditer("ByteProperty\0".encode("utf-16-le") + b"\0\0", d):
        o = m.start()
        if o % 2: continue
        out = []
        while True:
            e = o
            while e + 2 <= len(d) and d[e:e + 2] != b"\0\0" and e - o < 160: e += 2
            s = d[o:e].decode("utf-16-le", "replace")
            if not re.fullmatch(r"[A-Za-z0-9_]+", s): break
            out.append(s); o = e + 2; k = 0
            while d[o:o + 2] == b"\0\0" and k < 8: o += 2; k += 1
            if o + 2 > len(d): break
        if len(out) > len(best): best = out
    return ["None"] + best if best else []

def _registered_literals(im, linker):
    """The name pool's constructor registers every hardcoded name in order with its literal address
    in code (REGISTER_NAME). Decode the function that references ByteProperty, the first hardcoded
    name, and collect the identifier literals it loads, in order. Pooled literals leave no gap."""
    d = im.d; targets = set()
    def va(o):
        for lo, hi, ra in im.dsecs:
            if ra <= o < ra + hi - lo: return lo + o - ra
    for m in re.finditer(rb"(?<=\0)ByteProperty\0", d): targets.add(va(m.start()))
    for m in re.finditer(b"\0\0" + "ByteProperty".encode("utf-16-le") + b"\0\0", d):
        if m.start() % 2 == 0: targets.add(va(m.start() + 2))
    targets.discard(None)
    if not targets: return []
    import bisect, struct
    lo, hi, raw = im.pe.sec(".text"); best = []
    detail = linker.md.detail; linker.md.detail = False
    try:
        for start in range(lo, hi, 1 << 22):
            chunk = linker.read(start, min((1 << 22) + 7, hi - start))
            for m in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", chunk):
                pos = start + m.start()
                if m.start() + 7 > len(chunk): continue
                if pos + 7 + struct.unpack_from("<i", chunk, m.start() + 3)[0] not in targets: continue
                i = bisect.bisect_right(linker.starts, pos) - 1
                if i < 0: continue
                fn = linker.starts[i]; end = fn + linker.functions[fn][0]
                if not fn <= pos < end: continue
                names = []
                for a, n, mnem, ops in linker.md.disasm_lite(linker.read(pos, end - pos), pos):
                    if mnem != "lea" or "[rip" not in ops: continue
                    t = a + n + int(ops[ops.index("rip") + 3:-1].replace(" ", ""), 0); o = im.off(t)
                    if o is None: continue
                    s = im.wident(t) if o % 2 == 0 and d[o] and not d[o + 1] else im.ident(t)
                    if s and IDENT.fullmatch(s.encode()) and s not in names: names.append(s)
                if len(names) > len(best): best = names
    finally:
        linker.md.detail = detail
    return ["None"] + best if best else []

def hardcoded(d, im=None, linker=None):
    """Hardcoded names in registration order: from the registering code when it can be read,
    else from the literal block. Literals pooled with identical strings elsewhere leave gaps in
    either data copy; the two copies are aligned to fill each other's gaps."""
    code = _registered_literals(im, linker) if im is not None and linker and not linker.blocked else []
    a, w = _ansi_block(d), _wide_block(d)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=w, autojunk=False).get_opcodes():
        for x in (a[i1:i2] if tag == "equal" else a[i1:i2] + w[j1:j2]):
            if x not in out: out.append(x)
    return code if len(code) > len(out) else out

# mov r8d, 1 (FNAME_Add) and rdx from [rip+disp32]: the two halves of FName(TEXT("..."), FNAME_Add).
_FNAME_ADD = re.compile(rb"\x41\xb8\x01\x00\x00\x00")
_LEA_RDX = re.compile(rb"\x48[\x8b\x8d]\x15")      # lea or mov rdx, [rip+disp32]

def static_names(im, linker, tail_calls=False, with_targets=False):
    """Identifier literals passed to FName construction by CRT static initializers, in order.

    Global FName objects and FName temporaries alike (e.g. type-name registries filled at
    startup). Only callbacks whose bytes contain both halves of FName(TEXT("..."), FNAME_Add)
    are decoded, linearly up to their first return; a literal counts only at a call whose rdx
    holds it (lea rdx, [rip+disp]) and whose r8d is FNAME_Add (1)."""
    if not linker or linker.blocked: return []
    md = linker.md; out = []; callbacks = linker.startup_callbacks()
    detail = md.detail; md.detail = False
    try:
        for cb in callbacks:
            known = linker.functions.get(cb)
            size = min(known[0] if known else 512, 16384, im.text[1] - cb)
            body = linker.read(cb, size)
            if not _FNAME_ADD.search(body) or not _LEA_RDX.search(body): continue
            rdx = r8 = None; extra = False      # extra: a fourth argument (r9 or stack) is set
            for a, n, mnem, ops in md.disasm_lite(body, cb):
                dest = ops.split(",", 1)[0].strip()
                if dest in ("r9", "r9d") or dest.startswith(("qword ptr [rsp", "dword ptr [rsp", "byte ptr [rsp")): extra = True
                if mnem == "lea" and dest == "rdx" and ops.startswith("rdx, [rip"):
                    rdx = a + n + int(ops[ops.index("rip") + 3:-1].replace(" ", ""), 0)
                elif mnem == "mov" and ops.startswith("rdx, qword ptr [rip"):     # a const TCHAR* global
                    rdx = im.Q(a + n + int(ops[ops.index("rip") + 3:-1].replace(" ", ""), 0))
                elif mnem == "mov" and dest == "r8d" and ops == "r8d, 1": r8 = 1
                elif mnem == "call" or (tail_calls and mnem == "jmp" and ops.startswith("0x")):
                    # A callback that ends by constructing one FName tail-calls the constructor (jmp).
                    if r8 == 1 and not extra and rdx is not None and im.is_data(rdx) and ops.startswith("0x"):
                        o = im.off(rdx)
                        wide = o is not None and o % 2 == 0 and im.d[o] and not im.d[o + 1]
                        s_ = im.wident(rdx) if wide else im.ident(rdx)
                        if s_ and IDENT.fullmatch(s_.encode()): out.append((int(ops, 16), s_))
                    rdx = r8 = None; extra = False
                    if mnem == "jmp": break
                elif mnem in ("ret", "int3"): break
                else:
                    if dest in ("rdx", "edx", "dx", "dl", "dh"): rdx = None
                    if dest in ("r8", "r8d", "r8w", "r8b"): r8 = None
    finally:
        md.detail = detail
    # The FName constructors are the direct targets that dominate this call shape; other
    # callees with the same registers (e.g. console-variable defaults) are excluded.
    from collections import Counter
    counts = Counter(t for t, _ in out)
    if not counts: return []
    top = counts.most_common(1)[0][1]
    ctors = {t for t, n in counts.items() if n >= top / 4}
    if with_targets:
        return out, ctors
    return [s_ for t, s_ in out if t in ctors]

def _decode_fname_calls(im, linker, fn, size, tail_calls, on_call=None, need_r8=True):
    """(constructor target, literal) for each FName(literal, FNAME_Add) call shape in one function,
    decoded linearly over its function-table extent (without one: up to the first return or tail
    call). on_call(target) is invoked for other direct calls. need_r8=False accepts the call shape
    without the find-type argument."""
    md = linker.md; out = []
    body = linker.read(fn, size)
    rdx = r8 = None; extra = False      # extra: a fourth argument (r9 or stack) is set
    zero = set()                        # 64-bit register families known to hold zero (xor r, r)
    def fam(r):
        r = r.strip()
        m = re.fullmatch(r"r(\d+)[dwb]?", r)
        if m: return "r" + m[1]
        m = re.fullmatch(r"[re]?([abcd])[xlh]|[re]?(si|di|bp|sp)l?", r)
        return (m[1] or m[2]) if m else None
    framed = False                      # register spills of the prologue are not arguments
    bounded = fn in linker.functions    # the function table gives the body's extent
    for a, n, mnem, ops in md.disasm_lite(body, fn):
        dest = ops.split(",", 1)[0].strip()
        if mnem == "sub" and dest == "rsp": framed = True
        # A fourth argument (r9) or a fifth one (first stack argument slot) marks another call
        # shape; other stack stores are locals or the prologue's register spills.
        if dest in ("r9", "r9d") or framed and dest.endswith("[rsp + 0x20]"): extra = True
        if mnem == "xor" and "," in ops and fam(dest) and fam(dest) == fam(ops.split(",", 1)[1]):
            zero.add(fam(dest))
            if fam(dest) == "r8": r8 = 0
            continue
        if mnem == "lea" and dest == "rdx" and ops.startswith("rdx, [rip"):
            rdx = a + n + int(ops[ops.index("rip") + 3:-1].replace(" ", ""), 0)
        elif mnem == "mov" and ops.startswith("rdx, qword ptr [rip"):     # a const TCHAR* global
            rdx = im.Q(a + n + int(ops[ops.index("rip") + 3:-1].replace(" ", ""), 0))
        elif mnem == "mov" and dest == "r8d" and ops == "r8d, 1": r8 = 1
        elif mnem == "lea" and dest == "r8d" and re.fullmatch(r"r8d, \[(\w+) \+ 1\]", ops) and \
                fam(re.fullmatch(r"r8d, \[(\w+) \+ 1\]", ops)[1]) in zero:
            r8 = 1                     # lea r8d, [zeroed + 1]
        elif mnem == "mov" and fam(dest) and "," in ops and fam(ops.split(",", 1)[1]) in zero and fam(dest) != "r8":
            zero.add(fam(dest))        # copy of a zeroed register
        elif mnem == "jmp" and ops.startswith("0x") and (fn <= int(ops, 16) < fn + size or int(ops, 16) not in linker.functions):
            continue                   # a jump inside the function (or to a split-off block), not a tail call
        elif mnem == "call" or (tail_calls and mnem == "jmp" and ops.startswith("0x")):
            hit = False
            if (r8 == 1 or not need_r8 and r8 is None) and not extra and rdx is not None and im.is_data(rdx) and ops.startswith("0x"):
                o = im.off(rdx)
                wide = o is not None and o % 2 == 0 and im.d[o] and not im.d[o + 1]
                s_ = im.wident(rdx) if wide else im.ident(rdx)
                if s_ and IDENT.fullmatch(s_.encode()):
                    out.append((int(ops, 16), s_)); hit = True
            if not hit and on_call and ops.startswith("0x"):
                out.extend(on_call(int(ops, 16)))
            rdx = r8 = None; extra = False
            zero -= {"a", "c", "d", "r8", "r9", "r10", "r11"}      # volatile across calls
            if mnem == "jmp" and not bounded: break
        elif mnem in ("ret", "int3"):
            if not bounded: break      # without a function-table size, the first return ends the body
            rdx = r8 = None; extra = False
        else:
            if dest in ("rdx", "edx", "dx", "dl", "dh"): rdx = None
            if dest in ("r8", "r8d", "r8w", "r8b"): r8 = None
        if mnem not in ("call", "jmp", "cmp", "test", "push") and not mnem.startswith("j") and fam(dest) in zero and \
                not (mnem == "mov" and "," in ops and fam(ops.split(",", 1)[1]) in zero):
            zero.discard(fam(dest))
    return out


def static_names_nested(im, linker, with_targets=False, max_callee=16384):
    """v11: FNames constructed by CRT static initializers, in initializer order, including
    (1) callbacks that tail-call the constructor (one global FName per callback), and
    (2) functions called directly by exactly one callback (constructors of static registry
    objects), whose FName constructions are placed at the call site.
    Only literal operands and call targets are read; nothing is executed."""
    if not linker or linker.blocked: return ([], set()) if with_targets else []
    md = linker.md; callbacks = linker.startup_callbacks()
    detail = md.detail; md.detail = False
    def size_of(fn, cap):
        known = linker.functions.get(fn)
        return min(known[0] if known else 512, cap, im.text[1] - fn)
    try:
        # direct callees and how many callbacks reach each one
        from collections import Counter
        users = Counter()
        for cb in callbacks:
            seen = set()
            for a, n, mnem, ops in md.disasm_lite(linker.read(cb, size_of(cb, 16384)), cb):
                if mnem in ("call", "jmp") and ops.startswith("0x"): seen.add(int(ops, 16))
                if mnem in ("ret", "int3") or mnem == "jmp": break
            users.update(seen)
        callbacks_set = set(callbacks)
        def collect(need_r8):
            def nested(t):
                if users[t] != 1 or not im.is_text(t) or t in callbacks_set: return []
                known = linker.functions.get(t)
                if not known or known[0] > max_callee: return []
                return _decode_fname_calls(im, linker, t, size_of(t, max_callee), True, need_r8=need_r8)
            out = []
            for cb in callbacks:
                out += _decode_fname_calls(im, linker, cb, size_of(cb, 16384), True, on_call=nested, need_r8=need_r8)
            return out
        out = collect(True)
        strict_shape = bool(out)
        if not out:
            # Builds whose FName constructors take no find-type argument (observed on 5.7):
            # the call shape is rcx = object, rdx = literal.
            out = collect(False)
    finally:
        md.detail = detail
    counts = Counter(t for t, s_ in out)
    if not counts: return ([], set()) if with_targets else []
    top = counts.most_common(1)[0][1]
    ctors = {t for t, n in counts.items() if n >= top / 4}
    if not strict_shape:
        # Without the find-type argument, other constructors share the call shape (FString from
        # a literal). Builds have one wide and one narrow FName constructor; they are the two
        # most frequent targets, and must clearly lead the next one (else abstain).
        ranked = counts.most_common(3)
        if len(ranked) < 2 or len(ranked) == 3 and ranked[1][1] < 1.5 * ranked[2][1]:
            return ([], set()) if with_targets else []
        ctors = {ranked[0][0], ranked[1][0]}
    if with_targets:
        return out, ctors
    return [s_ for t, s_ in out if t in ctors]

def native_order_table(rows):
    """{kind: {key: (callback position, call address, index)}} from the enum registry's
    registration rows (class keys are NoRegister functions, struct and enum keys params records)."""
    if not rows: return None
    out = {"class": {}, "struct": {}, "enum": {}}
    for pos, addr, idx, kind, key in rows:
        out.setdefault(kind, {}).setdefault(key, (pos, addr, idx))
    return out


def registration_order(hard, static, sbyname, cbyname, ebyname, snames, class_outer, functions=None,
                       static_first=False, config=(), native_order=None, enums_first=False):
    """{lowercase: (spelling, source)} for every name in first-registration order.

    static_first (v11): the hardcoded names and then the static-initializer names come before the
    type and native function names. Static initializers run before main; zen builds register
    type names and native functions only when compiled-in classes are processed (5.1 source)."""
    seen = {}
    def emit(s, src):
        if s:
            stem, _ = name_parts(s)
            seen.setdefault(stem.lower(), (stem, src))
    # native_order (v11): {"class": {NoRegister fn: position}, "struct"/"enum": {params va: position}},
    # the CRT registration order of compiled-in types (deferred registries keep it). Records it
    # does not cover keep address order after the covered ones.
    import bisect
    def by(kind, records, keyname="va"):
        # Records the table does not cover sit right after the covered record nearest below
        # them in address order (registration tables follow translation-unit order).
        table = (native_order or {}).get(kind) or {}
        covered = sorted((x["va"], table[x.get(keyname)]) for x in records if x.get(keyname) in table)
        vas = [v for v, _ in covered]
        def key(x):
            if x.get(keyname) in table: return (table[x[keyname]], 0, x["va"])
            i = bisect.bisect_right(vas, x["va"]) - 1
            return (covered[i][1] if i >= 0 else (-1,), 1, x["va"])
        return key
    skey, ckey, ekey = by("struct", sbyname.values()), by("class", cbyname.values(), "noreg"), by("enum", ebyname.values())
    if not native_order:
        skey = ckey = ekey = lambda x: x["va"]
    if static_first == "after types":
        # Type names keep their early place; native function names (registered with the class
        # body, when compiled-in classes are processed) follow the static names.
        for x in sorted(sbyname.values(), key=skey):
            if x.get("ops"): emit(x["name"], "type")
        for x in sorted(cbyname.values(), key=ckey): emit(x["name"], "type")
    if static_first:
        for s in hard: emit(s, "hardcoded")
        for s in static: emit(s, "static")
        for s in config: emit(s, "config")      # startup configuration keys (AppInit), if given
    # Type names are created when compiled-in types register, before the name table is consulted for
    # anything else that reflection constructs; they also bridge gaps in the recovered hardcoded list.
    # Structs with C++ ops register their name first (DeferCppStructOps); a class registers its name
    # together with its native functions (StaticRegisterNatives runs inside the class registration).
    for x in sorted(sbyname.values(), key=skey):
        if x.get("ops"): emit(x["name"], "type")
    for x in sorted(cbyname.values(), key=ckey):
        emit(x["name"], "type")
        for f in x["funcs"]: emit(f, "native function")
    for s in hard: emit(s, "hardcoded")
    for s in static: emit(s, "static")
    for x in sorted(sbyname.values(), key=skey): emit(x["name"], "type")
    for x in sorted(ebyname.values(), key=ekey): emit(x["name"], "type")
    def enum_entries():
        for e in sorted(ebyname.values(), key=ekey):
            emit(e["name"], "enum")
            for n, _ in e["entries"]:
                if "::" not in n: emit(n, "enum entry")     # namespaced entries are distinct FNames
    if enums_first: enum_entries()          # 5.1 source: enums are constructed before structs
    struct_of = {fn: lab[0] for fn, lab in snames.items()}
    done = set()
    def prop(n):
        emit(n["name"], "property")
        if n["kind"] == "Struct" and n["fn"] in struct_of: struct(struct_of[n["fn"]])
        for c in reversed(n.get("children", [])): prop(c)
    def struct(name):
        if name in done or name not in sbyname: return
        done.add(name); x = sbyname[name]
        if x.get("super_fn") in struct_of: struct(struct_of[x["super_fn"]])
        emit(name, "struct")
        for n in reversed(x["tree"]): prop(n)
    for x in sorted(sbyname.values(), key=skey): struct(x["name"])
    cdone = set()
    def klass(name):
        if name in cdone or name not in cbyname: return
        cdone.add(name); x = cbyname[name]
        if x["deps"] and x["deps"][0] in class_outer: klass(class_outer[x["deps"][0]])
        emit(name, "class")
        for f, fn in zip(x["funcs"], x["ffns"]):       # FunctionLinkArray: each UFunction is constructed
            emit(f, "function")
            for n in reversed((functions or {}).get(fn, {}).get("tree", [])): prop(n)
        for n in reversed(x["tree"]): prop(n)
    for x in sorted(cbyname.values(), key=ckey): klass(x["name"])
    # Enum entry names measured later than struct/class properties on development builds (plugin
    # enums register in later module batches); unqualified entries share the property name space.
    enum_entries()
    return seen

def apply(out_e, out_s, display, namespaced=()):
    """Rewrite property names and regular enum entry names to their display spelling. Record names
    and type references keep their registered spelling (they are created first). Entries of
    namespaced enums are "Enum::Entry" FNames of their own. Returns (enums, structs, renamed)."""
    renamed = 0
    def N(s):
        nonlocal renamed
        stem, suffix = name_parts(s)
        d = display.get(stem.lower(), (stem,))[0] + suffix
        if d != s: renamed += 1
        return d
    structs = {name: {"super": x["super"], "count": x["count"],
                      "props": [dict(p, name=N(p["name"])) for p in x["props"]]} for name, x in out_s.items()}
    enums = {}
    for name, entries in out_e.items():
        if name in namespaced:
            enums[name] = entries
            continue
        rendered = [(value, N(entry)) for value, entry in entries]
        enums[name] = respell_enum_maximum(name, entries, rendered, display)
        renamed += enums[name] != rendered
    return enums, structs, renamed



def _signed(value):
    return value - (1 << 64) if value >= 1 << 63 else value


def automatic_maximum_name(name, entries, qualified):
    """Name of the maximum that registration would add, without any qualifier.

    The prefix is computed on the stored entry names, which carry "Enum::" for scoped forms.
    """
    stored = [f"{name}::{e}" if qualified else e for e in entries]
    prefix = os.path.commonprefix(stored)
    cut = prefix.rfind("_")
    prefix = prefix[:cut] if cut > 0 else name
    if qualified and prefix.startswith(name + "::"):
        prefix = prefix[len(name) + 2:]
    return prefix + "_MAX"


def restore_automatic_maxima(enums, info, flags_policy=None):
    """Append the maximum that registration adds when an enum has none.

    Registration looks for "MAX" and for "<prefix>_MAX", where the prefix is the case-sensitive
    common prefix of the stored entry names up to its last underscore (else the enum name). Shipping
    builds compare names without case. When neither exists, it appends "<prefix>_MAX" with the
    largest signed value plus one, unless another enum already uses that name. Saved mappings can
    lack the entry when entry spellings changed after generation or when the generator skipped it.

    info maps an enum name to {"qualified": bool, "flags": bool} from its parameter record.
    Enums without info are left alone. flags_policy gives the maximum of bit-flag enums:
    "signed maximum" (as for other enums) or "single-bit union" (the union of the zero and
    single-bit values); None leaves bit-flag enums alone.
    """
    # Only unscoped entries share one name space within a package.
    used = collections.Counter(entry.lower() for name, entries in enums.items()
                               if info.get(name) and not info[name]["qualified"] for _, entry in entries)
    out = {}
    for name, entries in enums.items():
        meta = info.get(name)
        names = [entry.rpartition("::")[2] for _, entry in entries]
        lowered = {entry.lower() for entry in names}
        if not entries or meta is None or name.startswith("Default__") or "max" in lowered:
            out[name] = entries
            continue
        if meta["flags"] and flags_policy is None:
            out[name] = entries
            continue
        maximum = automatic_maximum_name(name, names, meta["qualified"])
        if maximum.lower() in lowered or (not meta["qualified"] and used[maximum.lower()]):
            out[name] = entries
            continue
        values = [_signed(v) for v, _ in entries]
        if meta["flags"] and flags_policy == "single-bit union":
            union = 0
            for v in values:
                bits = v & ((1 << 64) - 1)
                if bits & (bits - 1) == 0:
                    union |= bits
            value = _signed(union) + 1
        else:
            value = max(values) + 1
        out[name] = [*entries, (value & ((1 << 64) - 1), maximum)]
        if not meta["qualified"]:
            used[maximum.lower()] += 1          # the first enum to register a shared maximum keeps it
    return out


def flags_maximum_policy(version):
    """Bit-flag enum maximum by engine version ("major.minor" prefix of the build string).

    UE 5.1 source takes the signed maximum for every enum. Builds from 5.5 on were observed to
    take the union of zero and single-bit values for bit-flag enums.
    """
    match = re.match(r"(\d+)\.(\d+)", version or "")
    if not match:
        return None
    return "single-bit union" if (int(match[1]), int(match[2])) >= (5, 5) else "signed maximum"
