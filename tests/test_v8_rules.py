"""v8 rules: automatic enum maxima on saved mappings, partly initialized enum records."""
import os, struct, tempfile, unittest

from usmap_generator.engine.fname_order import automatic_maximum_name, flags_maximum_policy, restore_automatic_maxima
from test_ue_usmap import BASE, Img


class AutomaticMaxima(unittest.TestCase):
    def test_case_sensitive_prefix_adds_a_maximum(self):
        # A respelled entry shortens the case-sensitive common prefix to one without an
        # underscore, so the enum name becomes the prefix and the declared REVERB_MAX no longer counts.
        enums = {"ReverbPreset": [(0, "REVERB_Default"), (1, "Reverb_Cave"), (2, "REVERB_MAX")]}
        info = {"ReverbPreset": {"qualified": True, "flags": False}}
        out = restore_automatic_maxima(enums, info, "signed maximum")
        self.assertEqual(out["ReverbPreset"][-1], (3, "ReverbPreset_MAX"))
        same = {"ReverbPreset": [(0, "REVERB_Default"), (1, "REVERB_Cave"), (2, "REVERB_MAX")]}
        self.assertEqual(restore_automatic_maxima(same, info, "signed maximum"), same)

    def test_scoped_prefix_can_end_inside_the_enum_name(self):
        self.assertEqual(automatic_maximum_name("ENDIMesh_SourceMode", ["Default", "Source"], True), "ENDIMesh_MAX")
        self.assertEqual(automatic_maximum_name("EFoo", ["Bar_A", "Bar_B"], True), "Bar_MAX")
        self.assertEqual(automatic_maximum_name("EFoo", ["A", "B"], True), "EFoo_MAX")
        self.assertEqual(automatic_maximum_name("EFoo", ["X_A", "X_B"], False), "X_MAX")

    def test_existing_max_unknown_enums_and_collisions(self):
        info = {k: {"qualified": False, "flags": False} for k in ("A", "B", "C")}
        enums = {"A": [(0, "F_One"), (1, "F_Two")], "B": [(0, "F_Three"), (1, "F_Four")],
                 "C": [(0, "One"), (1, "Max")], "D": [(0, "X"), (1, "Y")]}
        out = restore_automatic_maxima(enums, info, "signed maximum")
        self.assertEqual(out["A"][-1], (2, "F_MAX"))      # the first enum keeps a shared name
        self.assertEqual(out["B"], enums["B"])
        self.assertEqual(out["C"], enums["C"])              # "MAX" exists (names compare without case)
        self.assertEqual(out["D"], enums["D"])              # no parameter record: left alone

    def test_flag_policies_and_signed_values(self):
        entries = [(0, "None"), (1, "A"), (2, "B"), (8, "C"), ((1 << 64) - 1, "All")]
        info = {"E": {"qualified": True, "flags": True}}
        self.assertEqual(restore_automatic_maxima({"E": entries}, info, None)["E"], entries)
        self.assertEqual(restore_automatic_maxima({"E": entries}, info, "signed maximum")["E"][-1], (9, "E_MAX"))
        self.assertEqual(restore_automatic_maxima({"E": entries}, info, "single-bit union")["E"][-1], (12, "E_MAX"))
        self.assertEqual(flags_maximum_policy("5.1"), "signed maximum")
        self.assertEqual(flags_maximum_policy("5.6"), "single-bit union")
        self.assertIsNone(flags_maximum_policy(None))


def _partial_image(path):
    """A record with names and an enumerator pointer but a zero tail, and a constructor stub that
    loads its address. A complete record follows so that the arrays have a known neighbour."""
    im = Img()
    text = BASE + 0x1000
    def enumerators(qualifier, names):
        return im.put(".rdata", b"".join(struct.pack("<Qq", im.cstr(f"{qualifier}::{n}"), v)
                                       for v, n in enumerate(names)))
    first = enumerators("EPhase", ["Tracking", "Locking", "Done"])
    second = enumerators("EOther", ["A", "B"])
    partial = im.put(".data", struct.pack("<QQQQQQ", text + 0x10, 0, im.cstr("EPhase"), im.cstr("EPhase"), first, 0))
    complete = im.put(".data", struct.pack("<QQQQQIhBB", text + 0x10, 0, im.cstr("EOther"), im.cstr("EOther"),
                                           second, 0x45, 2, 0, 2))
    # Constructor stub at text+0x200: sub rsp; lea rdx,[rip+partial]; ...; and a jump thunk at +0x100.
    stub = text + 0x200
    code = bytearray(b"\x48\x83\xec\x28")
    code += b"\x48\x8d\x15" + struct.pack("<i", partial - (stub + len(code) + 7))
    code += b"\xc3"
    off = 0x200
    im.buf[".text"][off:off + len(code)] = code
    thunk = text + 0x100
    im.buf[".text"][0x100:0x105] = b"\xe9" + struct.pack("<i", stub - (thunk + 5))
    im.build(path)
    return partial, complete, thunk


class PartialRecords(unittest.TestCase):
    def test_partial_enum_is_bounded_and_linked(self):
        from usmap_generator.engine.scan import Image, Scanner
        from usmap_generator.engine import partial_enums
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "debug.exe")
            partial, complete, thunk = _partial_image(path)
            im = Image(path)
            sc = Scanner(im); sc.prepare()
            records = partial_enums.partial_records(im, sc, "E5x")
            self.assertEqual([(x["va"], x["name"]) for x in records], [(partial, "EPhase")])
            self.assertEqual([v for _, v in records[0]["entries"]], [0, 1, 2])
            window = partial_enums.CodeWindow(im)
            self.assertIn(partial, window.loaded_addresses(thunk))
            self.assertLessEqual(window.bytes_read, 2 * partial_enums.WINDOW)
            info, layout = partial_enums.enum_info(im, sc, "E5x")
            self.assertEqual(info["EPhase"], {"qualified": True, "flags": False})


if __name__ == "__main__":
    unittest.main()
