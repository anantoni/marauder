"""Synthetic tests for the v9 rules; no game data."""
import os, sys, unittest
from usmap_generator.engine import config_order


class ConfigOrder(unittest.TestCase):
    def test_startup_order_and_ignored_files(self):
        files = {
            "Game/Config/DefaultGame.ini": b"[S]\nsightRadius=1\n",
            "Engine/Config/BaseGame.ini": b"[S]\nSightRadius=1\n",
            "Engine/Config/BaseEngine.ini": b"[S]\n+ClientId=1\n",
            "Game/Config/DefaultEngine.ini": b"[S]\nClientID=2\n",
            "Engine/Config/Localization/Keywords.ini": b"[S]\nBone=1\n",
            "Engine/Plugins/X/Config/BaseX.ini": b"[S]\nVelocity=1\n",
        }
        self.assertEqual(config_order.startup_files(files)[:3],
                         ["Engine/Config/BaseEngine.ini", "Game/Config/DefaultEngine.ini", "Engine/Config/BaseGame.ini"])
        first = config_order.first_spellings(files)
        self.assertEqual(first["sightradius"], "SightRadius")
        self.assertEqual(first["clientid"], "ClientId")
        self.assertNotIn("bone", first)
        self.assertNotIn("velocity", first)

    def test_respell_keeps_namespaced_entries_and_suffix(self):
        enums = {"E": [(0, "sightRadius"), (1, "E_MAX")], "F": [(0, "sightRadius")]}
        structs = {"S": {"super": None, "count": 1, "props": [
            {"i": 0, "dim": 1, "name": "sightRadius_2", "type": {"t": "FloatProperty"}}]}}
        e, s, renamed = config_order.respell(enums, structs, {"sightradius": "SightRadius"},
                                             lambda n, x: n == "F")
        self.assertEqual(s["S"]["props"][0]["name"], "SightRadius_2")
        self.assertEqual(e["E"][0][1], "SightRadius")
        self.assertEqual(e["F"][0][1], "sightRadius")
        self.assertEqual(len(renamed), 2)



class ReferenceSpellings(unittest.TestCase):
    def mapping(self, name, extra=()):
        return ({"E": [(0, "A")]}, {"S": {"super": None, "count": 1, "props": [
            {"i": 0, "dim": 1, "name": name, "type": {"t": "IntProperty"}}]}, **{x: {"super": None, "count": 0, "props": []} for x in extra}})

    def test_unanimous_similar_references_respell(self):
        from usmap_generator.engine import reference_spellings as refs
        q = lambda n, e: False
        pool = [refs.reference(self.mapping("Bone", ("T",)), self.mapping("bone", ("T",)), q) for _ in range(2)]
        target = self.mapping("Bone", ("T",))
        choice = refs.choose(*target, pool, {"bone", "Bone"}, q)
        self.assertEqual(choice, {"bone": "bone"})
        self.assertEqual(refs.respell(*target, choice, q)[1]["S"]["props"][0]["name"], "bone")
        # the new spelling must occur in the target executable
        self.assertEqual(refs.choose(*target, pool, {"Bone"}, q), {})
        # one reference that kept the generated spelling blocks the change
        kept = refs.reference(self.mapping("Bone", ("T",)), self.mapping("Bone", ("T",)), q)
        self.assertEqual(refs.choose(*target, pool + [kept], {"bone"}, q, n=3), {})
        # a single vote is not enough
        self.assertEqual(refs.choose(*target, pool[:1], {"bone"}, q), {})


class IntrinsicClasses(unittest.TestCase):
    def test_markers(self):
        from usmap_generator.engine import intrinsic_classes as ic
        s = {"Object": {}}
        self.assertEqual(ic.recover(b"\0Z_Registration_Info_UClass_UGCBarrier.OuterSingleton\0", s), ["GCBarrier"])
        s = {}
        added = ic.recover(b"!(X->IsA<ULinkerPlaceholderFunction>())\0Cast<UDynamicClass>\0", s)
        self.assertIn("DynamicClass", added)
        self.assertNotIn("GCBarrier", added)
        self.assertEqual(s["Property"]["super"], "Field")
        self.assertEqual(ic.recover(b"shipping", {}), [])


if __name__ == "__main__":
    unittest.main()
