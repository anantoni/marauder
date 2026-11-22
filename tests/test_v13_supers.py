"""v13: data-only naming of super constructors and engine reference relations (synthetic only)."""
import os, struct, sys, tempfile, unittest
from usmap_generator.engine import naming, engine_references as R
from usmap_generator.engine.genusmap import generate
from test_ue_usmap import Img, BASE


def image(child_offset=8):
    """Protected (.bind) P51 module: Base {X} (size 16, align 16), Small {Y} (size 4) and
    Child : Base {Z at child_offset}; Holder {Count, SmallItem: Small}. Child's first own member sits in Base's tail padding."""
    im = Img(); T = lambda off: BASE + 0x1000 + off
    pkg = T(0x10)
    z = {"Base": T(0x100), "Small": T(0x130), "Child": T(0x160), "Holder": T(0x190)}
    ss = {"Base": T(0x400), "Small": T(0x480), "Child": T(0x500), "Holder": T(0x580)}
    ops = {n: a + 0x40 for n, a in ss.items()}
    pd = bytearray()
    for a in z.values(): pd += struct.pack("<III", a - BASE, a - BASE + 0x2F, 0x7000)
    for n in ss: pd += struct.pack("<III", ss[n] - BASE, ss[n] - BASE + 0x37, 0x7010) + struct.pack("<III", ops[n] - BASE, ops[n] - BASE + 0x37, 0x7010)
    im.buf[".pdata"][:len(pd)] = pd
    def prop(name, gen, off, fn=0):
        return im.put(".rdata", struct.pack("<QQQBxxxIIxxxxQQIxxxxQQ", im.cstr(name), 0, 0, gen, 0x45, 1, 0, 0, off, fn, 0))
    def params(name, sup, props, size, align):
        arr = im.put(".rdata", b"".join(struct.pack("<Q", p) for p in props))
        return im.put(".data", struct.pack("<QQQQQQQIII", pkg, sup, ops[name], im.cstr(name), size, align, arr, len(props), 0x45, 1))
    params("Base", 0, [prop("X", 3, 0)], 16, 16)
    params("Small", 0, [prop("Y", 3, 0)], 4, 4)
    params("Child", z["Base"], [prop("Z", 3, child_offset)], 24, 8)
    params("Holder", 0, [prop("Count", 3, 0), prop("SmallItem", 0x19, 4, z["Small"])], 8, 4)
    for n in ss:
        im.put(".rdata", struct.pack("<QQQQQ", ss[n], ops[n], im.wstr(n), im.va(".data", 0x1800), 0))
    return im, z


def run(im, **kw):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "protected.exe"); im.build(p)
        return generate(p, **kw)


class SuperNaming(unittest.TestCase):
    def test_abstract_parent_beats_a_leaf_sibling(self):
        # Palworld/UE5 AnimGraphRuntime: every SkeletalControlBase child got AnimNode_ApplyAdditive.
        children = ["AnimNode_ModifyBone", "AnimNode_TwoBoneIK", "AnimNode_ApplyLimits", "AnimNode_CopyBone"]
        score = lambda n: naming.base_score(children, n, naming.toks(n))
        self.assertGreater(score("AnimNode_SkeletalControlBase"), score("AnimNode_ApplyAdditive"))
        # A word only some children share counts for their share, not for being present at all.
        kids = ["BoxLimitData", "CapsuleLimitData", "SphericalLimitData", "PlanarLimitData"]
        s = lambda n: naming.base_score(kids, n, naming.toks(n))
        self.assertGreater(s("CollisionLimitDataBase"), s("CapsuleLimit"))

    def test_base_in_tail_padding_is_a_feasible_super(self):
        im, z = image(child_offset=8)
        _, structs, _ = run(im)
        self.assertEqual(structs["Child"]["super"], "Base")   # old rule: Base (16) > first own offset (8) -> Small


class EngineReferences(unittest.TestCase):
    tree = {"Holder": [{"name": "Items", "kind": "Array", "fn": None,
                        "children": [{"name": "Items", "kind": "Struct", "fn": 77}]}]}

    def test_relations_and_unanimous_hints(self):
        ref = {"Holder": {"super": None, "count": 1, "props": [
            {"i": 0, "dim": 1, "name": "Items", "type": {"t": "ArrayProperty", "inner": {"t": "StructProperty", "struct": "ItemInfo"}}}]},
               "Child": {"super": "ParentBase", "count": 0, "props": []}}
        rel = R.relations(ref)
        self.assertEqual(rel[("field", "Holder", "Items", "i")], "ItemInfo")
        self.assertEqual(rel[("super", "Child")], "ParentBase")
        targets = {"ItemInfo", "ItemHandle", "ParentBase"}
        self.assertEqual(R.hints([rel, rel], self.tree, {"Child": 88}, targets), {77: "ItemInfo", 88: "ParentBase"})
        # one reference is not enough; a disagreeing reference blocks; the name must exist in the target
        self.assertEqual(R.hints([rel], self.tree, {}, targets), {})
        other = dict(rel); other[("field", "Holder", "Items", "i")] = "ItemHandle"
        self.assertEqual(R.hints([rel, rel, other], self.tree, {}, targets), {})
        self.assertEqual(R.hints([rel, rel], self.tree, {}, {"ItemHandle"}), {})
        # spelling differs only in case between games
        low = {k: v.lower() for k, v in rel.items()}
        self.assertEqual(R.hints([rel, low], self.tree, {}, targets), {77: "ItemInfo"})

    def test_load_reads_usmap_files(self):
        from usmap_generator.engine import usmap
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ref.usmap")
            usmap.write(p, {}, {"Child": {"super": "Base", "count": 0, "props": []}})
            self.assertEqual(R.load([p]), [{("super", "Child"): "Base"}])

    def test_reference_names_a_protected_super(self):
        im, z = image(child_offset=16)
        rel = {("super", "Child"): "Base"}
        _, structs, rep = run(im, engine_refs=[rel, rel])
        self.assertEqual(structs["Child"]["super"], "Base")
        self.assertEqual(rep["counts"]["referenceHints"], 1)
        _, _, rep = run(im, engine_refs=[rel, {("super", "Child"): "Small"}])
        self.assertEqual(rep["counts"]["referenceHints"], 0)



# ---- wrapped executables: plain .text beside a SteamStub .bind wrapper (owner decision 22 Nov) ----
import random
from types import SimpleNamespace
from unittest.mock import patch
import builtins
from usmap_generator.engine import code_links as CL

TEXT_VA, BIND_VA = 0x140001000, 0x140100000


def wrapped_file(d, text, functions_count):
    """Raw file: [.text bytes][.bind bytes]; a fake PE view with both sections."""
    r = random.Random(7); bind = bytes(r.getrandbits(8) for _ in range(0x4000))   # encrypted-looking wrapper
    p = os.path.join(d, "wrapped.bin")
    with open(p, "wb") as f: f.write(text + bind)
    secs = {".text": (TEXT_VA - 0x140000000, len(text), 0, len(text)),
            ".bind": (BIND_VA - 0x140000000, len(bind), len(text), len(bind))}
    pe = SimpleNamespace(secs=secs, path=p, base=0x140000000,
                         sec=lambda n: (0x140000000 + secs[n][0], 0x140000000 + secs[n][0] + secs[n][1], secs[n][2]))
    return SimpleNamespace(pe=pe), len(text)


def plain_code(n=400, seed=1):
    """n small valid x86-64 functions (sub rsp; mov ecx/edx imm32; lea r8,[rip]; call; add rsp; ret)."""
    r = random.Random(seed); size = 32; code = bytearray(); funcs = {}
    for i in range(n):
        a = TEXT_VA + i * size; f = bytearray(b"\x48\x83\xec\x28")
        f += b"\xb9" + struct.pack("<I", r.getrandbits(32)) + b"\xba" + struct.pack("<I", r.getrandbits(32))
        f += b"\x4c\x8d\x05" + struct.pack("<i", r.randrange(-0x1000, 0x1000))
        target = TEXT_VA + r.randrange(n) * size
        f += b"\xe8" + struct.pack("<i", target - (a + len(f) + 5)) + b"\x48\x83\xc4\x28\xc3"
        funcs[a] = (len(f), 0); code += f + b"\xcc" * (size - len(f))
    return bytes(code), funcs


def tracked_reads(path):
    reads = []; real_open = builtins.open
    class Tracked:
        def __init__(self, *a, **k): self.file = real_open(*a, **k)
        def __enter__(self): return self
        def __exit__(self, *a): self.file.close()
        def seek(self, *a): return self.file.seek(*a)
        def read(self, n=-1):
            start = self.file.tell(); data = self.file.read(n); reads.append((start, start + len(data))); return data
    return reads, patch("builtins.open", Tracked)


class WrappedCode(unittest.TestCase):
    def test_plain_text_beside_wrapper_is_read_and_wrapper_never(self):
        text, funcs = plain_code()
        with tempfile.TemporaryDirectory() as d:
            image, tlen = wrapped_file(d, text, len(funcs))
            reads, p = tracked_reads(image.pe.path)
            with p:
                links = CL.CodeLinks(image, funcs)
            self.assertEqual(links.blocked, []); self.assertEqual(links.wrapper, [".bind"])
            self.assertTrue(reads and all(end <= tlen for _, end in reads))      # .bind (at tlen..) never read
            summary = links.summary()
            self.assertEqual(summary["code_sections_read"], [".text"])
            self.assertEqual(summary["plain_code_check"]["wrapper_sections_unread"], [".bind"])
            self.assertIn("wrapper unread", summary["status"])
            self.assertEqual(CL.CodeLinks(image, funcs, plain_wrapped=False).blocked, [".bind"])

    def test_encrypted_text_keeps_code_links_off(self):
        _, funcs = plain_code()
        r = random.Random(3); text = bytes(r.getrandbits(8) for _ in range(len(plain_code()[0])))
        with tempfile.TemporaryDirectory() as d:
            image, tlen = wrapped_file(d, text, len(funcs))
            reads, p = tracked_reads(image.pe.path)
            with p:
                links = CL.CodeLinks(image, funcs)
            self.assertEqual(links.blocked, [".bind"]); self.assertEqual(links.wrapper, [])
            self.assertEqual(links.plain_check["reason"], "entropy above plain-code range")
            self.assertTrue(all(end <= tlen for _, end in reads))                 # wrapper still unread
            with self.assertRaises(ValueError): links.read(TEXT_VA, 1)
            self.assertNotIn("code_bytes_read", links.summary())

    def test_too_few_or_undecodable_functions_keep_code_links_off(self):
        text, funcs = plain_code()
        with tempfile.TemporaryDirectory() as d:
            image, _ = wrapped_file(d, text, len(funcs))
            few = dict(list(funcs.items())[:50])
            self.assertEqual(CL.plain_code_check(image, few)[1]["reason"], "too few functions to verify")
            bad = {a + 3: (n, u) for a, (n, u) in funcs.items()}                  # starts mid-instruction
            ok, ev = CL.plain_code_check(image, bad)
            self.assertFalse(ok)

    def test_constructor_patterns_must_resolve_or_code_links_stay_off(self):
        # A wrapped image whose .text passes the plain check but has no constructor code:
        # the generator restarts in data-only mode and reports why.
        im, z = image(child_offset=8)
        with patch.object(CL, "plain_code_check", lambda image, functions: (True, {"sections_read": [".text"], "reason": None})):
            _, structs, rep = run(im, code_links=True)
        self.assertEqual(rep["staticCodeLinks"]["status"], "protected; code unread")
        self.assertEqual(rep["staticCodeLinks"]["plain_code_rejected"]["constructor_coverage"], 0.0)
        self.assertEqual(structs["Child"]["super"], "Base")


if __name__ == "__main__":
    unittest.main()
