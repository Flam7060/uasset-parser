"""
Безопасность модов: сетевые RPC и ключи ini.

`mod_net_rpcs` — функции с флагом Net, переопределения серверных точек входа
ARK и места, где клиентский код мода их вызывает.

`mod_ini_keys` — ключи конфигов, которые мод читает, с контекстом чтения:
    server_rpc  функция — серверная RPC либо достижима из неё / из серверной
                точки входа внутри мода по графу вызовов;
    widget      класс ассета — UserWidget (код только на клиенте);
    client      NetClient/NetMulticast либо достижима только из них;
    unknown     ничего из перечисленного.
Приоритет: server_rpc > widget > client > unknown.
Кроме места чтения ключа считаются `consumers`: читатели переменных, куда
функция положила значение (цепочка присваиваний в той же функции), и по ним —
`server_consumed`: лимит доходит до серверного пути или остаётся только в UI. Пара server_rpc/widget
ловит лимиты, которые проверяются только в UI (PulseMax в S+).

Графы вызовов строятся по xrefs AssetView; виртуальные вызовы в байткоде
несут голое имя без класса: такой вызов разрешается в функцию того же ассета,
а в чужой — только если имя в моде единственное. Классы-виджеты из серверной
достижимости исключены.
"""
from __future__ import annotations

import os
from typing import Optional

from . import analysis, assets, xref

# Геттеры ini у ShooterGameMode: имя → индексы аргументов (секция, ключ, умолчание).
# Список снят по реальным вызовам S+ и ArkFun (шаг 2 плана); пополняется.
INI_GETTERS = {
    "GetBoolOptionIni": {"section": 0, "key": 1, "default": 2},
    "GetIntOptionIni": {"section": 0, "key": 1, "default": 2},
    "GetFloatOptionIni": {"section": 0, "key": 1, "default": 2},
    "GetStringOption": {"section": 0, "key": 1, "default": 2},
}

CONTEXT_ORDER = ("server_rpc", "widget", "client", "unknown")


class ModAssets:
    """Все ассеты мода с их AssetView и индексами, нужными обоим инструментам."""

    def __init__(self, workshop_id: str):
        self.mod = assets.get_mod(workshop_id)
        self.rel_by_game: dict = {}
        self.items: dict = {}           # rel → {"view", "file", "widget"}
        for rel in assets.mod_files(self.mod):
            file = os.path.join(self.mod.dir, rel)
            view = analysis.view_for_file(file)
            self.items[rel] = {"view": view, "file": file, "widget": self._is_widget(view)}
            self.rel_by_game[assets.game_path(self.mod, rel)] = rel
        self._chain_cache: dict = {}

    def _is_widget(self, view: dict, depth: int = 0) -> bool:
        if any(e["class"] == "WidgetBlueprintGeneratedClass" for e in view["exports"]):
            return True
        sup = (view.get("class") or {}).get("super") or ""
        if "UserWidget" in sup:
            return True
        if sup.startswith("/Game/") and depth < 10:
            try:
                parent = analysis.view_for_file(assets.resolve(sup.split(".")[0]).file)
            except assets.AssetError:
                return False
            return self._is_widget(parent, depth + 1)
        return False

    def label(self, rel: str) -> str:
        return f"{self.mod.workshop_id}:{rel}"

    def line(self, rel: str, function: str, mem: int) -> str:
        fn = self.items[rel]["view"]["functions"].get(function)
        if fn:
            for ln in fn["lines"]:
                if ln["off"] == mem:
                    return ln["text"].strip()
        return ""


# --- сетевые RPC -----------------------------------------------------------

def net_rpcs(workshop_id: str) -> dict:
    ma = ModAssets(workshop_id)
    entry_names = set(analysis.SERVER_ENTRYPOINTS)
    out_assets = []
    callable_names: set = set()
    for rel, it in ma.items.items():
        rpc = it["view"]["rpc"]
        for r in rpc["net"]:
            callable_names.add(r["function"])
        for e in rpc["entry_points"]:
            callable_names.add(e["function"])
    # где мод вызывает эти RPC
    callers: dict = {}
    for rel, it in ma.items.items():
        for fname, fn in it["view"]["functions"].items():
            for x in fn["xrefs"]:
                if x["kind"] == "call" and x["member"] in callable_names:
                    callers.setdefault(x["member"], []).append(
                        {"asset": ma.label(rel), "function": (x.get("via") or [fname])[0],
                         "client_side": it["widget"] or None,
                         "line": ma.line(rel, fname, x["mem"])})
    for rel, it in ma.items.items():
        rpc = it["view"]["rpc"]
        if not rpc["net"] and not rpc["entry_points"]:
            continue
        names = [r["function"] for r in rpc["net"]] + [e["function"] for e in rpc["entry_points"]]
        row = {"asset": ma.label(rel), "net": rpc["net"], "entry_points": rpc["entry_points"],
               "client_callers": {n: [c for c in callers.get(n, []) if c["asset"] != ma.label(rel)
                                      or c["function"] != n] for n in names}}
        problems = it["view"]["problems"]
        if problems:
            row["problems"] = problems
        out_assets.append(row)
    return {"workshop_id": str(workshop_id), "entry_points_known": sorted(entry_names),
            "assets": out_assets}


# --- ключи ini -------------------------------------------------------------

class CallGraph:
    """Граф «ассет, функция/событие» → вызываемые, по xrefs мода."""

    def __init__(self, ma: ModAssets):
        self.ma = ma
        self.edges: dict = {}
        self.server_roots: set = set()
        self.client_roots: set = set()
        by_name: dict = {}
        for rel, it in ma.items.items():
            for fname in it["view"]["functions"]:
                by_name.setdefault(fname, []).append(rel)
        for rel, it in ma.items.items():
            view = it["view"]
            for fname, fn in view["functions"].items():
                node = (rel, fname)
                net = fn.get("net")
                if net and net["direction"] == "server" or fname in analysis.SERVER_ENTRYPOINTS:
                    self.server_roots.add(node)
                if net and net["direction"] in ("client", "multicast"):
                    self.client_roots.add(node)
                for x in fn["xrefs"]:
                    if x["kind"] != "call":
                        continue
                    owners = x.get("via") or [fname]
                    # заглушка события → убер-граф: событие и есть сам вход
                    if x["member"].startswith("ExecuteUbergraph"):
                        continue
                    for target in self._targets(rel, x, by_name):
                        for owner in owners:
                            self.edges.setdefault((rel, owner), set()).add(target)

    def _targets(self, rel: str, x: dict, by_name: dict):
        sym, member = x["symbol"], x["member"]
        if sym.startswith("self:"):
            yield (rel, member)
        elif sym.startswith("/Game/"):
            pkg = sym.split(".")[0]
            target_rel = self.ma.rel_by_game.get(pkg)
            if target_rel:
                yield (target_rel, member)
        elif "." not in sym and ":" not in sym:
            # голое имя: своя функция ассета; чужая — только если имя в моде единственное
            # (иначе `Initialize` и подобные склеили бы весь мод в одну компоненту)
            if member in self.ma.items[rel]["view"]["functions"]:
                yield (rel, member)
            elif len(by_name.get(member, ())) == 1:
                yield (by_name[member][0], member)

    def reach(self, roots: set, skip_widgets: bool = False) -> set:
        """Достижимые узлы. `skip_widgets`: в классы-виджеты не заходим — виджет
        исполняется только на клиенте, серверный путь в него попасть не может."""
        seen, todo = set(roots), list(roots)
        while todo:
            for nxt in self.edges.get(todo.pop(), ()):
                if skip_widgets and self.ma.items[nxt[0]]["widget"]:
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen


def _find_wrappers(ma: ModAssets) -> dict:
    """Функции мода, прокидывающие параметр в геттер ini: имя → описание."""
    wrappers: dict = {}
    for _ in range(6):
        grew = False
        for rel, it in ma.items.items():
            for fname, fn in it["view"]["functions"].items():
                if fname in wrappers:
                    continue
                params = [p["name"] for p in fn["params"]]
                for x in fn["xrefs"]:
                    if x["kind"] != "call" or not x.get("args"):
                        continue
                    spec = INI_GETTERS.get(x["member"])
                    inner = wrappers.get(x["member"])
                    if spec:
                        key_i, sect_i = spec["key"], spec["section"]
                        getter, sect_const = x["member"], _const(x["args"], sect_i)
                    elif inner:
                        key_i, sect_i = inner["key_arg"], None
                        getter, sect_const = inner["getter"], inner["section"]
                    else:
                        continue
                    arg = x["args"][key_i] if key_i < len(x["args"]) else None
                    if arg and "v" in arg and arg["v"] in params:
                        wrappers[fname] = {"key_arg": params.index(arg["v"]), "getter": getter,
                                           "section": sect_const}
                        grew = True
                        break
        if not grew:
            break
    return wrappers


def _const(args: list, i: int):
    a = args[i] if i < len(args) else None
    if a:
        return a.get("s") if "s" in a else a.get("n") if "n" in a else a.get("c")
    return None


def ini_keys(workshop_id: str) -> dict:
    ma = ModAssets(workshop_id)
    graph = CallGraph(ma)
    server = graph.reach(graph.server_roots, skip_widgets=True)
    client = graph.reach(graph.client_roots)
    wrappers = _find_wrappers(ma)

    keys: dict = {}
    unresolved = []

    def context(rel: str, owner: str) -> str:
        node = (rel, owner)
        if node in server:
            return "server_rpc"
        if ma.items[rel]["widget"]:
            return "widget"
        if node in client:
            return "client"
        return "unknown"

    # чтения переменных по всему моду: куда уходит значение ключа
    reads: dict = {}
    for rel, it in ma.items.items():
        for fname, fn in it["view"]["functions"].items():
            for x in fn["xrefs"]:
                if x["kind"] == "read":
                    reads.setdefault(x["member"], []).append((rel, (x.get("via") or [fname])[0]))

    def consumers(rel: str, fname: str, args: list) -> list:
        """Читатели переменной, в которую функция чтения положила значение."""
        copies = ma.items[rel]["view"]["functions"][fname].get("copies", [])
        names = {a["v"] for a in args if a and "v" in a}
        dst: set = set()
        todo = list(names)
        while todo:
            n = todo.pop()
            for c in copies:
                if c["src"] == n and c["dst"] not in dst:
                    dst.add(c["dst"])
                    todo.append(c["dst"])
        out = []
        for var in sorted(dst):
            for crel, owner in reads.get(var, ()):
                item = {"variable": var, "asset": ma.label(crel), "function": owner,
                        "context": context(crel, owner)}
                if item not in out and not (crel == rel and owner == fname):
                    out.append(item)
        return out

    for rel, it in ma.items.items():
        for fname, fn in it["view"]["functions"].items():
            for x in fn["xrefs"]:
                if x["kind"] != "call" or not x.get("args"):
                    continue
                member = x["member"]
                if member in INI_GETTERS and fname not in wrappers:
                    spec = INI_GETTERS[member]
                    getter, key_i = member, spec["key"]
                    section = _const(x["args"], spec["section"])
                    default = _const(x["args"], spec["default"])
                elif member in wrappers and fname != member:
                    w = wrappers[member]
                    getter, key_i, section, default = w["getter"], w["key_arg"], w["section"], None
                else:
                    continue
                key = _const(x["args"], key_i)
                owner = (x.get("via") or [fname])[0]
                site = {"asset": ma.label(rel), "function": owner,
                        "context": context(rel, owner), "line": ma.line(rel, fname, x["mem"])}
                if key is None:
                    unresolved.append(site)
                    continue
                row = keys.setdefault((section, key, getter), {
                    "section": section, "key": key, "getter": getter, "default": default,
                    "sites": []})
                if row["default"] is None:
                    row["default"] = default
                for c in consumers(rel, fname, x["args"]):
                    row.setdefault("consumers", [])
                    if c not in row["consumers"]:
                        row["consumers"].append(c)
                if not any(s["asset"] == site["asset"] and s["function"] == site["function"]
                           for s in row["sites"]):
                    row["sites"].append(site)
    rows = sorted(keys.values(), key=lambda r: (str(r["section"]), r["key"]))
    for r in rows:
        ctxs = {s["context"] for s in r["sites"]}
        r["contexts"] = [c for c in CONTEXT_ORDER if c in ctxs]
        # значение проверяется на сервере, если его читают на серверном пути
        r["server_consumed"] = "server_rpc" in ctxs or any(
            c["context"] == "server_rpc" for c in r.get("consumers", []))
    return {"workshop_id": str(workshop_id), "total": len(rows), "keys": rows,
            "wrappers": sorted(wrappers), "unresolved_sites": unresolved}
