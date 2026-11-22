"""End-to-end CLI runs on a synthetic install folder (synthetic executable + synthetic archives)."""
import contextlib, io, json, os, tempfile, unittest

from synthetic import make_key, make_pak
from test_ue_usmap import synth
from usmap_generator.cli import main
from usmap_generator.engine import usmap as U
from usmap_generator.pipeline import ANIM_STRUCT_CLASS, ensure_anim_struct_class

INI = b"[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=/Game/Maps/Start.Start\n"


def install(root, version="5.3", key=None):
    """<root>/MyGame/{Binaries/Win64/MyGame-Win64-Shipping.exe, Content/Paks/pakchunk0-Windows.pak}."""
    exe_dir = os.path.join(root, "MyGame", "Binaries", "Win64"); os.makedirs(exe_dir)
    paks = os.path.join(root, "MyGame", "Content", "Paks"); os.makedirs(paks)
    os.makedirs(os.path.join(root, "Engine"))
    im = synth("P5x")
    if version:
        im.put(".rdata", f"++UE{version[0]}+Release-{version}".encode() + b"\0", 1)
    exe = os.path.join(exe_dir, "MyGame-Win64-Shipping.exe"); im.build(exe)
    make_pak(os.path.join(paks, "pakchunk0-Windows.pak"), {"MyGame/Config/DefaultEngine.ini": INI}, version=11,
             key=key, encrypt_index=bool(key), encrypt_entries={"MyGame/Config/DefaultEngine.ini"} if key else ())
    return exe


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class Generate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_success_prints_mischief_managed(self):
        install(os.path.join(self.d, "game"))
        out = os.path.join(self.d, "out", "MyGame.usmap"); rep = os.path.join(self.d, "report.json")
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "-o", out, "--report", rep)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip().splitlines()[-1], "Mischief managed.")
        self.assertIn("[1/6]", stdout); self.assertIn("[6/6]", stdout)
        enums, structs = U.read(out)
        self.assertIn("Outer", structs); self.assertIn("Inner", structs)
        self.assertIn(ANIM_STRUCT_CLASS, structs)
        self.assertEqual(json.load(open(rep))["engineVersion"], "5.3")

    def test_global_usmap_always_has_anim_struct_class_on_ue5(self):
        for version in ("5.0", "5.3", "5.7", None):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as d:
                install(os.path.join(d, "game"), version=version)
                out = os.path.join(d, "m.usmap")
                args = ["generate", os.path.join(d, "game"), "-o", out, "-q"]
                if version is None: args += ["--engine-version", "5.4"]
                code, _, stderr = run(*args)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(U.read(out)[1][ANIM_STRUCT_CLASS], {"super": "ScriptStruct", "count": 0, "props": []})

    def test_ensure_keeps_an_existing_record(self):
        rec = {"super": "ScriptStruct", "count": 0, "props": []}
        structs = {ANIM_STRUCT_CLASS: rec}
        self.assertFalse(ensure_anim_struct_class(structs, 5)); self.assertIs(structs[ANIM_STRUCT_CLASS], rec)
        structs = {}
        self.assertTrue(ensure_anim_struct_class(structs, 5)); self.assertIn(ANIM_STRUCT_CLASS, structs)
        structs = {}
        self.assertFalse(ensure_anim_struct_class(structs, 4)); self.assertEqual(structs, {})

    def test_encrypted_install_with_own_key(self):
        key = make_key()
        install(os.path.join(self.d, "game"), key=key)
        out = os.path.join(self.d, "m.usmap")
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "-o", out, "--aes-key", "0x" + key.hex())
        self.assertEqual(code, 0, stderr)
        self.assertIn("1 opened with your key", stdout)
        self.assertNotIn(key.hex(), stdout + stderr)            # the key is never printed
        self.assertTrue(stdout.rstrip().endswith("Mischief managed."))

    def test_encrypted_install_without_key_lists_what_was_not_read(self):
        install(os.path.join(self.d, "game"), key=make_key())
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "-o", os.path.join(self.d, "m.usmap"))
        self.assertEqual(code, 0, stderr)
        self.assertIn("pakchunk0-Windows.pak", stderr); self.assertIn("no AES key supplied", stderr)
        code, _, stderr = run("generate", os.path.join(self.d, "game"), "-o", os.path.join(self.d, "m.usmap"), "--strict")
        self.assertEqual(code, 4)

    def test_wrong_key_fails_with_clear_message(self):
        install(os.path.join(self.d, "game"), key=make_key())
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "--aes-key", make_key().hex(),
                                   "-o", os.path.join(self.d, "m.usmap"))
        self.assertEqual(code, 1)
        self.assertIn("does not open", stderr); self.assertNotIn("Traceback", stderr)
        self.assertNotIn("Mischief managed.", stdout)

    def test_bad_key_text(self):
        install(os.path.join(self.d, "game"))
        code, _, stderr = run("generate", os.path.join(self.d, "game"), "--aes-key", "0x1234")
        self.assertEqual(code, 1); self.assertIn("64 hex digits", stderr)

    def test_missing_folder_and_no_executable(self):
        code, _, stderr = run("generate", os.path.join(self.d, "nope"))
        self.assertEqual(code, 1); self.assertIn("folder not found", stderr)
        os.makedirs(os.path.join(self.d, "empty"))
        code, _, stderr = run("generate", os.path.join(self.d, "empty"))
        self.assertEqual(code, 1); self.assertIn("no Unreal Engine shipping executable", stderr)

    def test_not_an_unreal_executable(self):
        exe_dir = os.path.join(self.d, "game", "X", "Binaries", "Win64"); os.makedirs(exe_dir)
        with open(os.path.join(exe_dir, "X-Win64-Shipping.exe"), "wb") as f: f.write(b"MZ" + b"\0" * 4000)
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "-o", os.path.join(self.d, "m.usmap"))
        self.assertEqual(code, 1); self.assertIn("error:", stderr); self.assertNotIn("Mischief", stdout)

    def test_per_asset_on_ue4_says_nothing_to_write(self):
        install(os.path.join(self.d, "game"), version="4.27")
        code, stdout, stderr = run("generate", os.path.join(self.d, "game"), "-o", os.path.join(self.d, "m.usmap"), "--per-asset")
        self.assertEqual(code, 0, stderr)
        self.assertIn("not UE5", stderr)
        self.assertNotIn(ANIM_STRUCT_CLASS, U.read(os.path.join(self.d, "m.usmap"))[1])

    def test_compare_and_inspect(self):
        install(os.path.join(self.d, "game"))
        out = os.path.join(self.d, "m.usmap")
        self.assertEqual(run("generate", os.path.join(self.d, "game"), "-o", out, "-q")[0], 0)
        code, stdout, _ = run("compare", out, out, "--json")
        self.assertEqual(code, 0); res = json.loads(stdout)
        self.assertTrue(res["identical"]); self.assertEqual(res["fields"][0], res["fields"][1])
        code, stdout, _ = run("inspect", out, "Outer")
        self.assertEqual(code, 0); self.assertIn("InnerItem Struct<Inner>", stdout)
        self.assertEqual(run("inspect", out, "NoSuchType")[0], 1)


if __name__ == "__main__":
    unittest.main()
