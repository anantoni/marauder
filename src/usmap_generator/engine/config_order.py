"""Order in which a packaged game's startup configuration creates FNames (read-only, data only).

Every key of a parsed .ini line becomes an FName when the file is loaded. Startup loads the
configuration remap file, then each known configuration type through its layer hierarchy, then the
desktop-only files. Plugin configuration is mounted later, in an order this module does not know;
it is not used (no development effect was measured). Files the runtime never loads (localization,
editor and filter lists) are ignored.

respell() gives a property name, or an entry of a regular enum, the first startup spelling of its
case group when that differs.

v11: used again, as an input of the FName display order after the static-initializer names
(genusmap.generate(config_keys=startup_keys(files))). The v9 failure came from the incomplete
static-initializer list; the post-pass respell() below stays off.

NOT ENABLED (v9 measurement): development improved by 45 fields with no game worse, but the frozen
hold-out run lost 9 fields net and two games got worse. Names that static initializers create
(before configuration loads) win over configuration keys, and the saved mapping cannot tell which
spellings came from static initializers. Kept for the record and for later work.
"""
import re
from .fname_order import name_parts, respell_enum_maximum

INI_KEY = re.compile(rb"^[ \t]*[+\-.!]?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", re.M)
KNOWN = ("Engine", "Game", "Input", "DeviceProfiles", "GameUserSettings", "Scalability",
         "RuntimeOptions", "InstallBundle", "Hardware", "GameplayTags")
DESKTOP = ("Compat", "Lightmass")
PLATFORM = "Windows"


def _norm(path):
    return path.replace("\\", "/").lstrip("/")


def startup_files(paths):
    """Archive paths of the startup configuration, in load order."""
    by_lower = {_norm(p).lower(): p for p in paths}
    projects = sorted({p.split("/", 1)[0] for p in by_lower
                       if not p.startswith("engine/") and "/config/" in p and p.count("/") == 2})
    project = projects[0] if len(projects) == 1 else None
    ordered = []

    def add(rel):
        p = by_lower.get(rel.lower())
        if p is not None and p not in ordered:
            ordered.append(p)

    add("Engine/Config/ConfigRedirects.ini")
    for kind in KNOWN + DESKTOP:
        add("Engine/Config/Base.ini")
        add(f"Engine/Config/Base{kind}.ini")
        add(f"Engine/Config/{PLATFORM}/Base{PLATFORM}{kind}.ini")
        if project:
            add(f"{project}/Config/Default{kind}.ini")
            add(f"{project}/Config/Generated{kind}.ini")
        add(f"Engine/Config/{PLATFORM}/{PLATFORM}{kind}.ini")
        if project:
            add(f"{project}/Config/{PLATFORM}/{PLATFORM}{kind}.ini")
            add(f"{project}/Config/{PLATFORM}/Generated{PLATFORM}{kind}.ini")
    return ordered


def startup_keys(files):
    """v11: every startup configuration key, first spelling per case group, in load order
    (for genusmap.generate(config_keys=...))."""
    out, seen = [], set()
    for p in startup_files(files):
        for k in keys(files[p]):
            if k.lower() not in seen:
                seen.add(k.lower()); out.append(k)
    return out


def keys(data):
    return [m.decode("latin-1") for m in INI_KEY.findall(data)]


def first_spellings(files):
    """files: {archive path: bytes}. Returns {lowercase key: first spelling} over the startup files."""
    first = {}
    for p in startup_files(files):
        for k in keys(files[p]):
            first.setdefault(k.lower(), k)
    return first


def read_files(archives, key_provider=None, problems=None):
    """{path: bytes} of the readable .ini entries of the given .pak archives. Archives and entries
    that cannot be read are appended to `problems` as (archive, entry, reason) when it is given."""
    from .archives import Pak
    out = {}
    note = problems.append if problems is not None else (lambda row: None)
    for a in archives:
        try:
            pak = Pak(a, key_provider)
        except Exception as ex:
            note((a, "", f"unreadable archive: {type(ex).__name__}: {ex}")); continue
        for n, e in pak.entries.items():
            if not n.lower().endswith(".ini"): continue
            if not pak.readable(n):
                note((a, n, "encrypted: no AES key supplied")); continue
            try:
                out[n] = pak.read(n)
            except Exception as ex:
                note((a, n, f"{type(ex).__name__}: {ex}"))
    return out


def respell(enums, structs, first, qualified):
    """Rename to the first startup spelling. qualified(enum, entry) tells whether an entry is the
    FName "Enum::Entry" of a namespaced enum (left alone). Returns (enums, structs, renamed)."""
    renamed = []

    def pick(name):
        stem, suffix = name_parts(name)
        return first.get(stem.lower(), stem) + suffix

    out_s = {}
    for n, x in structs.items():
        props = []
        for p in x["props"]:
            nm = pick(p["name"])
            if nm != p["name"]:
                renamed.append(("prop", n, p["name"], nm))
            props.append(dict(p, name=nm))
        out_s[n] = dict(x, props=props)
    out_e = {}
    for n, ents in enums.items():
        rendered = [(v, e if "::" in e or qualified(n, e) else pick(e)) for v, e in ents]
        rendered = respell_enum_maximum(n, ents, rendered, set())
        renamed += [("enum", n, a[1], b[1]) for a, b in zip(ents, rendered) if a != b]
        out_e[n] = rendered
    return out_e, out_s, renamed
