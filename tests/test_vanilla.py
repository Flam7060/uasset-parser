"""Разбор ванильных (Wildcard-cooked) пакетов.

Ваниль и моды делят раскладку FPackageFileSummary (magic, Legacy=-3,
FileVersionUE4=404, Licensee=10) вплоть до хвоста. Единственное расхождение:
в слоте AdditionalPackagesToCook dev-kit кук (моды) пишет счётчик 0, а
Wildcard кук (ваниль) — сентинел 0x1234 (4660). Массив при этом пуст в обоих
случаях (редакторное поле, вырезано при кукинге), строки инлайн не
сериализованы. Раньше парсер принимал сентинел за длину массива и падал.
"""
import os
import struct
import unittest

from arkre import analysis, assets, config
from uasset.package import Package, parse_summary
from uasset.reader import Reader


def _fstr(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def _summary(marker: int, ths: int) -> bytes:
    """Минимальный валидный FPackageFileSummary ARK (ver 404) без таблиц:
    name/import/export/depends пусты, все смещения указывают на конец заголовка.
    `marker` кладётся в слот AdditionalPackagesToCook."""
    b = struct.pack("<I", 0x9E2A83C1)      # magic
    b += struct.pack("<i", -3)             # LegacyFileVersion
    b += struct.pack("<i", 864)            # LegacyUE3Version
    b += struct.pack("<i", 404)            # FileVersionUE4
    b += struct.pack("<i", 10)             # FileVersionLicensee
    b += struct.pack("<i", 0)              # custom versions count
    b += struct.pack("<i", ths)            # TotalHeaderSize
    b += _fstr("None")                     # FolderName
    b += struct.pack("<I", 0x80000000)     # PackageFlags (PKG_FilterEditorOnly)
    b += struct.pack("<ii", 0, ths)        # Name count/offset
    b += struct.pack("<ii", 0, ths)        # Export count/offset
    b += struct.pack("<ii", 0, ths)        # Import count/offset
    b += struct.pack("<i", ths)            # Depends offset
    b += struct.pack("<ii", 0, ths)        # StringAssetReferences count/offset
    b += struct.pack("<i", 0)              # Thumbnail offset
    b += b"\x00" * 16                      # Guid
    b += struct.pack("<i", 1)              # Generations count
    b += struct.pack("<ii", 0, 0)          # gen[0] export/name count
    b += struct.pack("<HHH", 0, 0, 0) + struct.pack("<I", 0) + _fstr("")  # EngineVersion
    b += struct.pack("<I", 0)              # CompressionFlags
    b += struct.pack("<i", 0)              # CompressedChunks count
    b += struct.pack("<I", 4660)           # PackageSource
    b += struct.pack("<i", marker)         # AdditionalPackagesToCook (маркер)
    b += struct.pack("<i", 0)              # NumTextureAllocations
    b += struct.pack("<ii", 0, 0)          # ARK-хвост: два int32
    b += struct.pack("<i", ths)            # AssetRegistryDataOffset
    b += struct.pack("<q", ths)            # BulkDataStartOffset (int64)
    b += struct.pack("<i", 0)              # WorldTileInfo offset
    b += struct.pack("<i", 0)              # ChunkIDs count
    return b


class SyntheticSummaryTests(unittest.TestCase):
    """Фикс читается и на мод-значении слота, и на ванильном сентинеле."""

    def _build(self, marker: int) -> bytes:
        ths = len(_summary(marker, 0))      # длина фиксированная, значение ths на неё не влияет
        buf = _summary(marker, ths)
        self.assertEqual(len(buf), ths)
        return buf

    def test_mod_marker_zero(self):
        s = parse_summary(Reader(self._build(0)))
        self.assertEqual(s.additional_packages, [])
        self.assertEqual(s.additional_packages_marker, 0)

    def test_vanilla_sentinel_0x1234(self):
        buf = self._build(0x1234)
        s = parse_summary(Reader(buf))
        self.assertEqual(s.additional_packages_marker, 0x1234)
        self.assertEqual(s.additional_packages, [])      # строк нет, не падаем
        # и полный разбор пакета доходит до конца без исключений
        pk = Package(buf, "synthetic")
        self.assertEqual((len(pk.names), len(pk.imports), len(pk.exports)), (0, 0, 0))


@unittest.skipUnless(os.path.isdir(config.vanilla_content_dir()),
                     "нет ванильного Content (задай ARK_GAME_DIR)")
class RealVanillaTests(unittest.TestCase):
    """Сквозной разбор настоящего ванильного ассета через тул-чейн."""

    # стабильный core-ассет: класс урона рукопашной, есть во всех сборках
    ASSET = "/Game/PrimalEarth/CoreBlueprints/DamageTypes/DmgType_Melee_Human"

    def test_resolve_and_parse(self):
        r = assets.resolve(self.ASSET)
        self.assertIsNone(r.workshop_id)                 # ваниль, не мод
        v = analysis.view_for_file(r.file)
        self.assertFalse(v.get("broken"), v.get("problems"))
        self.assertIsNotNone(v["class"])
        self.assertTrue(v["class"]["name"].endswith("_C"))

    def test_tek_transmitter_defaults(self):
        asset = ("/Game/PrimalEarth/CoreBlueprints/Items/Structures/Misc/"
                 "PrimalItemStructure_TekTransmitter")
        if not os.path.isfile(assets.resolve(asset).file):
            self.skipTest("ассет отсутствует в этой сборке")
        v = analysis.view_for_file(assets.resolve(asset).file)
        self.assertFalse(v.get("broken"))
        props = {p["name"]: p for p in v["cdo"]}
        self.assertIn("StructureToBuild", props)          # tagged-свойства читаются


if __name__ == "__main__":
    unittest.main()
