"""Synthetic tests for the v10 Anim Blueprint struct reader and copy choice; no game data."""
import os, struct, sys, unittest
from usmap_generator.engine import anim_structs as A
from usmap_generator.engine import anim_choice as C

NAMES = ["None", "AnimBlueprintGeneratedConstantData", "NameProperty", "__NameProperty", "StructProperty",
         "__StructProperty", "ByteProperty", "__ByteProperty", "ArrayProperty", "__ArrayProperty",
         "FloatProperty", "BoolProperty"]


def fname(s, number=0):
    return struct.pack("<II", NAMES.index(s), number)


def common(name, number, dim=1):
    # field name, object flags, array size, element size, property flags, rep index, rep notify, condition
    return fname(name, number) + struct.pack("<IiiQH", 0, dim, 4, 0, 0) + fname("None") + b"\0"


def struct_body(fields):
    b = struct.pack("<H", 0x100)                          # unversioned header, no values
    b += struct.pack("<I", 0)                             # no object guid
    b += struct.pack("<i", -1)                            # super struct: import 0
    b += struct.pack("<i", 0)                             # no children
    b += struct.pack("<i", len(fields)) + b"".join(fields)
    b += struct.pack("<ii", 0, 0) + struct.pack("<I", 0)  # script sizes, struct flags
    return b


class Reader(unittest.TestCase):
    def setUp(self):
        self.refs = {-1: "AnimBlueprintConstantData", -2: "AnimNodeFunctionRef", -3: "EAnimGroupRole"}

    def parse(self, body):
        name = lambda i, n: (NAMES[i] + (f"_{n - 1}" if n else ""))
        return A._object_body(body, 0, len(body), name, lambda i: self.refs[i])

    def test_fields_and_references(self):
        body = struct_body([
            fname("NameProperty") + common("__NameProperty", 52),
            fname("StructProperty") + common("__StructProperty", 7) + struct.pack("<i", -2),
            fname("ByteProperty") + common("__ByteProperty", 59) + struct.pack("<i", -3),
            fname("ArrayProperty") + common("__ArrayProperty", 0, 1) + fname("FloatProperty") + common("None", 0),
            fname("BoolProperty") + common("__NameProperty", 0, 2) + bytes(6),
        ])
        rec = self.parse(body)
        self.assertEqual(rec["super"], "AnimBlueprintConstantData")
        self.assertEqual(rec["count"], 6)
        p = rec["props"]
        self.assertEqual([x["name"] for x in p], ["__NameProperty_51", "__StructProperty_6", "__ByteProperty_58",
                                                   "__ArrayProperty", "__NameProperty"])
        self.assertEqual(p[1]["type"], {"t": "StructProperty", "struct": "AnimNodeFunctionRef"})
        self.assertEqual(p[2]["type"], {"t": "EnumProperty", "inner": {"t": "ByteProperty"}, "enum": "EAnimGroupRole"})
        self.assertEqual(p[3]["type"], {"t": "ArrayProperty", "inner": {"t": "FloatProperty"}})
        self.assertEqual((p[4]["i"], p[4]["dim"]), (4, 2))

    def test_size_mismatch_is_rejected(self):
        body = struct_body([fname("NameProperty") + common("__NameProperty", 1)]) + b"\0"
        with self.assertRaises(A.Unreadable):
            self.parse(body)

    def test_pak_package_names(self):
        self.assertEqual(A.pak_package_name("Game/Content/A/ABP_X.uasset"), "/Game/A/ABP_X")
        self.assertEqual(A.pak_package_name("Engine/Content/B.uasset"), "/Engine/B")
        self.assertEqual(A.pak_package_name("Game/Plugins/Pl/Content/C.uasset"), "/Pl/C")


class Scan:
    def __init__(self, packages, records, anim, supers=None):
        self.packages = packages; self.records = records; self.anim_classes = set(anim)
        self.class_supers = supers or {}


def rec(n):
    return {"super": "AnimBlueprintConstantData", "count": n, "props": []}


class Choice(unittest.TestCase):
    def test_post_order(self):
        g = {"M": ["A", "B"], "A": ["C"], "B": ["C"], "C": []}
        self.assertEqual(C.post_order(g, ["M"]), ["C", "A", "B", "M"])

    def test_settings(self):
        ini = {"P/Config/DefaultEngine.ini": b"[/Script/EngineSettings.GameMapsSettings]\r\n"
                                              b"GameDefaultMap=/Game/Maps/Menu.Menu\r\nGameInstanceClass=/Game/GI.GI_C\r\n",
               "P/Plugins/X/Config/DefaultEngine.ini": b"GameDefaultMap=/Game/Other.Other\n"}
        self.assertEqual(C.startup_settings(ini), {"GameDefaultMap": "/Game/Maps/Menu", "GameInstanceClass": "/Game/GI"})

    def test_single_copy_is_kept_and_unloaded_ones_ignored(self):
        sc = Scan({"/M": ["/ABP"], "/ABP": [], "/Other": []},
                  [("/ABP", "ABP_C", C.NAMES[0], rec(3)), ("/Other", "O_C", C.NAMES[0], rec(9))], ["/ABP", "/Other"])
        chosen, why = C.choose(sc, {"GameDefaultMap": "/M"}, {})
        self.assertEqual(chosen, {C.NAMES[0]: rec(3)})
        self.assertIn(C.NAMES[1], why)

    def test_abstains_when_orders_disagree(self):
        sc = Scan({"/M": ["/A", "/B"], "/A": [], "/B": []},
                  [("/A", "A_C", C.NAMES[0], rec(1)), ("/B", "B_C", C.NAMES[0], rec(2))], ["/A", "/B"])
        chosen, why = C.choose(sc, {"GameDefaultMap": "/M"}, {})
        self.assertEqual(chosen, {})

    def test_last_created_and_closest_must_agree(self):
        recs = [("/A", "A_C", C.NAMES[0], rec(1)), ("/B", "B_C", C.NAMES[0], rec(2))]
        agree = Scan({"/M": ["/X", "/A"], "/X": ["/B"], "/A": [], "/B": []}, recs, ["/A", "/B"])
        self.assertEqual(C.choose(agree, {"GameDefaultMap": "/M"}, {}, agreement=True)[0], {C.NAMES[0]: rec(1)})
        self.assertEqual(C.choose(agree, {"GameDefaultMap": "/M"}, {})[0], {})     # off by default
        differ = Scan({"/M": ["/A", "/X"], "/X": ["/B"], "/A": [], "/B": []}, recs, ["/A", "/B"])
        self.assertEqual(C.choose(differ, {"GameDefaultMap": "/M"}, {}, agreement=True)[0], {})

    def test_nothing_loaded(self):
        sc = Scan({"/M": [], "/A": []}, [("/A", "A_C", C.NAMES[0], rec(1))], ["/A"])
        self.assertEqual(C.choose(sc, {"GameDefaultMap": "/M"}, {})[0], {})

    def test_default_game_mode_only_without_map_override(self):
        graph = {"/M": [], "/GM": ["/ABP"], "/ABP": []}
        sc = Scan(graph, [("/ABP", "ABP_C", C.NAMES[0], rec(4))], ["/ABP"], {"/GM": [("script", "GameModeBase")]})
        st = {"GameDefaultMap": "/M", "GlobalDefaultGameMode": "/GM"}
        self.assertEqual(C.choose(sc, st, {})[0], {C.NAMES[0]: rec(4)})
        graph["/M"] = ["/GM2"]; graph["/GM2"] = []
        sc.class_supers["/GM2"] = [("script", "MyMode")]
        self.assertEqual(C.choose(sc, st, {"MyMode": {"super": "GameModeBase"}})[0], {})


if __name__ == "__main__":
    unittest.main()
