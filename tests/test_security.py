"""Регрессия на реальном S+ (ID 731604991); без установленного мода — пропуск."""
import os
import unittest

from arkre import assets, config, security, xref

S_PLUS = "731604991"


def installed() -> bool:
    return os.path.isdir(os.path.join(config.mods_dir(), S_PLUS))


@unittest.skipUnless(installed(), "S+ не установлен")
class SPlusRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rpcs = security.net_rpcs(S_PLUS)
        cls.ini = {r["key"]: r for r in security.ini_keys(S_PLUS)["keys"]}

    def test_propagator_start_command_found(self):
        rows = [a for a in self.rpcs["assets"] if a["asset"].endswith("Misc/Propagator/BP_Propagator.uasset")]
        self.assertEqual(len(rows), 1)
        ep = {e["function"]: e["names"] for e in rows[0]["entry_points"]}
        self.assertIn("PropagatorStart", ep["BPServerHandleNetExecCommand"])

    def test_pulsemax_never_read_on_server_path(self):
        ma = security.ModAssets(S_PLUS)
        graph = security.CallGraph(ma)
        server = graph.reach(graph.server_roots, skip_widgets=True)
        reads = xref.mod_search(S_PLUS, "PulseMax", kinds=["read"], limit=1000)
        self.assertGreater(reads["total"], 0)
        for item in reads["items"]:
            rel = item["asset"].split(":", 1)[1]
            owner = (item.get("via") or [item["function"]])[0]
            self.assertNotIn((rel, owner), server, item)

    def test_disable_dino_mods_has_no_server_consumer(self):
        row = self.ini["PropagatorDisableDinoMods"]
        self.assertEqual(row["section"], "StructuresPlus")
        self.assertFalse(row["server_consumed"])
        self.assertTrue(row["consumers"])

    def test_respect_mutation_limit_is_consumed_on_server(self):
        self.assertTrue(self.ini["PropagatorRespectMutationLimit"]["server_consumed"])


if __name__ == "__main__":
    unittest.main()
