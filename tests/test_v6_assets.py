"""v6: synthetic tests for the archive, tagged-package, content-spelling, IoStore and key-provider code.
No game data is used; every archive here is built by the test."""
import os, struct, sys, tempfile, unittest
from usmap_generator.engine import archives
from usmap_generator.engine import content_names as CN
from usmap_generator.engine import iostore_probe as IO
from usmap_generator.engine import key_provider as KP
from usmap_generator.engine import tagged_catalog as TC
from usmap_generator.engine import uasset_tags as T


def fstr(s):
    b = s.encode() + b"\0"
    return struct.pack("<i", len(b)) + b


def pak_entry(off, size, flags=0):
    return struct.pack("<qqqi", off, size, size, 0) + b"\0" * 20 + bytes([flags]) + struct.pack("<I", 0)


def make_pak(path, files, version=3, encrypted_index=False, encrypted=()):
    """files: {name: bytes}. Pak version 3 or 4, uncompressed, optional encrypted flags (bytes are not
    actually encrypted: the reader must refuse them by flag alone)."""
    out = bytearray(); index = {}
    for n, data in files.items():
        flags = 1 if n in encrypted else 0
        index[n] = (len(out), len(data), flags)
        out += pak_entry(len(out), len(data), flags) + data
    idx = fstr("../../../") + struct.pack("<i", len(index))
    for n, (o, s, fl) in index.items(): idx += fstr(n) + pak_entry(o, s, fl)
    io = len(out); out += idx
    footer = struct.pack("<IIQQ", archives.PAK_MAGIC, version, io, len(idx)) + b"\0" * 20
    if version >= 4: footer = bytes([1 if encrypted_index else 0]) + footer
    out += footer
    with open(path, "wb") as fh: fh.write(out)


def name_ref(names, s, number=0):
    return struct.pack("<ii", names.index(s), number)


def make_package(names, imports, exports, chains):
    """Legacy (-7) cooked package with 104-byte export entries; chains are the export data blobs."""
    name_tab = b"".join(fstr(n) + b"\0\0\0\0" for n in names)
    imp = b"".join(struct.pack("<iiiiiii", names.index(cp), 0, names.index(c), 0, outer, names.index(nm), 0)
                   for cp, c, outer, nm in imports)
    head_fixed = 7 * 4 + len(fstr("None")) + 4 * 3 + 8 + 4 * 5
    no = head_fixed; io = no + len(name_tab); eo = io + len(imp); do = eo + 104 * len(exports)
    header_size = do
    exp = b""; off = header_size
    for (cls, outer, nm), data in zip(exports, chains):
        e = struct.pack("<iiiiiiIqq", cls, 0, 0, outer, names.index(nm), 0, 0, len(data), off)
        exp += e + b"\0" * (104 - len(e)); off += len(data)
    summ = struct.pack("<Iiiiiii", 0x9E2A83C1, -7, 0, 0, 0, 0, header_size) + fstr("None") + \
        struct.pack("<Iii", 0x80000000, len(names), no) + b"\0" * 8 + struct.pack("<iiiii", len(exports), eo, len(imports), io, do)
    assert len(summ) == head_fixed
    return summ + name_tab + imp + exp + b"".join(chains)


def tag(names, name, ty, value, extra=b"", index=0):
    return name_ref(names, name) + name_ref(names, ty) + struct.pack("<ii", len(value), index) + extra + b"\0" + value


NAMES = ["None", "/Script/Engine", "/Script/CoreUObject", "Package", "Class", "Actor", "Default__Actor", "Health",
         "IntProperty", "Loc", "StructProperty", "MyStruct", "X", "Mode", "ByteProperty", "EMode", "EMode::Fast",
         "Enum", "ScriptStruct"]


def sample_package():
    n = NAMES
    inner = tag(n, "X", "IntProperty", struct.pack("<i", 7)) + name_ref(n, "None")
    chain = tag(n, "Health", "IntProperty", struct.pack("<i", 100)) + \
        tag(n, "Loc", "StructProperty", inner, name_ref(n, "MyStruct") + b"\0" * 16) + \
        tag(n, "Mode", "ByteProperty", name_ref(n, "EMode::Fast"), name_ref(n, "EMode")) + name_ref(n, "None")
    imports = [("/Script/CoreUObject", "Package", 0, "/Script/Engine"), ("/Script/CoreUObject", "Class", -1, "Actor"),
               ("/Script/CoreUObject", "Enum", -1, "EMode")]
    return make_package(n, imports, [(-2, 0, "Default__Actor")], [chain])


class V6Archives(unittest.TestCase):
    def test_unencrypted_entries_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.pak"); make_pak(p, {"Game/Content/A.uasset": b"abc", "Game/Config/X.ini": b"k=v"})
            with archives.Pak(p) as pak:
                self.assertEqual(pak.read("Game/Content/A.uasset"), b"abc")
                self.assertEqual(pak.excluded, [])

    def test_encrypted_entries_are_excluded_and_never_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.pak"); make_pak(p, {"A.uasset": b"abc", "B.uasset": b"xyz"}, version=4, encrypted={"B.uasset"})
            with archives.Pak(p) as pak:
                with self.assertRaises(PermissionError): pak.read("B.uasset")
                self.assertEqual([(x.what, x.entries, x.reason) for x in pak.excluded], [("entries", 1, KP.NO_PROVIDER)])

    def test_encrypted_index_excludes_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.pak"); make_pak(p, {"A.uasset": b"abc"}, version=4, encrypted_index=True)
            with archives.Pak(p) as pak:
                self.assertEqual(pak.entries, {})
                self.assertEqual(pak.excluded[0].what, "index")


class V6KeyProvider(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(KP.policy_for("a.pak", "index").reason, KP.NO_PROVIDER)

    def test_supplied_key_is_used_and_missing_key_is_reported(self):
        class Mine:
            def key_for(self, archive, guid): return b"\1" * 32
        class NoKey:
            def key_for(self, archive, guid): return None
        self.assertEqual(KP.key_for("a.pak", "", Mine()), b"\1" * 32)
        self.assertIsNone(KP.key_for("a.pak", "", NoKey()))
        self.assertIsNone(KP.key_for("a.pak", "", None))
        self.assertEqual(KP.policy_for("a.pak", "index", "", NoKey()).reason, KP.NO_KEY)

    def test_cipher_code_lives_only_in_the_aes_module(self):
        import usmap_generator.engine as E
        here = os.path.dirname(os.path.abspath(E.__file__))
        for f in sorted(os.listdir(here)):
            if not f.endswith(".py") or f == "aes.py": continue
            with open(os.path.join(here, f), encoding="utf-8") as fh: src = fh.read().lower()
            for word in ("cipher", "from crypto", "cryptography", "modes.ecb"):
                self.assertNotIn(word, src, f + ": " + word)

    def test_no_key_search_code_ships(self):
        import usmap_generator as P
        root = os.path.dirname(os.path.abspath(P.__file__))
        for dirpath, _, files in os.walk(root):
            for f in files:
                if not f.endswith(".py"): continue
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh: src = fh.read().lower()
                for word in ("brute", "find_key", "findkey", "key_search", "guess_key", "aes_finder"):
                    self.assertNotIn(word, src, f + ": " + word)


class V6Tagged(unittest.TestCase):
    def test_tagged_chain(self):
        pkg = T.Package(sample_package())
        (k, ch), = list(T.export_chains(pkg))
        self.assertEqual([t["name"] for t, _ in ch], ["Health", "Loc", "Mode"])
        self.assertEqual(ch[1][1]["fields"][0][0]["name"], "X")
        self.assertEqual(ch[2][1]["enum_values"], ["EMode::Fast"])

    def test_catalog(self):
        cat = TC.Catalog(); pkg = T.Package(sample_package())
        cat.add_package(pkg, "/Game/Sample", (True, True)); cat.resolve()
        e, s = cat.mapping()
        self.assertEqual([(p["name"], p["type"]["t"]) for p in s["Actor"]["props"]],
                         [("Health", "IntProperty"), ("Loc", "StructProperty"), ("Mode", "EnumProperty")])
        self.assertEqual(s["Actor"]["props"][2]["type"]["enum"], "EMode")
        self.assertEqual(s["MyStruct"]["props"][0]["name"], "X")
        self.assertEqual(e["EMode"], [(0, "Fast")])
        self.assertIsNone(s["Actor"]["super"])            # native supers are not in tagged data

    def test_blueprint_declared_properties_stay_off_native_class(self):
        n = NAMES + ["BlueprintGeneratedClass", "BP_A_C", "Default__BP_A_C", "MyVar", "BoolProperty"]
        chain = tag(n, "MyVar", "IntProperty", struct.pack("<i", 1)) + tag(n, "Health", "IntProperty", struct.pack("<i", 5)) + name_ref(n, "None")
        imports = [("/Script/CoreUObject", "Package", 0, "/Script/Engine"), ("/Script/CoreUObject", "Class", -1, "Actor"),
                   ("/Script/CoreUObject", "Class", -1, "BlueprintGeneratedClass"), ("/Script/CoreUObject", "Class", -1, "IntProperty")]
        exports = [(-3, 0, "BP_A_C"), (-4, 1, "MyVar"), (1, 0, "Default__BP_A_C")]
        data = [name_ref(n, "None"), name_ref(n, "None"), chain]
        raw = make_package(n, imports, exports, data)
        # SuperIndex of the Blueprint class: the native Actor import
        pkg = T.Package(raw); pkg.exports[0]["super"] = -2
        cat = TC.Catalog(); cat.add_package(pkg, "/Game/BP_A", (True, True)); cat.resolve()
        e, s = cat.mapping()
        self.assertEqual([p["name"] for p in s["Actor"]["props"]], ["Health"])


class V6Spelling(unittest.TestCase):
    def test_versioned_registry_name_tables(self):
        old_header = CN.REGISTRY_GUID + struct.pack("<iq", 7, 28)
        old = old_header + struct.pack("<I", 1) + fstr("Title") + b"\0" * 4
        self.assertEqual(CN.registry_name_table(old), ["Title"])
        self.assertEqual(CN.registry_name_table(old[:-1]), [])

        records = b"Title" + "Étage".encode("utf-16-le")
        batch = struct.pack("<IIQ", 2, len(records), 0) + b"\0" * 16
        batch += b"\x00\x05\x80\x05" + records
        for version, flag in ((8, b""), (16, struct.pack("<I", 1))):
            data = CN.REGISTRY_GUID + struct.pack("<i", version) + flag + batch
            self.assertEqual(CN.registry_name_table(data), ["Title", "Étage"])
            self.assertEqual(CN.registry_name_table(data[:-1]), [])

    def test_numbered_name_refinement_and_automatic_maximum(self):
        enum_entries = [(0, "SIZE_0"), (1, "SIZE_16"), (2, "SIZE_MAX")]
        enums = {"EMode": enum_entries, "EOther": [(0, "SIZE_0")]}
        structs = {"Owner": {"props": [{"name": "Size"}, {"name": "SIZE_4"}, {"name": "SIZE_04"}]}}
        exe = CN.ExeStrings(b"\0EOther::Size_0\0")
        emitted, records = CN.respell_numbered(enums, structs, exe)
        self.assertEqual([p["name"] for p in records["Owner"]["props"]], ["Size", "Size_4", "SIZE_04"])
        self.assertEqual(emitted["EOther"], enums["EOther"])
        self.assertEqual(emitted["EMode"], [(0, "Size_0"), (1, "Size_16"), (2, "Size_MAX")])

        exe = CN.ExeStrings(b"\0Size_MAX\0")
        emitted, _ = CN.respell_numbered(enums, structs, exe)
        self.assertEqual(emitted["EMode"][-1], enum_entries[-1])
        structs["Owner"]["props"].append({"name": "SIZE"})
        self.assertEqual(CN.respell_numbered(enums, structs, exe)[0]["EMode"], enum_entries)

    def test_redirect_and_registry_names(self):
        ini = b'[CoreRedirects]\n+StructRedirects=(OldName="ClothLODData",NewName="/Script/X.ClothLODDataCommon")\nTooltip=1\n'
        self.assertIn("ClothLODData", CN.redirect_names(ini))
        self.assertEqual(CN.registry_names(b"\0\x08\0\0\0Tooltip\0\x05\0\0\0abcd\0"), ["Tooltip", "abcd"])

    def test_respell_only_when_absent_from_executable(self):
        sp = CN.Spellings(); sp.add("redirect", ["ClothLODData"])
        structs = {"C": {"super": None, "count": 1, "props": [{"i": 0, "dim": 1, "name": "ClothLodData", "type": {"t": "IntProperty"}}]}}
        e, s, ren = CN.respell({}, structs, b"\0ClothLodData\0", sp)
        self.assertEqual(s["C"]["props"][0]["name"], "ClothLODData"); self.assertEqual(len(ren), 1)
        e, s, ren = CN.respell({}, structs, b"\0ClothLODData\0", sp)
        self.assertEqual(s["C"]["props"][0]["name"], "ClothLodData")

    def test_respell_ambiguous_source_is_ignored(self):
        sp = CN.Spellings(); sp.add("registry", ["Sound"]); sp.add("registry", ["sound"])
        structs = {"C": {"super": None, "count": 1, "props": [{"i": 0, "dim": 1, "name": "Sound", "type": {"t": "IntProperty"}}]}}
        self.assertEqual(CN.respell({}, structs, b"", sp)[1]["C"]["props"][0]["name"], "Sound")

    def test_registry_name_table_fills_an_uncovered_name(self):
        sp = CN.Spellings()
        sp.add("registry_table", ["Tooltip"])
        structs = {"C": {"props": [{"name": "ToolTip"}]}}
        sources = CN.REFINEMENT_SOURCES
        self.assertEqual(CN.respell({}, structs, b"", sp, sources)[1]["C"]["props"][0]["name"], "Tooltip")
        exe = CN.ExeStrings(b"\0Tooltip\0")
        self.assertEqual(CN.respell({}, structs, exe, sp, sources)[1]["C"]["props"][0]["name"], "ToolTip")
        sp.add("registry", ["ToolTip", "Tooltip"])
        self.assertEqual(CN.respell({}, structs, b"", sp, sources)[1]["C"]["props"][0]["name"], "ToolTip")

    def test_name_table(self):
        self.assertEqual(CN.name_table(sample_package())[:3], ["None", "/Script/Engine", "/Script/CoreUObject"])


class V6IoStore(unittest.TestCase):
    def make(self, d, flags, pkg_flags):
        head = bytearray(144); head[:16] = IO.TOC_MAGIC; head[16] = 3
        struct.pack_into("<9I", head, 20, 144, 1, 1, 12, 0, 32, 65536, 0, 1); head[80] = flags
        ids = b"\0" * 11 + bytes([IO.EXPORT_BUNDLE_DATA])
        offlen = (0).to_bytes(5, "big") + (64).to_bytes(5, "big")
        block = (0).to_bytes(5, "little") + (64).to_bytes(3, "little") + (64).to_bytes(3, "little") + b"\0"
        p = os.path.join(d, "g.utoc"); open(p, "wb").write(bytes(head) + ids + offlen + block)
        summary = struct.pack("<II", 0, 64) + b"\0" * 8 + struct.pack("<I", pkg_flags)
        open(os.path.join(d, "g.ucas"), "wb").write(summary + b"\0" * (64 - len(summary)))
        return p

    def test_unversioned_flag_counted(self):
        with tempfile.TemporaryDirectory() as d:
            rows, ex = IO.probe([self.make(d, 0, 0x80002000)])
            self.assertEqual((rows[0]["packages"], rows[0]["unversioned"]), (1, 1))
            rows, ex = IO.probe([self.make(d, 0, 0x80000000)])
            self.assertEqual(rows[0]["versioned"], 1)

    def test_encrypted_container_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            rows, ex = IO.probe([self.make(d, IO.FLAG_ENCRYPTED, 0x2000)])
            self.assertEqual(rows[0]["packages"], 0); self.assertEqual(ex[0]["what"], "container")


if __name__ == "__main__":
    unittest.main()
