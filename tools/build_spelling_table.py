#!/usr/bin/env python3
"""Derive the version-keyed spelling table from your own captures.
Input: a directory of reference pickles, one per benchmark game
({"rows": {lowercase stem: (generated spelling, runtime-dump spelling)}}), plus a JSON map {pickle stem: "major.minor"}
giving each game's engine version (from its capture metadata).
Output (no game names, no structures): {"major.minor": {stem: {generated spelling: {dump spelling: count}}}},
plus one table per major version ("5") pooled over all its minor versions, used when only the major version is known;
containing only stems for which at least one game shows a different dump spelling (the only stems the rule can act on;
unchanged-only stems are dropped because they can never trigger a respelling).
usage: build_spelling_table.py <references-dir> <versions.json> <out.json>"""
import json, pickle, sys
from pathlib import Path
refs, versions, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), sys.argv[3]
tv = {}
for p in sorted(refs.glob('*.pkl')):
    v = versions.get(p.stem)
    if not v: continue
    for stem, (gen, dump) in pickle.loads(p.read_bytes())['rows'].items():
        for key in (v, v.split('.')[0]):      # the major.minor table and the table pooled over the major
            d = tv.setdefault(key, {}).setdefault(stem, {}).setdefault(gen, {})
            d[dump] = d.get(dump, 0) + 1
table = {v: {s: g for s, g in sorted(t.items()) if any(d != gen for gen, dd in g.items() for d in dd)} for v, t in sorted(tv.items())}
Path(out).write_text(json.dumps(table, indent=0, sort_keys=True))
print({v: len(t) for v, t in table.items()}, 'rows', sum(len(t) for t in table.values()))
