"""Which Anim Blueprint generated struct copy a startup runtime dump keeps (data only).

Every loaded Anim Blueprint owns its own `AnimBlueprintGeneratedConstantData` and
`AnimBlueprintGeneratedMutableData` struct. A runtime mapping is written in object-array order and
keyed by name, so the copy that counts is the one in the highest object slot among the Anim
Blueprints loaded at dump time.

Load model (from the engine source, checked on development dumps):
- startup loads the game instance class, then the default map; the map's game mode comes from the
  map's world settings, else from the project default game mode;
- a package is created after the packages it imports, so the startup set is the hard package
  graph closure of those roots (post-order gives a creation order);
- object slots freed by garbage collection are reused last-freed first, and the default map may
  open further maps through soft references within the first seconds.

On development dumps no single order predicts the kept copy reliably: slot reuse and later map
loads depend on runtime state that cooked data does not show. A wrong copy turns one missing record
into many wrong fields, so the rule keeps a record only when every copy in the startup set is
identical and the default map side loads an Anim Blueprint. Otherwise it abstains and the record
stays missing. (A second signal, "last created copy is also the closest one", was right on every
development case it fired on and failed hold-out confirmation; it stays available but off.)

v12 study (development gold only, 34 UE5 games with records):
- the structs are cooked exports, not generated at load: 311 of 313 gold copies equal a copy read
  from the packages (the other 2 come from a package the scan did not read). Gold holds every
  loaded copy as one block after the native types, and the name-keyed reader keeps the last one;
- the last copy is the one in the highest object slot. Under last-freed-first slot reuse it is the
  first created copy when every copy lands in freed slots, otherwise the last created one. Both
  cases occur in most engine versions from 5.0 to 5.7, and 16 of 47 cases with differing copies
  match neither.
  The number of freed slots is runtime state, so no rule from cooked data passed: first/last
  created under package post-order, container offset order or an IO-order simulation, a version
  regime, and agreement of these made 8 to 16 of 38 development games worse.

A child Anim Blueprint (its class derives from another Anim Blueprint class) has no struct export
in its package; at load it gets an empty constant-data struct whose super is the parent's struct.
"""
import re

NAMES = ("AnimBlueprintGeneratedConstantData", "AnimBlueprintGeneratedMutableData")
GAME_MODE_NATIVE = ("GameModeBase",)
_KEYS = ("GameDefaultMap", "GameInstanceClass", "GlobalDefaultGameMode")


def startup_settings(ini_files):
    """{key: long package name} from the project DefaultEngine.ini (map and class paths)."""
    best = None
    for path, raw in ini_files.items():
        p = path.replace("\\", "/")
        if not p.lower().endswith("/config/defaultengine.ini") or "/Plugins/" in p: continue
        if p.lower().startswith(("engine/", "/engine/")): continue     # engine tree, not the project
        if best is None or len(p) < len(best[0]): best = (p, raw)
    out = {}
    if best is None: return out
    text = best[1].decode("utf-8", "replace") if isinstance(best[1], bytes) else best[1]
    for k, v in re.findall(r"(?im)^\s*(" + "|".join(_KEYS) + r")\s*=\s*(\S+)", text):
        v = v.strip().strip('"')
        if v.startswith("/") and k not in out: out[k] = v.split(".")[0]
    return out


def post_order(graph, roots):
    """Depth-first post-order of the package graph from the roots (imports in stored order)."""
    seen, out = set(), []
    for root in roots:
        if root not in graph or root in seen: continue
        seen.add(root); stack = [(root, iter(graph.get(root) or ()))]
        while stack:
            node, it = stack[-1]
            nxt = next((q for q in it if q in graph and q not in seen), None)
            if nxt is None:
                stack.pop(); out.append(node)
            else:
                seen.add(nxt); stack.append((nxt, iter(graph.get(nxt) or ())))
    return out


def _native_chain(structs, name, limit=64):
    out = []
    while name and len(out) < limit:
        out.append(name); name = (structs.get(name) or {}).get("super")
    return out


def game_mode_packages(scan, structs):
    """Packages whose Blueprint class derives (through parents) from a native game mode."""
    memo = {}

    def is_gm(pkg, depth=0):
        if pkg in memo: return memo[pkg]
        memo[pkg] = False
        if depth > 32: return False
        res = False
        for kind, sup in scan.class_supers.get(pkg, ()):
            if kind == "script" and sup and any(n in GAME_MODE_NATIVE for n in _native_chain(structs, sup)): res = True
            elif kind == "package" and sup and is_gm(sup, depth + 1): res = True
        memo[pkg] = res
        return res

    return {p for p in scan.class_supers if is_gm(p)}


def load_sequence(scan, settings, structs):
    """(creation order of the startup packages, how many come with the game instance, roots)."""
    gi, mp, gm = (settings.get(k) for k in ("GameInstanceClass", "GameDefaultMap", "GlobalDefaultGameMode"))
    roots = [gi, mp]
    if gm and mp in scan.packages:
        modes = game_mode_packages(scan, structs)
        if not any(p in modes for p in post_order(scan.packages, [mp])): roots.append(gm)
    seq = post_order(scan.packages, [r for r in roots if r])
    return seq, (len(post_order(scan.packages, [gi])) if gi else 0), roots


def _copies(scan, seq, name):
    recs = {}
    for pkg, _owner, nm, rec in scan.records:
        recs.setdefault(pkg, {})[nm] = rec
    out = []
    for p in seq:
        if p not in scan.anim_classes: continue
        if p in recs:
            if name in recs[p]: out.append(recs[p][name])
        elif name == NAMES[0] and any(s[0] == "package" and s[1] in scan.anim_classes for s in scan.class_supers.get(p, ())):
            out.append({"super": name, "count": 0, "props": []})
    return out


def depths(graph, roots):
    """Breadth-first import distance from the roots."""
    out = {}; frontier = [r for r in roots if r in graph]
    for r in frontier: out[r] = 0
    while frontier:
        nxt = []
        for p in frontier:
            for q in graph.get(p) or ():
                if q in graph and q not in out: out[q] = out[p] + 1; nxt.append(q)
        frontier = nxt
    return out


def choose(scan, settings, structs, agreement=False):
    """({struct name: record} the rule is sure of, {struct name: reason it abstained}).

    1. identical copies: every copy in the startup set is the same record (the pipeline rule);
    2. agreement (agreement=True, measured, OFF): the last created copy is also the only record
       among the Anim Blueprints closest to the startup roots. It had no wrong pick on development
       but made 5 of 31 hold-out games worse, so it is not used."""
    seq, early, roots = load_sequence(scan, settings, structs)
    dist = depths(scan.packages, [r for r in roots if r])
    chosen, why = {}, {}
    for name in NAMES:
        copies = _copies(scan, seq, name)
        if not copies:
            why[name] = "no Anim Blueprint in the startup load sequence"; continue
        if not _copies(scan, post_order(scan.packages, roots[1:]), name):
            # the default map (and its game mode) loads no Anim Blueprint: the dump-time copies were
            # loaded by content the map opens later (soft references), which the data does not show
            why[name] = "the default map loads no Anim Blueprint"; continue
        if all(c == copies[0] for c in copies):
            chosen[name] = copies[0]; continue
        if agreement:
            own = [(dist[p], c[0]) for p in seq if p in dist for c in [_copies(scan, [p], name)] if c]
            near = min(d for d, _ in own)
            closest = [c for d, c in own if d == near]
            if all(c == closest[0] for c in closest) and closest[0] == copies[-1]:
                chosen[name] = copies[-1]; continue
        why[name] = f"{len(copies)} copies that differ; last created and closest disagree"
    return chosen, why
