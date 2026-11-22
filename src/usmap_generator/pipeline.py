"""End-to-end generation: installed game folder -> global .usmap (+ optional per-asset mapping).

Stage order (the configuration measured in the accompanying paper, with the public spelling table):
 1. type schemas from the executable's reflection tables (engine/genusmap.py), with the startup
    configuration keys for the name display order;
 2. display spellings from configuration, core redirects and the asset registry (content_names);
 3. legacy type records, numbered names, partly initialized enums and automatic enum maxima;
 4. spelling votes per engine version (data/spelling_table_by_engine_version.json) and
    intrinsic classes;
 5. UE5: Anim Blueprint generated structs read from the cooked packages; a record is added to the
    global map only when every copy in the startup set is identical;
 6. UE5: the AnimBlueprintGeneratedStruct record is always present (readers need it to reach
    ScriptStruct).
Nothing is executed, injected or modified. Encrypted data is read only with the user's own key.
"""
import json, os, tempfile, time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable, List, Optional

from .engine import genusmap as G
from .engine import usmap as U
from .engine import content_names as names
from .engine import config_order
from .engine import partial_enums
from .engine import reference_spellings as refs
from .engine import intrinsic_classes
from .engine import anim_structs as A
from .engine import anim_choice as C
from .engine.archives import Pak
from .engine.iostore_probe import Toc
from .engine.key_provider import WRONG_KEY
from .engine.fname_order import flags_maximum_policy, restore_automatic_maxima
from .engine.genusmap import recover_legacy_types, version_string
from .engine.scan import Image

ANIM_STRUCT_CLASS = "AnimBlueprintGeneratedStruct"
ANIM_STRUCT_RECORD = {"super": "ScriptStruct", "count": 0, "props": []}


class GenerationError(Exception):
    """Generation cannot continue; the message says what to do."""


@dataclass
class Result:
    enums: dict
    structs: dict
    engine_version: Optional[str]
    report: dict = field(default_factory=dict)
    unreadable: List[dict] = field(default_factory=list)   # every archive/entry/package not read, with reason
    anim_scan: object = None                                # engine.anim_structs.GameScan (UE5)


def spelling_table():
    with resources.files("usmap_generator").joinpath("data/spelling_table_by_engine_version.json").open("rb") as f:
        return json.load(f)


def choose_spellings(enums, structs, exe, qualified, table, min_votes=2):
    """Spelling votes for the target's engine version: {lowercase stem: new spelling}.
    A stem is respelled when its generated spelling is the only one in the target, the table lists
    exactly one other spelling for it, no game kept the generated spelling, at least `min_votes`
    games agree, and the new spelling occurs in the executable's strings."""
    out = {}
    for k, ms in refs.groups(enums, structs, qualified).items():
        if len(ms) != 1: continue
        mine = next(iter(ms)); votes = table.get(k, {}).get(mine)
        if not votes or mine in votes or len(votes) != 1: continue
        spelling, count = next(iter(votes.items()))
        if count >= min_votes and spelling in exe: out[k] = spelling
    return out


def ensure_anim_struct_class(structs, major):
    """UE5 readers resolve an Anim Blueprint struct export's class through this record; keep it."""
    if major is not None and major >= 5 and ANIM_STRUCT_CLASS not in structs:
        structs[ANIM_STRUCT_CLASS] = dict(ANIM_STRUCT_RECORD, props=[])
        return True
    return False


def _roundtrip(enums, structs):
    """Normalize through the writer and reader, exactly as a saved map would be read back."""
    fd, path = tempfile.mkstemp(suffix=".usmap"); os.close(fd)
    try:
        U.write(path, enums, structs)
        return U.read(path)
    finally:
        os.unlink(path)


def _major(version):
    try: return int(str(version).split(".")[0])
    except (TypeError, ValueError): return None


def inventory(install, keys, unreadable):
    """Open every archive once, so that every part that will not be read is reported up front."""
    stats = {"pak": 0, "utoc": 0, "encrypted": 0, "opened": 0, "wrongKey": 0, "oodle": 0}
    seen = set()
    for p in install.paks:
        seen.add(str(p))
        try:
            pak = Pak(str(p), keys)
        except Exception as ex:
            unreadable.append({"archive": str(p), "reason": f"not a readable .pak ({type(ex).__name__}: {ex})"}); continue
        stats["pak"] += 1
        stats["oodle"] += any(e["method"] and pak.method_name(e["method"]).lower() == "oodle" for e in pak.entries.values())
        enc = pak.enc_index or any(e["encrypted"] for e in pak.entries.values())
        stats["encrypted"] += bool(enc); stats["opened"] += bool(enc and pak.key)
        for x in pak.excluded:
            unreadable.append(x.row()); stats["wrongKey"] += x.reason == WRONG_KEY
    cdir = install.containers_dir
    utocs = sorted(cdir.rglob("*.utoc")) if cdir and cdir.is_dir() else []
    for u in utocs:
        try:
            t = Toc(str(u), keys)
        except Exception as ex:
            unreadable.append({"archive": str(u), "reason": f"not a readable .utoc ({type(ex).__name__}: {ex})"}); continue
        stats["utoc"] += 1
        stats["oodle"] += any("oodle" in m.lower() for m in t.methods[1:])
        enc = bool(t.flags & 2)
        stats["encrypted"] += enc; stats["opened"] += bool(enc and t.key)
        for x in t.excluded:
            unreadable.append(x.row()); stats["wrongKey"] += x.reason == WRONG_KEY
    return stats


def find_oodle(install):
    """Windows: the Oodle runtime many games ship next to their executable (set UE_OODLE_LIB to override)."""
    if os.environ.get("UE_OODLE_LIB") or os.name != "nt": return None
    for d in (install.exe.parent, install.root / "Engine" / "Binaries" / "ThirdParty", install.root):
        for p in sorted(Path(d).rglob("oo2core_*win64.dll")) if Path(d).is_dir() else ():
            os.environ["UE_OODLE_LIB"] = str(p); return p
    return None


def generate(install, *, keys=None, engine_version=None, code_links=True, engine_refs=None,
             progress: Callable[[str], None] = lambda s: None) -> Result:
    t0 = time.time()
    unreadable: List[dict] = []
    step = lambda n, s: progress(f"[{n}/6] {s}")

    step(1, f"Reading archives in {install.containers_dir or install.root}")
    oodle = find_oodle(install)
    if oodle: progress(f"      using the game's Oodle library {oodle.name}")
    inv = inventory(install, keys, unreadable)
    progress(f"      {inv['pak']} .pak, {inv['utoc']} .utoc; {inv['encrypted']} encrypted"
             + (f", {inv['opened']} opened with your key" if keys else ""))
    from .engine.archives import _Oodle
    if inv["oodle"] and not _Oodle.get():
        msg = (f"{inv['oodle']} archive(s) use Oodle compression and no Oodle library was found; their compressed "
               "entries cannot be read. Set UE_OODLE_LIB to the Oodle runtime (see README: Oodle).")
        progress("      warning: " + msg)
        unreadable.append({"archive": "(Oodle-compressed archives)", "entries": inv["oodle"], "reason": msg})
    if keys and inv["encrypted"] and not inv["opened"]:
        raise GenerationError("the AES key you supplied does not open any of this game's encrypted archives. "
                              "Check that the key belongs to this game and version (64 hex digits, 0x prefix optional); "
                              "games with several keys need --aes-keys-file with one key per key GUID.")
    problems = []
    files = config_order.read_files([str(p) for p in install.paks], keys, problems)
    unreadable += [{"archive": a, "entry": n, "reason": r} for a, n, r in problems]

    step(2, f"Reading type tables from {install.exe.name}")
    ckeys = config_order.startup_keys(files)
    try:
        ge, gs, rep = G.generate(str(install.exe), code_links=code_links, config_keys=ckeys,
                                 engine_refs=engine_refs)
    except Exception as ex:
        raise GenerationError(f"{install.exe}: no Unreal Engine reflection tables could be read "
                              f"({type(ex).__name__}: {ex}). Protected or packed executables and non-x64 "
                              "builds are not supported; see Troubleshooting in the README.") from ex
    if not gs:
        raise GenerationError(f"{install.exe}: no Unreal Engine type records found. Is this the game's "
                              "*-Shipping.exe (not a launcher)? Protected executables are not supported.")
    enums, structs = _roundtrip(ge, gs)
    image = Image(str(install.exe))
    vs = version_string(image)
    version = engine_version or vs or (rep.get("engineVersionEstimate") or {}).get("version")
    major = _major(version)
    progress(f"      {len(structs)} classes/structs, {len(enums)} enums; engine "
             f"{version or 'unknown'}{' (from --engine-version)' if engine_version else ''}")

    step(3, "Restoring name spellings from configuration and the asset registry")
    sp0 = names.read_archives([str(p) for p in install.paks], keys, packages=False)
    for row in sp0.excluded:
        if row not in unreadable and not any(u.get("archive") == row.get("archive") for u in unreadable):
            unreadable.append(row)
    for kind in ("unreadable", "unsupported compression"):
        if sp0.stats.get(kind):
            unreadable.append({"archive": "(configuration and asset registry entries)", "entries": sp0.stats[kind],
                               "reason": kind + (" (set UE_OODLE_LIB, see README)" if "compression" in kind else "")})
    exe = names.ExeStrings(image.d)
    sp = names.Spellings()
    sp.by_source.update({k: dict(v) for k, v in sp0.by_source.items()})
    recover_legacy_types(image, structs, vs)
    enums, structs = names.respell_numbered(*names.respell(enums, structs, exe, sp, names.REFINEMENT_SOURCES)[:2], exe)
    enums, structs, _ = partial_enums.recover(image, enums, structs)
    info, _ = partial_enums.enum_info(image)
    enums = restore_automatic_maxima(enums, info, flags_maximum_policy(vs))

    step(4, "Applying spelling votes for this engine version")
    qualified = lambda n, e: f"{n}::{e}" in exe
    mm = ".".join(str(version).split(".")[:2]) if version else None
    table = spelling_table().get(mm) if mm else None
    respelled = 0
    if table:
        choice = choose_spellings(enums, structs, exe, qualified, table)
        enums, structs = refs.respell(enums, structs, choice, qualified); respelled = len(choice)
        progress(f"      {respelled} names respelled from the {mm} table")
    else:
        progress(f"      no spelling table for engine {version or 'unknown'}; step skipped"
                 + ("" if engine_version else " (use --engine-version X.Y if you know it)"))
    structs = dict(structs)
    intrinsic_classes.recover(image.d, structs)

    scan = None; chosen = []
    if major is None or major >= 5:                  # unknown version: the scan finds nothing in UE4 games
        step(5, "Reading Anim Blueprint structs from the cooked packages")
        scan = A.scan(str(install.containers_dir), keys)
        settings = C.startup_settings(files)
        identical, why = C.choose(scan, settings, structs, agreement=False)
        for name, rec in identical.items():
            if name not in structs: structs[name] = rec; chosen.append(name)
        for a, k, reason in scan.failed:
            unreadable.append({"archive": str(a), "entry": str(k), "reason": "Anim Blueprint scan: " + reason})
        progress(f"      {len(scan.records)} struct copies in {len({r[0] for r in scan.records})} packages; "
                 f"{len(chosen)} added to the global map (identical copies only)")
    else:
        step(5, "Anim Blueprint structs: UE4 game, nothing to do")
    step(6, "Final checks")
    if major is None and scan is not None and (scan.records or scan.anim_classes): major = 5
    added = ensure_anim_struct_class(structs, major)
    report = {"engineVersion": version, "engineVersionBasis": "--engine-version" if engine_version else
              ("build string" if vs else "layout estimate"), "archives": inv, "spellingVotes": respelled,
              "animStructsAdded": chosen, "animStructClassAdded": added, "generator": rep,
              "seconds": round(time.time() - t0, 1)}
    return Result(enums, structs, version, report, unreadable, scan)
