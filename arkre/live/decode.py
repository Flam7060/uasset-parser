"""Декодирование значений свойств по рефлексии (только чтение)."""
from __future__ import annotations

import struct
from typing import Any

from .proc import ok
from .reflect import (FIELD_NEXT, OBJ_FLAGS, PROP_ELEMENT_SIZE, PROP_EXTRA, STRUCT_SIZE,
                      Reflection)

MAX_ITEMS = 32          # элементов массива в одном ответе
MAX_FIELDS = 200        # полей объекта в одном ответе

_SCALARS = {
    "Int8Property": ("b", 1), "ByteProperty": ("B", 1), "Int16Property": ("h", 2),
    "UInt16Property": ("H", 2), "IntProperty": ("i", 4), "UInt32Property": ("I", 4),
    "Int64Property": ("q", 8), "UInt64Property": ("Q", 8), "FloatProperty": ("f", 4),
    "DoubleProperty": ("d", 8),
}
_OBJECTS = {"ObjectProperty", "ClassProperty", "AssetObjectProperty", "AssetClassProperty"}


def _round(v):
    return round(v, 6) if isinstance(v, float) else v


def value(r: Reflection, kind: str, field: int, addr: int, depth: int) -> Any:
    """Значение свойства класса `kind` (имя класса поля) по адресу `addr`."""
    p = r.p
    if kind in _SCALARS:
        fmt, size = _SCALARS[kind]
        raw = p.rd(addr, size)
        if raw is None:
            return None
        v = _round(struct.unpack("<" + fmt, raw)[0])
        if kind == "ByteProperty" and ok(p.u64(field + PROP_EXTRA)):
            return {"value": v, "enum": r.objname(p.u64(field + PROP_EXTRA))}
        return v
    if kind == "BoolProperty":
        byte_offset, mask = p.u8(field + PROP_EXTRA + 1), p.u8(field + PROP_EXTRA + 3)
        raw = p.rd(addr + byte_offset, 1)
        return None if raw is None else bool(raw[0] & (mask or 0xFF))
    if kind == "NameProperty":
        return r.name_at(addr)
    if kind == "StrProperty":
        return r.fstring(addr)
    if kind == "TextProperty":
        return r.ftext(addr)
    if kind in _OBJECTS:
        return r.ref(p.u64(addr))
    if kind in ("WeakObjectProperty", "LazyObjectProperty"):
        return r.ref(r.weak(addr))
    if kind == "StructProperty":
        st = p.u64(field + PROP_EXTRA)
        if not ok(st):
            return {"unsupported": "StructProperty без Struct"}
        name = r.objname(st)
        if depth <= 0:
            return {"struct": name, "elided": True}
        return {"struct": name, "fields": fields(r, st, addr, None, depth - 1)}
    if kind == "ArrayProperty":
        inner = p.u64(field + PROP_EXTRA)
        data, n = r.tarray(addr)
        if not ok(inner) or n < 0 or n > 1_000_000:
            return {"unsupported": "ArrayProperty: битый TArray"}
        size = p.i32(inner + PROP_ELEMENT_SIZE)
        kind_in = r.objname(r.cls(inner))
        out = []
        for i in range(min(n, MAX_ITEMS)):
            out.append(value(r, kind_in, inner, data + i * size, depth - 1) if ok(data) and size > 0
                       else None)
        res: dict = {"count": n, "items": out}
        if n > MAX_ITEMS:
            res["truncated"] = True
        return res
    return {"unsupported": kind}


def fields(r: Reflection, struct_or_class: int, base: int, wanted, depth: int) -> dict:
    """Поля объекта/структуры по рефлексии: имя → значение."""
    out: dict = {}
    for name, (field, offset, kind) in r.props(struct_or_class).items():
        if wanted is not None and name not in wanted:
            continue
        if not kind.endswith("Property"):       # в Children класса лежат и UFunction
            continue
        if len(out) >= MAX_FIELDS and wanted is None:
            out["…"] = f"показаны первые {MAX_FIELDS} полей; уточни `props`"
            break
        if offset < 0:
            continue
        out[name] = value(r, kind, field, base + offset, depth)
    return out


def describe(r: Reflection, o: int, wanted=None, depth: int = 1) -> dict:
    c = r.cls(o)
    return {"address": hex(o), "name": r.objname(o), "class": r.objname(c),
            "outer": r.objname(r.p.u64(o + 0x20)), "package": r.package_of(o),
            "flags": hex(r.p.i32(o + OBJ_FLAGS) & 0xFFFFFFFF),
            "fields": fields(r, c, o, wanted, depth)}
