"""
Tagged-свойства UObject (FPropertyTag, формат UE4 до v503).

Тег: Name, Type, Size(i32), ArrayIndex(i32), затем по типу: StructName
(Struct), BoolVal u8 (Bool, Size=0), EnumName (Byte), InnerType (Array).
PropertyGuid появился в v503, в ARK его нет.

Значение всегда читается в под-окне ровно Size байт: неизвестный тип или
нестандартная сериализация структуры не сбивают разбор остальных свойств —
такое значение отдаётся как сырой hex с пометкой.
"""
from dataclasses import dataclass, field

from .reader import Reader, ParseError

# Защита от зацикливания на битых данных: вложенные структуры глубже этого
# в cooked-ассетах не встречаются.
MAX_STRUCT_DEPTH = 16
MAX_ARRAY_COUNT = 1 << 20
RAW_PREVIEW_BYTES = 64


@dataclass
class Prop:
    name: str
    type: str
    size: int
    array_index: int = 0
    struct_name: str = ""
    enum_name: str = ""
    inner_type: str = ""
    value: object = None
    note: str = ""          # почему значение не разобрано / разобрано частично
    offset: int = 0


@dataclass
class Raw:
    data: bytes
    reason: str = ""

    def __str__(self):
        h = self.data[:RAW_PREVIEW_BYTES].hex(' ')
        more = f" …(+{len(self.data) - RAW_PREVIEW_BYTES})" if len(self.data) > RAW_PREVIEW_BYTES else ""
        why = f" [{self.reason}]" if self.reason else ""
        return f"raw[{len(self.data)}]{why} {h}{more}"


@dataclass
class ObjRef:
    index: int
    name: str

    def __str__(self):
        return self.name


@dataclass
class StructValue:
    struct_name: str
    fields: object                  # dict для нативных, list[Prop] для tagged
    native: bool = False


@dataclass
class ArrayValue:
    inner_type: str
    items: list = field(default_factory=list)


# Нативные структуры, которые UE4 пишет бинарно, а не тегами
# (UScriptStruct с STRUCT_SerializeNative / immutable). Значение — (размер,
# функция чтения). Размер сверяется с Size тега, иначе пробуем tagged.
def _vec(r, n): return tuple(round(r.f32(), 6) for _ in range(n))

NATIVE_STRUCTS = {
    "Vector":          (12, lambda r: dict(zip("XYZ", _vec(r, 3)))),
    "Vector2D":        (8,  lambda r: dict(zip("XY", _vec(r, 2)))),
    "Vector4":         (16, lambda r: dict(zip("XYZW", _vec(r, 4)))),
    "Rotator":         (12, lambda r: dict(zip(("Pitch", "Yaw", "Roll"), _vec(r, 3)))),
    "Quat":            (16, lambda r: dict(zip("XYZW", _vec(r, 4)))),
    "Plane":           (16, lambda r: dict(zip("XYZW", _vec(r, 4)))),
    "LinearColor":     (16, lambda r: dict(zip("RGBA", _vec(r, 4)))),
    "Color":           (4,  lambda r: dict(zip("BGRA", (r.u8(), r.u8(), r.u8(), r.u8())))),
    "Guid":            (16, lambda r: {"Guid": r.guid()}),
    "IntPoint":        (8,  lambda r: {"X": r.i32(), "Y": r.i32()}),
    "IntVector":       (12, lambda r: {"X": r.i32(), "Y": r.i32(), "Z": r.i32()}),
    "Box":             (25, lambda r: {"Min": _vec(r, 3), "Max": _vec(r, 3), "IsValid": r.u8()}),
    "Box2D":           (17, lambda r: {"Min": _vec(r, 2), "Max": _vec(r, 2), "IsValid": r.u8()}),
    "DateTime":        (8,  lambda r: {"Ticks": r.i64()}),
    "Timespan":        (8,  lambda r: {"Ticks": r.i64()}),
    "Matrix":          (64, lambda r: {"M": _vec(r, 16)}),
    "Vector_NetQuantize":          (12, lambda r: dict(zip("XYZ", _vec(r, 3)))),
    "Vector_NetQuantize10":        (12, lambda r: dict(zip("XYZ", _vec(r, 3)))),
    "Vector_NetQuantize100":       (12, lambda r: dict(zip("XYZ", _vec(r, 3)))),
    "Vector_NetQuantizeNormal":    (12, lambda r: dict(zip("XYZ", _vec(r, 3)))),
}

_SCALARS = {
    "IntProperty":    lambda r: r.i32(),
    "Int8Property":   lambda r: r.i8(),
    "Int16Property":  lambda r: r.i16(),
    "Int64Property":  lambda r: r.i64(),
    "UInt16Property": lambda r: r.u16(),
    "UInt32Property": lambda r: r.u32(),
    "UInt64Property": lambda r: r.u64(),
    "FloatProperty":  lambda r: round(r.f32(), 6),
    "DoubleProperty": lambda r: r.f64(),
    "StrProperty":    lambda r: r.fstring(),
}

# Размер элемента массива для типов фиксированной ширины — нужен, чтобы не
# гадать, когда count*elem не сходится с Size.
_FIXED_ELEM = {
    "IntProperty": 4, "Int8Property": 1, "Int16Property": 2, "Int64Property": 8,
    "UInt16Property": 2, "UInt32Property": 4, "UInt64Property": 8,
    "FloatProperty": 4, "DoubleProperty": 8, "BoolProperty": 1,
    "NameProperty": 8, "ObjectProperty": 4, "ClassProperty": 4,
    "WeakObjectProperty": 4, "InterfaceProperty": 4, "LazyObjectProperty": 16,
}


class PropertyParser:
    def __init__(self, pkg):
        self.pkg = pkg

    def objref(self, r):
        at = r.tell()
        idx = r.i32()
        if not self.pkg.valid_index(idx):
            raise ParseError(f"ссылка на объект {idx} @ {at:#x} вне таблиц")
        return ObjRef(idx, self.pkg.full_name(idx))

    def read_list(self, r, depth=0):
        """Читает теги до None. r — окно, в котором лежит поток."""
        if depth > MAX_STRUCT_DEPTH:
            raise ParseError(f"вложенность структур > {MAX_STRUCT_DEPTH} @ {r.tell():#x}")
        props = []
        while True:
            at = r.tell()
            name = self.pkg.read_fname(r)
            if name == "None":
                return props
            p = Prop(name=name, type=self.pkg.read_fname(r), size=r.i32(), array_index=r.i32(), offset=at)
            if p.size < 0:
                raise ParseError(f"свойство {name} @ {at:#x}: отрицательный Size {p.size}")
            if p.type == "StructProperty":
                p.struct_name = self.pkg.read_fname(r)
            elif p.type == "BoolProperty":
                p.value = bool(r.u8())
            elif p.type == "ByteProperty":
                p.enum_name = self.pkg.read_fname(r)
            elif p.type == "ArrayProperty":
                p.inner_type = self.pkg.read_fname(r)
            body = r.sub(p.size)
            if p.type != "BoolProperty":
                self._read_value(p, body, depth)
            props.append(p)

    def _read_value(self, p, r, depth):
        start = r.tell()
        try:
            p.value = self.value(p.type, r, p.size, struct_name=p.struct_name,
                                 inner_type=p.inner_type, depth=depth)
            if not r.at_end():
                p.note = f"не дочитано {r.remaining()} байт"
        except ParseError as ex:
            r.seek(start)
            p.value = Raw(r.bytes(r.remaining()), str(ex))

    def value(self, ptype, r, size, struct_name="", inner_type="", depth=0):
        if ptype in _SCALARS:
            return _SCALARS[ptype](r)
        if ptype == "NameProperty":
            return self.pkg.read_fname(r)
        if ptype == "ByteProperty":
            # u8 для «голого» байта, FName — если байт привязан к enum
            return r.u8() if size == 1 else self.pkg.read_fname(r)
        if ptype == "BoolProperty":
            return bool(r.u8())
        if ptype in ("ObjectProperty", "ClassProperty", "WeakObjectProperty",
                     "InterfaceProperty", "ComponentProperty"):
            return self.objref(r)
        if ptype == "LazyObjectProperty":
            return {"Guid": r.guid()}
        if ptype in ("AssetObjectProperty", "AssetClassProperty", "SoftObjectProperty"):
            return r.fstring()
        if ptype == "TextProperty":
            return self._text(r)
        if ptype == "DelegateProperty":
            return {"Object": self.objref(r), "Function": self.pkg.read_fname(r)}
        if ptype == "MulticastDelegateProperty":
            n = r.i32()
            if not 0 <= n <= MAX_ARRAY_COUNT:
                raise ParseError(f"MulticastDelegate: {n} подписчиков")
            return [{"Object": self.objref(r), "Function": self.pkg.read_fname(r)} for _ in range(n)]
        if ptype == "StructProperty":
            return self._struct(struct_name, r, size, depth)
        if ptype == "ArrayProperty":
            return self._array(inner_type, r, size, depth)
        return Raw(r.bytes(r.remaining()), f"тип {ptype} не поддержан")

    def _text(self, r):
        # FText 4.x с историей (VER_UE4_FTEXT_HISTORY=368): Flags, HistoryType
        flags = r.u32()
        history = r.i8()
        if history == -1:
            return {"Flags": flags, "Text": None}
        if history == 0:
            return {"Flags": flags, "Namespace": r.fstring(), "Key": r.fstring(), "Source": r.fstring()}
        raise ParseError(f"FText history type {history} не поддержан")

    def _struct(self, struct_name, r, size, depth):
        native = NATIVE_STRUCTS.get(struct_name)
        if native and native[0] == size:
            return StructValue(struct_name, native[1](r), native=True)
        return StructValue(struct_name, self.read_list(r, depth + 1))

    def _array(self, inner, r, size, depth):
        count = r.i32()
        if not 0 <= count <= MAX_ARRAY_COUNT:
            raise ParseError(f"массив {inner}: count={count}")
        arr = ArrayValue(inner)
        if count == 0:
            return arr
        body = r.remaining()
        if inner in _FIXED_ELEM and body != count * _FIXED_ELEM[inner]:
            raise ParseError(f"массив {inner}: {count} × {_FIXED_ELEM[inner]} ≠ {body} байт")
        if inner == "ByteProperty":
            # у элементов нет EnumName — ширину берём из размера
            if body == count:
                arr.items = list(r.bytes(count))
            elif body == count * 8:
                arr.items = [self.pkg.read_fname(r) for _ in range(count)]
            else:
                raise ParseError(f"массив байт: {count} элементов в {body} байтах")
            return arr
        if inner == "StructProperty":
            # До v500 у массива нет тега внутреннего типа: имя структуры
            # неизвестно, элементы пробуем как tagged-потоки, при неудаче —
            # отдаём одним куском (размер элемента ровный — значит нативная).
            start = r.tell()
            try:
                arr.items = [StructValue("?", self.read_list(r, depth + 1)) for _ in range(count)]
                if r.at_end():
                    return arr
            except ParseError:
                pass
            r.seek(start)
            elem = body // count if body % count == 0 else None
            reason = f"структура неизвестна, элемент {elem} байт" if elem else "структура неизвестна"
            arr.items = [Raw(r.bytes(elem), reason) for _ in range(count)] if elem else \
                [Raw(r.bytes(body), reason)]
            return arr
        arr.items = [self.value(inner, r, 0, depth=depth) for _ in range(count)]
        return arr
