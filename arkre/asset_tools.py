"""
Инструменты чтения одного ассета: summary, function, defaults, find.

Возвращают dict/строки; форматирование ответа и лимит ~20 тыс. символов — на
стороне server.py, но обрезку по строкам (`max_lines`) делаем здесь, чтобы
продолжение запрашивалось параметром `from_line`.
"""
from __future__ import annotations

import os
from typing import Optional

from uasset import render
from uasset.objects import parse_all
from uasset.package import Package

from . import analysis, assets, pdb

IMPORT_MODES = ("classes", "full", "none")


class ToolError(Exception):
    """Ошибка аргументов инструмента; текст на русском."""


def _signature(fn: dict) -> str:
    parts = [f"{'out ' if p.get('out') else ''}{p['name']}: {p['type']}" for p in fn["params"]]
    ret = f" -> {fn['returns']}" if fn.get("returns") else ""
    return f"({', '.join(parts)}){ret}"


def _view(asset: str):
    r = assets.resolve(asset)
    v = analysis.view_for_file(r.file)
    if v.get("broken"):
        why = v["problems"][0]["why"] if v["problems"] else "?"
        raise ToolError(f"{r.game_path}: пакет не читается парсером ({why}). Для ванильных "
                        f"ассетов это известное ограничение: у них другая раскладка заголовка "
                        f"пакета, чем у модов.")
    return r, v


def asset_summary(asset: str, imports: str = "classes") -> dict:
    if imports not in IMPORT_MODES:
        raise ToolError("imports: classes, full или none.")
    r, v = _view(asset)
    cls = v["class"] or {}
    labels = {}
    for ev, info in v["events"].items():
        labels[ev] = f"{info['ubergraph']} → {render.label(info['entry'])}"
    out = {
        "asset": r.game_path, "file": r.file, "workshop_id": r.workshop_id,
        "parent": cls.get("super"), "generated_class": cls.get("name"), "cdo": cls.get("cdo"),
        "interfaces": cls.get("interfaces", []),
        "functions": [
            {"name": n, "flags": f["flags"], "signature": _signature(f),
             "lines": sum(1 for ln in f["lines"] if ln["off"] is not None),
             **({"net": f["net"]} if "net" in f else {})}
            for n, f in v["functions"].items()],
        "events": [f"{ev}: {lab}" for ev, lab in labels.items()],
        "variables": v["variables"],
        "problems": v["problems"],
    }
    if imports != "none":
        keep = (analysis.CLASS_IMPORTS if imports == "classes" else None)
        out["imports"] = sorted({i["path"] for i in v["imports"]
                                 if keep is None or i["class"] in keep})
    return out


def _reparse(file: str):
    with open(file, "rb") as fh:
        data = fh.read()
    pkg = Package(data, os.path.basename(file))
    bodies = parse_all(pkg)
    return pkg, bodies, render.Context(pkg, bodies)


def _label_addr(text: str) -> Optional[int]:
    if text.startswith("L_") and text.endswith(":"):
        try:
            return int(text[2:-1], 16)
        except ValueError:
            return None
    return None


def _with_native(calls: list) -> list:
    """BP → натив: для вызовов /Script/… добавляет символ из PDB, если он уже импортирован."""
    for c in calls:
        native = pdb.native_for(c["target"])
        if native:
            c["native"] = native
    return calls


def asset_function(asset: str, function: str, entry: Optional[str] = None,
                   from_line: int = 0, max_lines: int = 400, tree: bool = False) -> dict:
    r, v = _view(asset)
    funcs = v["functions"]
    # имя события-заглушки вместо убер-графа: ведём к метке события
    if entry is None and function in v["events"] and function in funcs \
            and len(funcs[function]["lines"]) <= 6:
        function, entry = v["events"][function]["ubergraph"], function
    fn = funcs.get(function)
    if fn is None:
        close = [n for n in funcs if function.lower() in n.lower()][:10]
        hint = f" Похожие: {', '.join(close)}." if close else f" Есть: {', '.join(list(funcs)[:20])}."
        raise ToolError(f"В {r.game_path} нет функции {function}.{hint}")
    entry_info = None
    if entry is not None:
        entry_info = v["events"].get(entry)
        if entry_info is None or entry_info["ubergraph"] != function:
            known = [e for e, i in v["events"].items() if i["ubergraph"] == function]
            raise ToolError(f"{function}: нет события {entry}. Есть: {', '.join(known) or 'нет'}.")
    elif function.startswith("ExecuteUbergraph") and len(fn["lines"]) > max_lines:
        known = [e for e, i in v["events"].items() if i["ubergraph"] == function]
        raise ToolError(f"{function} — убер-граф на {len(fn['lines'])} строк; укажи entry — "
                        f"имя события: {', '.join(known)}.")

    lines = fn["lines"]
    reach = None
    if tree or entry_info:
        pkg, bodies, ctx = _reparse(r.file)
        body = next(b for b in bodies if b.kind == "function"
                    and pkg.exports[b.index].object_name == function)
        if entry_info:
            reach = analysis.reachable(body.script, entry_info["entry"])
            lines = [ln for ln in lines
                     if (ln["off"] in reach) or (_label_addr(ln["text"]) in reach)
                     or ln["off"] is None and ln["label"] is None and ln["text"].startswith("//")]
    texts = []
    if tree:
        # дерево узлов — отдельным рендером того же ассета
        out: list = []
        render.render_script(ctx, body.script, out, "", tree=True)
        texts = out
    else:
        texts = [ln["text"] for ln in lines]
    total = len(texts)
    page = texts[from_line:from_line + max_lines]
    xrefs = [x for x in fn["xrefs"] if reach is None or x["mem"] in reach]
    calls: dict = {}
    for x in xrefs:
        if x["kind"] == "call":
            virtual = "." not in x["symbol"] and ":" not in x["symbol"]
            c = calls.setdefault(x["symbol"], {"target": x["symbol"], "count": 0,
                                               "kind": "virtual" if virtual else "final"})
            c["count"] += 1
    meta = {
        "asset": r.game_path, "function": function,
        **({"entry": entry} if entry else {}),
        "flags": fn["flags"], "overrides": fn["overrides"],
        "signature": _signature(fn), "params": fn["params"], "locals": fn["locals"],
        "calls": _with_native(sorted(calls.values(), key=lambda c: c["target"])),
        "reads": sorted({x["symbol"] for x in xrefs if x["kind"] == "read"}),
        "writes": sorted({x["symbol"] for x in xrefs if x["kind"] == "write"}),
        "problems": fn["problems"],
        "total_lines": total, "from_line": from_line,
        "truncated": from_line + max_lines < total,
    }
    if meta["truncated"]:
        meta["next_from_line"] = from_line + max_lines
    return {"meta": meta, "code": "\n".join(page)}


def _flatten_defaults(props: list, wanted: Optional[set]) -> list:
    return [p for p in props if wanted is None or p["name"] in wanted]


def asset_defaults(asset: str, props: Optional[list] = None, resolve_parents: bool = False) -> dict:
    r, v = _view(asset)
    wanted = set(props) if props else None
    merged: dict = {}
    chain = []
    cur_view, cur_path = v, r.game_path
    seen = set()
    native_parent = None
    while True:
        chain.append(cur_path)
        for p in _flatten_defaults(cur_view["cdo"], wanted):
            key = (p["name"], p.get("index", 0))
            if key not in merged:
                merged[key] = {**p, "from": cur_path}
        if not resolve_parents:
            break
        sup = (cur_view.get("class") or {}).get("super")
        if not sup:
            break
        if sup.startswith("/Script/"):
            native_parent = sup
            break
        base = sup.split(".")[0]
        if base in seen:
            break
        seen.add(base)
        try:
            pr = assets.resolve(base)
        except assets.AssetError:
            native_parent = f"{sup} (ассет не найден)"
            break
        parent = analysis.view_for_file(pr.file)
        if parent.get("broken"):
            native_parent = f"{sup} (пакет родителя не читается парсером: {parent['problems'][0]['why']})"
            break
        cur_view, cur_path = parent, pr.game_path
    out = {"asset": r.game_path, "class": (v["class"] or {}).get("name"),
           "properties": list(merged.values())}
    if resolve_parents:
        out["chain"] = chain
        if native_parent:
            out["native_parent"] = native_parent
            out["note"] = ("native default — нет в ассете: значения родителя "
                           f"{native_parent} в этом .uasset не хранятся или не прочитаны")
    return out


def asset_find(name: str, scope: str = "all", limit: int = 50) -> dict:
    try:
        hits = assets.find(name, scope, limit + 1)
    except assets.AssetError:
        raise
    items = [{"game_path": h.game_path, "file": h.file, "workshop_id": h.workshop_id}
             for h in hits[:limit]]
    return {"total_shown": len(items), "truncated": len(hits) > limit, "items": items}
