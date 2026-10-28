"""
Сканер установленных модов и статусы отчётов.

Индекс `.cache/index.json` хранит только вычисляемое: текущие хеши ассетов
(с size+mtime, чтобы не хешировать неизменные файлы). Baseline — что покрыто
отчётом — лежит во frontmatter отчёта и от удаления кеша не страдает.

Статус мода (раздел 6 спеки):
    missing     нет документа draft/done с этим id
    unreviewed  документ есть, covered_assets для id пуст
    fresh       все покрытые ассеты на месте с теми же хешами
    stale       хотя бы один покрытый ассет изменился или исчез
`drifted` — mod_hash изменился, а покрытые ассеты нет (мод обновили мимо отчёта).

Если документов несколько: stale, если stale любой; иначе fresh, если fresh
любой; иначе unreviewed.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from . import analysis, assets, cache, config, reports


class ModError(Exception):
    """Ошибка работы с модами/отчётами; текст на русском."""


def hash12(sha: str) -> str:
    return sha[:12]


def compute_mod_hash(files: dict) -> str:
    lines = sorted(f"{rel}:{info['sha256']}" for rel, info in files.items())
    return hash12(hashlib.sha256("\n".join(lines).encode()).hexdigest())


def _hash_assets(mod: assets.Mod, previous: dict) -> dict:
    out = {}
    for rel in assets.mod_files(mod):
        full = os.path.join(mod.dir, rel)
        try:
            st = os.stat(full)
        except OSError:
            continue
        prev = previous.get(rel)
        if prev and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns:
            out[rel] = prev
            continue
        out[rel] = {"sha256": cache.sha256_file(full), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    return out


def _entry(mod: assets.Mod, prev: dict) -> dict:
    files = _hash_assets(mod, prev.get("assets", {}))
    return {"name": mod.name, "internal": mod.internal, "mod_hash": compute_mod_hash(files),
            "assets": files}


def scan(mods_dir: Optional[str] = None) -> dict:
    """Обходит Content/Mods, обновляет index.json. Возвращает сводку."""
    mods = assets.list_mods(mods_dir)
    if not mods and not os.path.isdir(mods_dir or config.mods_dir()):
        raise ModError(f"Нет каталога модов {mods_dir or config.mods_dir()}. "
                       f"Задай ARK_MODS_DIR или ARK_GAME_DIR.")
    idx = cache.load_index()
    old = idx.get("mods", {})
    new: dict = {}
    changed, added = [], []
    for mod in mods:
        prev = old.get(mod.workshop_id, {})
        e = _entry(mod, prev)
        new[mod.workshop_id] = e
        if not prev:
            added.append(mod.workshop_id)
        elif prev.get("mod_hash") != e["mod_hash"]:
            changed.append(mod.workshop_id)
    removed = sorted(set(old) - set(new))
    idx["mods"] = new
    idx["version"] = 2
    cache.save_index(idx)
    counts: dict = {}
    for wid in new:
        st = mod_status(wid)["status"]
        counts[st] = counts.get(st, 0) + 1
    return {"total": len(new), "by_status": counts, "changed": changed, "new": added,
            "removed": removed, "index": cache.index_path()}


def _current(workshop_id: str) -> dict:
    """Свежие хеши одного мода (stat-проверка, быстро) + запись в индекс."""
    mod = assets.get_mod(workshop_id)
    idx = cache.load_index()
    prev = idx.get("mods", {}).get(mod.workshop_id, {})
    e = _entry(mod, prev)
    if e != prev:
        idx.setdefault("mods", {})[mod.workshop_id] = e
        cache.save_index(idx)
    return e


def docs_for(workshop_id: str) -> list[reports.Report]:
    return [r for r in reports.list_reports() if str(workshop_id) in r.workshop_ids]


def _doc_state(rep: reports.Report, workshop_id: str, files: dict, mod_hash: str) -> dict:
    covered = rep.covered(workshop_id)
    row = {"report": rep.name, "doc_status": rep.status, "reviewed": rep.meta.get("reviewed"),
           "title": rep.title}
    if not covered:
        row.update(state="unreviewed", changed=[])
        return row
    changed = [rel for rel, h in covered.items()
               if rel not in files or hash12(files[rel]["sha256"]) != str(h)]
    row["changed"] = changed
    row["state"] = "stale" if changed else "fresh"
    if not changed:
        stored = rep.mod_hash(workshop_id)
        row["drifted"] = bool(stored and stored != mod_hash)
    return row


def mod_status(workshop_id: str) -> dict:
    wid = str(workshop_id)
    mod = assets.get_mod(wid)
    e = _current(wid)
    docs = [_doc_state(r, wid, e["assets"], e["mod_hash"]) for r in docs_for(wid)]
    counted = [d for d in docs if d["doc_status"] in reports.STATUS_REVIEWED]
    states = {d["state"] for d in counted}
    if not counted:
        status = "missing"
    elif "stale" in states:
        status = "stale"
    elif "fresh" in states:
        status = "fresh"
    else:
        status = "unreviewed"
    drifted = any(d.get("drifted") for d in counted if d["state"] == "fresh")
    changed = sorted({rel for d in counted if d["state"] == "stale" for rel in d["changed"]})
    dates = [str(d["reviewed"]) for d in counted if d.get("reviewed")]
    return {
        "workshop_id": wid, "name": mod.name, "game_path": f"/Game/Mods/{mod.internal or wid}",
        "assets": len(e["assets"]), "status": status, "drifted": drifted,
        "docs": docs, "changed_assets": changed,
        "last_reviewed": max(dates) if dates else None, "mod_hash": e["mod_hash"],
    }


def mods_list(status: Optional[str] = None, query: Optional[str] = None,
              limit: int = 50, offset: int = 0) -> dict:
    idx = cache.load_index()
    if not idx.get("mods"):
        scan()
        idx = cache.load_index()
    rows = []
    q = (query or "").lower()
    for mod in assets.list_mods():
        if q and q not in (mod.name or "").lower() and q not in (mod.internal or "").lower() \
                and q not in mod.workshop_id:
            continue
        st = mod_status(mod.workshop_id)
        if status and st["status"] != status:
            continue
        rows.append({"workshop_id": mod.workshop_id, "name": mod.name, "status": st["status"],
                     "drifted": st["drifted"], "assets": st["assets"],
                     "docs": [d["report"] for d in st["docs"]]})
    return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit]}


def mark_reviewed(workshop_id: str, report: str, only: Optional[list] = None) -> dict:
    """Фиксирует текущие хеши покрытых ассетов во frontmatter отчёта."""
    wid = str(workshop_id)
    try:
        rep = reports.find_report(report)
    except FileNotFoundError as ex:
        raise ModError(str(ex)) from None
    if wid not in rep.workshop_ids:
        raise ModError(f"В frontmatter {rep.name} нет workshop_id {wid} "
                       f"(есть: {', '.join(rep.workshop_ids) or 'пусто'}). "
                       f"Добавь id в workshop_ids и повтори.")
    e = _current(wid)
    files = e["assets"]
    if only:
        wanted = [a.replace(os.sep, "/") for a in only]
        missing = [a for a in wanted if a not in files]
        if missing:
            raise ModError(f"В моде {wid} нет ассетов: {', '.join(missing)}. "
                           f"Пути — относительно каталога мода.")
        files = {a: files[a] for a in wanted}
    covered = {rel: hash12(info["sha256"]) for rel, info in files.items()}
    reports.write_baseline(rep, wid, covered, e["mod_hash"], time.strftime("%Y-%m-%d"))
    # AssetView покрытых ассетов — в кеше: он нужен будущему diff
    mod = assets.get_mod(wid)
    for rel in files:
        analysis.view_for_file(os.path.join(mod.dir, rel))
    return {"workshop_id": wid, "report": rep.name, "covered": len(covered),
            "mod_hash": e["mod_hash"]}
