"""marauder: build a .usmap mapping file for an installed Unreal Engine game, without running it."""
import argparse, json, os, sys
from pathlib import Path

from . import __version__
from .install import InstallError, locate
from .engine.key_provider import KeyError_, StaticKeys, load_keys_file, parse_key

EXAMPLES = """examples:
  marauder generate "C:\\Program Files (x86)\\Steam\\steamapps\\common\\MyGame"
  marauder generate ~/Games/MyGame -o MyGame.usmap --per-asset
  marauder generate ~/Games/MyGame --aes-key 0x0123...cdef
  marauder compare MyGame.usmap reference.usmap
  marauder inspect MyGame.usmap PlayerState
"""


class Console:
    def __init__(self, quiet=False):
        self.quiet = quiet

    def info(self, msg):
        if not self.quiet: print(msg, flush=True)

    def warn(self, msg):
        print(f"warning: {msg}", file=sys.stderr, flush=True)

    def error(self, msg):
        print(f"error: {msg}", file=sys.stderr, flush=True)


def _keys(args):
    keys = None
    if args.aes_keys_file:
        keys = load_keys_file(args.aes_keys_file)
    if args.aes_key:
        keys = keys or StaticKeys()
        for k in args.aes_key:
            if ":" in k and len(k.split(":", 1)[0].replace("-", "")) == 32:
                from .engine.key_provider import norm_guid
                g, v = k.split(":", 1); keys.by_guid[norm_guid(g)] = parse_key(v)
            else:
                keys.default = parse_key(k)
    return keys


def _summarize_unreadable(con, rows, limit=15):
    if not rows: return
    con.warn(f"{len(rows)} archive(s) or entries were not read:")
    for r in rows[:limit]:
        where = r.get("archive", "?") + (f" :: {r['entry']}" if r.get("entry") else "")
        n = f" ({r['entries']} entries)" if r.get("entries") else ""
        print(f"  - {where}{n}: {r.get('reason')}", file=sys.stderr)
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more (full list: --report FILE)", file=sys.stderr)


def cmd_generate(args, con):
    from .pipeline import generate
    from .engine import usmap as U
    from . import perasset
    keys = _keys(args)
    if args.verbose:
        from .engine import genusmap
        genusmap.VERBOSE = True
    inst = locate(args.game, exe=args.exe, paks_dir=args.paks)
    con.info(f"Game executable: {inst.exe}")
    out = Path(args.output or f"{inst.project.name}.usmap")
    if out.suffix.lower() != ".usmap": out = out.with_name(out.name + ".usmap")
    if out.parent and not out.parent.exists(): out.parent.mkdir(parents=True, exist_ok=True)
    res = generate(inst, keys=keys, engine_version=args.engine_version, code_links=not args.data_only, progress=con.info)
    U.write(str(out), res.enums, res.structs)
    con.info(f"Wrote {out} ({len(res.structs)} classes/structs, {len(res.enums)} enums, engine {res.engine_version or 'unknown'})")
    if args.per_asset:
        if res.anim_scan is None:
            con.warn("--per-asset: this game is not UE5, so it has no per-asset Anim Blueprint structs; nothing written")
        else:
            from .perasset import records_by_package
            pk = records_by_package(res.anim_scan)
            base = str(out)[:-len(".usmap")]
            j, b = perasset.write(base, pk, res.engine_version)
            con.info(f"Wrote {j} and {b} ({sum(len(r) for _, r in pk)} struct copies in {len(pk)} packages)")
    if args.report:
        rep = dict(res.report, output=str(out), unreadable=res.unreadable, version=__version__)
        with open(args.report, "w", encoding="utf-8") as f: json.dump(rep, f, indent=1, default=str)
        con.info(f"Wrote report {args.report}")
    _summarize_unreadable(con, res.unreadable)
    if res.unreadable and args.strict:
        con.error("--strict: some archives or entries were not read (listed above)"); return 4
    con.info(f"Done in {res.report.get('seconds', 0):.0f} s.")
    print("Mischief managed.", flush=True)
    return 0


def cmd_compare(args, con):
    from .engine import usmap as U
    from .engine.schema_diff import accuracy, differences
    gen, ref = U.read(args.generated), U.read(args.reference)
    a, ci = accuracy(gen, ref), accuracy(gen, ref, fold=True)
    rows = differences(gen, ref)
    res = {"fields": a["fields"], "fieldsIgnoringCase": ci["fields"], "records": a["records"], "byKind": a["by_kind"],
           "identical": not rows, "differences": len(rows), "caseOnlyDifferences": sum(bool(r.get("ascii_case_only")) for r in rows)}
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        pct = lambda x: 100.0 * x[0] / x[1] if x[1] else 100.0
        print(f"fields right:           {a['fields'][0]:,} of {a['fields'][1]:,} ({pct(a['fields']):.3f}%)")
        print(f"ignoring letter case:   {ci['fields'][0]:,} of {ci['fields'][1]:,} ({pct(ci['fields']):.3f}%)")
        print(f"records identical:      {a['records'][0]:,} of {a['records'][1]:,} ({pct(a['records']):.3f}%)")
        print(f"maps identical:         {'yes' if not rows else 'no'} ({len(rows):,} differences, {res['caseOnlyDifferences']:,} case only)")
    return 0


def cmd_inspect(args, con):
    from .engine import usmap as U
    enums, structs = U.read(args.file)
    print(f"{args.file}: {len(structs):,} classes/structs, {len(enums):,} enums, "
          f"{sum(len(s['props']) for s in structs.values()):,} properties")
    missing = 0
    for name in args.names:
        if name in enums:
            print(f"enum {name}: " + ", ".join(f"{n}={v}" for v, n in enums[name])); continue
        st = structs.get(name)
        if st is None:
            print(f"{name}: not in this mapping"); missing += 1; continue
        print(f"{name} : {st['super'] or '-'} (properties: {st['count']})")
        for p in st["props"]:
            print(f"  {p['i']:4} {p['name']} {U.fmt(p['type'])}" + (f" [{p['dim']}]" if p["dim"] > 1 else ""))
    return 1 if missing else 0


def parser():
    ap = argparse.ArgumentParser(prog="marauder", description=__doc__, epilog=EXAMPLES,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"marauder {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    g = sub.add_parser("generate", help="build a .usmap from an installed game folder",
                       description="Build a .usmap from an installed Unreal Engine game (Windows x64 shipping build). "
                                   "Reads the executable and the cooked archives; never runs the game.")
    g.add_argument("game", help="the game's install folder (or its *-Win64-Shipping.exe)")
    g.add_argument("-o", "--output", metavar="FILE", help="output .usmap (default: <Project>.usmap in the current folder)")
    g.add_argument("--exe", metavar="EXE", help="the game executable, if the folder holds several")
    g.add_argument("--paks", metavar="DIR", help="folder with the .pak/.utoc archives, if not <Project>/Content/Paks")
    g.add_argument("--per-asset", action="store_true",
                   help="also write the per-asset Anim Blueprint struct mapping (<out>.perasset.json and .perasset.bin)")
    g.add_argument("--aes-key", metavar="KEY", action="append",
                   help="your AES-256 key for encrypted archives: 64 hex digits (0x optional) or base64; "
                        "GUID:KEY for a key with a GUID; may be repeated")
    g.add_argument("--aes-keys-file", metavar="FILE", help="file with several keys (see README: Encrypted games)")
    g.add_argument("--engine-version", metavar="X.Y", help="engine version, if the executable does not store it (e.g. 5.3)")
    g.add_argument("--data-only", action="store_true",
                   help="read only data sections of the executable (skips the static code links; slightly less accurate)")
    g.add_argument("--report", metavar="FILE", help="write a JSON report (counts, every unread archive and the reason)")
    g.add_argument("--strict", action="store_true", help="exit with code 4 if any archive or entry could not be read")
    g.add_argument("-q", "--quiet", action="store_true", help="print only warnings, errors and the final line")
    g.add_argument("-v", "--verbose", action="store_true", help="also print internal diagnostics of the type-table reader")
    g.set_defaults(func=cmd_generate)
    c = sub.add_parser("compare", help="score a .usmap against a reference .usmap (field accuracy)")
    c.add_argument("generated"); c.add_argument("reference")
    c.add_argument("--json", action="store_true", help="print the result as JSON")
    c.set_defaults(func=cmd_compare)
    i = sub.add_parser("inspect", help="print a summary of a .usmap, or the listed classes, structs and enums")
    i.add_argument("file"); i.add_argument("names", nargs="*", metavar="NAME")
    i.set_defaults(func=cmd_inspect)
    return ap


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help(); return 2
    con = Console(getattr(args, "quiet", False))
    from .pipeline import GenerationError
    try:
        return args.func(args, con)
    except (InstallError, GenerationError, KeyError_) as ex:
        con.error(str(ex)); return 1
    except FileNotFoundError as ex:
        con.error(f"{ex.filename}: file not found"); return 1
    except PermissionError as ex:
        con.error(f"{getattr(ex, 'filename', '') or ''}: permission denied ({ex.strerror or ex})"); return 1
    except MemoryError:
        con.error("out of memory. Close other programs and try again; very large games need about 8 GB free."); return 1
    except KeyboardInterrupt:
        con.error("interrupted"); return 130
    except Exception as ex:  # unexpected: short message plus where to report it
        con.error(f"unexpected {type(ex).__name__}: {ex}\n"
                  "Please open an issue on the project's GitHub page with the command you ran and this output "
                  "(set MARAUDER_DEBUG=1 for the full traceback).")
        if os.environ.get("MARAUDER_DEBUG"): raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
