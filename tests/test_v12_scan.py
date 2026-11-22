"""Synthetic zen packages for the Anim Blueprint struct scanner (v12 fixes); no game data.

Covers two cases the v10 scanner missed silently: a struct name that sits past the first 64 KiB of
a large header, and a child Anim Blueprint struct whose super is the parent's struct in another
package (a package import)."""
import os, struct, sys, unittest
from usmap_generator.engine import anim_structs as A

STRUCT_CLASS = (1 << 62) | 5                          # script object: AnimBlueprintGeneratedStruct
NATIVE_SUPER = (1 << 62) | 6                          # script object: AnimBlueprintConstantData
SCRIPT = {STRUCT_CLASS: "AnimBlueprintGeneratedStruct", NATIVE_SUPER: "AnimBlueprintConstantData"}
PARENT_HASH = 0x1234_5678_9ABC_DEF0


def name_batch(names):
    blob = b"".join(n.encode("latin-1") for n in names)
    out = struct.pack("<II", len(names), len(blob)) + bytes(8) + bytes(8 * len(names))
    out += b"".join(struct.pack(">H", len(n)) for n in names)
    return out + blob


def struct_body():
    """Struct export body with no fields; super = import 0."""
    return (struct.pack("<H", 0x100) + struct.pack("<I", 0) + struct.pack("<i", -1) + struct.pack("<i", 0)
            + struct.pack("<i", 0) + struct.pack("<ii", 0, 0) + struct.pack("<I", 0))


def zen_package(import0, filler=0):
    """5.0-5.2 layout zen package: one AnimBlueprintGeneratedConstantData export (class from the
    script table) whose super is import 0. `filler` extra names push the target name back."""
    names = ["/Game/ABP_Child"] + [f"FillerName_{i:012d}" for i in range(filler)] + list(A.TARGETS[:1])
    body = struct_body()
    batch = name_batch(names)
    hashes_off = 44 + len(batch)
    import_off = hashes_off + 8
    export_off = import_off + 8
    bundle_off = export_off + 72
    header_size = bundle_off + 16
    head = struct.pack("<IIIIIIiiii", 0, header_size, 0, 0, 0, 0, hashes_off, import_off, export_off, bundle_off)
    head += struct.pack("<i", header_size) + batch
    head += struct.pack("<Q", PARENT_HASH) + struct.pack("<Q", import0)
    head += struct.pack("<QQIIQQQQQIB", 0, len(body), len(names) - 1, 0, A.NULL_INDEX, STRUCT_CLASS, 0, 0,
                        0x42, 0, 0) + bytes(3)
    head += struct.pack("<IIII", 0, 0, 0, 1)            # create, then serialize export 0
    assert len(head) == header_size
    return head + body


class ZenScan(unittest.TestCase):
    def test_package_import_super_resolves_through_imported_exports(self):
        b = zen_package((2 << 62) | (0 << 32) | 0)      # imported package 0, export hash index 0
        errors = []
        self.assertEqual(A.read_zen(b, SCRIPT, False, errors=errors), [])
        self.assertEqual(len(errors), 1)               # not silent: reported as unreadable
        imported = {0: ("/Game/ABP_Parent", {PARENT_HASH: "AnimBlueprintGeneratedConstantData"})}
        supers = {}
        rows = A.read_zen(b, SCRIPT, False, imported.get, super_packages=supers)
        self.assertEqual(rows, [(None, "AnimBlueprintGeneratedConstantData",
                                 {"super": "AnimBlueprintGeneratedConstantData", "count": 0, "props": []})])
        # same-name super: a name-keyed reader needs the package that exports it
        self.assertEqual(supers, {(None, "AnimBlueprintGeneratedConstantData"): "/Game/ABP_Parent"})

    def test_unknown_imported_export_is_reported(self):
        b = zen_package((2 << 62) | (0 << 32) | 0)
        errors = []
        self.assertEqual(A.read_zen(b, SCRIPT, False, {0: ("/Game/P", {1: "Other"})}.get, errors), [])
        self.assertIn("unresolved reference", errors[0][2])

    def test_struct_name_past_first_64k_of_header(self):
        b = zen_package(NATIVE_SUPER, filler=3000)
        self.assertGreater(b.index(A.TARGETS[0].encode()), 64 * 1024)
        supers = {}
        rows = A.read_zen(b, SCRIPT, False, super_packages=supers)
        self.assertEqual([(n, r["super"]) for _, n, r in rows], [(A.TARGETS[0], "AnimBlueprintConstantData")])
        self.assertEqual(supers, {})                   # native super: no package

    def test_package_without_target_names_is_skipped(self):
        b = zen_package(NATIVE_SUPER).replace(A.TARGETS[0].encode(), b"SomethingElseEntirelyHereXYZ_ABCDEFG"[:len(A.TARGETS[0])])
        self.assertEqual(A.read_zen(b, SCRIPT, False), [])


if __name__ == "__main__":
    unittest.main()
