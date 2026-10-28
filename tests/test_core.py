import os
import subprocess
import sys
import unittest

from tests.helpers import REPO, Sandbox, FIXTURES

from arkre import analysis, assets, cache, config


def run_cli(*args, env=None):
    e = dict(os.environ, **(env or {}))
    p = subprocess.run([sys.executable, os.path.join(REPO, "parse_uasset.py"), *args],
                       capture_output=True, text=True, env=e)
    return p.returncode, p.stdout


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_cli_cache_is_byte_identical(self):
        for name in FIXTURES:
            path = os.path.join(REPO, name + ".uasset")
            _, plain = run_cli("--no-cache", path)
            _, first = run_cli(path)
            _, second = run_cli(path)
            self.assertEqual(plain, first, name)
            self.assertEqual(plain, second, name)

    def test_parser_ver_change_hides_old_cache(self):
        path = os.path.join(REPO, "Buff_AC.uasset")
        v1 = analysis.view_for_file(path)
        self.assertIsNotNone(cache.read_view(v1["sha256"]))
        real = cache.parser_ver
        cache.parser_ver = lambda: "ffffffffffff"
        try:
            self.assertIsNone(cache.read_view(v1["sha256"]))
        finally:
            cache.parser_ver = real
        self.assertIsNotNone(cache.read_view(v1["sha256"]))

    def test_gc_removes_other_versions_only(self):
        old = cache.version_dir("000000000000")
        os.makedirs(old)
        cache.write_view("a" * 64, {"x": 1})
        removed = cache.gc()
        self.assertTrue(any("v000000000000" in r for r in removed))
        self.assertFalse(os.path.isdir(old))
        self.assertIsNotNone(cache.read_view("a" * 64))


class ViewTests(unittest.TestCase):
    def test_view_has_functions_and_xrefs(self):
        with open(os.path.join(REPO, "Actor_BP_AC.uasset"), "rb") as fh:
            v = analysis.build_view(fh.read(), "Actor_BP_AC")
        self.assertEqual(v["class"]["name"], "Actor_BP_AC_C")
        self.assertIn("ReceiveBeginPlay", v["events"])
        calls = {x["member"] for f in v["functions"].values() for x in f["xrefs"] if x["kind"] == "call"}
        self.assertIn("PrintString", calls)
        self.assertEqual(v["problems"], [])


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_three_forms(self):
        by_id = assets.resolve(f"{self.sb.workshop_id}:Buff_AC")
        self.assertEqual(by_id.game_path, "/Game/Mods/FixMod/Sub/Buff_AC")
        by_game = assets.resolve("/Game/Mods/FixMod/Sub/Buff_AC.Buff_AC_C")
        self.assertEqual(by_game.file, by_id.file)
        by_abs = assets.resolve(by_id.file)
        self.assertEqual(by_abs.workshop_id, self.sb.workshop_id)

    def test_ambiguous_game_path_lists_candidates(self):
        self.sb.add_mod("999002", "FixMod")
        with self.assertRaises(assets.AssetError) as cm:
            assets.resolve("/Game/Mods/FixMod/Sub/Buff_AC")
        self.assertIn("999001", str(cm.exception))
        self.assertIn("999002", str(cm.exception))

    def test_not_found_gives_candidates(self):
        with self.assertRaises(assets.AssetError) as cm:
            assets.resolve(f"{self.sb.workshop_id}:Buff_AD")
        self.assertIn("не найден", str(cm.exception))

    def test_find_glob(self):
        hits = assets.find("*buff*", scope="mods")
        self.assertEqual([h.rel for h in hits], ["Sub/Buff_AC.uasset"])


if __name__ == "__main__":
    unittest.main()
