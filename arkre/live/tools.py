"""Живые инструменты (ARK_LIVE=1): статус, объекты, CDO, снимки, сырое чтение."""
from __future__ import annotations

import json
import os
import re
import struct
from typing import Optional

from .. import asset_tools, assets, cache, pdb
from . import LiveError, decode
from .proc import ok
from .reflect import OBJ_FLAGS, TEMPLATE_FLAGS
from .session import session

MAX_INSTANCES = 500
MAX_RAW = 8192
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _addr(v) -> int:
    try:
        return int(str(v), 0)
    except ValueError:
        raise LiveError(f"Адрес «{v}» не разобрать: нужен вид 0x7ff6… или десятичное число.") from None


def _is_template(r, o: int) -> bool:
    return bool(r.p.i32(o + OBJ_FLAGS) & TEMPLATE_FLAGS) or r.objname(o).startswith("Default__")


def live_status() -> dict:
    p, r, build = session()
    arr = p.base + r.object_array_rva
    return {"pid": p.pid, "module_base": hex(p.base), "build": build,
            "gnames": hex(r.gnames), "gworld": hex(r.gworld) if r.gworld else None,
            "guobjectarray": hex(arr), "objects": r.object_count()}


def resolve_ref(r, ref: str) -> int:
    ref = str(ref).strip()
    if ref.lower().startswith("0x") or ref.isdigit():
        o = _addr(ref)
        if not ok(o) or not ok(r.cls(o)):
            raise LiveError(f"По адресу {hex(o)} нет объекта UE (нечитаемо или не UObject).")
        return o
    low = ref.lower()
    hits = []
    for objs in r.index().values():
        for o in objs:
            if r.objname(o).lower() == low or r.full_name(o).lower().endswith(low):
                if not _is_template(r, o) or "default__" in low:
                    hits.append(o)
    if not hits:
        raise LiveError(f"Объект «{ref}» не найден среди живых. Найти адрес: live_instances.")
    if len(hits) > 1:
        shown = ", ".join(f"{hex(h)} ({r.full_name(h)})" for h in hits[:6])
        raise LiveError(f"Имя «{ref}» неоднозначно ({len(hits)} объектов): {shown}. Передай адрес.")
    return hits[0]


def live_instances(cls: str, include_subclasses: bool = True, limit: int = 50) -> dict:
    p, r, _ = session()
    limit = max(1, min(limit, MAX_INSTANCES))
    want = cls.lower()
    rows, total = [], 0
    for cptr, objs in r.index().items():
        names = [n.lower() for n in r.chain(cptr)]
        if not names or not (names[0] == want or (include_subclasses and want in names)):
            continue
        for o in objs:
            if _is_template(r, o):
                continue
            total += 1
            if len(rows) < limit:
                rows.append({"address": hex(o), "name": r.objname(o), "class": r.objname(cptr),
                             "outer": r.objname(p.u64(o + 0x20)),
                             "flags": hex(p.i32(o + OBJ_FLAGS) & 0xFFFFFFFF)})
    out = {"class": cls, "total": total, "items": rows}
    if total > len(rows):
        out["truncated"] = True
    return out


def live_object(ref: str, props: Optional[list] = None, depth: int = 1) -> dict:
    _, r, _ = session()
    o = resolve_ref(r, ref)
    return decode.describe(r, o, set(props) if props else None, max(0, min(depth, 4)))


def _comparable(v) -> bool:
    return isinstance(v, (bool, int, float, str))


def _same(live, static) -> bool:
    if isinstance(live, dict) and "value" in live:
        live = live["value"]
    if isinstance(live, float) or isinstance(static, float):
        try:
            return abs(float(live) - float(static)) <= 1e-4 * max(1.0, abs(float(static)))
        except (TypeError, ValueError):
            return False
    return live == static


def live_cdo(cls: str) -> dict:
    """CDO из памяти + расхождение со статическими дефолтами ассета."""
    p, r, _ = session()
    low = cls.lower()
    cdo = class_obj = 0
    for cptr, objs in r.index().items():
        for o in objs:
            if r.objname(o).lower() == "default__" + low and r.objname(cptr).lower() == low:
                cdo, class_obj = o, cptr
    if not cdo:
        raise LiveError(f"CDO класса {cls} не найден: класс не загружен или имя неточное "
                        f"(для BP — вместе с `_C`, например BP_Propagator_C).")
    live = decode.describe(r, cdo, None, 1)
    package = r.package_of(class_obj)
    out = {"class": cls, "cdo": live["address"], "package": package}
    try:
        static = asset_tools.asset_defaults(package, None, resolve_parents=True)
    except (assets.AssetError, asset_tools.ToolError) as ex:
        out.update(static_error=str(ex), fields=live["fields"])
        return out
    sprops = {pr["name"]: pr for pr in static["properties"] if "index" not in pr}
    diffs, same, skipped = [], 0, []
    for name, lv in live["fields"].items():
        pr = sprops.get(name)
        if pr is None:
            continue
        sv = pr["value"]
        lval = lv["value"] if isinstance(lv, dict) and "value" in lv and len(lv) <= 2 else lv
        if not (_comparable(lval) and _comparable(sv)):
            skipped.append(name)
        elif _same(lval, sv):
            same += 1
        else:
            diffs.append({"property": name, "live": lval, "static": sv, "static_from": pr["from"]})
    out.update(differences=diffs, same=same, not_comparable=skipped[:50],
               static_properties=len(sprops),
               note="сравниваются только скаляры, переопределённые в ассете или его BP-родителях; "
                    "нативные дефолты в .uasset отсутствуют" + (
                        f"; {static.get('native_parent')}" if static.get("native_parent") else ""))
    return out


def _live_dir() -> str:
    return os.path.join(cache.root(), "live")


def live_snapshot(ref: str, label: str) -> dict:
    if not LABEL_RE.match(label):
        raise LiveError("label: латиница, цифры, _ . - (до 64 символов).")
    _, r, _ = session()
    o = resolve_ref(r, ref)
    snap = decode.describe(r, o, None, 2)
    os.makedirs(_live_dir(), exist_ok=True)
    path = os.path.join(_live_dir(), f"{label}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False)
    os.replace(tmp, path)
    return {"path": path, "object": snap["name"], "class": snap["class"], "fields": len(snap["fields"])}


def _flatten(v, prefix: str, out: dict) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            _flatten(x, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _flatten(x, f"{prefix}[{i}]", out)
    else:
        out[prefix] = v


def _load_snapshot(label: str) -> dict:
    if not LABEL_RE.match(label):
        raise LiveError("label: латиница, цифры, _ . - (до 64 символов).")
    path = os.path.join(_live_dir(), f"{label}.json")
    if not os.path.isfile(path):
        raise LiveError(f"Нет снимка «{label}» ({path}). Сними его через live_snapshot.")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def live_diff(label_a: str, label_b: str, limit: int = 200) -> dict:
    a, b = _load_snapshot(label_a), _load_snapshot(label_b)
    fa, fb = {}, {}
    _flatten(a["fields"], "", fa)
    _flatten(b["fields"], "", fb)
    changed = [{"path": k, "a": fa[k], "b": fb[k]} for k in fa if k in fb and fa[k] != fb[k]]
    out = {"a": label_a, "b": label_b, "same_object": a["address"] == b["address"],
           "changed": changed[:limit], "total_changed": len(changed),
           "added": sorted(set(fb) - set(fa))[:limit], "removed": sorted(set(fa) - set(fb))[:limit]}
    if len(changed) > limit:
        out["truncated"] = True
    return out


# --- сырое чтение ---------------------------------------------------------------

_PRIM = {"float": ("f", 4), "double": ("d", 8), "int": ("i", 4), "unsigned": ("I", 4),
         "unsigned long": ("I", 4), "long": ("i", 4), "short": ("h", 2), "unsigned short": ("H", 2),
         "char": ("b", 1), "unsigned char": ("B", 1), "signed char": ("b", 1), "bool": ("B", 1),
         "__int64": ("q", 8), "unsigned __int64": ("Q", 8)}


def _follow(p, start: int, path: str) -> int:
    """Цепочка `+0x10->+0x38`: прибавить смещение, затем разыменовать указатель."""
    steps = [s.strip() for s in path.split("->")]
    cur = start
    for i, s in enumerate(steps):
        if not re.fullmatch(r"\+(0x[0-9A-Fa-f]+|\d+)", s):
            raise LiveError(f"path: шаг «{s}» не разобрать; формат `+0x10->+0x38`.")
        cur += int(s[1:], 0)
        if i < len(steps) - 1:
            nxt = p.u64(cur)
            if not ok(nxt):
                raise LiveError(f"path: по {hex(cur)} нет указателя (шаг {i + 1}: {s}).")
            cur = nxt
    return cur


def _decode_typed(p, addr: int, type_name: str, depth: int) -> dict:
    layout = pdb.pdb_type(type_name, "client")
    out = {}
    for m in layout["members"]:
        if m.get("bits"):
            continue
        at = addr + m["offset"]
        t = m["type"]
        if t in _PRIM:
            fmt, size = _PRIM[t]
            raw = p.rd(at, size)
            out[m["name"]] = None if raw is None else _num(struct.unpack("<" + fmt, raw)[0])
        elif t.endswith("*"):
            out[m["name"]] = hex(p.u64(at))
        elif depth > 0 and m["size"] and m["size"] <= 256 and not t.startswith(("T", "const ")):
            try:
                out[m["name"]] = _decode_typed(p, at, t, depth - 1)
            except pdb.PdbError:
                out[m["name"]] = (p.rd(at, min(m["size"], 32)) or b"").hex()
        else:
            out[m["name"]] = (p.rd(at, min(m["size"], 32)) or b"").hex()
    return {"type": layout["name"], "size": layout["size"], "fields": out}


def _num(v):
    return round(v, 6) if isinstance(v, float) else v


def live_read(addr, size: int = 64, type: Optional[str] = None, path: Optional[str] = None) -> dict:
    p, r, _ = session()
    a = _addr(addr)
    if path:
        a = _follow(p, a, path)
    if not ok(a):
        raise LiveError(f"Адрес {hex(a)} вне пользовательского пространства.")
    if type:
        return {"address": hex(a), **_decode_typed(p, a, type, 1)}
    size = max(1, min(int(size), MAX_RAW))
    raw = p.rd(a, size)
    return {"address": hex(a), "size": size, "readable": raw is not None,
            "bytes": raw.hex(" ") if raw is not None else None}
