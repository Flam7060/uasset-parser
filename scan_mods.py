#!/usr/bin/env python3
"""
Сканер установленных модов ARK: CLI поверх ядра `arkre.mods`.

Индекс (.cache/index.json) хранит текущие хеши ассетов; baseline разбора лежит
во frontmatter отчётов docs/mods/*.md, так что чистка кеша его не теряет.

Команды:
    scan_mods.py scan                          — пересчитать хеши, обновить индекс
    scan_mods.py list [--status S]             — таблица: мод → статус отчёта
    scan_mods.py mark-reviewed <id> --report R — зафиксировать хеши во frontmatter R
    scan_mods.py cache-gc [--dry-run]          — удалить кеш старых версий парсера

Статусы: missing / unreviewed / fresh / stale (+ drifted).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arkre import assets, cache, config, mods  # noqa: E402


def cmd_scan(args) -> int:
    try:
        summary = mods.scan(args.mods_dir)
    except mods.ModError as ex:
        print(ex, file=sys.stderr)
        return 2
    for m in assets.list_mods(args.mods_dir):
        st = mods.mod_status(m.workshop_id)
        flag = " drifted" if st["drifted"] else ""
        print(f"{m.workshop_id:>12}  {st['status']:<10}{flag:<8}  {st['assets']:>4} assets  {m.name or ''}")
    by = ", ".join(f"{k}={v}" for k, v in sorted(summary["by_status"].items()))
    print(f"\nмодов {summary['total']} ({by}); изменились: {len(summary['changed'])}, "
          f"новых: {len(summary['new'])}, удалено: {len(summary['removed'])}")
    print(f"index: {summary['index']}")
    return 0


def cmd_list(args) -> int:
    page = mods.mods_list(args.status, args.query, limit=10 ** 6)
    for r in page["items"]:
        flag = "*" if r["drifted"] else " "
        print(f"{r['workshop_id']:>12}  {r['status']:<10}{flag} {r['assets']:>4}  "
              f"{(r['name'] or ''):<36}  {', '.join(r['docs'])}")
    return 0


def cmd_mark_reviewed(args) -> int:
    rc = 0
    for wid in args.workshop_id:
        try:
            res = mods.mark_reviewed(wid, args.report, args.asset or None)
        except (mods.ModError, assets.AssetError) as ex:
            print(f"{wid}: {ex}", file=sys.stderr)
            rc = 2
            continue
        print(f"{wid}: покрыто {res['covered']} ассетов → {res['report']} (mod_hash {res['mod_hash']})")
    return rc


def cmd_cache_gc(args) -> int:
    removed = cache.gc(args.dry_run)
    for p in removed:
        print(("удалил бы " if args.dry_run else "удалён ") + p)
    print(f"текущая версия парсера: v{cache.parser_ver()}; чужих каталогов: {len(removed)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сканер модов ARK → индекс кеша")
    ap.add_argument("--mods-dir", default=None,
                    help=f"каталог Content/Mods (по умолчанию {config.mods_dir()})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="обойти моды и обновить index.json")

    p_list = sub.add_parser("list", help="показать статусы")
    p_list.add_argument("--status", choices=["missing", "unreviewed", "fresh", "stale"])
    p_list.add_argument("--query", help="подстрока имени или id")

    p_mark = sub.add_parser("mark-reviewed",
                            help="зафиксировать хеши покрытых ассетов во frontmatter отчёта")
    p_mark.add_argument("workshop_id", nargs="+")
    p_mark.add_argument("--report", required=True,
                        help="файл отчёта в docs/mods/ (в его workshop_ids должен быть id)")
    p_mark.add_argument("--asset", action="append",
                        help="покрыть только этот ассет (путь внутри мода); можно несколько раз")

    p_gc = sub.add_parser("cache-gc", help="удалить кеш других версий парсера")
    p_gc.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)
    if args.mods_dir:
        os.environ["ARK_MODS_DIR"] = os.path.expanduser(args.mods_dir)
    return {"scan": cmd_scan, "list": cmd_list, "mark-reviewed": cmd_mark_reviewed,
            "cache-gc": cmd_cache_gc}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
