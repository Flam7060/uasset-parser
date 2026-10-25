"""
Сериализация тел экспортов (UE4 v404, как пишет ARK SE).

Цепочка как в движке: UObject (tagged-свойства + GUID) → UField (Next) →
UStruct / UProperty → конкретный класс. Тип выбирается по имени класса
экспорта. Если класс неизвестен — читается только часть UObject, остаток
показывается как хвост.
"""
from dataclasses import dataclass, field

from .reader import Reader, ParseError
from .package import RF_ClassDefaultObject
from .properties import PropertyParser, ObjRef
from . import kismet

FUNC_Net = 0x40
MAX_TABLE = 1 << 16

STRUCT_KINDS = {
    "Function": "function",
    "DelegateFunction": "function",
    "Class": "class",
    "BlueprintGeneratedClass": "class",
    "WidgetBlueprintGeneratedClass": "class",
    "AnimBlueprintGeneratedClass": "class",
    "ScriptStruct": "struct",
    "UserDefinedStruct": "struct",
    "Enum": "enum",
    "UserDefinedEnum": "enum",
    "Blueprint": "blueprint",
    "AnimBlueprint": "blueprint",
    "WidgetBlueprint": "blueprint",
}

# Дополнительные поля UProperty-наследников после ArrayDim/Flags/RepNotify
PROPERTY_EXTRA = {
    "ByteProperty": ("Enum",),
    "ObjectProperty": ("PropertyClass",),
    "WeakObjectProperty": ("PropertyClass",),
    "LazyObjectProperty": ("PropertyClass",),
    "AssetObjectProperty": ("PropertyClass",),
    "ClassProperty": ("PropertyClass", "MetaClass"),
    "AssetClassProperty": ("PropertyClass", "MetaClass"),
    "InterfaceProperty": ("InterfaceClass",),
    "StructProperty": ("Struct",),
    "ArrayProperty": ("Inner",),
    "MapProperty": ("KeyProp", "ValueProp"),
    "SetProperty": ("ElementProp",),
    "DelegateProperty": ("SignatureFunction",),
    "MulticastDelegateProperty": ("SignatureFunction",),
}


@dataclass
class ExportBody:
    index: int
    kind: str                       # object | class | function | struct | enum | property
    class_name: str
    props: list = field(default_factory=list)
    guid: str = ""
    fields: dict = field(default_factory=dict)
    script: object = None           # kismet.Script
    trailing: bytes = b""
    error: str = ""


def _ref(pkg, r):
    at = r.tell()
    idx = r.i32()
    if not pkg.valid_index(idx):
        raise ParseError(f"ссылка {idx} @ {at:#x} вне таблиц")
    return ObjRef(idx, pkg.full_name(idx))


def _count(r, what):
    n = r.i32()
    if not 0 <= n <= MAX_TABLE:
        raise ParseError(f"{what}: {n} элементов @ {r.tell() - 4:#x}")
    return n


def _uobject(pkg, export, r, body):
    body.props = PropertyParser(pkg).read_list(r)
    # CDO сериализуется через UClass::SerializeDefaultObject — только теги,
    # без GUID (проверено на Default__*_C: ровно 8 байт None).
    if export.object_flags & RF_ClassDefaultObject:
        return
    has_guid = r.u32()
    if has_guid > 1:
        raise ParseError(f"флаг GUID объекта = {has_guid} — данные сдвинуты")
    if has_guid:
        body.guid = r.guid()


def _ustruct(pkg, r, body):
    f = body.fields
    f["Next"] = _ref(pkg, r)
    f["SuperStruct"] = _ref(pkg, r)
    f["Children"] = _ref(pkg, r)
    # Первым идёт размер в памяти (на 64-бит клиенте), вторым — на диске;
    # проверено: у ReceiveBeginPlay 33/29 при 29 байтах кода в файле.
    mem_size, disk_size = r.i32(), r.i32()
    if not 0 <= disk_size <= r.remaining() or mem_size < disk_size:
        raise ParseError(f"размеры байткода mem={mem_size} disk={disk_size} нереальны (осталось {r.remaining()})")
    f["BytecodeMemSize"], f["BytecodeDiskSize"] = mem_size, disk_size
    if disk_size:
        body.script = kismet.decode(pkg, r.data, r.tell(), disk_size, mem_size)
        r.seek(r.tell() + disk_size)


def _uclass(pkg, r, body):
    f = body.fields
    f["FuncMap"] = [(pkg.read_fname(r), _ref(pkg, r)) for _ in range(_count(r, "FuncMap"))]
    f["ClassFlags"] = r.u32()
    f["ClassWithin"] = _ref(pkg, r)
    f["ClassConfigName"] = pkg.read_fname(r)
    f["ClassGeneratedBy"] = _ref(pkg, r)
    f["Interfaces"] = [{"Class": _ref(pkg, r), "PointerOffset": r.i32(), "bImplementedByK2": r.u32()}
                       for _ in range(_count(r, "Interfaces"))]
    f["bDeprecatedForceScriptOrder"] = r.u32()
    f["Dummy"] = pkg.read_fname(r)
    f["bCooked"] = r.u32()
    f["ClassDefaultObject"] = _ref(pkg, r)


def _uenum(pkg, r, body):
    body.fields["Next"] = _ref(pkg, r)
    body.fields["Names"] = [pkg.read_fname(r) for _ in range(_count(r, "Enum.Names"))]
    # CppForm (u8) в 4.5 уже есть; если байта нет — enum старого формата
    if r.remaining() >= 1:
        body.fields["CppForm"] = r.u8()


def _uproperty(pkg, r, body):
    f = body.fields
    f["Next"] = _ref(pkg, r)
    f["ArrayDim"] = r.i32()
    f["PropertyFlags"] = r.u64()
    f["RepNotifyFunc"] = pkg.read_fname(r)
    if body.class_name == "BoolProperty":
        f["BoolSize"], f["NativeBool"] = r.u8(), r.u8()
    for name in PROPERTY_EXTRA.get(body.class_name, ()):
        f[name] = _ref(pkg, r)


def parse_export(pkg, index):
    e = pkg.exports[index]
    cls = pkg.export_class(e)
    kind = STRUCT_KINDS.get(cls)
    if kind is None:
        kind = "property" if cls.endswith("Property") else "object"
    body = ExportBody(index, kind, cls)
    r = Reader(pkg.data, e.serial_offset, e.serial_offset + e.serial_size)
    try:
        _uobject(pkg, e, r, body)
        if kind in ("function", "class", "struct"):
            _ustruct(pkg, r, body)
        if kind == "function":
            body.fields["FunctionFlags"] = flags = r.u32()
            if flags & FUNC_Net:
                body.fields["RepOffset"] = r.u16()
        elif kind == "struct":
            body.fields["StructFlags"] = r.u32()
        elif kind == "class":
            _uclass(pkg, r, body)
        elif kind == "enum":
            _uenum(pkg, r, body)
        elif kind == "property":
            _uproperty(pkg, r, body)
        elif kind == "blueprint":
            # ARK дописывает к UBlueprint u32 (во всех ассетах мода = 1);
            # в стоковом 4.x этого поля нет, смысл не установлен.
            body.fields["ArkBlueprintU32"] = r.u32()
    except ParseError as ex:
        body.error = str(ex)
    body.trailing = pkg.data[r.tell():r.end]
    return body


def parse_all(pkg):
    return [parse_export(pkg, i) for i in range(len(pkg.exports))]
