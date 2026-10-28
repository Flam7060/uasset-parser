"""
Индекс перекрёстных ссылок (SQLite) и поиск по модам.

Строки привязаны к (parser_ver, sha256): тот же ассет в нескольких модах
индексируется один раз, а смена версии парсера делает старые строки
невидимыми. Источник строк — xrefs из AssetView (analysis.FunctionAnalyzer).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import analysis, assets, cache

KINDS = ("call", "read", "write", "cast", "ref")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed(parser_ver TEXT, sha256 TEXT, PRIMARY KEY(parser_ver, sha256));
CREATE TABLE IF NOT EXISTS xref(
    parser_ver TEXT, sha256 TEXT, function TEXT, kind TEXT, symbol TEXT, member TEXT,
    mem INTEGER, via TEXT, args TEXT);
CREATE INDEX IF NOT EXISTS xref_member ON xref(member COLLATE NOCASE, kind);
CREATE INDEX IF NOT EXISTS xref_sha ON xref(parser_ver, sha256);
"""


@dataclass
class Target:
    """Ассет в области поиска."""
    file: str
    sha256: str
    workshop_id: Optional[str]
    rel: str

    @property
    def label(self) -> str:
        return f"{self.workshop_id}:{self.rel}" if self.workshop_id else self.rel


def db_path() -> str:
    return os.path.join(cache.root(), "xref.sqlite")


def connect() -> sqlite3.Connection:
    os.makedirs(cache.root(), exist_ok=True)
    con = sqlite3.connect(db_path())
    con.executescript(_SCHEMA)
    return con


def _index_view(con: sqlite3.Connection, view: dict) -> None:
    ver, sha = cache.parser_ver(), view["sha256"]
    if con.execute("SELECT 1 FROM indexed WHERE parser_ver=? AND sha256=?", (ver, sha)).fetchone():
        return
    rows = []
    for fname, fn in view["functions"].items():
        for x in fn["xrefs"]:
            rows.append((ver, sha, fname, x["kind"], x["symbol"], x["member"], x["mem"],
                         json.dumps(x["via"]) if x.get("via") else None,
                         json.dumps(x["args"], ensure_ascii=False) if x.get("args") else None))
    with con:
        con.executemany("INSERT INTO xref VALUES(?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO indexed VALUES(?,?)", (ver, sha))


def mod_targets(workshop_id: str) -> list[Target]:
    mod = assets.get_mod(workshop_id)
    return [Target(os.path.join(mod.dir, rel), "", mod.workshop_id, rel)
            for rel in assets.mod_files(mod)]


def scope_targets(scope) -> list[Target]:
    """workshop_id, адрес ассета или список таких значений."""
    items = [scope] if isinstance(scope, str) else list(scope)
    out: list[Target] = []
    for item in items:
        item = str(item).strip()
        if item.isdigit():
            out += mod_targets(item)
        else:
            r = assets.resolve(item)
            out.append(Target(r.file, "", r.workshop_id, r.rel))
    return out


def ensure_indexed(targets: list[Target], con: Optional[sqlite3.Connection] = None) -> None:
    """Разбирает и индексирует то, чего нет в кеше; заполняет Target.sha256."""
    own = con is None
    con = con or connect()
    try:
        for t in targets:
            view = analysis.view_for_file(t.file)
            t.sha256 = view["sha256"]
            _index_view(con, view)
    finally:
        if own:
            con.close()


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _member_clause(query: str) -> tuple[str, list]:
    cls, _, member = query.rpartition(":")
    if "*" in member or "?" in member:
        cond = "member LIKE ? ESCAPE '\\'"
        arg = _like(member).replace("*", "%").replace("?", "_")
    else:
        cond, arg = "member = ? COLLATE NOCASE", member
    args = [arg]
    if cls:
        # у виртуальных вызовов в байткоде нет класса (символ — голое имя):
        # их не отсекаем, иначе `Class:Func` теряет вызовы собственных функций
        cond += " AND (symbol LIKE ? ESCAPE '\\' OR (symbol NOT LIKE '%.%' AND symbol NOT LIKE '%:%'))"
        args.append("%" + _like(cls) + "%")
    return cond, args


def _line_of(view: dict, function: str, mem: int) -> tuple[str, Optional[str]]:
    fn = view["functions"].get(function)
    if fn:
        for ln in fn["lines"]:
            if ln["off"] == mem:
                return ln["text"].strip(), ln["label"]
    return "", None


def query_rows(targets: list[Target], query: str, kinds: Optional[list] = None) -> list[tuple]:
    """Строки (sha256, function, kind, symbol, mem, via, args) по символу в области."""
    bad = [k for k in (kinds or []) if k not in KINDS]
    if bad:
        raise ValueError(f"kinds: допустимо {', '.join(KINDS)}; получено {', '.join(bad)}")
    con = connect()
    try:
        ensure_indexed(targets, con)
        con.execute("DROP TABLE IF EXISTS temp.scope")
        con.execute("CREATE TEMP TABLE scope(sha256 TEXT PRIMARY KEY)")
        con.executemany("INSERT OR IGNORE INTO scope VALUES(?)", [(t.sha256,) for t in targets])
        cond, args = _member_clause(query)
        sql = (f"SELECT sha256, function, kind, symbol, mem, via, args FROM xref "
               f"WHERE parser_ver=? AND sha256 IN (SELECT sha256 FROM scope) AND {cond}")
        params = [cache.parser_ver(), *args]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += list(kinds)
        return con.execute(sql + " ORDER BY sha256, function, mem", params).fetchall()
    finally:
        con.close()


def search_xref(targets: list[Target], query: str, kinds: Optional[list] = None,
                limit: int = 50, offset: int = 0) -> dict:
    rows = query_rows(targets, query, kinds)
    by_sha: dict = {}
    for t in targets:
        by_sha.setdefault(t.sha256, []).append(t)
    views: dict = {}
    items, total = [], 0
    for sha, function, kind, symbol, mem, via, _args in rows:
        for t in by_sha[sha]:
            total += 1
            if total <= offset or len(items) >= limit:
                continue
            if sha not in views:
                views[sha] = cache.read_view(sha) or analysis.view_for_file(t.file)
            line, label = _line_of(views[sha], function, mem)
            row = {"asset": t.label, "function": function, "kind": kind, "symbol": symbol,
                   "label": label, "line": line}
            if via:
                row["via"] = json.loads(via)
            items.append(row)
    return _page(items, total, limit, offset)


def search_text(targets: list[Target], query: str, limit: int = 50, offset: int = 0) -> dict:
    if len(query) >= 2 and query.startswith("/") and query.endswith("/"):
        try:
            match = re.compile(query[1:-1]).search
        except re.error as ex:
            raise ValueError(f"Неверный регэксп {query}: {ex}") from None
    else:
        low = query.lower()
        match = lambda s: low in s.lower()      # noqa: E731
    items, total = [], 0
    for t in targets:
        view = analysis.view_for_file(t.file)
        t.sha256 = view["sha256"]
        for fname, fn in view["functions"].items():
            for ln in fn["lines"]:
                if ln["off"] is not None and match(ln["text"]):
                    total += 1
                    if total <= offset or len(items) >= limit:
                        continue
                    items.append({"asset": t.label, "function": fname, "kind": "text",
                                  "label": ln["label"], "line": ln["text"].strip()})
    return _page(items, total, limit, offset)


def _page(items: list, total: int, limit: int, offset: int) -> dict:
    out = {"total": total, "offset": offset, "items": items}
    if offset + len(items) < total:
        out["truncated"] = True
        out["next_offset"] = offset + len(items)
    return out


def mod_search(scope, query: str, mode: str = "xref", kinds: Optional[list] = None,
               limit: int = 50, offset: int = 0) -> dict:
    if mode not in ("xref", "text"):
        raise ValueError("mode: xref или text.")
    targets = scope_targets(scope)
    if not targets:
        raise ValueError("В области поиска нет ассетов.")
    if mode == "xref":
        return search_xref(targets, query, kinds, limit, offset)
    return search_text(targets, query, limit, offset)
