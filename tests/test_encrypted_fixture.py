"""User-supplied AES key path, tested on synthetic archives that the test encrypts with its own key."""
import json, os, tempfile, unittest, zlib

from synthetic import make_pak, make_utoc, make_key
from usmap_generator.engine import archives, config_order, iostore_probe as IO, key_provider as KP

INI = b"[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=/Game/Maps/Start.Start\n"
BIG = bytes(range(256)) * 700          # > 64 KiB: several compression blocks


class EncryptedPak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = self.tmp.name
        self.key = make_key()
        self.files = {"MyGame/Config/DefaultEngine.ini": INI, "MyGame/Content/Data.bin": BIG, "MyGame/Content/Plain.txt": b"plain"}

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, version):
        return make_pak(os.path.join(self.d, f"v{version}.pak"), self.files, version=version, key=self.key,
                        encrypt_index=True, encrypt_entries=set(self.files) - {"MyGame/Content/Plain.txt"},
                        compress={"MyGame/Content/Data.bin"})

    def test_reads_with_own_key(self):
        for version in (8, 11):
            with self.subTest(version=version):
                p = self.build(version)
                with archives.Pak(p, KP.StaticKeys(default=self.key)) as pak:
                    self.assertEqual(pak.excluded, [])
                    self.assertEqual(sorted(pak.entries), sorted(self.files))
                    for name, data in self.files.items():
                        self.assertEqual(pak.read(name), data, name)

    def test_without_key_everything_is_excluded_and_listed(self):
        p = self.build(11)
        with archives.Pak(p) as pak:
            self.assertEqual(pak.entries, {})
            self.assertEqual([x.reason for x in pak.excluded], [KP.NO_PROVIDER])

    def test_wrong_key_is_reported_not_silent(self):
        p = self.build(11)
        with archives.Pak(p, KP.StaticKeys(default=make_key())) as pak:
            self.assertEqual([x.reason for x in pak.excluded], [KP.WRONG_KEY])
            self.assertEqual(pak.entries, {})

    def test_encrypted_entries_with_plain_index(self):
        p = make_pak(os.path.join(self.d, "e.pak"), self.files, version=8, key=self.key,
                     encrypt_entries={"MyGame/Config/DefaultEngine.ini"})
        with archives.Pak(p) as pak: self.assertFalse(pak.readable("MyGame/Config/DefaultEngine.ini"))
        with archives.Pak(p) as pak, self.assertRaises(PermissionError): pak.read("MyGame/Config/DefaultEngine.ini")
        problems = []
        self.assertEqual(config_order.read_files([p], None, problems), {})
        self.assertEqual(problems[0][1], "MyGame/Config/DefaultEngine.ini")   # reported, not skipped silently
        got = config_order.read_files([p], KP.StaticKeys(default=self.key))
        self.assertEqual(got, {"MyGame/Config/DefaultEngine.ini": INI})

    def test_key_guid_selects_the_key(self):
        guid = bytes(range(1, 17))
        p = make_pak(os.path.join(self.d, "g.pak"), self.files, version=11, key=self.key, encrypt_index=True, key_guid=guid)
        keys = KP.StaticKeys(default=make_key(), by_guid={guid.hex(): self.key})
        with archives.Pak(p, keys) as pak: self.assertEqual(pak.read("MyGame/Content/Plain.txt"), b"plain")


class EncryptedIoStore(unittest.TestCase):
    def test_container_reads_with_own_key_and_is_excluded_without(self):
        key = make_key(); chunk = bytes(range(200)) * 3
        with tempfile.TemporaryDirectory() as d:
            p = make_utoc(d, chunk, key=key)
            t = IO.Toc(p, KP.StaticKeys(default=key))
            self.assertTrue(t.readable); self.assertEqual(t.excluded, [])
            self.assertEqual(t.read_range(0, 0, len(chunk)), chunk)
            t = IO.Toc(p)
            self.assertFalse(t.readable); self.assertEqual(t.excluded[0].reason, KP.NO_PROVIDER)
            with self.assertRaises(PermissionError): t.read_range(0, 0, 16)
            t = IO.Toc(p, KP.StaticKeys(default=make_key()))
            self.assertFalse(t.readable); self.assertEqual(t.excluded[0].reason, KP.WRONG_KEY)


class KeyParsing(unittest.TestCase):
    def test_hex_and_base64(self):
        import base64
        k = make_key()
        self.assertEqual(KP.parse_key("0x" + k.hex().upper()), k)
        self.assertEqual(KP.parse_key(k.hex()), k)
        self.assertEqual(KP.parse_key(base64.b64encode(k).decode()), k)
        for bad in ("0x1234", "", "zz" * 32):
            with self.assertRaises(KP.KeyError_): KP.parse_key(bad)

    def test_keys_file_json_and_text(self):
        a, b = make_key(), make_key(); guid = "0123456789abcdef0123456789ABCDEF"
        with tempfile.TemporaryDirectory() as d:
            j = os.path.join(d, "keys.json")
            with open(j, "w") as f: json.dump({"default": "0x" + a.hex(), guid: b.hex()}, f)
            ks = KP.load_keys_file(j)
            self.assertEqual((ks.default, ks.by_guid[guid.lower()]), (a, b))
            t = os.path.join(d, "keys.txt")
            with open(t, "w") as f: f.write(f"# my keys\n0x{a.hex()}\n{guid} 0x{b.hex()}\n")
            ks = KP.load_keys_file(t)
            self.assertEqual((ks.key_for("x", ""), ks.key_for("x", guid)), (a, b))
            self.assertNotIn(a.hex(), repr(ks))      # keys never printed

    def test_no_key_finding_in_cli_help(self):
        from usmap_generator.cli import parser
        text = parser().format_help().lower()
        self.assertNotIn("find key", text)


if __name__ == "__main__":
    unittest.main()
