import os
import shutil
import unittest

from tests.helpers import REPO, Sandbox

from arkre import analysis, assets, mods, reports, xref, asset_tools, respond

REPORT = """---
title: "Тестовый отчёт"
status: done
workshop_ids: [999001]
custom: keep me
---

# Тело

Текст, который не трогаем.
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_report(sb, name="Test_999001.md", text=REPORT):
    p = os.path.join(sb.reports, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


class FrontmatterTests(unittest.TestCase):
    def test_all_repo_reports_parse_and_noop_rewrite_is_identical(self):
        found = reports.list_reports(os.path.join(REPO, "docs", "mods"))
        self.assertGreater(len(found), 40)
        for r in found:
            self.assertIn(r.status, reports.STATUSES, r.name)
            with open(r.path, encoding="utf-8") as fh:
                text = fh.read()
            same = {k: r.meta[k] for k in ("mod_hash", "covered_assets") if k in r.meta}
            if same:
                self.assertEqual(reports.set_keys(text, same), text, r.name)

    def test_digit_only_hash_stays_string(self):
        text = "---\nmod_hash:\n  1: 123456789012\nworkshop_ids: [1, 2]\n---\n"
        meta = reports.parse(text)
        self.assertEqual(meta["mod_hash"]["1"], "123456789012")
        self.assertEqual(meta["workshop_ids"], [1, 2])


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.mod_dir = os.path.join(self.sb.mods, "999001")

    def status(self):
        return mods.mod_status("999001")

    def test_missing_without_document(self):
        self.assertEqual(self.status()["status"], "missing")

    def test_template_does_not_count(self):
        write_report(self.sb, text=REPORT.replace("status: done", "status: template"))
        self.assertEqual(self.status()["status"], "missing")

    def test_unreviewed_then_fresh_then_stale_and_drifted(self):
        path = write_report(self.sb)
        self.assertEqual(self.status()["status"], "unreviewed")

        # mark_reviewed: только покрытые ассеты, тело и чужие ключи не меняются
        before = read(path)
        res = mods.mark_reviewed("999001", "Test_999001", ["Sub/Buff_AC.uasset"])
        self.assertEqual(res["covered"], 1)
        after = read(path)
        self.assertTrue(after.endswith("# Тело\n\nТекст, который не трогаем.\n"))
        self.assertIn("custom: keep me", after)
        self.assertEqual(reports.parse(after)["title"], "Тестовый отчёт")
        self.assertEqual(self.status()["status"], "fresh")
        self.assertNotEqual(before, after)
        # повторная фиксация без изменений файл не меняет, кроме даты в тот же день
        mods.mark_reviewed("999001", "Test_999001", ["Sub/Buff_AC.uasset"])
        self.assertEqual(read(path), after)

        # мод обновили, но покрытый ассет не задет → drifted, а не stale
        shutil.copy(os.path.join(REPO, "Actor_BP_AC.uasset"), os.path.join(self.mod_dir, "Sub", "New.uasset"))
        st = self.status()
        self.assertEqual((st["status"], st["drifted"]), ("fresh", True))

        # покрытый ассет изменился → stale
        with open(os.path.join(self.mod_dir, "Sub", "Buff_AC.uasset"), "ab") as fh:
            fh.write(b"\0")
        st = self.status()
        self.assertEqual(st["status"], "stale")
        self.assertEqual(st["changed_assets"], ["Sub/Buff_AC.uasset"])

        # покрытый ассет исчез → тоже stale
        os.remove(os.path.join(self.mod_dir, "Sub", "Buff_AC.uasset"))
        self.assertEqual(self.status()["status"], "stale")

    def test_mark_reviewed_rejects_foreign_id_and_unknown_asset(self):
        write_report(self.sb)
        with self.assertRaises(mods.ModError):
            mods.mark_reviewed("999002", "Test_999001")
        with self.assertRaises(mods.ModError):
            mods.mark_reviewed("999001", "Test_999001", ["Sub/Нет.uasset"])

    def test_mark_reviewed_caches_asset_views(self):
        write_report(self.sb)
        mods.mark_reviewed("999001", "Test_999001", ["Sub/Buff_AC.uasset"])
        from arkre import cache
        sha = cache.sha256_file(os.path.join(self.mod_dir, "Sub", "Buff_AC.uasset"))
        self.assertIsNotNone(cache.read_view(sha))

    def test_scan_reports_changes(self):
        first = mods.scan()
        self.assertEqual(first["new"], ["999001"])
        self.assertEqual(mods.scan()["changed"], [])
        with open(os.path.join(self.mod_dir, "Sub", "Buff_AC.uasset"), "ab") as fh:
            fh.write(b"\0")
        self.assertEqual(mods.scan()["changed"], ["999001"])


class SearchAndToolsTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_xref_and_text_search(self):
        r = xref.mod_search("999001", "PrintString", kinds=["call"])
        self.assertGreater(r["total"], 0)
        self.assertTrue(all(i["kind"] == "call" for i in r["items"]))
        t = xref.mod_search("999001", "/PrintString/", mode="text", limit=2)
        self.assertEqual(len(t["items"]), 2)
        self.assertTrue(t["truncated"])
        with self.assertRaises(ValueError):
            xref.mod_search("999001", "x", kinds=["nope"])

    def test_function_by_event_and_entry(self):
        a = "999001:Actor_BP_AC"
        s = asset_tools.asset_summary(a)
        self.assertEqual(s["generated_class"], "Actor_BP_AC_C")
        f = asset_tools.asset_function(a, "ExecuteUbergraph_Actor_BP_AC", entry="ReceiveBeginPlay")
        self.assertIn("PrintString", f["code"])
        self.assertEqual(f["meta"]["entry"], "ReceiveBeginPlay")
        with self.assertRaises(asset_tools.ToolError):
            asset_tools.asset_function(a, "НетТакой")

    def test_defaults_note_for_native_parent(self):
        d = asset_tools.asset_defaults("999001:Actor_BP_AC", resolve_parents=True)
        self.assertEqual(d["native_parent"], "/Script/Engine.Actor")

    def test_respond_truncates_long_lists(self):
        text = respond.fit({"items": [{"x": "я" * 100}] * 1000})
        self.assertLessEqual(len(text), respond.LIMIT)
        self.assertIn('"truncated":true', text)


if __name__ == "__main__":
    unittest.main()
