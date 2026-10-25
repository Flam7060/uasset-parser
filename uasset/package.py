"""
Заголовок пакета (FPackageFileSummary), name map, импорты, экспорты, depends.

Пороговые версии — из ObjectVersion.h UE4. ARK SE сохраняет пакеты с
FileVersionUE4=404, LegacyFileVersion=-3, поэтому большинство «новых» полей
просто отсутствует; ветки по версиям оставлены, чтобы читались и соседние
сборки движка.
"""
from dataclasses import dataclass, field

from .reader import Reader, ParseError

PACKAGE_TAG = 0x9E2A83C1

VER_UE4_REMOVE_NET_INDEX = 196
VER_UE4_ENGINE_VERSION_OBJECT = 336
VER_UE4_LOAD_FOR_EDITOR_GAME = 365
VER_UE4_ADD_STRING_ASSET_REFERENCES_MAP = 384
VER_UE4_PACKAGE_SUMMARY_HAS_COMPATIBLE_ENGINE_VERSION = 444
VER_UE4_SERIALIZE_TEXT_IN_PACKAGES = 459
VER_UE4_COOKED_ASSETS_IN_EDITOR_SUPPORT = 485
VER_UE4_NAME_HASHES_SERIALIZED = 504
VER_UE4_PRELOAD_DEPENDENCIES_IN_COOKED_EXPORTS = 507
VER_UE4_TemplateIndex_IN_COOKED_EXPORTS = 508
VER_UE4_ADDED_SEARCHABLE_NAMES = 510
VER_UE4_64BIT_EXPORTMAP_SERIALSIZES = 511

PKG_FilterEditorOnly = 0x80000000

# В ARK между NumTextureAllocations и AssetRegistryDataOffset лежат ещё два
# int32 (во всех ассетах мода — нули). В стоковом 4.x их нет. Значение
# подобрано по файлам и проверяется ниже: AssetRegistryDataOffset должен
# попасть внутрь заголовка, BulkDataStartOffset — внутрь файла.
ARK_SUMMARY_EXTRA_INT32 = 2

RF_ClassDefaultObject = 0x10


@dataclass
class Summary:
    legacy_version: int = 0
    legacy_ue3_version: int = 0
    file_version_ue4: int = 0
    file_version_licensee: int = 0
    custom_versions: list = field(default_factory=list)
    total_header_size: int = 0
    folder_name: str = ""
    package_flags: int = 0
    name_count: int = 0
    name_offset: int = 0
    gatherable_count: int = 0
    gatherable_offset: int = 0
    export_count: int = 0
    export_offset: int = 0
    import_count: int = 0
    import_offset: int = 0
    depends_offset: int = 0
    string_ref_count: int = 0
    string_ref_offset: int = 0
    searchable_names_offset: int = 0
    thumbnail_offset: int = 0
    guid: str = ""
    generations: list = field(default_factory=list)
    engine_version: str = ""
    compatible_engine_version: str = ""
    compression_flags: int = 0
    package_source: int = 0
    additional_packages: list = field(default_factory=list)
    texture_allocations: int = 0
    ark_extra: list = field(default_factory=list)
    asset_registry_offset: int = 0
    bulk_data_start: int = 0
    world_tile_info_offset: int = 0
    chunk_ids: list = field(default_factory=list)
    preload_dep_count: int = 0
    preload_dep_offset: int = 0


@dataclass
class ImportEntry:
    class_package: str
    class_name: str
    outer_index: int
    object_name: str


@dataclass
class ExportEntry:
    class_index: int
    super_index: int
    template_index: int
    outer_index: int
    object_name: str
    object_flags: int
    serial_size: int
    serial_offset: int
    forced_export: int
    not_for_client: int
    not_for_server: int
    package_guid: str
    package_flags: int
    not_for_editor_game: int = 0
    is_asset: int = 0
    deps: tuple = ()


def _engine_version(r):
    major, minor, patch = r.u16(), r.u16(), r.u16()
    changelist = r.u32()
    branch = r.fstring()
    return f"{major}.{minor}.{patch}-{changelist}+{branch}"


def parse_summary(r):
    s = Summary()
    tag = r.u32()
    if tag != PACKAGE_TAG:
        raise ParseError(f"неверная сигнатура пакета {tag:#010x}, ожидалась {PACKAGE_TAG:#010x}")
    s.legacy_version = r.i32()
    if s.legacy_version >= 0 or s.legacy_version < -7:
        raise ParseError(f"неподдерживаемый LegacyFileVersion {s.legacy_version}")
    if s.legacy_version != -4:
        s.legacy_ue3_version = r.i32()
    s.file_version_ue4 = r.i32()
    s.file_version_licensee = r.i32()
    ver = s.file_version_ue4
    if ver == 0:
        raise ParseError("unversioned-пакет: версию движка взять неоткуда")

    if s.legacy_version <= -2:
        count = r.i32()
        if not 0 <= count <= 4096:
            raise ParseError(f"число custom versions {count} нереально")
        for _ in range(count):
            if s.legacy_version == -2:
                s.custom_versions.append((f"enum:{r.i32()}", r.i32()))
            elif s.legacy_version >= -5:
                g, v = r.guid(), r.i32()
                s.custom_versions.append((f"{g} {r.fstring()}", v))
            else:
                s.custom_versions.append((r.guid(), r.i32()))

    s.total_header_size = r.i32()
    s.folder_name = r.fstring()
    s.package_flags = r.u32()
    s.name_count, s.name_offset = r.i32(), r.i32()
    if ver >= VER_UE4_SERIALIZE_TEXT_IN_PACKAGES:
        s.gatherable_count, s.gatherable_offset = r.i32(), r.i32()
    s.export_count, s.export_offset = r.i32(), r.i32()
    s.import_count, s.import_offset = r.i32(), r.i32()
    s.depends_offset = r.i32()
    if ver >= VER_UE4_ADD_STRING_ASSET_REFERENCES_MAP:
        s.string_ref_count, s.string_ref_offset = r.i32(), r.i32()
    if ver >= VER_UE4_ADDED_SEARCHABLE_NAMES:
        s.searchable_names_offset = r.i32()
    s.thumbnail_offset = r.i32()
    s.guid = r.guid()

    gen_count = r.i32()
    if not 0 <= gen_count <= 1024:
        raise ParseError(f"число generations {gen_count} нереально")
    for _ in range(gen_count):
        export_count, name_count = r.i32(), r.i32()
        if ver < VER_UE4_REMOVE_NET_INDEX:
            r.i32()  # NetObjectCount
        s.generations.append((export_count, name_count))

    if ver >= VER_UE4_ENGINE_VERSION_OBJECT:
        s.engine_version = _engine_version(r)
    else:
        s.engine_version = str(r.i32())
    if ver >= VER_UE4_PACKAGE_SUMMARY_HAS_COMPATIBLE_ENGINE_VERSION:
        s.compatible_engine_version = _engine_version(r)

    s.compression_flags = r.u32()
    chunks = r.i32()
    if chunks != 0:
        # Сжатые чанки в cooked-пакетах ARK не встречаются; распаковку не
        # делаем, чтобы не парсить мусор как данные экспортов.
        raise ParseError(f"пакет сжат ({chunks} чанков, флаги {s.compression_flags:#x}) — не поддерживается")
    s.package_source = r.u32()
    add_count = r.i32()
    if not 0 <= add_count <= 4096:
        raise ParseError(f"AdditionalPackagesToCook {add_count} нереально")
    s.additional_packages = [r.fstring() for _ in range(add_count)]

    if s.legacy_version > -7:
        s.texture_allocations = r.i32()
        if s.texture_allocations != 0:
            raise ParseError(f"NumTextureAllocations={s.texture_allocations}: формат FTextureAllocations не реализован")
    s.ark_extra = [r.i32() for _ in range(ARK_SUMMARY_EXTRA_INT32)]

    s.asset_registry_offset = r.i32()
    s.bulk_data_start = r.i64()
    s.world_tile_info_offset = r.i32()
    chunk_count = r.i32()
    if not 0 <= chunk_count <= 4096:
        raise ParseError(f"число ChunkIDs {chunk_count} нереально (сдвиг в хвосте заголовка?)")
    s.chunk_ids = [r.i32() for _ in range(chunk_count)]
    if ver >= VER_UE4_PRELOAD_DEPENDENCIES_IN_COOKED_EXPORTS:
        s.preload_dep_count, s.preload_dep_offset = r.i32(), r.i32()

    file_size = len(r.data)
    if not 0 <= s.asset_registry_offset <= s.total_header_size:
        raise ParseError(
            f"AssetRegistryDataOffset {s.asset_registry_offset:#x} вне заголовка "
            f"{s.total_header_size:#x}: раскладка хвоста FPackageFileSummary не совпала "
            f"(см. ARK_SUMMARY_EXTRA_INT32)")
    if not 0 <= s.bulk_data_start <= file_size:
        raise ParseError(f"BulkDataStartOffset {s.bulk_data_start:#x} вне файла {file_size:#x}")
    if r.tell() > s.name_offset:
        raise ParseError(f"заголовок прочитан до {r.tell():#x}, а name map начинается с {s.name_offset:#x}")
    for what, off, cnt in (("names", s.name_offset, s.name_count),
                           ("imports", s.import_offset, s.import_count),
                           ("exports", s.export_offset, s.export_count)):
        if cnt < 0 or not 0 <= off <= s.total_header_size:
            raise ParseError(f"таблица {what}: {cnt} записей @ {off:#x} вне заголовка {s.total_header_size:#x}")
    return s


class Package:
    def __init__(self, data, path=""):
        self.data = data
        self.path = path
        r = Reader(data)
        self.summary = parse_summary(r)
        self.names = self._parse_names()
        self.imports = self._parse_imports()
        self.exports = self._parse_exports()
        self._parse_depends()

    # --- таблицы -------------------------------------------------------

    def _parse_names(self):
        s = self.summary
        r = Reader(self.data, s.name_offset, s.total_header_size)
        names = []
        for _ in range(s.name_count):
            names.append(r.fstring())
            if s.file_version_ue4 >= VER_UE4_NAME_HASHES_SERIALIZED:
                r.u32()
        return names

    def read_fname(self, r):
        at = r.tell()
        idx, number = r.i32(), r.i32()
        return self.fname(idx, number, at)

    def fname(self, idx, number, at=None):
        if not 0 <= idx < len(self.names):
            where = f" @ {at:#x}" if at is not None else ""
            raise ParseError(f"индекс FName {idx}{where} вне name map ({len(self.names)})")
        # UE4 хранит Number+1: 0 — без суффикса, N — суффикс _(N-1)
        return self.names[idx] if number == 0 else f"{self.names[idx]}_{number - 1}"

    def _parse_imports(self):
        s = self.summary
        r = Reader(self.data, s.import_offset, s.total_header_size)
        out = []
        for _ in range(s.import_count):
            cp = self.read_fname(r)
            cn = self.read_fname(r)
            outer = r.i32()
            out.append(ImportEntry(cp, cn, outer, self.read_fname(r)))
        return out

    def _parse_exports(self):
        s = self.summary
        ver = s.file_version_ue4
        r = Reader(self.data, s.export_offset, s.total_header_size)
        out = []
        for i in range(s.export_count):
            class_index, super_index = r.i32(), r.i32()
            template_index = r.i32() if ver >= VER_UE4_TemplateIndex_IN_COOKED_EXPORTS else 0
            outer_index = r.i32()
            name = self.read_fname(r)
            flags = r.u32()
            if ver >= VER_UE4_64BIT_EXPORTMAP_SERIALSIZES:
                size, offset = r.i64(), r.i64()
            else:
                size, offset = r.i32(), r.i32()
            e = ExportEntry(class_index, super_index, template_index, outer_index, name, flags,
                            size, offset, r.i32(), r.i32(), r.i32(), r.guid(), r.u32())
            if ver >= VER_UE4_LOAD_FOR_EDITOR_GAME:
                e.not_for_editor_game = r.i32()
            if ver >= VER_UE4_COOKED_ASSETS_IN_EDITOR_SUPPORT:
                e.is_asset = r.i32()
            if ver >= VER_UE4_PRELOAD_DEPENDENCIES_IN_COOKED_EXPORTS:
                e.deps = tuple(r.i32() for _ in range(5))
            if size < 0 or offset < 0 or offset + size > len(self.data):
                raise ParseError(f"экспорт [{i}] {name}: данные {offset:#x}+{size} вне файла")
            out.append(e)
        return out

    def _parse_depends(self):
        s = self.summary
        self.depends = [[] for _ in self.exports]
        if s.depends_offset <= 0 or not self.exports:
            return
        r = Reader(self.data, s.depends_offset, s.total_header_size)
        for i in range(len(self.exports)):
            n = r.i32()
            if not 0 <= n <= len(self.imports) + len(self.exports):
                raise ParseError(f"depends экспорта [{i}]: {n} записей — таблица битая")
            self.depends[i] = [r.i32() for _ in range(n)]

    # --- разрешение ссылок ----------------------------------------------

    def entry(self, index):
        """FPackageIndex → запись: >0 экспорт, <0 импорт, 0 — null."""
        if index > 0 and index <= len(self.exports):
            return self.exports[index - 1]
        if index < 0 and -index <= len(self.imports):
            return self.imports[-index - 1]
        return None

    def valid_index(self, index):
        return index == 0 or self.entry(index) is not None

    def obj_name(self, index):
        if index == 0:
            return "null"
        e = self.entry(index)
        if e is None:
            return f"<bad index {index}>"
        return e.object_name

    def full_name(self, index):
        """Имя с цепочкой outer без пакета: KismetSystemLibrary.PrintString."""
        parts = []
        seen = set()
        while index != 0 and index not in seen:
            seen.add(index)
            e = self.entry(index)
            if e is None:
                parts.append(f"<bad index {index}>")
                break
            if isinstance(e, ImportEntry) and e.class_name == "Package":
                break
            parts.append(e.object_name)
            index = e.outer_index
        return ".".join(reversed(parts)) if parts else "null"

    def class_name(self, index):
        """Имя класса объекта по индексу (для импорта — ClassName)."""
        e = self.entry(index)
        if e is None:
            return "?"
        if isinstance(e, ImportEntry):
            return e.class_name
        return "Class" if e.class_index == 0 else self.obj_name(e.class_index)

    def export_class(self, export):
        return "Class" if export.class_index == 0 else self.obj_name(export.class_index)
