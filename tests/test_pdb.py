import unittest

from arkre import pdb


class ParsingTests(unittest.TestCase):
    def test_type_header_with_aligned_indices(self):
        # индекс выровнен по ширине: у 4-значных пять пробелов, у 5-значных четыре
        for line in ("     0x1003 | LF_ARRAY [size = 16]", "    0x38CA9 | LF_CLASS [size = 72] `X`"):
            self.assertIsNotNone(pdb._HDR.match(line), line)
        self.assertIsNone(pdb._HDR.match("              type = 0x1003, offset = 0"))

    def test_member_regex_handles_primitive_and_indexed_types(self):
        m = pdb._MEMBER.search("- LF_MEMBER [name = `Health`, Type = 0x0040 (float), offset = 1292, attrs = public]")
        self.assertEqual((m.group(1), m.group(3), m.group(4)), ("Health", "float", "1292"))
        m = pdb._MEMBER.search("- LF_MEMBER [name = `V`, Type = 0x188A, offset = 0, attrs = public]")
        self.assertEqual((m.group(2), m.group(3)), ("0x188A", None))

    def test_like_pattern_is_glob(self):
        self.assertEqual(pdb._like_pattern("Foo"), "%Foo%")
        self.assertEqual(pdb._like_pattern("A*::B?"), "A%::B_")

    def test_native_for_ignores_non_script_symbols(self):
        self.assertIsNone(pdb.native_for("self:Foo_C.Bar"))
        self.assertIsNone(pdb.native_for("ApplyModifications"))


class NoPdbTests(unittest.TestCase):
    def test_missing_pdb_has_actionable_message(self):
        import os
        old = os.environ.get("ARK_PDB_SERVER")
        os.environ["ARK_PDB_SERVER"] = "/nonexistent/x.pdb"
        try:
            with self.assertRaises(pdb.PdbError) as cm:
                pdb.pdb_path("server")
            self.assertIn("ARK_PDB_SERVER", str(cm.exception))
        finally:
            if old is None:
                os.environ.pop("ARK_PDB_SERVER")
            else:
                os.environ["ARK_PDB_SERVER"] = old

    def test_missing_pdbutil_has_actionable_message(self):
        import os
        old = os.environ.get("ARK_PDBUTIL")
        os.environ["ARK_PDBUTIL"] = "/nonexistent/llvm-pdbutil"
        try:
            with self.assertRaises(pdb.PdbError) as cm:
                pdb._tool()
            self.assertIn("ARK_PDBUTIL", str(cm.exception))
        finally:
            if old is None:
                os.environ.pop("ARK_PDBUTIL")
            else:
                os.environ["ARK_PDBUTIL"] = old


@unittest.skipUnless(pdb.imported("server"), "серверный PDB не импортирован (python3 -m arkre.pdb server)")
class RealPdbTests(unittest.TestCase):
    def test_dino_character_layout(self):
        t = pdb.pdb_type("APrimalDinoCharacter")
        self.assertEqual(t["bases"][0]["name"], "APrimalCharacter")
        by_name = {m["name"]: m for m in t["members"]}
        self.assertEqual(by_name["TamedAtTime"]["type"], "double")
        self.assertEqual(by_name["BaseDinoScale"]["type"], "FVector")
        self.assertEqual(by_name["bIsBaby"]["bits"], 1)

    def test_server_entrypoint_symbol(self):
        r = pdb.pdb_symbols("*AActor::BPServerHandleNetExecCommand*")
        self.assertTrue(any("AActor::BPServerHandleNetExecCommand" in i["name"] for i in r["items"]))

    def test_unknown_type_suggests_names(self):
        with self.assertRaises(pdb.PdbError) as cm:
            pdb.pdb_type("APrimalDino")
        self.assertIn("Ближайшие", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
