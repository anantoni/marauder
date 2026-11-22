"""Engine type relations from other games' static mappings (data-only naming aid).

When code links are unavailable (protected executable), a struct constructor is named from data
evidence only (naming.py). Engine and plugin types keep the same relations in every game built on
them: StaticMeshComponent.StreamingTextureData is an array of StreamingTextureBuildInfo and
AnimNode_ModifyBone derives from AnimNode_SkeletalControlBase in any UE5 build. Those relations are
read from mappings this generator produced with verified code links for other games (their own
files; no runtime dump, no third-party mapping).

A relation names the struct type at one role of one field (owner, field, role) or the super of one
struct. A constructor gets a reference name only when every reference relation that reaches it
agrees on one name, at least min_votes references show it, and the name is a struct of the target.
"""
from collections import Counter, defaultdict


def _roles(t, role=""):
    """(role, struct name) for each struct inside a usmap property type."""
    k = t["t"]
    if k == "StructProperty": yield role, t["struct"]
    elif k in ("ArrayProperty", "SetProperty", "OptionalProperty"): yield from _roles(t["inner"], role + "i")
    elif k == "MapProperty":
        yield from _roles(t["key"], role + "k"); yield from _roles(t["value"], role + "v")


def relations(structs):
    """{("field", owner, field, role) or ("super", child): struct name} of one usmap structs table."""
    out = {}
    for owner, rec in structs.items():
        if rec.get("super"): out[("super", owner)] = rec["super"]
        for p in rec["props"]:
            for role, name in _roles(p["type"]):
                out[("field", owner, p["name"], role)] = name
    return out


def load(paths):
    """relations() of each .usmap file (mappings generated with verified code links)."""
    from . import usmap
    return [relations(usmap.read(p)[1]) for p in paths]


def node_roles(node, role=""):
    """(role, constructor) for each struct constructor inside a scanned property node."""
    k = node["kind"]
    if k == "Struct":
        if node.get("fn"): yield role, node["fn"]
        return
    ch = node.get("children") or []
    if k in ("Array", "Set", "Optional") and ch: yield from node_roles(ch[0], role + "i")
    elif k == "Map" and len(ch) == 2:
        yield from node_roles(ch[0], role + "k"); yield from node_roles(ch[1], role + "v")


def hints(pool, owners, supers, targets, min_votes=2):
    """{constructor: name} from reference relations.

    pool: list of relations() of reference games; owners: {owner name: property tree};
    supers: {child name: super constructor}; targets: struct names of the target game."""
    keys = defaultdict(set)
    for owner, tree in owners.items():
        for n in tree:
            for role, z in node_roles(n):
                keys[z].add(("field", owner, n["name"], role))
    for child, z in supers.items():
        if z: keys[z].add(("super", child))
    lower = {t.lower(): t for t in targets}
    out = {}
    for z, ks in keys.items():
        votes = Counter(); refs = 0
        for rel in pool:
            seen = {rel[k] for k in ks if k in rel}
            if not seen: continue
            refs += 1
            for name in seen: votes[name.lower()] += 1
        if len(votes) == 1:
            name, k = votes.most_common(1)[0]
            if k >= min_votes and name in lower: out[z] = lower[name]
    return out
