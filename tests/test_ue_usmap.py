"""Tests for the static usmap generator. Run: python3 -I -m pytest test_ue_usmap.py, or python3 -I test_ue_usmap.py.

The synthetic executables are built here from scratch (a minimal PE with .text/.rdata/.data/.pdata); no game
file is involved."""
import os, struct, sys, tempfile
from usmap_generator.engine import usmap as U
from usmap_generator.engine import naming
from usmap_generator.engine.genusmap import enum_entries, generate, version_string

def test_usmap_roundtrip():
    enums = {"EColor": [(0, "Red"), (5, "Blue"), (6, "EColor_MAX")]}
    structs = {"Thing": {"super": None, "count": 3, "props": [
        {"i": 0, "dim": 1, "name": "A", "type": {"t": "IntProperty"}},
        {"i": 1, "dim": 2, "name": "B", "type": {"t": "ArrayProperty", "inner": {"t": "StructProperty", "struct": "Vector"}}},
        {"i": 3, "dim": 1, "name": "C", "type": {"t": "MapProperty", "key": {"t": "NameProperty"},
                                                   "value": {"t": "EnumProperty", "inner": {"t": "ByteProperty"}, "enum": "EColor"}}}]}}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.usmap"); U.write(p, enums, structs)
        e, s = U.read(p)
    assert e == enums and s == structs

def test_enum_entries_follow_uenum():
    assert enum_entries("EInterpCurveMode", [("CIM_Linear", 0), ("CIM_CurveAuto", 1)], 0)[-1] == (2, "CIM_MAX")
    assert enum_entries("EObjectTypeQuery", [("ObjectTypeQuery1", 0), ("ObjectTypeQuery_MAX", 1)], 0)[-1] == (2, "EObjectTypeQuery_MAX")
    assert len(enum_entries("ELifetimeCondition", [("COND_None", 0), ("COND_Max", 1)], 0)) == 2   # case-insensitive match
    assert enum_entries("EFlags", [("EFlags::None", 0), ("EFlags::B", 64)], 1, "signed maximum")[-1] == (65, "EFlags_MAX")
    assert enum_entries("ERule", [("ERule::A", 0), ("ERule::B", 1)], 0) == [(0, "A"), (1, "B"), (2, "ERule_MAX")]

def test_align_preserves_order():
    sc = {("z1", "Alpha"): 1.0, ("z2", "Beta"): 1.0, ("z2", "Alpha"): 1.5, ("z3", "Gamma"): 1.0}
    out = naming.align(["z1", "z2", "z3"], ["Alpha", "Beta", "Gamma"], lambda z, n: sc.get((z, n)))
    assert out == {"z1": "Alpha", "z2": "Beta", "z3": "Gamma"}

def test_alignment_regret_measures_the_whole_run():
    scores = {(1, "Alpha"): 2, (1, "Beta"): 3, (2, "Beta"): 2}
    out, regret = naming.align([1, 2], ["Alpha", "Beta"], lambda z, n: scores.get((z, n)), marginals=True)
    assert out == {1: "Alpha", 2: "Beta"}
    assert regret[1]["Alpha"] == regret[2]["Beta"] == 0
    assert regret[1]["Beta"] == -1  # taking Beta here also loses the second constructor

def test_joint_names_prevent_a_container_use_stealing_a_base_type():
    # Greedy duplicate repair keeps 1->Base, then gives 2 its poor fallback (total 6.1).
    # The joint assignment preserves the only strong candidate for 2 (total 10.9).
    out = naming.joint_assignment({1: {"Base": 6, "Element": 5}, 2: {"Base": 5.9, "Other": 0.1}})
    assert out == {1: "Element", 2: "Base"}
    assert naming.joint_assignment({1: {}, 2: {"Bad": -20}}) == {}

def test_joint_repair_does_not_retarget_an_uncontested_type():
    scores = {1: {"Shared": 6, "Other": 5}, 2: {"Shared": 5.9, "CollisionResponse": 5.8},
              3: {"CollisionResponse": 2, "Other": 1}}
    out = naming.assign([1, 2, 3], {"Shared": 100, "Other": 120, "CollisionResponse": 110},
                        [(0, 100), (4, 120)], lambda z, n: scores[z].get(n), {},
                        w=0, base=0, joint=True)
    assert out[3][0] == "CollisionResponse"
    assert len({n for n, _ in out.values()}) == len(out)

def test_word_tokens():
    assert naming.toks("ImpactNormals") == {"impact", "normal"}
    assert naming.toks("RigUnit_HUDOverview") >= {"rig", "unit", "hud", "overview"}

def test_four_letter_able_root_supports_linkable_members():
    target = naming.toks("AnimLinkableElement")
    supported = naming.word_score(naming.use_profile(["EndLink"]), set(), "AnimLinkableElement", target)
    unrelated = naming.word_score(naming.use_profile(["OtherField"]), set(), "AnimLinkableElement", target)
    assert supported - unrelated > 2

def test_entire_type_name_evidence_does_not_reward_a_subset_or_color_alias():
    profile = naming.use_profile(["GameplayTagRedirects"])
    forms = {frozenset(naming.toks("GameplayTagRedirects"))}
    score = lambda n, f: naming.word_score(profile, set(), n, naming.toks(n), exact_names=f)
    assert score("GameplayTagRedirect", forms) > score("GameplayTagRedirect", ())
    assert score("GameplayTag", forms) == score("GameplayTag", ())
    colors = {frozenset({"color"})}
    p = naming.use_profile(["Colors"])
    assert naming.word_score(p, set(), "Color", {"color"}, colors) == naming.word_score(p, set(), "Color", {"color"})

def test_signature_hints_keep_native_identity_and_conflicts():
    p = lambda z, name: {"kind": "Struct", "fn": z, "name": name}
    fs = {1: {"name": "Conv_IntPointToVector2D", "tree": [p(10, "InIntPoint"), p(20, "ReturnValue")]},
          2: {"name": "IntPoint_Zero", "tree": [p(10, "ReturnValue")]},
          3: {"name": "MakeBox", "tree": [p(30, "ReturnValue")]},
          4: {"name": "BreakBox", "tree": [p(30, "InBox")]},
          5: {"name": "BreakIntVector2", "tree": [p(40, "A"), p(50, "B")]}}
    types = {"IntPoint", "IntVector2", "Vector2D", "Box", "BoxSphereBounds"}
    assert naming.signature_hints(fs, types) == {10: {"IntPoint"}, 20: {"Vector2D"}, 30: {"Box"}}
    fs[6] = {"name": "MakeIntVector2", "tree": [p(10, "ReturnValue")]}
    assert naming.signature_hints(fs, types)[10] == {"IntPoint", "IntVector2"}

def test_getter_setter_identity_needs_both_signatures_and_exact_size():
    def method(name, arg, z=10, size=16):
        return {"name": name, "size": size, "tree": [{"kind": "Struct", "fn": z, "name": arg, "off": 0, "dim": 1}]}
    types = {"Margin": {"size": 16}, "Color": {"size": 4}}
    fs = {1: method("GetMargin", "ReturnValue")}
    assert naming.signature_hints(fs, types) == {}
    fs[2] = method("SetMargin", "InMargin")
    assert naming.signature_hints(fs, types) == {10: {"Margin"}}
    # The public property name Color need not be the native struct name LinearColor.
    fs[3] = method("GetColor", "ReturnValue", 20)
    fs[4] = method("SetColor", "InColor", 20)
    assert naming.signature_hints(fs, types) == {10: {"Margin"}}
    # Distinct packages may reuse a short type name. Accessor names are weaker than a factory.
    types["Color"]["size"] = 16
    types["LinearColor"] = {"size": 16}
    fs[5] = method("MakeLinearColor", "ReturnValue", 20)
    assert naming.signature_hints(fs, types)[20] == {"LinearColor"}

# ---------------- synthetic executable ----------------
BASE = 0x140000000
SECS = [(".text", 0x1000, 0x2000), (".rdata", 0x3000, 0x3000), (".data", 0x6000, 0x2000),
        (".pdata", 0x8000, 0x1000), (".bind", 0x9000, 0x1000)]

class Img:
    def __init__(self):
        self.buf = {n: bytearray(sz) for n, _, sz in SECS}
        self.cur = {".rdata": 0, ".data": 0}
    def va(self, sec, off): return BASE + dict((n, r) for n, r, _ in SECS)[sec] + off
    def put(self, sec, data, align=8):
        o = (self.cur[sec] + align - 1) // align * align
        self.buf[sec][o:o + len(data)] = data; self.cur[sec] = o + len(data)
        return self.va(sec, o)
    def cstr(self, s): return self.put(".rdata", s.encode() + b"\0", 1)
    def wstr(self, s): return self.put(".rdata", s.encode("utf-16-le") + b"\0\0", 2)
    def build(self, path):
        hdr = bytearray(0x400)
        hdr[0:2] = b"MZ"; struct.pack_into("<I", hdr, 0x3C, 0x80)
        hdr[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", hdr, 0x84, 0x8664, len(SECS)); struct.pack_into("<H", hdr, 0x94, 0xF0)
        struct.pack_into("<H", hdr, 0x98, 0x20B)
        struct.pack_into("<Q", hdr, 0x98 + 24, BASE)
        raw = 0x400; body = b""
        for i, (n, rva, sz) in enumerate(SECS):
            struct.pack_into("<8sIIII", hdr, 0x98 + 0xF0 + 40 * i, n.encode(), sz, rva, sz, raw + len(body))
            body += bytes(self.buf[n])
        with open(path, "wb") as f: f.write(bytes(hdr) + body)

def synth(layout):
    """One module with two structs, Inner {X: int} and Outer {Count: int, Item: Inner}."""
    im = Img(); T = lambda off: BASE + 0x1000 + off
    pkg, z_inner, z_outer = T(0x10), T(0x100), T(0x130)          # Z_Construct functions (opaque addresses)
    ss_inner, ops_inner, ss_outer, ops_outer = T(0x400), T(0x440), T(0x480), T(0x4C0)
    pd = bytearray()
    for a in (z_inner, z_outer): pd += struct.pack("<III", a - BASE, a - BASE + 0x2F, 0x7000)   # same signature
    for a in (ss_inner, ops_inner, ss_outer, ops_outer): pd += struct.pack("<III", a - BASE, a - BASE + 0x37, 0x7010)
    im.buf[".pdata"][:len(pd)] = pd
    def prop(name, gen, off, fn=0):
        if layout == "P50":
            r = struct.pack("<QQQBxxxIIIQQ", im.cstr(name), 0, 0, gen, 0x45, 1, off, fn, 0)
        elif layout == "P51":
            r = struct.pack("<QQQBxxxIIxxxxQQIxxxxQQ", im.cstr(name), 0, 0, gen, 0x45, 1, 0, 0, off, fn, 0)
        else:
            r = struct.pack("<QQQBxxxIQQHHxxxxQQ", im.cstr(name), 0, 0, gen, 0x45, 0, 0, 1, off, fn, 0)
        return im.put(".rdata", r)
    def params(name, sup, ops, props, size):
        arr = im.put(".rdata", b"".join(struct.pack("<Q", p) for p in props)) if props else 0
        if layout in ("P50", "P51"):
            r = struct.pack("<QQQQQQQIII", pkg, sup, ops, im.cstr(name), size, 4, arr, len(props), 0x45, 1)
        else:
            r = struct.pack("<QQQQQHHHxxII", pkg, sup, ops, im.cstr(name), arr, len(props), size, 4, 0x45, 1)
        return im.put(".data", r)
    params("Inner", 0, ops_inner, [prop("X", 3, 0)], 4)
    params("Outer", 0, ops_outer, [prop("Count", 3, 0), prop("InnerItem", 0x19, 4, z_inner)], 8)
    for ss, ops, nm in ((ss_inner, ops_inner, "Inner"), (ss_outer, ops_outer, "Outer")):
        im.put(".rdata", struct.pack("<QQQQQ", ss, ops, im.wstr(nm), im.va(".data", 0x1800), 0))
    return im

def check(layout):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "synth.exe"); synth(layout).build(p)
        e, s, rep = generate(p)
    assert rep["layouts"]["chosen"]["prop"] == layout, rep["layouts"]
    assert [x["name"] for x in s["Outer"]["props"]] == ["Count", "InnerItem"]
    assert U.fmt(s["Outer"]["props"][1]["type"]) == "Struct<Inner>", s["Outer"]
    assert U.fmt(s["Inner"]["props"][0]["type"]) == "Int"

def test_synthetic_51():
    check("P51")

def test_synthetic_50_is_not_binary_version_verification():
    check("P50")

def test_synthetic_5x():
    check("P5x")

def test_code_sections_are_never_read_or_searched():
    from unittest.mock import patch
    from usmap_generator.engine.scan import Image
    import builtins
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "data-only.exe")
        im = synth("P5x")
        marker = b"++UE5+Release-5.0\0"
        im.buf[".text"][:len(marker)] = marker
        im.buf[".bind"][:len(marker)] = marker
        code_name = b"\0CodeOnlyNativeType\0"
        im.buf[".text"][0x80:0x80 + len(code_name)] = code_name
        im.buf[".bind"][0x80:0x80 + len(code_name)] = code_name
        im.build(p)
        reads = []
        real_open = builtins.open
        class Tracked:
            def __enter__(self): return self
            def __exit__(self, *args): self.file.close()
            def __init__(self, *args, **kwargs): self.file = real_open(*args, **kwargs)
            def seek(self, *args): return self.file.seek(*args)
            def read(self, n=-1):
                start = self.file.tell(); data = self.file.read(n)
                reads.append((start, start + len(data)))
                return data
        with patch("builtins.open", Tracked):
            image = Image(p)
        for section in (".text", ".bind"):
            _, _, raw, size = image.pe.secs[section]
            assert all(end <= raw or start >= raw + size for start, end in reads)
        assert version_string(image) is None
        from usmap_generator.engine.compare import native_names
        # The evaluator also stays within permitted sections when classifying native names.
        assert native_names(p, {"Inner", "CodeOnlyNativeType"}) == {"Inner"}
        try: image.pe.q(BASE + 0x1000)
        except ValueError: pass
        else: raise AssertionError("code read allowed")
        im.put(".rdata", "++UE5+Release-5.3".encode("utf-16-le") + b"\0\0", 2)
        im.build(p)
        assert version_string(Image(p)) == "5.3"

def test_generator_rejects_reference_input():
    """The generator has no option that feeds a reference mapping into generation."""
    import subprocess
    proc = subprocess.run([sys.executable, "-m", "usmap_generator", "generate", "missing-folder",
                           "--names", "reference.usmap"], capture_output=True, text=True)
    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr
    assert "Traceback" not in proc.stderr

def test_function_records_link_by_owner_and_name():
    from usmap_generator.engine.scan import Image, Scanner
    for layout, family in (("P51", "F51"), ("P5x", "F51"), ("P5x", "F5xNames"), ("P5x", "F5x")):
        im = Img()
        owner, fn, ctor = BASE + 0x1100, BASE + 0x1200, BASE + 0x1300
        noreg, other_owner, other_inner = BASE + 0x1400, BASE + 0x1500, BASE + 0x1600
        info = im.put(".data", bytes(32))
        im.put(".rdata", struct.pack("<QQQQQ", owner, noreg, im.wstr("ULibrary"), info, 0))
        im.put(".rdata", struct.pack("<QQQQQ", other_owner, other_inner, im.wstr("ULibrary"), info, 0))
        if layout == "P51":
            prop = struct.pack("<QQQBxxxIIxxxxQQIxxxxQQ", im.cstr("ReturnValue"), 0, 0, 0x19, 0x45, 1, 0, 0, 0, ctor, 0)
        else:
            prop = struct.pack("<QQQBxxxIQQHHxxxxQQ", im.cstr("ReturnValue"), 0, 0, 0x19, 0x45, 0, 0, 1, 0, ctor, 0)
        pv = im.put(".rdata", prop)
        arr = im.put(".rdata", struct.pack("<Q", pv))
        rec = bytearray(80)
        struct.pack_into("<QQQQQ", rec, 0, owner, 0, im.cstr("MakeInner"), 0, 0)
        if family == "F51":
            struct.pack_into("<H", rec, 0x28, 4)
            struct.pack_into("<QII", rec, 0x30, arr, 1, 0x45)
        elif family == "F5xNames":
            struct.pack_into("<QHHI", rec, 0x28, arr, 1, 4, 0x45)
        else:
            struct.pack_into("<QHHI", rec, 0x18, arr, 1, 4, 0x45)
        va = im.put(".rdata", rec)
        classes = {"Library": {"noreg": noreg, "funcs": ["MakeInner"], "ffns": [fn]}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "functions.exe"); im.build(p)
            sc = Scanner(Image(p))
            fs = sc.functions(layout, {owner: "Library"}, classes)
            assert list(fs) == [fn], (layout, fs)
            assert fs[fn]["va"] == va and fs[fn]["size"] == 4
            assert fs[fn]["layout"] == family
            assert fs[fn]["tree"][0]["fn"] == ctor
            assert sc.functions(layout, {owner: "OtherClass"}, classes) == {}
            assert fs[fn]["ownerCtor"] == owner
            # Same short name, different constructor pair: never borrow the selected class's
            # function identity (the old name join would mark the real function ambiguous).
            other = bytearray(rec); struct.pack_into("<Q", other, 0, other_owner)
            im.put(".rdata", other); im.build(p)
            fs = Scanner(Image(p)).functions(layout, {owner: "Library", other_owner: "Library"}, classes)
            assert list(fs) == [fn] and fs[fn]["va"] == va
            # Ambiguous identity fails closed instead of inventing an exact anchor.
            im.put(".rdata", rec); im.build(p)
            assert Scanner(Image(p)).functions(layout, {owner: "Library"}, classes) == {}

def test_make_signature_resolves_equal_size_structs_end_to_end():
    # Small stand-ins for two unrelated native types with identical layouts. Their identities
    # must survive even when a using property's name favours the wrong type.
    for layout in ("P51", "P5x"):
        im = synth(layout)
        owner, noreg, fn, ctor = (BASE + 0x1000 + k for k in (0x600, 0x640, 0x680, 0x100))
        # Rename the single-int Inner type to IntPoint in this small fixture; the evidence rule
        # does not depend on an engine type dictionary or the name of the containing game.
        data = im.buf[".rdata"]
        pos = data.index(b"Inner\0")
        # Use fresh strings and replace pointers, keeping all record positions stable.
        old = im.va(".rdata", pos); new = im.cstr("IntPoint")
        for sec in (".rdata", ".data"):
            for off in range(0, im.cur[sec] - 7, 8):
                if struct.unpack_from("<Q", im.buf[sec], off)[0] == old:
                    struct.pack_into("<Q", im.buf[sec], off, new)
        # Add the equal-layout alternative; do not alias unrelated names by shape.
        twin = bytearray(im.buf[".data"][:68 if layout == "P51" else 56])
        struct.pack_into("<Q", twin, 0x10, BASE + 0x1740)
        struct.pack_into("<Q", twin, 0x18, im.cstr("IntVector2"))
        im.put(".data", twin)
        im.put(".rdata", struct.pack("<QQQQQ", BASE + 0x1700, BASE + 0x1740,
                                      im.wstr("IntVector2"), im.va(".data", 0x1800), 0))
        pos = im.buf[".rdata"].index(b"InnerItem\0")
        im.buf[".rdata"][pos:pos + 10] = b"Vector2\0" + bytes(2)
        arr = im.put(".rdata", struct.pack("<Q", BASE + 0x1010))
        fl = im.put(".rdata", struct.pack("<QQ", fn, im.cstr("MakeIntPoint")))
        cpp = im.put(".rdata", bytes(16))
        cl = bytearray(80)
        struct.pack_into("<QQQQQQQ", cl, 0, noreg, 0, cpp, arr, fl, 0, 0)
        if layout == "P51": struct.pack_into("<IIII", cl, 0x38, 1, 1, 0, 0)
        else: struct.pack_into("<I", cl, 0x38, 1 | (1 << 4))
        im.put(".data", cl)
        im.put(".rdata", struct.pack("<QQQQQ", owner, noreg, im.wstr("ULibrary"), im.va(".data", 0x1800), 0))
        if layout == "P51":
            pr = struct.pack("<QQQBxxxIIxxxxQQIxxxxQQ", im.cstr("ReturnValue"), 0, 0, 0x19, 0x45, 1, 0, 0, 0, ctor, 0)
        else:
            pr = struct.pack("<QQQBxxxIQQHHxxxxQQ", im.cstr("ReturnValue"), 0, 0, 0x19, 0x45, 0, 0, 1, 0, ctor, 0)
        pv = im.put(".rdata", pr); pa = im.put(".rdata", struct.pack("<Q", pv))
        rec = bytearray(80)
        struct.pack_into("<QQQQQ", rec, 0, owner, 0, im.cstr("MakeIntPoint"), 0, 0)
        if layout == "P51":
            struct.pack_into("<H", rec, 0x28, 4); struct.pack_into("<QII", rec, 0x30, pa, 1, 0x45)
        else: struct.pack_into("<QHHI", rec, 0x18, pa, 1, 4, 0x45)
        im.put(".rdata", rec)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "signature.exe"); im.build(p)
            _, structs, rep = generate(p)
        assert structs["Outer"]["props"][1]["type"]["struct"] == "IntPoint"
        assert rep["naming"]["structFunctionHints"] == 1

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)
