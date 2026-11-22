"""Name the type constructors (Z_Construct_UScriptStruct_* / Z_Construct_UEnum_*) that property records
point to, from data only.

A property record names its struct or enum type only through the address of a code function. The code
is never read (it may be encrypted, e.g. by a SteamStub wrapper), so the link from that address to a
named FStructParams/FEnumParams record is inferred from evidence that holds for any UE build:

1. Link order. Each object file's code and its read-only data are laid out in the same order, so the
   position of a constructor in .text predicts where its type's registration entry lies in .rdata.
   Anchors with exact identity on both sides come from the registration tables themselves (class
   constructors and StaticClass, StaticStruct, NewStructOps, StaticEnum all point to code and sit in
   a named entry). For each constructor, the anchors around it bound a window of candidate types and
   give an interpolated expected position.
2. Alphabetical runs. In shipping builds the constructors with identical code shape (same size and
   unwind record in .pdata) appear in runs sorted case-insensitively by name within an object file;
   an order-preserving alignment over each run enforces that order.
3. Local evidence: the size window left for a member by its neighbours' offsets (hard upper bound,
   bonus for an exact fit) and word overlap between the type name and the names that use it
   (property names, the containing type's name; UE names nested types <Parent>_<Child>).

Every assignment records whether it came from a run, a window, strong local evidence or joint repair
of competing claims. Uncontested assignments remain fixed during that repair.
"""
import bisect, re
from collections import Counter, defaultdict
from functools import lru_cache

def signature_hints(functions, type_names):
    """Engine naming conventions on validated reflected signatures, not reference mappings.

    Only single struct inputs/returns are interpreted. Conflicting hints are retained together so
    the scorer can decline them; equal memory layout alone never implies equal native identity.
    """
    hints = defaultdict(set)
    accessors = defaultdict(set)
    for f in functions.values():
        method = f["name"]
        inputs = [n for n in f["tree"] if n["kind"] == "Struct" and n["name"] != "ReturnValue"]
        returns = [n for n in f["tree"] if n["kind"] == "Struct" and n["name"] == "ReturnValue"]
        def hint(nodes, name):
            if len(nodes) == 1 and name in type_names: hints[nodes[0]["fn"]].add(name)
        if method.startswith("Make"): hint(returns, method[4:])
        elif method.startswith("Break"): hint(inputs, method[5:])
        elif method.startswith("Conv_"):
            body = method[5:]
            pairs = [(nm, body[len(nm) + 2:]) for nm in type_names
                     if body.startswith(nm + "To") and body[len(nm) + 2:] in type_names]
            if len(pairs) == 1:
                hint(inputs, pairs[0][0]); hint(returns, pairs[0][1])
        elif "_" in method and not inputs:
            hint(returns, method.split("_", 1)[0])   # e.g. IntPoint_Zero
        # Typed math methods carry the native operand identities in their reflected names.
        # Require a complete, unambiguous parse of the ordered input signature. A partial
        # word match (or the same constructor acquiring two native identities) is rejected.
        for z, target in operator_hints(f, type_names).items(): hints[z].add(target)
        if "_" in method:
            target = method.split("_", 1)[0]
            nodes = inputs + returns
            if target in type_names and nodes and len({p["fn"] for p in nodes}) == 1:
                hints[nodes[0]["fn"]].add(target)
        # A property getter name alone is weak evidence (GetColor may return LinearColor).
        # Require matching Get<Type>/Set<Type> signatures on the same constructor, and an
        # exact inline size on both sides. No container stride is inferred here.
        for prefix, nodes in (("Get", returns), ("Set", inputs)):
            target = method[len(prefix):]
            if not method.startswith(prefix) or len(nodes) != 1 or target not in type_names: continue
            if not isinstance(type_names, dict) or "size" not in type_names[target]: continue
            node = nodes[0]; off = node.get("off")
            if off is None or node.get("dim", 1) != 1: continue
            following = [p["off"] for p in f["tree"] if p.get("off") is not None and p["off"] > off]
            following.append(f["size"])
            if min(following) - off == type_names[target]["size"]:
                accessors[(node["fn"], target)].add(prefix)
    paired = defaultdict(set)
    for (z, target), sides in accessors.items():
        if sides == {"Get", "Set"}: paired[z].add(target)
    for z, targets in paired.items():
        # Accessors name properties; factory/conversion names identify types more directly.
        # A weaker alias must not erase an existing unambiguous factory hint.
        if not hints.get(z): hints[z].update(targets)
    return dict(hints)

def operator_hints(function, type_names):
    method = function["name"]
    operation, sep, suffix = method.partition("_")
    if not sep or operation not in {"Add", "Subtract", "Multiply", "Divide", "Percent", "EqualEqual",
                                    "NotEqual", "Less", "Greater", "LessEqual", "GreaterEqual", "Negate"}:
        return {}
    nodes = [p for p in function["tree"] if p["name"] != "ReturnValue"]
    if not 1 <= len(nodes) <= 2 or not any(p["kind"] == "Struct" for p in nodes): return {}
    scalar = {"Int": ("Int", "Int32"), "Int64": ("Int64",), "Byte": ("Byte",), "Float": ("Float",),
              "Double": ("Double",), "Bool": ("Bool",), "Name": ("Name",), "Str": ("String",)}
    # The reflected operand suffix must account for every input, in declaration order.
    candidates = sorted(type_names)
    matches = []
    def parse(i, remaining, mapping):
        if i == len(nodes):
            if not remaining: matches.append(mapping)
            return
        node = nodes[i]
        for label in candidates if node["kind"] == "Struct" else scalar.get(node["kind"], ()):
            if not remaining.startswith(label): continue
            updated = dict(mapping)
            if node["kind"] == "Struct":
                z = node["fn"]
                if z in updated and updated[z] != label: continue
                updated[z] = label
            parse(i + 1, remaining[len(label):], updated)
    parse(0, suffix, {})
    unique = {tuple(sorted(m.items())) for m in matches}
    return dict(next(iter(unique))) if len(unique) == 1 else {}

def toks(s):
    out = set()
    for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", s):
        t = t.lower()
        if len(t) > 3 and t.endswith("ies"): t = t[:-3] + "y"
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"): t = t[:-1]
        out.add(t)
    return out

def use_profile(names):
    """Word statistics of the property names that use one constructor: (Counter of words, count)."""
    c = Counter()
    for n in names: c.update(toks(n))
    return c, max(1, len(names))

def word_score(profile, parent_toks, type_name, type_toks, exact_names=()):
    """Each word of the type name earns credit by the share of using names that contain it (so one odd
    name among hundreds of uses cannot outvote the common word), a little for appearing at all, and a
    little when it only appears in the containing type's name."""
    c, n = profile
    v = 0.0
    for t in type_toks:
        k = c.get(t, 0)
        # Four-letter technical roots are common in type names (Linkable / EndLink).
        # Keep the normal five-letter prefix rule; admit only the explicit -able suffix here.
        if not k and len(t) == 8 and t.endswith("able"):
            k = 0.6 * c.get(t[:-4], 0)
        if not k and len(t) >= 5:   # shared stem (Rotator / Rotation, Transform / Transforms)
            k = 0.6 * sum(m for u, m in c.items() if len(u) >= 5 and u[:5] == t[:5])
        f = min(k / n, 1.0)
        if k >= 2 or f >= 0.02: v += 2.5 * f + 0.5
        elif k: v += 2.5 * f
        elif t in parent_toks: v += 0.5
        else: v -= 0.5
    v -= 0.3 * max(0, len(type_toks) - 3)   # long names collect stray words from many uses
    flat = type_name.lower().replace("_", "")
    v += 0.5 * max((c[t] / n for t in c if len(t) >= 5 and t in flat and t not in type_toks), default=0.0)
    # An entire multiword type name is stronger than a subset of a longer field name.
    # E.g. GameplayTagRedirects names GameplayTagRedirect, not merely GameplayTag.
    if len(type_toks) >= 2 and frozenset(type_toks) in exact_names: v += 1.0
    return v

def base_score(children, type_name, type_toks):
    """Words of a candidate for a constructor that is only ever a super. Each word earns the share
    of the children whose names contain it. A word no child has costs nothing: base types carry
    abstract words (SkeletalControlBase, BinaryAggregateOp) that their children replace, while a
    leaf sibling shares the family words and so must not win on them alone. "Base" is the engine's
    own suffix for such parents."""
    ch = [toks(c) for c in children]
    return sum(sum(t in c for c in ch) / len(ch) + (0.5 if t == "base" else 0.0) for t in type_toks)

def runs(fsig, refs, gap=0x80):
    """Runs of functions sharing the constructor signature (the (size, unwind) of most referenced ones)."""
    c = Counter(fsig.get(z) for z in refs if z in fsig)
    if not c: return {}
    total = sum(c.values())
    sigs = {sg for sg, k in c.most_common(3) if k >= 0.02 * total}
    seq = sorted(a for a, v in fsig.items() if v in sigs)
    rid = {}; r = 0
    for i, a in enumerate(seq):
        if i == 0 or a - seq[i - 1] > gap: r += 1
        rid[a] = r
    return rid

DEBUG = {}

def sort_key(name):
    """Runs are sorted case-insensitively by type name. (Observed exceptions: in one 5.7 core run
    Rotator3d/Rotator3f precede Rotator, as a mangled-symbol order would give; elsewhere Vector precedes
    Vector2D. The plain key scored best on all four reference games.)"""
    return name.lower()

def assign(refs, datapos, anchors, score, fsig, prior_anchors=None, k=3, slack=0x10000, w=0.5, scale=0x4000, base=1.0, maxcands=3000,
           cap=6.0, topk=0, others=frozenset(), eps=0.0, override=3.0, unique=True, extra_candidates=None, joint=False,
           preserve_uncontested=True):
    """refs: constructor addresses to name; datapos: {type name: registration entry address};
    anchors: [(code address, data address)] with exact identity; score(z, name) -> float or None.
    Returns {z: (name, how)}. With joint repair, preserve_uncontested keeps independent run/window
    choices fixed; only duplicate or unnamed claims compete for the remaining names."""
    A = sorted(anchors); az = [a for a, _ in A]
    items = sorted(datapos.items(), key=lambda kv: kv[1]); pv = [v for _, v in items]
    if not A: return {}
    B = sorted(prior_anchors) if prior_anchors else A; bz = [a for a, _ in B]
    win, exp = {}, {}
    for z in refs:
        i = bisect.bisect_left(az, z)
        ds = [A[j][1] for j in range(max(0, i - k), min(len(A), i + k))]
        lo, hi = min(ds) - slack, max(ds) + slack
        win[z] = [items[j][0] for j in range(bisect.bisect_left(pv, lo), bisect.bisect_right(pv, hi))]
        if extra_candidates:
            win[z] = list(dict.fromkeys(win[z] + [nm for nm in extra_candidates.get(z, ()) if nm in datapos]))
        if topk:   # the best few by local evidence anywhere (types far from any anchor, e.g. core structs)
            sc = sorted(((score(z, nm), nm) for nm in datapos), key=lambda x: -1e9 if x[0] is None else x[0], reverse=True)[:topk]
            win[z] = list(dict.fromkeys(win[z] + [nm for v, nm in sc if v is not None]))
        i = bisect.bisect_left(bz, z)
        if 0 < i < len(B):
            (al, dl), (ar, dr) = B[i - 1], B[i]
            exp[z] = dl + (dr - dl) * (z - al) / max(1, ar - al)
        else:
            exp[z] = B[min(i, len(B) - 1)][1]
    @lru_cache(maxsize=None)
    def S(z, nm):
        v = score(z, nm)
        return None if v is None else v + base - w * min(cap, abs(datapos[nm] - exp[z]) / scale)
    DEBUG.update(win=win, exp=exp, S=S)
    rid = runs(fsig, refs)
    byrun = defaultdict(list)
    for z in refs: byrun[rid.get(z, ("solo", z))].append(z)
    members = defaultdict(list)
    if eps:
        for a, r in rid.items(): members[r].append(a)
    refset = set(refs)
    out = {}; options = {}
    for _, zs in byrun.items():
        zs.sort()
        cn = sorted(set().union(*[win[z] for z in zs]), key=sort_key)
        if len(zs) == 1 or len(cn) > maxcands:
            for z in zs:
                sc = [(S(z, n), n) for n in win[z]]
                sc = [x for x in sc if x[0] is not None]
                if sc: out[z] = (max(sc)[1], "window")
                if joint and sc:
                    best = max(v for v, _ in sc)
                    options[z] = {n: v + 0.5 * (v - best) for v, n in sc}
            continue
        wset = {z: set(win[z]) for z in zs}
        seq = zs
        if eps and zs[0] in rid:
            # unreferenced constructors between referenced ones still occupy a name in the sorted run
            seq = [a for a in sorted(members[rid[zs[0]]]) if zs[0] <= a <= zs[-1] and (a in refset or a not in others)]
        al = align(seq, cn, lambda z, n: (S(z, n) if n in wset[z] else None) if z in refset else eps,
                   free=lambda z: z not in refset, marginals=joint)
        if joint:
            al, regret = al
            for z in zs:
                options[z] = {n: S(z, n) + 0.5 * loss for n, loss in regret.get(z, {}).items()}
        al = {z: n for z, n in al.items() if z in refset}
        for z, n in al.items(): out[z] = (n, "run")
        if override:   # strong local evidence beats the order constraint (runs are mostly, not strictly, sorted)
            for z in zs:
                sc = [(S(z, n), n) for n in win[z]]
                sc = [x for x in sc if x[0] is not None]
                if not sc: continue
                bv, bn = max(sc)
                cur = S(z, out[z][0]) if z in out else None
                if bn != (out.get(z) or (None,))[0] and (cur is None or bv - cur >= override):
                    out[z] = (bn, "local")
    if joint:
        # Resolve competing claims together, retaining alternatives from whole-run alignment.
        # Greedy duplicate repair can let a container-only use steal a base type's only good name.
        DEBUG["pre_joint"] = dict(out)
        fixed = {}
        if preserve_uncontested:
            claims = Counter(n for n, _ in out.values())
            fixed = {z: lab for z, lab in out.items() if claims[lab[0]] == 1}
            taken = {n for n, _ in fixed.values()}
            options = {z: {n: v for n, v in row.items() if n not in taken}
                       for z, row in options.items() if z not in fixed}
        return {**fixed, **{z: (n, "joint") for z, n in joint_assignment(options).items()}}
    if unique:   # one constructor per type: the weaker claim moves to its best untaken candidate
        for _ in range(3):
            claims = defaultdict(list)
            for z, (n, h) in out.items(): claims[n].append(z)
            moved = False
            for n, zs in claims.items():
                if len(zs) < 2: continue
                zs.sort(key=lambda z: S(z, n) if S(z, n) is not None else -1e9, reverse=True)
                for z in zs[1:]:
                    taken = {m for m, _ in out.values()}
                    alt = [(S(z, m), m) for m in win[z] if m not in taken]
                    alt = [x for x in alt if x[0] is not None]
                    if alt: out[z] = (max(alt)[1], "unique"); moved = True
                    else: del out[z]
            if not moved: break
    return out

def align(zs, names, score, free=lambda z: True, marginals=False):
    """Order-preserving injection, with skips. With marginals, also return each candidate's loss
    relative to the best whole-run alignment (zero for a candidate on an optimal path)."""
    n, m = len(zs), len(names)
    NEG = float("-inf")
    best = [[0.0] * (m + 1)] + [[NEG] * (m + 1) for _ in range(n)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, prev = best[i], best[i - 1]
        row[0] = prev[0]; back[i][0] = 2
        for j in range(1, m + 1):
            b, t = row[j - 1], 0
            if prev[j] > b: b, t = prev[j], 2          # leave zs[i-1] unnamed
            if prev[j - 1] > NEG:
                s = score(zs[i - 1], names[j - 1])
                if s is not None and prev[j - 1] + s > b: b, t = prev[j - 1] + s, 1
            row[j] = b; back[i][j] = t
    out = {}; i, j = n, m
    while i > 0:
        t = back[i][j]
        if t == 1: out[zs[i - 1]] = names[j - 1]; i -= 1; j -= 1
        elif t == 2: i -= 1
        else: j -= 1
    if not marginals: return out
    suffix = [[0.0] * (m + 1) for _ in range(n + 1)]
    regret = {}
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            s = score(zs[i], names[j])
            suffix[i][j] = max(suffix[i + 1][j], suffix[i][j + 1],
                               suffix[i + 1][j + 1] + s if s is not None else NEG)
    optimum = best[n][m]
    for i, z in enumerate(zs):
        regret[z] = {}
        for j, name in enumerate(names):
            s = score(z, name)
            if s is not None:
                regret[z][name] = best[i][j] + s + suffix[i + 1][j + 1] - optimum
    return out, regret

def joint_assignment(options):
    """Maximum-weight one-to-one naming, with a private unmatched slot for every constructor.

    These weights combine local evidence with the loss of the best entire run alignment that
    includes each candidate. Run order is soft; identity uniqueness is hard. No reference is read.
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    zs = sorted(options); names = sorted({name for row in options.values() for name in row})
    if not zs or not names: return {}
    index = {name: j for j, name in enumerate(names)}
    weights = np.full((len(zs), len(names) + len(zs)), -1e12)
    for i, z in enumerate(zs):
        for name, weight in options[z].items(): weights[i, index[name]] = weight
        weights[i, len(names) + i] = -10.0
    rows, cols = linear_sum_assignment(weights, maximize=True)
    return {zs[i]: names[j] for i, j in zip(rows, cols) if j < len(names)}
