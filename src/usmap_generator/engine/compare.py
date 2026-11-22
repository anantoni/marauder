"""Compare a generated .usmap with a reference .usmap (validation only; the reference never feeds generation).

usage: python3 -I compare.py <generated.usmap> <reference.usmap> [--exe <exe>] [--json out.json] [--differences census.json]

Metrics (ref = reference, gen = generated):
- types: struct/class names in both / in ref (recall) and / in gen (precision). With --exe, ref types whose
  name never occurs in the executable as an ASCII string are counted as asset-defined (Blueprint classes,
  user-defined structs) and reported separately: a static reader of the executable cannot see them.
- props: over types present in both, a property matches when (index, array dim, name, full type) are equal;
  recall = matches / ref props, precision = matches / gen props. Also by field: names only, types only.
- exact: types whose whole property list and super equal the reference.
- enums: names in both; entries exact (same value->name pairs).
"""
import json, os, sys
from . import usmap as U
from .pe import PE

def pkey(p): return (p["i"], p["dim"], p["name"], U.fmt(p["type"]))

def native_names(exe, names):
    """Names occurring in the executable as a NUL-delimited ASCII identifier, or as a UTF-16 identifier
    (registration tables; classes carry their A/U/I prefix there)."""
    import re
    image = PE(exe)
    found = set()
    for section in (".rdata", ".data"):
        if section not in image.secs: continue
        lo, hi, raw = image.sec(section)
        m = memoryview(image.d)[raw:raw + hi - lo]
        found.update(x.decode() for x in re.findall(rb"(?<=\0)[A-Za-z_][A-Za-z0-9_]{1,200}(?=\0)", m))
        for x in re.findall(rb"(?:[A-Za-z0-9_]\0){2,200}\0\0", m):
            w = x[:-2].decode("utf-16-le", "ignore")
            found.add(w)
            if len(w) > 1 and w[0] in "AUI": found.add(w[1:])
    return {n for n in names if n in found}

def compare(gen, ref, exe=None):
    ge, gs = gen; re_, rs = ref
    rnames = set(rs)
    native = native_names(exe, rnames) if exe else rnames
    out = {}
    both = [n for n in rs if n in gs]
    rn = [n for n in rs if n in native]
    out["types"] = {"ref": len(rs), "refNative": len(rn), "gen": len(gs), "both": len(both),
                    "recallNative": len([n for n in rn if n in gs]) / max(1, len(rn)),
                    "recallAll": len(both) / max(1, len(rs)),
                    "precision": len(both) / max(1, len(gs))}
    m = rp = gp = mname = mtype = exact = sup_ok = 0
    worst = []
    for n in both:
        r, g = rs[n], gs[n]
        rk = {p["i"]: pkey(p) for p in r["props"]}; gk = {p["i"]: pkey(p) for p in g["props"]}
        rp += len(rk); gp += len(gk)
        hit = sum(1 for i, k in rk.items() if gk.get(i) == k)
        m += hit
        mname += sum(1 for i, k in rk.items() if i in gk and gk[i][1:3] == k[1:3])
        mtype += sum(1 for i, k in rk.items() if i in gk and gk[i][3] == k[3])
        sup_ok += (r["super"] == g["super"])
        if hit == len(rk) == len(gk) and r["super"] == g["super"] and r["count"] == g["count"]: exact += 1
        elif len(rk) - hit: worst.append((len(rk) - hit, n))
    out["props"] = {"ref": rp, "gen": gp, "match": m, "recall": m / max(1, rp), "precision": m / max(1, gp),
                    "nameRecall": mname / max(1, rp), "typeRecall": mtype / max(1, rp)}
    out["exactTypes"] = exact / max(1, len(both))
    out["superMatch"] = sup_ok / max(1, len(both))
    eb = [n for n in re_ if n in ge]
    eexact = sum(1 for n in eb if sorted(re_[n]) == sorted(ge[n]))
    out["enums"] = {"ref": len(re_), "gen": len(ge), "both": len(eb), "recall": len(eb) / max(1, len(re_)),
                    "precision": len(eb) / max(1, len(ge)), "entriesExact": eexact / max(1, len(eb))}
    out["worstTypes"] = [n for _, n in sorted(worst, reverse=True)[:25]]
    out["missingNative"] = sorted(n for n in rn if n not in gs)[:40]
    return out

if __name__ == "__main__":
    a = sys.argv[1:]
    exe = a[a.index("--exe") + 1] if "--exe" in a else None
    js = a[a.index("--json") + 1] if "--json" in a else None
    gen, ref = U.read(a[0]), U.read(a[1])
    res = compare(gen, ref, exe)
    if '--differences' in a:
        from .schema_diff import differences
        with open(a[a.index('--differences') + 1], 'w') as f:
            json.dump(differences(gen, ref), f, indent=2)
    if js: json.dump(res, open(js, "w"), indent=1)
    for k in ("types", "props", "enums"): print(k, {x: (round(y, 4) if isinstance(y, float) else y) for x, y in res[k].items()})
    print("exactTypes", round(res["exactTypes"], 4), "superMatch", round(res["superMatch"], 4))
