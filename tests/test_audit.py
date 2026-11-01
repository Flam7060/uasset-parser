"""
Аудит: детекторы на синтетике (всегда) и регрессия на S+ (если установлен).

Регрессия проверяет, что `mod_audit` сам переоткрывает находки, которые мы
нашли руками: Converter (D1+D2), ChibiDisplay (D2), ограничитель только в UI
(D3), число клиента без клампа (D4), решение на поле команды (D6).
"""
import os
import unittest

from arkre import audit, config

S_PLUS = "731604991"


def splus_installed() -> bool:
    return os.path.isdir(os.path.join(config.mods_dir(), S_PLUS))


class VocabTests(unittest.TestCase):
    def test_detector_set_and_severity_complete(self):
        for d in audit.DETECTORS:
            self.assertIn(d, audit.SEVERITY)
            self.assertIn(audit.SEVERITY[d], audit.SEV_RANK)

    def test_identity_fields_all_described(self):
        self.assertEqual(set(audit.IDENTITY_FIELDS), audit.IDENTITY_READS)
        for field, why in audit.IDENTITY_FIELDS.items():
            self.assertTrue(why.strip())

    def test_limit_key_matches_caps_not_slot_counts(self):
        self.assertTrue(audit.LIMIT_KEY.search("PulseMax"))
        self.assertTrue(audit.LIMIT_KEY.search("PropagatorDisableDinoMods"))
        self.assertTrue(audit.LIMIT_KEY.search("TribePropagatorLimit"))
        self.assertIsNone(audit.LIMIT_KEY.search("CampfireSlotCount"))

    def test_unknown_detector_rejected(self):
        with self.assertRaises(ValueError):
            audit.audit("1", detectors=["D9"])

    def test_untrusted_param_filters_controller(self):
        self.assertTrue(audit._is_untrusted_param({"name": "Target", "type": "ObjectProperty<Object>"}))
        self.assertTrue(audit._is_untrusted_param({"name": "ItemClass", "type": "ClassProperty<PrimalItem>"}))
        self.assertFalse(audit._is_untrusted_param({"name": "ForPC", "type": "ObjectProperty<PlayerController>"}))
        self.assertFalse(audit._is_untrusted_param({"name": "Count", "type": "IntProperty"}))


@unittest.skipUnless(splus_installed(), "S+ не установлен")
class SPlusAuditRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = audit.audit(S_PLUS)
        cls.by = {}
        for f in cls.r["findings"]:
            cls.by.setdefault(f["detector"], []).append(f)

    def _has(self, detector, asset_sub, function):
        return any(asset_sub in f["asset"] and f["function"] == function
                   for f in self.by.get(detector, []))

    def test_converter_flagged_d1_and_d2(self):
        self.assertTrue(self._has("D1", "BP_Converter", "BPServerHandleNetExecCommand"))
        self.assertTrue(self._has("D2", "BP_Converter", "ConvertSelectedItem"))

    def test_chibi_display_dupe_flagged_d2(self):
        self.assertTrue(self._has("D2", "BP_ChibiDisplay", "SelectChibi"))

    def test_starter_block_unclamped_flagged_d4(self):
        self.assertTrue(self._has("D4", "BP_StarterBlock", "BPServerHandleNetExecCommand"))

    def test_ui_only_limit_flagged_d3(self):
        keys = {f["symbol"] for f in self.by.get("D3", [])}
        self.assertTrue(any("PropagatorDisableDinoMods" in k for k in keys))

    def test_trust_boundary_targetingteam_flagged_d6(self):
        self.assertTrue(any(f["symbol"] == "TargetingTeam" for f in self.by.get("D6", [])))

    def test_hitching_post_interproc_flagged_d5(self):
        # источник в обработчике RPC, сток (StaticAddBuff) в вызванной ToggleHitchDino
        self.assertTrue(self._has("D5", "BP_HitchingPost", "BPServerHandleNetExecCommand"))

    def test_omnitool_interproc_flagged_d5(self):
        # FiredEvent — серверный RPC с Object-параметром Target, выдача предмета в вызванной функции
        self.assertTrue(self._has("D5", "BP_OmniTool", "FiredEvent"))

    def test_findings_ranked_server_reachable_and_severity(self):
        ranks = [(not f.get("server_reachable", False), audit.SEV_RANK[f["severity"]])
                 for f in self.r["findings"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_detector_subset_runs_only_requested(self):
        only = audit.audit(S_PLUS, detectors=["D2"])
        self.assertTrue(only["findings"])
        self.assertEqual({f["detector"] for f in only["findings"]}, {"D2"})


if __name__ == "__main__":
    unittest.main()
