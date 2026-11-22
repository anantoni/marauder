"""Display spelling from reference dumps of other games built on the same engine (evaluation data
the project already holds; no game code is read).

Some engine names are first created by engine code (FName literals in constructors and startup
code), before reflection registers the same name with another spelling. The static order model
cannot see those. When the generator made the same choice on similar reference games and every
one of their dumps shows one other spelling, the target most likely has the same engine code.

A reference is built from a generated mapping and the runtime dump of the same build:
{"records": set of record names, "rows": {lowercase stem: (generated spelling, dump spelling)}}.
Similarity is the Jaccard index of record names (types and enums), so engine version and plugin
set dominate. A group is respelled only when the n most similar references that contain it with the
same generated spelling all show one other dump spelling (at least min_votes of them), none of them
and no reference of nearly the same similarity kept the generated spelling, and the new spelling
occurs in the target executable's strings.
"""
from collections import Counter, defaultdict
from .fname_order import name_parts


def groups(enums, structs, qualified):
    """{lowercase stem: set of spellings} over property names and entries of regular enums."""
    out = defaultdict(set)
    for record in structs.values():
        for p in record["props"]:
            stem = name_parts(p["name"])[0]
            out[stem.lower()].add(stem)
    for name, entries in enums.items():
        for _, e in entries:
            if "::" in e or qualified(name, e):
                continue
            stem = name_parts(e)[0]
            out[stem.lower()].add(stem)
    return out


def reference(generated, dump, qualified):
    g, d = groups(*generated, qualified), groups(*dump, qualified)
    rows = {}
    for k in g.keys() & d.keys():
        if len(g[k]) == 1 and len(d[k]) == 1:
            rows[k] = (next(iter(g[k])), next(iter(d[k])))
    return {"records": set(generated[0]) | set(generated[1]), "rows": rows}


def choose(enums, structs, references, exe, qualified, n=3, min_votes=2, band=0.05):
    """{lowercase stem: new spelling} for the target mapping.

    band: a reference whose similarity is within band of the n-th most similar one and that kept
    the generated spelling also blocks the change (the spelling then depends on game content,
    not on the engine build alone). None disables the check."""
    records = set(enums) | set(structs)
    sim = lambda r: len(records & r["records"]) / max(1, len(records | r["records"]))
    scored = sorted(((sim(r), i, r) for i, r in enumerate(references)), key=lambda x: (-x[0], x[1]))
    ranked = [r for _, _, r in scored[:n]]
    near = [r for s, _, r in scored[n:] if band is not None and scored[:n] and s >= scored[:n][-1][0] - band]
    out = {}
    for k, spellings in groups(enums, structs, qualified).items():
        if len(spellings) != 1:
            continue
        mine = next(iter(spellings))
        votes, kept = Counter(), 0
        for r in ranked:
            row = r["rows"].get(k)
            if not row or row[0] != mine:
                continue
            if row[1] == mine:
                kept += 1
            else:
                votes[row[1]] += 1
        if not kept:
            kept = sum(1 for r in near if r["rows"].get(k) == (mine, mine))
        if kept or len(votes) != 1:
            continue
        spelling, count = votes.most_common(1)[0]
        if count >= min_votes and spelling in exe:
            out[k] = spelling
    return out


def respell(enums, structs, choice, qualified):
    """Apply choose() output to property names and regular enum entries (suffixes kept)."""
    def pick(name):
        stem, suffix = name_parts(name)
        return choice.get(stem.lower(), stem) + suffix
    out_s = {n: dict(x, props=[dict(p, name=pick(p["name"])) for p in x["props"]]) for n, x in structs.items()}
    out_e = {n: [(v, e if "::" in e or qualified(n, e) else pick(e)) for v, e in ents] for n, ents in enums.items()}
    return out_e, out_s
