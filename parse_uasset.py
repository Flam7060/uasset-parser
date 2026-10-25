#!/usr/bin/env python3
"""
Парсер cooked .uasset ARK: Survival Evolved (UE4 v404, LegacyFileVersion -3).

Читает заголовок пакета, name map, импорты/экспорты, tagged-свойства,
определения UProperty/UFunction/UClass/UStruct и Kismet-байткод с выводом
в псевдокод.

Код возврата: 0 — всё разобрано и байткод сошёлся по размеру,
2 — не читается сам пакет, 3 — часть экспортов разобрана не полностью.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uasset.reader import ParseError          # noqa: E402
from uasset.package import Package            # noqa: E402
from uasset.objects import parse_all          # noqa: E402
from uasset import render                     # noqa: E402

SECTIONS = ("header", "names", "imports", "exports", "bodies")


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Дамп cooked .uasset ARK SE")
    ap.add_argument("paths", nargs="+", metavar="path.uasset",
                    help="файлы или каталоги (каталог — все *.uasset в нём)")
    ap.add_argument("-f", "--filter", help="подстрока для name map (без учёта регистра)")
    ap.add_argument("-e", "--export", action="append", default=[],
                    help="показывать тела только экспортов, чьё имя содержит подстроку (можно несколько)")
    ap.add_argument("-s", "--sections", default=",".join(SECTIONS),
                    help=f"секции через запятую: {','.join(SECTIONS)}")
    ap.add_argument("--tree", action="store_true",
                    help="под каждой инструкцией — дерево узлов Kismet с оффсетами диск/память")
    args = ap.parse_args(argv)

    # Совместимость со старым вызовом `parse_uasset.py <path> [filter]`:
    # последний позиционный аргумент, не являющийся файлом/каталогом, — фильтр.
    if len(args.paths) > 1 and not os.path.exists(args.paths[-1]) and args.filter is None:
        args.filter = args.paths.pop()
    args.filter = args.filter.lower() if args.filter else None

    args.sections = {x.strip() for x in args.sections.split(",") if x.strip()}
    unknown = args.sections - set(SECTIONS)
    if unknown:
        ap.error(f"неизвестные секции: {', '.join(sorted(unknown))}")
    return args


def expand(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(os.path.join(p, n) for n in os.listdir(p) if n.endswith(".uasset")))
        else:
            files.append(p)
    return files


def dump(path, args):
    """Возвращает код возврата для одного файла."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as ex:
        print(f"{path}: не открыть: {ex}", file=sys.stderr)
        return 2
    try:
        pkg = Package(data, os.path.basename(path))
    except ParseError as ex:
        print(f"{path}: пакет не разобран: {ex}", file=sys.stderr)
        return 2

    bodies = parse_all(pkg)
    ctx = render.Context(pkg, bodies)
    out = []
    if "header" in args.sections:
        render.render_header(pkg, out)
    if "names" in args.sections:
        render.render_names(pkg, out, args.filter)
    if "imports" in args.sections:
        render.render_imports(pkg, out)
    if "exports" in args.sections:
        render.render_export_table(pkg, out)
    if "bodies" in args.sections:
        wanted = [w.lower() for w in args.export]
        for b in bodies:
            name = pkg.exports[b.index].object_name.lower()
            if not wanted or any(w in name for w in wanted):
                render.render_body(ctx, b, out, args.tree)
    sys.stdout.write("\n".join(out) + "\n")

    bad = render.problems(bodies)
    for b in bad:
        e = pkg.exports[b.index]
        why = b.error or (b.script.error if b.script and not b.script.mem_ok else
                          f"не прочитано {len(b.trailing)} байт")
        print(f"{path}: экспорт [{b.index + 1}] {e.object_name}: {why}", file=sys.stderr)
    return 3 if bad else 0


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    rc = 0
    for path in expand(args.paths):
        rc = max(rc, dump(path, args))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `| head` закрыл пайп — это не ошибка разбора
        sys.stderr.close()
        sys.exit(0)
