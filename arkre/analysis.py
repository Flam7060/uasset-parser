"""
Ассет → AssetView: всё, что нужно инструментам, без повторного разбора.

AssetView — JSON-совместимый dict (кешируется по sha256 файла и parser_ver):
    imports      [{index, path, class}]               пути в игровой нотации
    exports      [{index, name, class, flags, super, outer}]
    class        {name, super, generated_by, cdo, interfaces, functions}
    variables    [{name, type, flags}]                переменные класса
    functions    {имя: {...}}                         флаги, сигнатура, псевдокод, xrefs
    events       {событие: {ubergraph, entry}}        заглушка → метка убер-графа
    cdo          [свойства Default__X]
    rpc          сетевые функции и переопределённые точки входа ARK
    problems     [{export, why}]                      нераспознанное; молча не теряем

Этот файл входит в parser_ver: правка здесь делает старый кеш невидимым.
"""
from __future__ import annotations

import os
from typing import Optional

from uasset import render
from uasset import kismet
from uasset.objects import parse_all
from uasset.package import Package, ImportEntry, RF_ClassDefaultObject
from uasset.properties import Raw, ObjRef, StructValue, ArrayValue
from uasset.reader import ParseError

from . import cache

# Серверные точки входа ARK, вызываемые по запросу клиента. Пополняется и
# сверяется по PDB (шаг 4).
SERVER_ENTRYPOINTS = ("BPServerHandleNetExecCommand",)

CLASS_IMPORTS = {"Class", "BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass",
                 "AnimBlueprintGeneratedClass", "Package"}

CALL_OPS = {"VirtualFunction", "LocalVirtualFunction", "FinalFunction", "LocalFinalFunction",
            "CallMulticastDelegate"}
VAR_OPS = {"InstanceVariable", "LocalVariable", "DefaultVariable", "LocalOutVariable"}
LET_OPS = {"Let", "LetObj", "LetBool", "LetWeakObjPtr", "LetDelegate", "LetMulticastDelegate"}
CONST_OPS = {"IntConst", "IntConstByte", "ByteConst", "Int64Const", "UInt64Const", "FloatConst",
             "True", "False", "IntZero", "IntOne"}
CAST_OPS = {"DynamicCast", "MetaCast", "ObjToInterfaceCast", "CrossInterfaceCast", "InterfaceToObjCast"}

F_NET, F_NET_RELIABLE, F_NET_MULTICAST = 0x40, 0x80, 0x4000
F_NET_SERVER, F_NET_CLIENT = 0x200000, 0x1000000


class Names:
    """Пути объектов пакета в игровой нотации."""

    def __init__(self, pkg: Package):
        self.pkg = pkg

    def path(self, index: int) -> str:
        """`/Script/Engine.KismetSystemLibrary:PrintString` / `self:Func` / `null`."""
        if index == 0:
            return "null"
        e = self.pkg.entry(index)
        if e is None:
            return f"<bad index {index}>"
        if index > 0:
            return "self:" + self._chain(index)
        chain = []
        seen = set()
        i = index
        while i != 0 and i not in seen:
            seen.add(i)
            ent = self.pkg.entry(i)
            if ent is None:
                break
            chain.append(ent.object_name)
            i = ent.outer_index
        chain.reverse()
        if not chain:
            return "null"
        head, rest = chain[0], chain[1:]
        out = head
        for n, part in enumerate(rest):
            out += ("." if n == 0 else ":") + part
        return out

    def _chain(self, index: int) -> str:
        parts, seen = [], set()
        while index != 0 and index not in seen:
            seen.add(index)
            e = self.pkg.entry(index)
            if e is None:
                break
            parts.append(e.object_name)
            index = e.outer_index
        return ".".join(reversed(parts))

    def member(self, index: int) -> str:
        return self.pkg.obj_name(index)


# --- сериализация значений свойств ----------------------------------------

def value_json(v, names: Names):
    if isinstance(v, Raw):
        return {"raw": v.data[:64].hex(), "size": len(v.data), "reason": v.reason}
    if isinstance(v, ObjRef):
        return names.path(v.index) if v.index else None
    if isinstance(v, StructValue):
        if v.native:
            return {"struct": v.struct_name, "value": _plain(v.fields)}
        return {"struct": v.struct_name, "fields": props_json(v.fields, names)}
    if isinstance(v, ArrayValue):
        return [value_json(x, names) for x in v.items]
    if isinstance(v, (list, tuple)):
        return [value_json(x, names) for x in v]
    if isinstance(v, dict):
        return {k: value_json(x, names) for k, x in v.items()}
    if isinstance(v, bytes):
        return v.hex()
    return v


def _plain(v):
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def props_json(props, names: Names) -> list:
    out = []
    for p in props:
        typ = p.type
        if p.struct_name:
            typ += f"<{p.struct_name}>"
        elif p.enum_name and p.enum_name != "None":
            typ += f"<{p.enum_name}>"
        elif p.inner_type:
            typ += f"<{p.inner_type}>"
        row = {"name": p.name, "type": typ, "value": value_json(p.value, names)}
        if p.array_index:
            row["index"] = p.array_index
        if p.note:
            row["note"] = p.note
        out.append(row)
    return out


# --- обход выражений -------------------------------------------------------

def walk(e):
    """Все подвыражения (как render._walk, публичное имя)."""
    yield from render._walk(e)


def reachable(script, start_mem: int) -> set:
    """Адреса инструкций, достижимых от `start_mem` по потоку исполнения.

    Идём по fallthrough, Jump/JumpIfNot и целям PushExecutionFlow (их потом
    достанет PopExecutionFlow). ComputedJump, Return и конец скрипта обрывают
    ветку: куда ComputedJump прыгает, известно только в рантайме.
    """
    stmts = script.statements
    pos = {s.mem: i for i, s in enumerate(stmts)}
    seen: set = set()
    todo = [start_mem]
    while todo:
        m = todo.pop()
        i = pos.get(m)
        while i is not None and stmts[i].mem not in seen:
            s = stmts[i]
            seen.add(s.mem)
            if s.name == "Jump":
                i = pos.get(s.args[0])
                continue
            if s.name == "JumpIfNot":
                todo.append(s.args[0])
            elif s.name == "PushExecutionFlow":
                todo.append(s.args[0])
            elif s.name in ("Return", "ComputedJump", "PopExecutionFlow", "EndOfScript"):
                break
            i += 1
            if i >= len(stmts):
                break
    return seen


class FunctionAnalyzer:
    def __init__(self, ctx: "render.Context", names: Names):
        self.ctx = ctx
        self.names = names
        self.fmt = render.ExprFormatter(ctx)

    # псевдокод построчно: {off, label, text}
    def lines(self, script) -> list:
        stmt_addrs = {s.mem for s in script.statements}
        targets = kismet.jump_targets(script.statements)
        entries = self.ctx.ubergraph_entries if any(
            s.name == "ComputedJump" for s in script.statements) else {}
        labels = targets | set(entries)
        out = []
        cur = None
        if not script.tail:
            for t in sorted(labels):
                if t not in stmt_addrs and t != script.mem_size:
                    out.append({"off": None, "label": None,
                                "text": f"!! {render.label(t)} не попадает на границу инструкции"})
        for s in script.statements:
            if s.mem in labels:
                cur = render.label(s.mem)
                note = f"  // вход из {', '.join(entries[s.mem])}" if s.mem in entries else ""
                out.append({"off": None, "label": cur, "text": f"{cur}:{note}"})
            out.append({"off": s.mem, "label": cur, "text": f"  {s.mem:04x}  {self.fmt.fmt(s)}"})
        if script.error:
            out.append({"off": None, "label": None, "text": f"!! {script.error}"})
            if script.tail:
                out.append({"off": None, "label": None,
                            "text": f"!! не разобрано {len(script.tail)} байт: {script.tail[:48].hex(' ')}"})
        else:
            out.append({"off": None, "label": None,
                        "text": f"// mem {script.mem_size} = заголовок {script.expected_mem}, проверено"})
        return out

    def xrefs(self, script, events_of: dict) -> list:
        """Перекрёстные ссылки инструкций. `events_of`: mem → [события-входы]."""
        rows = []
        for s in script.statements:
            self._expr(s, False, s, rows, events_of.get(s.mem))
        return rows

    def compact_args(self, items) -> list:
        """Аргументы вызова в компактном виде: {s|n|v|c: значение} или None."""
        out = []
        for x in items:
            if x.name == "ARK_LocString":
                x = x.args[0]
            if x.name in ("StringConst", "UnicodeStringConst"):
                out.append({"s": x.args[0]})
            elif x.name == "NameConst":
                out.append({"n": x.args[0]})
            elif x.name in VAR_OPS:
                out.append({"v": self.names.member(x.args[0].index)})
            elif x.name in CONST_OPS:
                out.append({"c": self.fmt.fmt(x)})
            else:
                out.append(None)
        return out if any(a is not None for a in out) else []

    def _add(self, rows, kind, ptr_or_name, stmt, via, args=None):
        if isinstance(ptr_or_name, str):
            symbol = member = ptr_or_name
        else:
            symbol = self.names.path(ptr_or_name.index)
            member = self.names.member(ptr_or_name.index)
        row = {"kind": kind, "symbol": symbol, "member": member, "mem": stmt.mem}
        if via:
            row["via"] = via
        if args:
            row["args"] = args
        rows.append(row)

    def _expr(self, e, writing, stmt, rows, via):
        n = e.name
        a = e.args
        if n in LET_OPS:
            self._expr(a[0], True, stmt, rows, via)
            self._expr(a[1], False, stmt, rows, via)
            return
        if n in VAR_OPS:
            self._add(rows, "write" if writing else "read", a[0], stmt, via)
            return
        if n in ("Context", "ClassContext", "Context_FailSilent"):
            self._expr(a[0], False, stmt, rows, via)
            self._expr(a[4], writing, stmt, rows, via)
            return
        if n == "StructMemberContext":
            self._add(rows, "write" if writing else "read", a[0], stmt, via)
            self._expr(a[1], writing, stmt, rows, via)
            return
        if n in ("FinalFunction", "LocalFinalFunction", "CallMulticastDelegate"):
            self._add(rows, "call", a[0], stmt, via, self.compact_args(a[1]))
            for x in a[1]:
                self._expr(x, False, stmt, rows, via)
            return
        if n in ("VirtualFunction", "LocalVirtualFunction"):
            self._add(rows, "call", a[0], stmt, via, self.compact_args(a[1]))
            for x in a[1]:
                self._expr(x, False, stmt, rows, via)
            return
        if n in CAST_OPS:
            self._add(rows, "cast", a[0], stmt, via)
            self._expr(a[1], False, stmt, rows, via)
            return
        if n == "ObjectConst":
            self._add(rows, "ref", a[0], stmt, via)
            return
        if n == "StructConst":
            self._add(rows, "ref", a[0], stmt, via)
            for x in a[2]:
                self._expr(x, False, stmt, rows, via)
            return
        spec = kismet.OPCODES[e.op][1]
        for kind, x in zip(spec, e.args):
            if kind == "e":
                self._expr(x, False, stmt, rows, via)
            elif kind in "PFA":
                for y in x:
                    self._expr(y, False, stmt, rows, via)


def _flags_list(value, table):
    return [name for bit, name in table if value & bit]


def _direction(flags: int) -> str:
    if flags & F_NET_MULTICAST:
        return "multicast"
    if flags & F_NET_SERVER:
        return "server"
    if flags & F_NET_CLIENT:
        return "client"
    return "net"


def _signature(ctx, body):
    """(params, locals, ret) по цепочке Children функции."""
    params, locs, ret = [], [], None
    for b in render.children(ctx, body):
        if b.kind != "property":
            continue
        fl = b.fields.get("PropertyFlags", 0)
        name = ctx.pkg.exports[b.index].object_name
        item = {"name": name, "type": render.property_type(ctx, b)}
        if fl & render.CPF_ReturnParm:
            ret = item["type"]
        elif fl & render.CPF_Parm:
            if fl & render.CPF_OutParm:
                item["out"] = True
            params.append(item)
        else:
            locs.append(item)
    return params, locs, ret


def _variables(ctx, class_body):
    out = []
    for b in render.children(ctx, class_body):
        if b.kind != "property":
            continue
        fl = b.fields.get("PropertyFlags", 0)
        flags = _flags_list(fl, render.CPF_FLAGS)
        if b.fields.get("RepNotifyFunc", "None") != "None":
            flags.append("RepNotify=" + b.fields["RepNotifyFunc"])
        out.append({"name": ctx.pkg.exports[b.index].object_name,
                    "type": render.property_type(ctx, b), "flags": flags})
    return out


def _ubergraph_calls(ctx, body):
    """Заглушки событий: (имя убер-графа, метка), если тело вызывает его."""
    if not body.script:
        return None
    for st in body.script.statements:
        for call in walk(st):
            if call.name in ("VirtualFunction", "LocalVirtualFunction",
                             "FinalFunction", "LocalFinalFunction"):
                target = call.args[0]
                tname = target if isinstance(target, str) else ctx.short(target.index)
                params = call.args[1]
                if tname.startswith("ExecuteUbergraph") and params and params[0].name == "IntConst":
                    return tname, params[0].args[0]
    return None


def build_view(data: bytes, name: str = "") -> dict:
    pkg = Package(data, name)
    bodies = parse_all(pkg)
    ctx = render.Context(pkg, bodies)
    names = Names(pkg)
    fa = FunctionAnalyzer(ctx, names)

    view: dict = {"name": name, "size": len(data), "problems": []}

    for b in render.problems(bodies):
        # у текстур, мешей и прочих нативных объектов хвост — их собственные
        # данные, которые парсер не разбирает по замыслу, а не сбой разбора
        if b.kind == "object" and not b.error:
            continue
        e = pkg.exports[b.index]
        why = b.error or (b.script.error if b.script and not b.script.mem_ok
                          else f"не прочитано {len(b.trailing)} байт")
        view["problems"].append({"export": e.object_name, "why": why})

    view["imports"] = [
        {"index": -i - 1, "path": names.path(-i - 1), "class": imp.class_name}
        for i, imp in enumerate(pkg.imports)]
    view["exports"] = [
        {"index": i + 1, "name": e.object_name, "class": pkg.export_class(e),
         "flags": render.flag_str(e.object_flags, render.RF_FLAGS),
         "super": names.path(e.super_index) if e.super_index else None,
         "outer": pkg.obj_name(e.outer_index) if e.outer_index else None}
        for i, e in enumerate(pkg.exports)]

    # класс
    class_body = next((b for b in bodies if b.kind == "class"), None)
    cdo_export = next((i for i, e in enumerate(pkg.exports)
                       if e.object_flags & RF_ClassDefaultObject), None)
    view["class"] = None
    view["variables"] = []
    if class_body is not None:
        f = class_body.fields
        view["class"] = {
            "name": pkg.exports[class_body.index].object_name,
            "super": names.path(f["SuperStruct"].index) if f.get("SuperStruct") else None,
            "generated_by": f["ClassGeneratedBy"].name if f.get("ClassGeneratedBy") else None,
            "cdo": pkg.exports[cdo_export].object_name if cdo_export is not None else None,
            "interfaces": [names.path(i["Class"].index) for i in f.get("Interfaces", [])],
            "functions": [n for n, _ in f.get("FuncMap", [])],
        }
        view["variables"] = _variables(ctx, class_body)

    view["cdo"] = props_json(bodies[cdo_export].props, names) if cdo_export is not None else []

    # события → метки убер-графа
    events: dict = {}
    for b in bodies:
        if b.kind != "function":
            continue
        hit = _ubergraph_calls(ctx, b)
        if hit:
            events[pkg.exports[b.index].object_name] = {"ubergraph": hit[0], "entry": hit[1]}
    view["events"] = events

    # функции
    functions: dict = {}
    for b in bodies:
        if b.kind != "function":
            continue
        fname = pkg.exports[b.index].object_name
        flags = b.fields.get("FunctionFlags", 0)
        params, locs, ret = _signature(ctx, b)
        fn = {
            "index": b.index + 1,
            "flags": render.flag_str(flags, render.FUNC_FLAGS),
            "overrides": names.path(b.fields["SuperStruct"].index) if b.fields["SuperStruct"].index else None,
            "params": params, "locals": locs, "returns": ret,
            "lines": [], "xrefs": [], "problems": [],
        }
        if flags & F_NET:
            fn["net"] = {"direction": _direction(flags), "reliable": bool(flags & F_NET_RELIABLE),
                         "rep_offset": b.fields.get("RepOffset")}
        if b.error:
            fn["problems"].append(b.error)
        if b.trailing:
            fn["problems"].append(f"не прочитано {len(b.trailing)} байт")
        if b.script:
            sc = b.script
            fn["lines"] = fa.lines(sc)
            if not sc.mem_ok:
                fn["problems"].append(sc.error or "размер байткода не сошёлся")
            via: dict = {}
            if fname.startswith("ExecuteUbergraph"):
                for ev, info in events.items():
                    if info["ubergraph"] == fname:
                        for m in reachable(sc, info["entry"]):
                            via.setdefault(m, []).append(ev)
            fn["xrefs"] = fa.xrefs(sc, via)
            fn["names"] = _name_consts(sc, events, fname)
        functions[fname] = fn
    view["functions"] = functions

    view["rpc"] = _rpc(view)
    return view


def _name_consts(script, events: dict, fname: str) -> dict:
    """NameConst по инструкциям: для убер-графа — по событиям (поток от метки)."""
    def collect(mems):
        out = []
        for s in script.statements:
            if mems is not None and s.mem not in mems:
                continue
            for x in walk(s):
                if x.name == "NameConst" and x.args[0] not in out:
                    out.append(x.args[0])
        return out

    if fname.startswith("ExecuteUbergraph"):
        return {ev: collect(reachable(script, info["entry"]))
                for ev, info in events.items() if info["ubergraph"] == fname}
    return {"": collect(None)}


def _rpc(view: dict) -> dict:
    net = []
    entry_points = []
    for fname, fn in view["functions"].items():
        if "net" in fn:
            net.append({"function": fname, **fn["net"], "params": fn["params"],
                        "flags": fn["flags"]})
    for ep in SERVER_ENTRYPOINTS:
        fn = view["functions"].get(ep)
        if fn is None:
            continue
        ev = view["events"].get(ep)
        if ev:
            names = view["functions"][ev["ubergraph"]]["names"].get(ep, [])
        else:
            names = fn.get("names", {}).get("", [])
        entry_points.append({"function": ep, "overrides": fn["overrides"], "names": names})
    return {"net": net, "entry_points": entry_points}


# --- общий доступ с кешем ---------------------------------------------------

def view_for_file(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    sha = cache.sha256_bytes(data)
    v = cache.read_view(sha)
    if v is None:
        try:
            v = build_view(data, os.path.basename(path))
        except ParseError as ex:
            v = {"name": os.path.basename(path), "size": len(data), "broken": True,
                 "problems": [{"export": "<package>", "why": str(ex)}],
                 "imports": [], "exports": [], "class": None, "variables": [], "cdo": [],
                 "events": {}, "functions": {}, "rpc": {"net": [], "entry_points": []}}
        v["sha256"] = sha
        cache.write_view(sha, v)
    return v


# --- полный текстовый дамп для CLI ------------------------------------------

def render_dump(pkg: Package, bodies, ctx, sections, name_filter, export_filter, tree) -> list:
    out: list = []
    if "header" in sections:
        render.render_header(pkg, out)
    if "names" in sections:
        render.render_names(pkg, out, name_filter)
    if "imports" in sections:
        render.render_imports(pkg, out)
    if "exports" in sections:
        render.render_export_table(pkg, out)
    if "bodies" in sections:
        wanted = [w.lower() for w in export_filter]
        for b in bodies:
            nm = pkg.exports[b.index].object_name.lower()
            if not wanted or any(w in nm for w in wanted):
                render.render_body(ctx, b, out, tree)
    return out


def full_dump(data: bytes, path: str, sections, name_filter=None, export_filter=(), tree=False):
    """(текст, нераспознанные экспорты); ParseError — если пакет не читается."""
    pkg = Package(data, path)
    bodies = parse_all(pkg)
    ctx = render.Context(pkg, bodies)
    out = render_dump(pkg, bodies, ctx, sections, name_filter, export_filter, tree)
    text = "\n".join(out) + "\n"
    msgs = []
    for b in render.problems(bodies):
        e = pkg.exports[b.index]
        why = b.error or (b.script.error if b.script and not b.script.mem_ok else
                          f"не прочитано {len(b.trailing)} байт")
        msgs.append(f"экспорт [{b.index + 1}] {e.object_name}: {why}")
    return text, msgs
