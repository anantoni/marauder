#!/usr/bin/env python3
"""Derive the engine-version markers from generated maps of games whose engine version you know.
Input: a directory of .usmap files (one per game, made by `marauder generate`) and a JSON map
{usmap stem: "major.minor"}. Output: {"major.minor": {"introduced": [record names], "removed": [record names]}}
(engine type names only: a marker must be in every game of that version or later).
usage: build_version_markers.py <maps-dir> <versions.json> <out.json>"""
import json, sys
from pathlib import Path
from usmap_generator.engine import usmap
from usmap_generator.engine.version_markers import build
from usmap_generator.engine.version_markers import late
maps, versions, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), sys.argv[3]
games = []
for p in sorted(maps.glob("*.usmap")):
    v = versions.get(p.stem)
    if not v: continue
    e, s = usmap.read(str(p)); games.append((v, {n for n in set(e) | set(s) if not late(n)}))
m = build(games)
Path(out).write_text(json.dumps(m, indent=0, sort_keys=True))
print({v: (len(x["introduced"]), len(x["removed"])) for v, x in m.items()})
