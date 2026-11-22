"""Minor engine version from the record names of the generated map, for executables that do not
store the engine build string.

Each engine minor version adds and removes reflected types. For a version v, the markers are the
record names present in every reference game of v or later (same major) and in none of the earlier
ones ("introduced"), and the names present in every earlier game and in none from v on ("removed").
The estimate is the newest version whose markers mostly agree with the target (at least half); when
none agrees, the oldest reference version of that major. No reference version of that major gives no
estimate."""
import json
from importlib import resources


def _key(v):
    a, b = v.split(".")
    return int(a), int(b)


def late(name):
    """Records added after the version is estimated (UE5 Anim Blueprint step); never markers."""
    return name.startswith("AnimBlueprintGenerated")


def load():
    with resources.files("usmap_generator").joinpath("data/engine_version_markers.json").open("rb") as f:
        return json.load(f)


def build(games, cap=50):
    """games: [(major.minor, set of record names)] -> {version: {"introduced": [...], "removed": [...]}}."""
    by = {}
    for v, names in games: by.setdefault(v, []).append(set(names))
    out = {}
    for v in sorted(by, key=_key):
        major = v.split(".")[0]
        same = [w for w in by if w.split(".")[0] == major]
        later = [s for w in same if _key(w) >= _key(v) for s in by[w]]
        earlier = [s for w in same if _key(w) < _key(v) for s in by[w]]
        if not earlier:                  # the oldest version: no markers, the default when none agree
            out[v] = {"introduced": [], "removed": []}; continue
        intro = set.intersection(*later) - set.union(*earlier)
        gone = set.intersection(*earlier) - set.union(*later)
        out[v] = {"introduced": sorted(intro)[:cap], "removed": sorted(gone)[:cap]}
    return out


def estimate(names, major, markers, threshold=0.5):
    """Newest major.minor whose markers agree with `names`, or None."""
    if major is None: return None
    best = None
    for v in sorted(markers, key=_key):
        if v.split(".")[0] != str(major): continue
        m = markers[v]; n = len(m["introduced"]) + len(m["removed"])
        if not n:                        # the oldest version of the major: older than every marker
            best = best or v; continue
        hit = (sum(x in names for x in m["introduced"]) + sum(x not in names for x in m["removed"])) / n
        if hit >= threshold: best = v
    return best
