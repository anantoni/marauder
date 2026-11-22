"""Display spelling from names stored in a game's UNENCRYPTED content, for names that the
executable-only order cannot place.

Unreal keeps the spelling of an FName's first registration. When that registration comes from
content loaded at startup, the spelling occurs nowhere in the executable. This module reads,
read-only: the name table of every unencrypted .uasset/.umap (legacy package summary), the key
names and core redirect names of every unencrypted .ini file, and the strings of the cooked asset
registry. respell() is a post-pass over a generated mapping: a property name (or an entry of a
regular enum) is respelled only when the first source (core redirect names, asset registry,
configuration keys) that holds its case-folded group has exactly one spelling, and that spelling
differs and is NOT in the executable. Package name tables are read for measurement only.
The mapping can also be refined using numbered FName stems and inferred enum maxima.
The structured asset registry name table supplies spellings for groups absent
from the earlier content sources.
Encrypted archives are read only with the user's own AES key. No program code is read.
"""
import collections, re, struct
from .archives import Pak, UnsupportedCompression, fstring
from .fname_order import name_parts, respell_enum_maximum
from .name_batch import read_name_batch

INI_KEY = re.compile(rb"^[ \t]*[+\-.!]?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", re.M)
# Core redirect lines ([CoreRedirects] and the older *Redirects= keys): their OldName/NewName object
# paths are split into FNames when the redirect table is built at startup.
REDIRECT_LINE = re.compile(rb"^[ \t]*[+\-.!]?\w*Redirects[ \t]*=(.*)$", re.M)
REDIRECT_VALUE = re.compile(rb'(?:Old|New)\w*Name[ \t]*=[ \t]*"?([^",)]+)"?')
PATH_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Length-prefixed ANSI strings (FString) inside a cooked asset registry file.
FSTRING = re.compile(rb"(?s)([\x02-\x80])\x00\x00\x00([A-Za-z_][A-Za-z0-9_]{0,126})\x00")
REGISTRY_GUID = bytes.fromhex("e79e7f713a49b0e93291b3880781381b")


def registry_names(data):
    """Identifier strings stored as FStrings in a cooked asset registry (AssetRegistry.bin)."""
    out = []
    for m in FSTRING.finditer(data):
        if m.group(1)[0] == len(m.group(2)) + 1: out.append(m.group(2).decode())
    return out


def registry_name_table(data):
    """Return the interned names from a versioned cooked asset registry."""
    if len(data) < 24 or data[:16] != REGISTRY_GUID:
        return []
    version, = struct.unpack_from("<i", data, 16)
    if 4 <= version < 8:
        return _legacy_registry_names(data)
    if version < 8:
        return []
    # The header added in version 16 includes a four-byte archive bool.
    batch = 24 if version >= 16 else 20
    try:
        names, _ = read_name_batch(data, batch)
    except (ValueError, struct.error):
        return []
    return names


def _legacy_registry_names(data):
    if len(data) < 32:
        return []
    offset, = struct.unpack_from("<q", data, 20)
    if not 28 <= offset <= len(data) - 4:
        return []
    count, = struct.unpack_from("<i", data, offset)
    offset += 4
    if not 0 <= count <= (len(data) - offset) // 8:
        return []
    names = []
    for _ in range(count):
        if offset + 8 > len(data):
            return []
        length, = struct.unpack_from("<i", data, offset)
        width = 2 if length < 0 else 1
        end = offset + 4 + abs(length) * width
        if not 0 < abs(length) <= 1024 or end + 4 > len(data) or any(data[end - width:end]):
            return []
        name, _ = fstring(data, offset)
        names.append(name)
        offset = end + 4  # Two 16-bit hashes follow the string.
    return names


def redirect_names(text):
    out = []
    for line in REDIRECT_LINE.findall(text):
        for v in REDIRECT_VALUE.findall(line):
            out += PATH_PART.findall(v.decode("latin-1"))
    return out


def name_table(data):
    """Names of a legacy-summary package (UE4 / UE5 pak packages); [] when not a package."""
    if len(data) < 64: return []
    magic, legacy = struct.unpack_from("<Ii", data, 0)
    if magic != 0x9E2A83C1 or not -8 <= legacy <= -6: return []
    o = 8 + (4 if legacy != -4 else 0)
    o += 8 + (4 if legacy <= -8 else 0)            # UE4 [, UE5] and licensee versions
    n, = struct.unpack_from("<i", data, o); o += 4 + 20 * n
    o += 4                                          # total header size
    _, o = fstring(data, o)
    flags, nc, no = struct.unpack_from("<Iii", data, o)
    out = []; p = no
    for _ in range(max(0, min(nc, 1 << 20))):
        s, p = fstring(data, p); p += 4; out.append(s)
    return out


class Spellings:
    def __init__(self):
        self.by_source = {"config": collections.defaultdict(collections.Counter),
                          "redirect": collections.defaultdict(collections.Counter),
                          "registry": collections.defaultdict(collections.Counter),
                          "registry_table": collections.defaultdict(collections.Counter),
                          "engine": collections.defaultdict(collections.Counter),
                          "game": collections.defaultdict(collections.Counter)}
        self.stats = collections.Counter(); self.excluded = []

    def add(self, source, names):
        d = self.by_source[source]
        for s in set(names):
            if s and s.isidentifier(): d[s.lower()][s] += 1

    def spellings(self, key, sources=("config", "engine", "game")):
        out = collections.Counter()
        for src in sources: out.update(self.by_source[src].get(key, {}))
        return out


def read_archives(archives, key_provider=None, packages=True):
    """packages=False reads only configuration files and the asset registry (fast)."""
    sp = Spellings()
    for a in archives:
        try:
            pak = Pak(a, key_provider)
        except (ValueError, struct.error, OSError) as ex:
            sp.stats["unreadable archive"] += 1; sp.excluded.append({"archive": a, "reason": "unreadable: " + str(ex)}); continue
        sp.excluded += [x.row() for x in pak.excluded]
        for n, e in pak.entries.items():
            low = n.lower()
            if not low.endswith((".uasset", ".umap", ".ini", "assetregistry.bin")): continue
            if not packages and low.endswith((".uasset", ".umap")): continue
            if not pak.readable(n): sp.stats["encrypted"] += 1; continue
            try:
                b = pak.read(n)
            except UnsupportedCompression:
                sp.stats["unsupported compression"] += 1; continue
            except Exception:
                sp.stats["unreadable"] += 1; continue
            if low.endswith(".ini"):
                sp.add("config", [m.decode("latin-1") for m in INI_KEY.findall(b)]); sp.stats["ini"] += 1
                sp.add("redirect", redirect_names(b))
                continue
            if low.endswith("assetregistry.bin"):
                sp.add("registry", registry_names(b)); sp.stats["registry"] += 1
                if low.rsplit("/", 1)[-1] == "assetregistry.bin":
                    sp.add("registry_table", registry_name_table(b))
                continue
            try:
                names = name_table(b)
            except (struct.error, UnicodeDecodeError):
                sp.stats["unreadable"] += 1; continue
            src = "engine" if low.startswith("engine/") or "/engine/" in low[:40] else "game"
            sp.add(src, names); sp.stats["packages"] += 1
    return sp


class ExeStrings:
    """Every NUL-delimited ANSI string and every UTF-16 string of an executable's bytes (data only)."""

    def __init__(self, exe_bytes):
        self.s = {m.decode("latin-1") for m in re.findall(rb"(?<=\0)[\x21-\x7e]{1,255}(?=\0)", exe_bytes)}
        self.s |= {m.decode("utf-16-le") for m in re.findall(rb"(?<=\0\0)(?:[\x21-\x7e]\0){1,255}(?=\0\0)", exe_bytes)}
        self.s |= {m.decode("utf-16-le") for m in re.findall(rb"(?<=\0)\0((?:[\x21-\x7e]\0){1,255})(?=\0)", exe_bytes)}

    def __contains__(self, x):
        return x in self.s

    @classmethod
    def from_pe(cls, path):
        """Build the string index from PE data sections only."""
        from .pe import PE
        return cls(PE(path).d)


def in_executable(exe, s):
    if isinstance(exe, ExeStrings): return s in exe
    b = s.encode()
    return (b"\0" + b + b"\0") in exe or (b"\0\0" + s.encode("utf-16-le") + b"\0\0") in exe


# Frozen on the development split (v6): redirect, then asset registry, then configuration keys.
DEFAULT_SOURCES = ("redirect", "registry", "config")
REFINEMENT_SOURCES = (*DEFAULT_SOURCES, "registry_table")


def respell(enums, structs, exe_bytes, sp, sources=DEFAULT_SOURCES, only_absent=True, enum_entries=True):
    """Post-pass over a generated mapping. For each property / enum entry name, the first source (in
    order) that holds its case-folded group decides: when that source has exactly one spelling,
    it differs from the current one and (only_absent) it occurs nowhere in the executable, the
    name takes that spelling. Only entries of regular enums are respelled (enum_entries=False leaves
    all entries alone): a namespaced entry is the single FName "EEnum::Entry" (its record string is
    in the executable) and does not share a case group with a bare name. Returns (enums, structs, renamed list)."""
    cache = {}; renamed = []
    def pick(s):
        k = s.lower()
        if k in cache: return cache[k]
        out = s
        for src in sources:
            c = sp.by_source[src].get(k)
            if not c: continue
            if src == "registry_table" and any(sp.by_source[earlier].get(k) for earlier in DEFAULT_SOURCES):
                break
            if len(c) == 1:
                x = next(iter(c))
                if x != s and (not only_absent or not in_executable(exe_bytes, x)): out = x
                break
        cache[k] = out
        return out
    S = {}
    for n, x in structs.items():
        props = []
        for p in x["props"]:
            nm = pick(p["name"])
            if nm != p["name"]: renamed.append(("prop", n, p["name"], nm))
            props.append(dict(p, name=nm))
        S[n] = dict(x, props=props)
    if not enum_entries: return enums, S, renamed
    E = {}
    for n, ents in enums.items():
        out = []
        for v, e in ents:
            head, sep, tail = e.rpartition("::")
            if not sep and in_executable(exe_bytes, f"{n}::{e}"):
                out.append((v, e)); continue            # namespaced entry: its FName is "EEnum::Entry"
            nm = pick(tail); nm = head + sep + nm if sep else nm
            if nm != e: renamed.append(("enum", n, e, nm))
            out.append((v, nm))
        E[n] = out
    return E, S, renamed


def respell_numbered(enums, structs, exe, *, automatic_maximum=True):
    """Extend the spelling of an unambiguous bare property name to numbered names.

    This works on saved mappings, which have lost the original registration order
    and may have stripped an enum entry's namespace.
    """
    bare = collections.defaultdict(set)
    for record in structs.values():
        for prop in record["props"]:
            name = prop["name"]
            if not name_parts(name)[1]:
                bare[name.lower()].add(name)
    literals = {literal.lower() for literal in exe.s}

    def pick(name):
        stem, suffix = name_parts(name)
        choices = bare.get(stem.lower())
        if suffix and choices and len(choices) == 1:
            return next(iter(choices)) + suffix
        return name

    result_structs = {
        name: dict(record, props=[dict(prop, name=pick(prop["name"])) for prop in record["props"]])
        for name, record in structs.items()
    }
    result_enums = {}
    for name, entries in enums.items():
        result = [(value, entry if "::" in entry or f"{name}::{entry}".lower() in literals else pick(entry))
                  for value, entry in entries]
        if automatic_maximum:
            result = respell_enum_maximum(name, entries, result, literals)
        result_enums[name] = result
    return result_enums, result_structs


if __name__ == "__main__":
    import argparse
    import json
    from .genusmap import recover_legacy_types, version_string
    from .scan import Image
    from .usmap import read, write

    parser = argparse.ArgumentParser(description="Refine an existing usmap from PE data and cooked content.")
    parser.add_argument("exe", help="Game PE file, read only for data-section strings")
    parser.add_argument("mapping")
    parser.add_argument("output")
    parser.add_argument("--pak", action="append", default=[], help="Pak to check for startup names (repeatable)")
    args = parser.parse_args()

    image = Image(args.exe)
    exe = ExeStrings(image.d)
    spellings = read_archives(args.pak, packages=False)
    enums, structs = read(args.mapping)
    recover_legacy_types(image, structs, version_string(image))
    enums, structs, _ = respell(enums, structs, exe, spellings, REFINEMENT_SOURCES)
    write(args.output, *respell_numbered(enums, structs, exe))
    if spellings.excluded:
        print(json.dumps({"excluded": spellings.excluded}))
