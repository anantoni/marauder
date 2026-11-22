"""Regressions found on the 121-game release run: spelling votes when only the major engine version
is known, and the code-section fields of the report."""
import json, os, pickle, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from usmap_generator import pipeline as P
import test_v13_supers as V13

ROOT = Path(__file__).resolve().parents[1]


def struct(*names):
    return {"props": [{"name": n, "type": "Int"} for n in names], "super": None, "count": len(names)}


class Exe(set):
    pass


class VersionTable(unittest.TestCase):
    TABLES = {"5.3": {"float": {"Float": {"float": 3}}},
              "5": {"float": {"Float": {"float": 5}}, "max": {"Max": {"Max": 9, "max": 7}}}}

    def test_minor_table_when_minor_version_known(self):
        self.assertEqual(P.table_for_version(self.TABLES, "5.3")[0], "5.3")

    def test_layout_estimate_uses_pooled_major_table(self):
        # "5.x" (layout estimate, no build string) used to skip the step entirely
        key, table = P.table_for_version(self.TABLES, "5.x")
        self.assertEqual(key, "5"); self.assertIs(table, self.TABLES["5"])

    def test_minor_version_without_table_uses_pooled_major_table(self):
        self.assertEqual(P.table_for_version(self.TABLES, "5.9")[0], "5")

    def test_unknown_or_other_major_has_no_table(self):
        self.assertEqual(P.table_for_version(self.TABLES, None), (None, None))
        self.assertEqual(P.table_for_version(self.TABLES, "4.x"), (None, None))

    def test_pooled_table_blocks_names_kept_by_any_game(self):
        structs = {"A": struct("Float", "Max")}
        exe = Exe({"float", "max"})
        choice = P.choose_spellings({}, structs, exe, lambda n, e: False, self.TABLES["5"])
        self.assertEqual(choice, {"float": "float"})       # Max: some games kept it, so no vote

    def test_shipped_table_has_consistent_major_tables(self):
        tables = P.spelling_table()
        for major in ("4", "5"):
            self.assertIn(major, tables)
            minors = [k for k in tables if k.startswith(major + ".")]
            for stem, row in tables[major].items():
                for gen, votes in row.items():
                    # every vote in the pooled table comes from the minor tables or from rows the
                    # minor filter dropped (kept-only there); never more changed votes than the minors hold
                    for sp, n in votes.items():
                        if sp == gen: continue
                        self.assertEqual(n, sum(tables[m].get(stem, {}).get(gen, {}).get(sp, 0) for m in minors))

    def test_builder_writes_pooled_major_tables(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / "refs").mkdir()
            rows = {"a": {"float": ("Float", "float"), "max": ("Max", "max")},
                    "b": {"float": ("Float", "float"), "max": ("Max", "Max")}}
            for g, r in rows.items(): (d / "refs" / f"{g}.pkl").write_bytes(pickle.dumps({"rows": r}))
            (d / "v.json").write_text(json.dumps({"a": "5.5", "b": "5.4"}))
            subprocess.run([sys.executable, str(ROOT / "tools/build_spelling_table.py"), str(d / "refs"), str(d / "v.json"),
                            str(d / "t.json")], check=True, capture_output=True)
            t = json.loads((d / "t.json").read_text())
            self.assertEqual(t["5.5"]["max"], {"Max": {"max": 1}})
            self.assertEqual(t["5"]["max"], {"Max": {"max": 1, "Max": 1}})
            self.assertNotIn("max", t["5.4"])                # kept-only in 5.4
            self.assertEqual(t["5"]["float"], {"Float": {"float": 2}})


class VersionFromTypeNames(unittest.TestCase):
    GAMES = [("5.1", {"Core", "Old"}), ("5.1", {"Core", "Old", "GameA"}),
             ("5.2", {"Core", "Old", "New2"}), ("5.3", {"Core", "New2", "New3"}), ("5.3", {"Core", "New2", "New3", "GameB"})]

    def test_markers_are_engine_names_only(self):
        from usmap_generator.engine import version_markers as VM
        m = VM.build(self.GAMES)
        self.assertEqual(m["5.1"], {"introduced": [], "removed": []})
        self.assertEqual(m["5.2"]["introduced"], ["New2"])
        self.assertEqual(m["5.3"], {"introduced": ["New3"], "removed": ["Old"]})
        self.assertFalse(any(n.startswith("Game") for v in m.values() for k in v.values() for n in k))

    def test_estimate(self):
        from usmap_generator.engine import version_markers as VM
        m = VM.build(self.GAMES)
        self.assertEqual(VM.estimate({"Core", "New2", "New3"}, 5, m), "5.3")
        self.assertEqual(VM.estimate({"Core", "Old", "New2"}, 5, m), "5.2")
        self.assertEqual(VM.estimate({"Core", "Old"}, 5, m), "5.1")      # older than every marker
        self.assertIsNone(VM.estimate({"Core"}, 4, m))
        self.assertIsNone(VM.estimate({"Core"}, None, m))

    def test_shipped_markers_load(self):
        from usmap_generator.engine import version_markers as VM
        m = VM.load()
        self.assertIn("5.4", m)
        self.assertFalse(any(VM.late(n) for v in m.values() for k in v.values() for n in k))


class CodeSectionFields(unittest.TestCase):
    def test_report_fields_present_without_code_links(self):
        cs = P.code_sections({})
        self.assertEqual(cs["code_sections_read"], []); self.assertEqual(cs["wrapper_sections_unread"], [])
        self.assertIsNone(cs["plain_code_rejected"])

    def test_wrapped_plain_text_fields(self):
        text, funcs = V13.plain_code()
        with tempfile.TemporaryDirectory() as d:
            image, _ = V13.wrapped_file(d, text, len(funcs))
            s = V13.CL.CodeLinks(image, funcs).summary()
            self.assertEqual(s["code_sections_read"], [".text"]); self.assertEqual(s["wrapper_sections_unread"], [".bind"])
            self.assertIsNone(s["plain_code_rejected"])
            s = V13.CL.CodeLinks(image, funcs, plain_wrapped=False).summary()
            self.assertEqual(s["code_sections_read"], []); self.assertEqual(s["wrapper_sections_unread"], [".bind"])

    def test_encrypted_text_fields(self):
        import random
        _, funcs = V13.plain_code()
        r = random.Random(3); text = bytes(r.getrandbits(8) for _ in range(len(V13.plain_code()[0])))
        with tempfile.TemporaryDirectory() as d:
            image, _ = V13.wrapped_file(d, text, len(funcs))
            s = V13.CL.CodeLinks(image, funcs).summary()
            self.assertEqual(s["code_sections_read"], []); self.assertEqual(s["wrapper_sections_unread"], [".bind"])
            self.assertEqual(s["plain_code_rejected"], {"reason": "entropy above plain-code range"})

    def test_rejected_constructor_patterns_report_no_code_read(self):
        im, z = V13.image(child_offset=8)
        with patch.object(V13.CL, "plain_code_check", lambda image, functions: (True, {"sections_read": [".text"], "reason": None})):
            _, _, rep = V13.run(im, code_links=True)
        links = rep["staticCodeLinks"]
        self.assertEqual(links["code_sections_read"], [])
        self.assertEqual(links["plain_code_rejected"]["reason"], "wrapped constructor patterns did not resolve")
        self.assertEqual(P.code_sections(rep)["plain_code_rejected"]["constructor_coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
