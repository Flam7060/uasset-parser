"""
Статический аудит мода: кодированные антипаттерны серверных RPC.

Каждый детектор — запрос по `AssetView` (xrefs + строки псевдокода), который
повторяет приём, которым баги ищутся руками (см. отчёты в docs/mods). Это не
доказательство эксплойта, а ранжированный список мест для ручной проверки:
детекторы сознательно переусердствуют (ловят с запасом), ложные срабатывания
отсеиваются глазами по приложенной строке.

Детекторы:
    D1 untrusted-object — серверная функция мутирует объект, пришедший от клиента
                          (ExecParams.ObjParam*), без проверки владельца/команды/дистанции;
    D2 dupe-give-take   — в одной функции и списание (RemoveItem), и выдача, а булев
                          результат списания не уходит в ветку до выдачи;
    D3 ui-only-limit    — лимит/настройка читается, но не на серверном пути (только UI);
    D4 unclamped-rpc    — число от клиента (Int/FloatParam*) втекает в количество/смещение
                          без Clamp/Min/Max;
    D6 trust-boundary   — проверка построена на поле идентичности, которое атакующий
                          двигает ванильной механикой (ноглин, смена трайба, криопод…).

D6 — про стык блюпринтов: сам мод корректен, ломает его внешнее воздействие.
Реестр изменяемых полей — `IDENTITY_FIELDS`, пополняется.
"""
from __future__ import annotations

import re
from typing import Optional

from . import analysis, security

# --- словари приёмников и защит --------------------------------------------

GIVE_SINKS = {
    "AddItemObject", "AddItemObjectEx", "AddNewItem", "SP_FastGiveItem",
    "BPIncrementItemTemplateQuantity", "AddItemToInventory",
}
TAKE_SINK = "RemoveItem"
# всё, что меняет состояние и опасно звать на чужом объекте.
# Голый K2_DestroyActor намеренно не тут: в баффах это почти всегда самоуничтожение
# (RemoveBuff) — шум; K2_DestroyComponent на чужом объекте оставлен (реальный вектор).
MUTATING_SINKS = GIVE_SINKS | {
    "RemoveItem", "IncrementItemQuantity", "SetQuantity", "IncrementStructureItemCount",
    "StaticAddBuff", "K2_DestroyComponent",
}
# приёмники количества/смещения — для D4
QTY_SINKS = GIVE_SINKS | {
    "IncrementItemQuantity", "SetQuantity", "IncrementStructureItemCount",
    "K2_SetTimer", "UpdatePosition", "UpdateRotation", "SetTimerByFunctionName",
}
# вызовы, выдающие проверку доступа/владельца/дистанции/команды — гасят D1
GUARD_CALLS = {
    "GetDistanceTo", "GetDistanceToActor", "IsAlliedTo", "IsAllied", "IsInRange",
    "SP_GetAccessLevel", "HasOwner", "IsOwner", "GetTribeID", "CanAccess",
    "IsAllowedToBuild", "BPAllowCrafting", "HasEngram", "IsValidToClone",
}
CLAMP_CALLS = {"Clamp", "FClamp", "ClampInt", "ClampFloat", "Min", "FMin", "Max", "FMax"}

RPC_OBJ = re.compile(r"ExecParams\.ObjParam\d")
RPC_NUM = re.compile(r"ExecParams\.(Int|Float)Param\d")
IF_NOT = re.compile(r"if \(!(.+?)\)")

# D6: поле идентичности → каким ванильным механизмом его можно сдвинуть.
# Если мод строит на таком поле решение о доступе — это кандидат на баг стыка.
IDENTITY_FIELDS = {
    "TargetingTeam": "команда — ноглин (контроль), смена трайба, передача",
    "GetTargetingTeam": "команда — ноглин, смена трайба",
    "TamingTeamID": "команда прирученного — передача/подчинение",
    "TargetingTeamName": "команда — смена трайба",
    "GetController": "контроллер — ноглин (mind control), возможение",
    "GetOwnerController": "контроллер — ноглин, возможение",
    "GetInstigatorController": "инстигатор — ноглин, возможение",
    "GetOwningPlayer": "владелец — ноглин, возможение",
    "OwningPlayerID": "владелец — криопод (переимпринт), загрузка/выгрузка",
    "OwnerPlayerDataID": "владелец — криопод, загрузка/выгрузка",
    "OwningPlayerName": "владелец — криопод, загрузка",
    "TribeID": "трайб — смена/роспуск трайба",
    "LinkedPlayerID": "привязка игрока — передача/перелинковка",
    "ImprinterPlayerDataID": "импринт — криопод, переимпринт",
}
IDENTITY_READS = set(IDENTITY_FIELDS)
COMPARE = re.compile(r"(EqualEqual|NotEqual)_")
ACCESS_FN = re.compile(r"(Allow|Access|CanDo|CanUse|IsAllowed|Permission|Gate)", re.I)
# D3: имена ключей-ограничителей, которые сервер обязан соблюдать
# (а не размеры инвентаря/кулдауны — те применяются на самой структуре).
LIMIT_KEY = re.compile(r"(Max|Limit|Disable|Prevent|Restrict|Respect|Obey)", re.I)

SEVERITY = {"D1": "высокая", "D2": "высокая", "D3": "средняя", "D4": "низкая", "D6": "средняя"}
SEV_RANK = {"высокая": 0, "средняя": 1, "низкая": 2}
DETECTORS = ("D1", "D2", "D3", "D4", "D6")


# --- вспомогательное -------------------------------------------------------

def _fn_lines(fn: dict) -> list[str]:
    return [ln["text"] for ln in fn["lines"] if ln["off"] is not None]


def _calls(fn: dict) -> list[dict]:
    return [x for x in fn["xrefs"] if x["kind"] == "call"]


def _call_members(fn: dict) -> set:
    return {x["member"] for x in _calls(fn)}


def _branch_tokens(lines: list[str]) -> str:
    """Склейка всех условий `if (!…)` — для проверки, используется ли переменная в ветке."""
    return " ".join(m.group(1) for ln in lines for m in IF_NOT.finditer(ln))


# --- детекторы -------------------------------------------------------------

def d1_untrusted_object(ma, fn, rel, fname) -> Optional[dict]:
    """Мутация объекта из ExecParams.ObjParam* без проверки владельца/команды/дистанции."""
    lines = _fn_lines(fn)
    if not any(RPC_OBJ.search(ln) for ln in lines):
        return None
    members = _call_members(fn)
    sinks = sorted(members & MUTATING_SINKS)
    if not sinks:
        return None
    # защита: явный вызов-гард или сравнение команды/дистанции в тексте
    if members & GUARD_CALLS or any("TargetingTeam" in ln or "GetDistanceTo" in ln for ln in lines):
        return None
    sink_line = next((ma.line(rel, fname, x["mem"]) for x in _calls(fn) if x["member"] in sinks), "")
    return {"detail": f"объект из ExecParams.ObjParam* уходит в {', '.join(sinks)} без проверки "
                      f"владельца/команды/дистанции", "line": sink_line}


def d2_dupe_give_take(ma, fn, rel, fname) -> Optional[dict]:
    """RemoveItem + выдача, результат списания не проверяется ветвлением."""
    members = _call_members(fn)
    if TAKE_SINK not in members or not (members & GIVE_SINKS):
        return None
    lines = _fn_lines(fn)
    branches = _branch_tokens(lines)
    unchecked = []
    for ln in lines:
        if not re.search(r"[.\s]RemoveItem\(", ln):
            continue
        m = re.search(r"(\w*RemoveItem\w*_ReturnValue\w*)\s*=", ln)
        if not m:                           # результат отброшен
            unchecked.append(ln.strip())
        elif m.group(1) not in branches:    # результат есть, но не в ветке
            unchecked.append(ln.strip())
    if not unchecked:
        return None
    return {"detail": f"списание и выдача ({', '.join(sorted(members & GIVE_SINKS))}) в одной "
                      f"функции, результат RemoveItem не проверяется",
            "line": unchecked[0]}


def d3_ui_only_limits(ma) -> list[dict]:
    """Лимиты/настройки, читаемые только вне серверного пути (из ini_keys)."""
    out = []
    data = security.ini_keys(ma.mod.workshop_id)
    for row in data["keys"]:
        if row.get("server_consumed") or not LIMIT_KEY.search(str(row["key"])):
            continue
        site = row["sites"][0] if row["sites"] else {}
        out.append({"detector": "D3", "severity": SEVERITY["D3"],
                    "asset": site.get("asset", ""), "function": site.get("function", ""),
                    "symbol": f"[{row['section']}] {row['key']}", "server_reachable": False,
                    "detail": f"ключ `{row['key']}` читается ({row['getter']}), но не доходит до "
                              f"серверного пути — ограничение живёт только в UI",
                    "line": site.get("line", "")})
    return out


def d4_unclamped_rpc(ma, fn, rel, fname) -> Optional[dict]:
    """Число от клиента (Int/FloatParam*) в приёмнике количества/смещения без клампа."""
    lines = _fn_lines(fn)
    if not any(RPC_NUM.search(ln) for ln in lines):
        return None
    members = _call_members(fn)
    sinks = sorted(members & QTY_SINKS)
    if not sinks:
        return None
    if members & CLAMP_CALLS:
        return None
    num_line = next((ln.strip() for ln in lines if RPC_NUM.search(ln)), "")
    return {"detail": f"число из ExecParams.*Param* втекает в {', '.join(sinks)} без Clamp/Min/Max",
            "line": num_line}


def d6_trust_boundary(ma, fn, rel, fname) -> list[dict]:
    """Решение, построенное на поле идентичности, изменяемом ванильной механикой.

    Сигнал высокой точности: поле идентичности участвует в сравнении
    (EqualEqual/NotEqual в той же строке) — то есть на нём строят ветвление —
    либо функция явно именована как проверка доступа. Иначе чтение поля
    (GetController и т.п.) слишком обыденно, чтобы флагать.
    """
    fields = {x["member"] for x in fn["xrefs"]
              if x["member"] in IDENTITY_READS and x["kind"] in ("read", "call")}
    if not fields:
        return None
    lines = _fn_lines(fn)
    access_named = bool(ACCESS_FN.search(fname))
    out = []
    for field in sorted(fields):
        in_compare = any(COMPARE.search(ln) and field in ln for ln in lines)
        if not (in_compare or access_named):
            continue
        line = next((ln.strip() for ln in lines if COMPARE.search(ln) and field in ln),
                    ma.line(rel, fname, next(x["mem"] for x in fn["xrefs"] if x["member"] == field)))
        out.append({"detail": f"решение опирается на `{field}` ({IDENTITY_FIELDS[field]})",
                    "symbol": field, "line": line})
    return out


# --- оркестратор -----------------------------------------------------------

def _server_funcs(ma, graph) -> set:
    """(rel, fname), исполняемые на серверном пути: достижимые + события убер-графов."""
    reach = graph.reach(graph.server_roots, skip_widgets=True)
    out = set(reach)
    for rel, it in ma.items.items():
        events = it["view"].get("events", {})
        ubers = {info["ubergraph"] for info in events.values()}
        for uber in ubers:
            if any((rel, ev) in reach for ev, info in events.items() if info["ubergraph"] == uber):
                out.add((rel, uber))
    return out


def audit(workshop_id: str, detectors: Optional[list] = None, limit: int = 200) -> dict:
    """Прогоняет детекторы по всем не-виджет функциям мода. Ранжирует находки."""
    want = set(detectors or DETECTORS)
    bad = want - set(DETECTORS)
    if bad:
        raise ValueError(f"detectors: допустимо {', '.join(DETECTORS)}; получено {', '.join(bad)}")
    ma = security.ModAssets(workshop_id)
    graph = security.CallGraph(ma)
    server = _server_funcs(ma, graph)

    per_fn = [("D1", d1_untrusted_object), ("D2", d2_dupe_give_take), ("D4", d4_unclamped_rpc)]
    findings: list[dict] = []

    if "D3" in want:
        findings += d3_ui_only_limits(ma)

    for rel, it in ma.items.items():
        if it["widget"]:                     # клиентский UI — пропускаем шумные детекторы
            continue
        for fname, fn in it["view"]["functions"].items():
            if not fn.get("xrefs"):
                continue
            srv = (rel, fname) in server
            for code, det in per_fn:
                if code not in want:
                    continue
                hit = det(ma, fn, rel, fname)
                if hit:
                    findings.append({"detector": code, "severity": SEVERITY[code],
                                     "asset": ma.label(rel), "function": fname,
                                     "server_reachable": srv, **hit})
            if "D6" in want:
                for hit in (d6_trust_boundary(ma, fn, rel, fname) or []):
                    findings.append({"detector": "D6", "severity": SEVERITY["D6"],
                                     "asset": ma.label(rel), "function": fname,
                                     "server_reachable": srv, **hit})

    # ранг: серверно достижимые выше, потом по серьёзности, потом по детектору
    findings.sort(key=lambda f: (not f.get("server_reachable", False),
                                 SEV_RANK[f["severity"]], f["detector"], f["asset"]))
    counts: dict = {}
    for f in findings:
        counts[f["detector"]] = counts.get(f["detector"], 0) + 1
    out = {"workshop_id": str(workshop_id), "name": ma.mod.name, "total": len(findings),
           "by_detector": counts, "findings": findings[:limit]}
    if len(findings) > limit:
        out["truncated"] = True
    return out
