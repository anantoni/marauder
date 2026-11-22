"""The per-asset mapping's JSON and binary encodings describe the same structs."""
import json, os, tempfile, unittest

from test_v12_scan import NATIVE_SUPER, SCRIPT, zen_package
from usmap_generator import perasset as P
from usmap_generator.engine import anim_structs as A

T = lambda kind, **kw: dict({"t": kind}, **kw)
RICH = {"super": "AnimBlueprintConstantData", "count": 6, "props": [
    {"i": 0, "dim": 1, "name": "Speed", "type": T("FloatProperty")},
    {"i": 1, "dim": 3, "name": "Bones", "type": T("ArrayProperty", inner=T("StructProperty", struct="BoneReference"))},
    {"i": 2, "dim": 1, "name": "Curves", "type": T("MapProperty", key=T("NameProperty"),
                                                   value=T("EnumProperty", inner=T("ByteProperty"), enum="ECurveKind"))},
    {"i": 3, "dim": 1, "name": "Tags", "type": T("SetProperty", inner=T("StrProperty"))},
    {"i": 4, "dim": 1, "name": "Maybe", "type": T("OptionalProperty", inner=T("IntProperty"))},
    {"i": 5, "dim": 1, "name": "Ünïcode", "type": T("Utf8StrProperty")}]}


def scan_fixture():
    """A GameScan with records read by the real scanner from synthetic zen packages, plus rich records."""
    sc = A.GameScan()
    rows = A.read_zen(zen_package(NATIVE_SUPER), SCRIPT, False)
    sc.add_records("/Game/ABP_Base", rows)
    supers = {}
    imported = {0: ("/Game/ABP_Base", {0x1234_5678_9ABC_DEF0: "AnimBlueprintGeneratedConstantData"})}
    rows = A.read_zen(zen_package((2 << 62) | 0), SCRIPT, False, imported.get, super_packages=supers)
    sc.add_records("/Game/ABP_Child", rows, supers)
    sc.add_records("/Game/ABP_Rich", [("ABP_Rich_C", "AnimBlueprintGeneratedConstantData", RICH),
                                      ("ABP_Rich_C", "AnimBlueprintGeneratedMutableData",
                                       {"super": "AnimBlueprintMutableData", "count": 0, "props": []})])
    return sc


class PerAsset(unittest.TestCase):
    def test_json_and_binary_describe_the_same_structs(self):
        packages = P.records_by_package(scan_fixture())
        self.assertEqual([p for p, _ in packages], ["/Game/ABP_Base", "/Game/ABP_Child", "/Game/ABP_Rich"])
        with tempfile.TemporaryDirectory() as d:
            j, b = P.write(os.path.join(d, "MyGame"), packages, "5.3")
            with open(j, encoding="utf-8") as f: from_json = P.from_json(json.load(f))
            with open(b, "rb") as f: from_bin = P.from_binary(f.read())
        self.assertEqual(from_json, from_bin)
        self.assertEqual(from_json, packages)

    def test_child_struct_names_the_package_of_its_super(self):
        doc = P.to_json(P.records_by_package(scan_fixture()))
        child = next(p for p in doc["packages"] if p["package"] == "/Game/ABP_Child")["structs"][0]
        self.assertEqual((child["super"], child["superPackage"]), ("AnimBlueprintGeneratedConstantData", "/Game/ABP_Base"))
        base = next(p for p in doc["packages"] if p["package"] == "/Game/ABP_Base")["structs"][0]
        self.assertIsNone(base["superPackage"])

    def test_same_name_different_structs_are_all_kept(self):
        """The reason for the file: one name, several different structs, one per package."""
        packages = P.records_by_package(scan_fixture())
        recs = [r for _, rows in packages for _, n, _, _, r in rows if n == "AnimBlueprintGeneratedConstantData"]
        self.assertEqual(len(recs), 3)
        self.assertEqual(len({json.dumps(r, sort_keys=True) for r in recs}), 3)

    def test_binary_rejects_other_files(self):
        with self.assertRaises(ValueError): P.from_binary(b"\xc4\x30" + b"\0" * 20)   # a .usmap is not a per-asset file
        with self.assertRaises(ValueError): P.from_json({"format": "something else"})


if __name__ == "__main__":
    unittest.main()
