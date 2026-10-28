#!/usr/bin/env python3
"""
Сканер установленных модов ARK → .cache/index.json.

Проходит по Content/Mods, считает sha256 всех .uasset в каждом workshop_id,
подхватывает mod_name из .mod (если можно), ищет отчёт в docs/ по
workshop_id в имени файла. Для каждого мода выставляет статус отчёта:

    missing     — отчёта нет;
    unreviewed  — отчёт есть, но covered_hashes ещё не зафиксированы;
    fresh       — все covered_hashes совпадают с текущими;
    stale       — расхождение (мод обновился после отчёта).

Команды:
    scan_mods.py scan                 — обойти моды, обновить index.json
    scan_mods.py list                 — показать таблицу: мод → статус
    scan_mods.py mark-reviewed <id>…  — зафиксировать текущие хеши как
                                        покрытые существующим отчётом
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uasset import cache  # noqa: E402

DEFAULT_MODS_DIR = os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/ARK/ShooterGame/Content/Mods"
)
PARSER_ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(PARSER_ROOT, "docs")


# --- вытащить имя мода из .mod -----------------------------------------

def _read_mod_name(mod_file: str) -> str | None:
    """UE FString: int32 длина + байты. В .mod первая строка — путь мода,
    это не имя, но имя лежит в mod.info рядом. Пробуем оба."""
    base = mod_file[:-4] if mod_file.endswith(".mod") else mod_file
    for cand in (os.path.join(base, "mod.info"), mod_file):
        try:
            with open(cand, "rb") as fh:
                data = fh.read(4096)
        except OSError:
            continue
        # UE FString: длина (int32 LE) + ASCII с \0 в конце
        import struct
        if len(data) < 4:
            continue
        n = struct.unpack("<i", data[:4])[0]
        if 1 < n < 512 and 4 + n <= len(data):
            s = data[4:4 + n].rstrip(b"\x00").decode("latin-1", errors="replace")
            if s and all(32 <= ord(c) < 127 for c in s):
                return s
    return None


# --- обход мода --------------------------------------------------------

def _collect_assets(mod_dir: str) -> dict:
    assets = {}
    for root, _, files in os.walk(mod_dir):
        for name in files:
            if not name.endswith(".uasset"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, mod_dir)
            try:
                sha = cache.sha256_file(full)
                size = os.path.getsize(full)
            except OSError:
                continue
            assets[rel] = {"sha256": sha, "size": size}
    return assets


# --- поиск отчёта в docs/ ---------------------------------------------

def _find_report(workshop_id: str) -> str | None:
    """Готовые отчёты лежат в руте docs/, шаблоны в docs/mods/ не считаются."""
    pat = re.compile(rf"(^|[_\-]){re.escape(workshop_id)}(\.md$|[_\-])")
    if not os.path.isdir(DOCS_DIR):
        return None
    for name in os.listdir(DOCS_DIR):
        full = os.path.join(DOCS_DIR, name)
        if os.path.isfile(full) and name.endswith(".md") and pat.search(name):
            return os.path.relpath(full, PARSER_ROOT)
    return None


def _report_status(report_rel: str | None, assets: dict, covered: dict | None) -> str:
    if report_rel is None:
        return "missing"
    if not covered:
        return "unreviewed"
    cur = {k: v["sha256"] for k, v in assets.items()}
    if cur == covered:
        return "fresh"
    return "stale"


# --- команды ----------------------------------------------------------

def cmd_scan(args) -> int:
    mods_dir = args.mods_dir
    if not os.path.isdir(mods_dir):
        print(f"нет каталога модов: {mods_dir}", file=sys.stderr)
        return 2
    idx = cache.load_index()
    mods = idx.setdefault("mods", {})

    seen = set()
    for name in sorted(os.listdir(mods_dir)):
        full = os.path.join(mods_dir, name)
        if not os.path.isdir(full) or not name.isdigit():
            continue
        workshop_id = name
        seen.add(workshop_id)
        mod_file = os.path.join(mods_dir, f"{workshop_id}.mod")
        mod_name = _read_mod_name(mod_file) if os.path.isfile(mod_file) else None
        assets = _collect_assets(full)

        entry = mods.get(workshop_id, {})
        prev_report = entry.get("report") or {}
        # автопоиск по ID в имени; если не нашли — уважаем ручную привязку (mark-reviewed --report)
        auto = _find_report(workshop_id)
        prev_path = prev_report.get("path")
        report_rel = auto or prev_path
        # covered_hashes валидны только если путь отчёта не поменялся
        covered = prev_report.get("covered_hashes") if prev_path == report_rel else None
        status = _report_status(report_rel, assets, covered)

        mods[workshop_id] = {
            "mod_name": mod_name,
            "mod_dir_mtime": int(os.path.getmtime(full)),
            "assets": assets,
            "report": {
                "path": report_rel,
                "status": status,
                "covered_hashes": covered or {},
                "last_reviewed": prev_report.get("last_reviewed"),
            },
        }
        print(f"{workshop_id:>12}  {status:<10}  {len(assets):>3} assets  {mod_name or ''}")

    # моды которые исчезли — помечаем, но не удаляем (ИИ может искать историю)
    for wid in list(mods.keys()):
        if wid not in seen:
            mods[wid]["mod_dir_mtime"] = None  # маркер «удалён»

    cache.save_index(idx)
    print(f"\nindex: {cache.index_path()}")
    return 0


def cmd_list(args) -> int:
    idx = cache.load_index()
    rows = []
    for wid, m in sorted(idx.get("mods", {}).items()):
        rows.append((wid, m.get("report", {}).get("status", "?"),
                     len(m.get("assets", {})), m.get("mod_name") or "",
                     m.get("report", {}).get("path") or ""))
    if args.status:
        rows = [r for r in rows if r[1] == args.status]
    for wid, status, n, name, path in rows:
        print(f"{wid:>12}  {status:<10}  {n:>3}  {name:<40}  {path}")
    return 0


def cmd_mark_reviewed(args) -> int:
    idx = cache.load_index()
    today = time.strftime("%Y-%m-%d")
    rc = 0
    override = args.report
    if override:
        full = os.path.join(PARSER_ROOT, override) if not os.path.isabs(override) else override
        if not os.path.isfile(full):
            print(f"нет такого отчёта: {override}", file=sys.stderr)
            return 2
        override = os.path.relpath(full, PARSER_ROOT)
    for wid in args.workshop_id:
        m = idx.get("mods", {}).get(wid)
        if not m:
            print(f"{wid}: не найден в индексе — запусти scan сначала", file=sys.stderr)
            rc = 2
            continue
        rep = m.setdefault("report", {})
        if override:
            rep["path"] = override
        if not rep.get("path"):
            print(f"{wid}: нет файла отчёта в docs/ — укажи --report path.md", file=sys.stderr)
            rc = 2
            continue
        rep["covered_hashes"] = {k: v["sha256"] for k, v in m["assets"].items()}
        rep["last_reviewed"] = today
        rep["status"] = "fresh"
        print(f"{wid}: отмечен как reviewed, {len(rep['covered_hashes'])} хешей → {rep['path']}")
    cache.save_index(idx)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сканер модов ARK → индекс кеша")
    ap.add_argument("--mods-dir", default=DEFAULT_MODS_DIR,
                    help=f"каталог Content/Mods (по умолчанию {DEFAULT_MODS_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="обойти моды и обновить index.json")

    p_list = sub.add_parser("list", help="показать статусы")
    p_list.add_argument("--status", choices=["missing", "unreviewed", "fresh", "stale"])

    p_mark = sub.add_parser("mark-reviewed",
                            help="зафиксировать текущие хеши как покрытые отчётом")
    p_mark.add_argument("workshop_id", nargs="+")
    p_mark.add_argument("--report",
                        help="явный путь к .md отчёту (для семейных/общих отчётов без ID в имени)")

    args = ap.parse_args(argv)
    return {"scan": cmd_scan, "list": cmd_list,
            "mark-reviewed": cmd_mark_reviewed}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
