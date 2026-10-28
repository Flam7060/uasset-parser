"""
PDB → SQLite через `llvm-pdbutil dump` (раздел 8 спеки).

Разовый импорт потоком, построчно, без файлов с текстом на диске:
    types   — классы/структуры/союзы/enum, указатели, модификаторы, массивы, битовые поля;
    members — поля классов и базы (LF_FIELDLIST, продолжения LF_INDEX);
    symbols — public-символы с RVA (по заголовкам секций), недекорированные имена
              через llvm-undname, размер функции — оценка по соседнему символу.

Замеры на ShooterGameServer.pdb (370 МБ): `dump -types` 9 с и 427 МБ текста,
`dump -publics` 0.7 с и 35 МБ — разовый импорт приемлем, запасной путь через
`pretty` по одному типу не понадобился.

База лежит в `.cache/pdb/<sha256 pdb>.sqlite`; ключ — хеш файла, так что новая
сборка игры даёт новую базу, а не подмешивается к старой.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import time
from typing import Iterator, Optional

from . import cache, config


class PdbError(Exception):
    """Нет PDB / llvm-pdbutil и т.п.; текст — что задать в конфиге."""


SCHEMA = """
CREATE TABLE types(idx INTEGER PRIMARY KEY, kind TEXT, name TEXT, size INTEGER, fl INTEGER,
                   fwd INTEGER, ref INTEGER, ref_name TEXT, extra TEXT);
CREATE TABLE members(fl INTEGER, ord INTEGER, kind TEXT, name TEXT, type INTEGER, type_name TEXT,
                     offset INTEGER);
CREATE TABLE symbols(name TEXT, undec TEXT, rva INTEGER, kind TEXT, size INTEGER);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""
INDEXES = """
CREATE INDEX types_name ON types(name);
CREATE INDEX members_fl ON members(fl, ord);
CREATE INDEX symbols_undec ON symbols(undec);
CREATE INDEX symbols_name ON symbols(name);
CREATE INDEX symbols_rva ON symbols(rva);
"""

PRIMITIVE_SIZES = {
    "char": 1, "signed char": 1, "unsigned char": 1, "bool": 1, "char8_t": 1,
    "wchar_t": 2, "char16_t": 2, "short": 2, "unsigned short": 2,
    "int": 4, "unsigned int": 4, "unsigned": 4, "HRESULT": 4, "long": 4, "unsigned long": 4, "float": 4, "char32_t": 4,
    "__int64": 8, "unsigned __int64": 8, "long long": 8, "unsigned long long": 8,
    "double": 8, "long double": 8, "void": 0,
}

# индекс выровнен по ширине: у 4-значных перед 0x пять пробелов, у 5-значных четыре
_HDR = re.compile(r"^ {2,8}(0x[0-9A-Fa-f]+) \| (LF_[A-Z0-9_]+)(.*)$")
_REF = re.compile(r"(0x[0-9A-Fa-f]+)(?: \((.*?)\))?")
_MEMBER = re.compile(r"LF_MEMBER \[name = `(.*)`, Type = (0x[0-9A-Fa-f]+)(?: \((.*?)\))?, offset = (-?\d+)")


# --- запуск llvm-pdbutil -------------------------------------------------------

def _tool() -> str:
    exe = config.pdbutil()
    if os.path.isabs(exe):
        found = exe if os.access(exe, os.X_OK) else None
    else:
        found = shutil.which(exe)
    if not found:
        raise PdbError(f"Нет llvm-pdbutil ({exe}). Задай ARK_PDBUTIL с путём к бинарнику "
                       f"(в nix: /nix/store/…-llvm-21.1.8/bin/llvm-pdbutil) или добавь llvm в PATH.")
    return found


def _undname() -> Optional[str]:
    sibling = os.path.join(os.path.dirname(_tool()), "llvm-undname")
    if os.access(sibling, os.X_OK):
        return sibling
    return shutil.which("llvm-undname")


def _stream(args: list[str]) -> Iterator[str]:
    p = subprocess.Popen([_tool(), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", bufsize=1 << 20)
    assert p.stdout is not None
    for line in p.stdout:
        yield line.rstrip("\n")
    err = p.stderr.read() if p.stderr else ""
    if p.wait() != 0:
        raise PdbError(f"llvm-pdbutil {' '.join(args[:2])} завершился с кодом {p.returncode}: {err[:300]}")


def pdb_path(which: str) -> str:
    if which not in ("server", "client"):
        raise PdbError("pdb: server или client.")
    p = config.pdb_server() if which == "server" else config.pdb_client()
    if not os.path.isfile(p):
        var = "ARK_PDB_SERVER" if which == "server" else "ARK_PDB_CLIENT"
        raise PdbError(f"Нет PDB {p}. Задай {var} в окружении MCP-сервера.")
    return p


def db_path(pdb_file: str) -> str:
    return os.path.join(cache.root(), "pdb", f"{cache.sha256_file(pdb_file)}.sqlite")


# --- импорт ----------------------------------------------------------------------

def _name_of(rest: str) -> str:
    a, b = rest.find("`"), rest.rfind("`")
    return rest[a + 1:b] if a >= 0 and b > a else ""


def _int(s: str) -> int:
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _import_types(con: sqlite3.Connection, pdb_file: str) -> int:
    types: list = []
    members: list = []
    cur = None          # (idx, kind, name, fl, fwd, size) для классов
    cur_kind = None
    cur_fl = 0
    ord_ = 0
    pending_base = False
    count = 0

    def flush():
        nonlocal types, members
        if types:
            con.executemany("INSERT OR REPLACE INTO types VALUES(?,?,?,?,?,?,?,?,?)", types)
            types = []
        if members:
            con.executemany("INSERT INTO members VALUES(?,?,?,?,?,?,?)", members)
            members = []

    def close_class():
        nonlocal cur
        if cur:
            types.append((cur["idx"], cur["kind"], cur["name"], cur["size"], cur["fl"], cur["fwd"],
                          cur["ref"], cur["ref_name"], None))
            cur = None

    for line in _stream(["dump", "-types", pdb_file]):
        m = _HDR.match(line) if " | LF_" in line[:24] else None
        if m:
            close_class()
            idx, kind, rest = _int(m.group(1)), m.group(2), m.group(3)
            cur_kind = kind
            count += 1
            if len(types) + len(members) > 50000:
                flush()
            if kind in ("LF_CLASS", "LF_STRUCTURE", "LF_UNION", "LF_ENUM"):
                cur = {"idx": idx, "kind": kind[3:].lower(), "name": _name_of(rest), "size": 0,
                       "fl": 0, "fwd": 0, "ref": None, "ref_name": None}
            elif kind == "LF_FIELDLIST":
                cur_fl, ord_ = idx, 0
            elif kind in ("LF_POINTER", "LF_MODIFIER", "LF_ARRAY", "LF_BITFIELD"):
                cur = {"idx": idx, "kind": kind[3:].lower(), "name": None, "size": 0, "fl": 0,
                       "fwd": 0, "ref": None, "ref_name": None}
            continue
        s = line.strip()
        if cur is not None and cur_kind is not None:
            k = cur["kind"]
            if k in ("class", "structure", "union", "enum"):
                fm = re.search(r"field list: (0x[0-9A-Fa-f]+)", s)
                if fm:
                    cur["fl"] = _int(fm.group(1))
                if s.startswith("options:"):
                    sm = re.search(r"sizeof (\d+)", s)
                    if sm:
                        cur["size"] = int(sm.group(1))
                    cur["fwd"] = 1 if "forward ref" in s else 0
                if k == "enum":
                    um = re.search(r"underlying type: (0x[0-9A-Fa-f]+)(?: \((.*?)\))?", s)
                    if um:
                        cur["ref_name"] = um.group(2)
                        cur["size"] = PRIMITIVE_SIZES.get(um.group(2) or "", 4)
            elif k in ("pointer", "modifier"):
                rm = re.search(r"referent = " + _REF.pattern, s)
                if rm:
                    cur["ref"], cur["ref_name"] = _int(rm.group(1)), rm.group(2)
                    mode = re.search(r"mode = ([a-z ]+),", s) or re.search(r"modifiers = (.*)$", s)
                    cur["name"] = mode.group(1).strip() if mode else ""
            elif k == "array":
                am = re.search(r"size: (\d+), index type: .*?, element type: " + _REF.pattern, s)
                if am:
                    cur["size"] = int(am.group(1))
                    cur["ref"], cur["ref_name"] = _int(am.group(2)), am.group(3)
            elif k == "bitfield":
                bm = re.search(r"type = " + _REF.pattern + r", bit offset = (\d+), # bits = (\d+)", s)
                if bm:
                    cur["ref"], cur["ref_name"] = _int(bm.group(1)), bm.group(2)
                    cur["size"] = int(bm.group(4))
                    cur["name"] = bm.group(3)           # битовое смещение
            continue
        if cur_kind == "LF_FIELDLIST":
            if pending_base:
                bm = re.search(r"type = (0x[0-9A-Fa-f]+), offset = (-?\d+)", s)
                if bm:
                    members.append((cur_fl, ord_, "base", None, _int(bm.group(1)), None, int(bm.group(2))))
                    ord_ += 1
                pending_base = False
            elif s.startswith("- LF_BCLASS"):
                pending_base = True
            elif s.startswith("- LF_MEMBER"):
                mm = _MEMBER.search(s)
                if mm:
                    members.append((cur_fl, ord_, "member", mm.group(1), _int(mm.group(2)),
                                    mm.group(3), int(mm.group(4))))
                    ord_ += 1
            elif s.startswith("- LF_INDEX"):
                im = re.search(r"continuation = (0x[0-9A-Fa-f]+)", s)
                if im:
                    members.append((cur_fl, ord_, "index", None, _int(im.group(1)), None, 0))
                    ord_ += 1
    close_class()
    flush()
    return count


def _section_rvas(pdb_file: str) -> dict:
    out, num, name = {}, 0, None
    lines = list(_stream(["dump", "-section-headers", pdb_file]))
    for i, line in enumerate(lines):
        m = re.match(r"\s+SECTION HEADER #(\d+)", line)
        if m:
            num = int(m.group(1))
        elif line.rstrip().endswith("virtual address") and num:
            out[num] = int(line.split()[0], 16)
    return out


def _undecorate(names: list[str]) -> dict:
    tool = _undname()
    todo = [n for n in names if n.startswith("?")]
    result = {}
    if not tool or not todo:
        return result
    p = subprocess.run([tool], input="\n".join(todo) + "\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout.split("\n")
    # на каждый вход: строка с исходным именем, затем расшифровка (или `error: …`),
    # затем пустая строка
    pos = {name: None for name in todo}
    i = 0
    while i + 1 < len(out):
        if out[i] in pos and pos[out[i]] is None:
            dec = out[i + 1]
            pos[out[i]] = None if dec.startswith("error:") else dec
            result[out[i]] = pos[out[i]]
            i += 2
        else:
            i += 1
    return result


def _import_publics(con: sqlite3.Connection, pdb_file: str) -> int:
    sections = _section_rvas(pdb_file)
    rows = []
    name = None
    for line in _stream(["dump", "-publics", pdb_file]):
        if "S_PUB32" in line:
            name = _name_of(line)
        elif name is not None and "addr = " in line:
            fm = re.search(r"flags = (.*?), addr = (\d+):(\d+)", line)
            if fm:
                sec, off = int(fm.group(2)), int(fm.group(3))
                if sec in sections:
                    kind = "function" if "function" in fm.group(1) else "data"
                    rows.append([name, None, sections[sec] + off, kind, 0])
            name = None
    undec = _undecorate([r[0] for r in rows])
    for r in rows:
        r[1] = undec.get(r[0]) or (r[0] if not r[0].startswith("?") else None)
    # размер функции — до следующего символа по RVA (оценка: публичные символы без размеров)
    rvas = sorted({r[2] for r in rows})
    nxt = {a: b for a, b in zip(rvas, rvas[1:])}
    for r in rows:
        if r[3] == "function":
            r[4] = min(nxt.get(r[2], r[2]) - r[2], 1 << 20)
    con.executemany("INSERT INTO symbols VALUES(?,?,?,?,?)", rows)
    return len(rows)


def _summary(pdb_file: str) -> dict:
    meta = {}
    for line in _stream(["dump", "-summary", pdb_file]):
        s = line.strip()
        for key in ("Signature", "Age", "GUID"):
            if s.startswith(key + ":"):
                meta[key.lower()] = s.split(":", 1)[1].strip()
    return meta


def import_pdb(which: str = "server", force: bool = False, log=None) -> str:
    """Импортирует PDB в SQLite (если ещё нет). Возвращает путь к базе."""
    src = pdb_path(which)
    dst = db_path(src)
    if os.path.isfile(dst) and not force:
        return dst
    _tool()
    log = log or (lambda msg: None)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    t0 = time.time()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        log("типы…")
        n_types = _import_types(con, src)
        log(f"типов {n_types}, {time.time() - t0:.0f} с; символы…")
        n_syms = _import_publics(con, src)
        con.executescript(INDEXES)
        meta = _summary(src)
        meta.update(source=src, types=str(n_types), symbols=str(n_syms),
                    imported_at=time.strftime("%Y-%m-%dT%H:%M:%S"), seconds=f"{time.time() - t0:.0f}")
        con.executemany("INSERT INTO meta VALUES(?,?)", list(meta.items()))
        con.commit()
    except BaseException:
        con.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    con.close()
    os.replace(tmp, dst)
    log(f"готово за {time.time() - t0:.0f} с: {dst}")
    return dst


def connect(which: str = "server") -> sqlite3.Connection:
    return sqlite3.connect(import_pdb(which))


# --- запросы ---------------------------------------------------------------------

class TypeNames:
    """Имя и размер типа по индексу (с мемоизацией и защитой от циклов)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.memo: dict = {}

    def _row(self, idx: int):
        return self.con.execute("SELECT kind, name, size, fl, fwd, ref, ref_name FROM types WHERE idx=?",
                                (idx,)).fetchone()

    def name(self, idx: int, prim: Optional[str] = None, depth: int = 0) -> str:
        if idx < 0x1000:
            return prim or f"<prim {idx:#x}>"
        if idx in self.memo:
            return self.memo[idx]
        row = self._row(idx)
        if row is None or depth > 12:
            return f"<type {idx:#x}>"
        kind, name, size, fl, fwd, ref, ref_name = row
        if kind in ("class", "structure", "union", "enum"):
            out = name
        elif kind == "pointer":
            inner = self.name(ref, ref_name, depth + 1)
            out = inner + {"pointer": "*", "lvalue ref": "&", "rvalue ref": "&&"}.get(name, "*")
        elif kind == "modifier":
            out = f"{name} {self.name(ref, ref_name, depth + 1)}"
        elif kind == "array":
            esz = self.size(ref, ref_name, depth + 1) or 1
            out = f"{self.name(ref, ref_name, depth + 1)}[{size // esz}]"
        elif kind == "bitfield":
            out = f"{self.name(ref, ref_name, depth + 1)}:{size}"
        else:
            out = f"<{kind} {idx:#x}>"
        self.memo[idx] = out
        return out

    def size(self, idx: int, prim: Optional[str] = None, depth: int = 0) -> int:
        if idx < 0x1000:
            if prim and prim.endswith("*"):
                return 8
            return PRIMITIVE_SIZES.get(prim or "", 0)
        row = self._row(idx)
        if row is None or depth > 12:
            return 0
        kind, name, size, fl, fwd, ref, ref_name = row
        if kind == "pointer":
            return 8
        if kind == "modifier":
            return self.size(ref, ref_name, depth + 1)
        if kind in ("class", "structure", "union") and fwd:
            real = self.con.execute(
                "SELECT size FROM types WHERE name=? AND fwd=0 AND kind IN ('class','structure','union') "
                "ORDER BY size DESC LIMIT 1", (name,)).fetchone()
            return real[0] if real else 0
        return size


def _walk_fields(con: sqlite3.Connection, fl: int) -> Iterator[tuple]:
    seen = set()
    while fl and fl not in seen:
        seen.add(fl)
        nxt = 0
        for row in con.execute("SELECT kind, name, type, type_name, offset FROM members "
                               "WHERE fl=? ORDER BY ord", (fl,)):
            if row[0] == "index":
                nxt = row[2]
            else:
                yield row
        fl = nxt


def pdb_type(name: str, pdb: str = "server") -> dict:
    con = connect(pdb)
    try:
        row = con.execute(
            "SELECT idx, kind, name, size, fl FROM types WHERE name=? AND fwd=0 AND fl!=0 "
            "AND kind IN ('class','structure','union') ORDER BY size DESC LIMIT 1", (name,)).fetchone()
        if row is None:
            near = [r[0] for r in con.execute(
                "SELECT DISTINCT name FROM types WHERE name LIKE ? AND fwd=0 AND fl!=0 "
                "AND kind IN ('class','structure','union') LIMIT 15",
                ("%" + name.replace("%", "") + "%",))]
            hint = f" Ближайшие имена: {', '.join(near)}." if near else ""
            raise PdbError(f"Тип {name} не найден в PDB ({pdb}); нужно точное имя, например "
                           f"APrimalDinoCharacter.{hint}")
        idx, kind, tname, size, fl = row
        tn = TypeNames(con)
        bases, members = [], []
        for k, mname, t, tprim, off in _walk_fields(con, fl):
            if k == "base":
                bases.append({"name": tn.name(t), "offset": off})
                continue
            item = {"offset": off, "size": tn.size(t, tprim), "type": tn.name(t, tprim), "name": mname}
            trow = tn._row(t) if t >= 0x1000 else None
            if trow and trow[0] == "bitfield":
                item["bits"] = trow[2]
                item["bit_offset"] = int(trow[1]) if str(trow[1]).isdigit() else None
                item["size"] = tn.size(trow[5], trow[6])
                item["type"] = tn.name(trow[5], trow[6])
            members.append(item)
        members.sort(key=lambda m: (m["offset"], m.get("bit_offset") or 0))
        return {"name": tname, "kind": kind, "size": size, "bases": bases, "members": members}
    finally:
        con.close()


def _like_pattern(query: str) -> str:
    if not any(c in query for c in "*?"):
        query = f"*{query}*"
    esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc.replace("*", "%").replace("?", "_")


def pdb_symbols(query: str, kind: str = "function", pdb: str = "server", limit: int = 50) -> dict:
    if kind not in ("function", "data", "any"):
        raise PdbError("kind: function, data или any.")
    con = connect(pdb)
    try:
        pat = _like_pattern(query)
        sql = ("SELECT name, undec, rva, kind, size FROM symbols "
               "WHERE (undec LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
        args: list = [pat, pat]
        if kind != "any":
            sql += " AND kind=?"
            args.append(kind)
        total = con.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
        rows = con.execute(sql + " ORDER BY rva LIMIT ?", args + [limit]).fetchall()
        items = [{"name": u or n, "decorated": n, "rva": f"{rva:#x}", "kind": k,
                  **({"size": s, "size_approx": True} if k == "function" else {})}
                 for n, u, rva, k, s in rows]
        out = {"pdb": pdb, "total": total, "items": items}
        if total > limit:
            out["truncated"] = True
        return out
    finally:
        con.close()


# --- BP → натив ------------------------------------------------------------------

_SCRIPT_SYM = re.compile(r"^/Script/([A-Za-z0-9_]+)\.([A-Za-z0-9_]+):([A-Za-z0-9_]+)$")
_native_cache: dict = {}


def native_for(symbol: str, pdb: str = "server") -> Optional[dict]:
    """`/Script/ShooterGame.PrimalDinoCharacter:Foo` → символы APrimalDinoCharacter::Foo / execFoo.

    Возвращает None, если символ не нативный или PDB недоступен (BP-анализ при
    этом не ломается, `native` в ответе просто отсутствует).
    """
    m = _SCRIPT_SYM.match(symbol)
    if not m:
        return None
    key = (pdb, symbol)
    if key in _native_cache:
        return _native_cache[key]
    _, cls, func = m.groups()
    try:
        con = connect(pdb)
    except PdbError:
        return None
    try:
        res = None
        for prefix in ("A", "U", "F"):
            q = f"{prefix}{cls}::{func}("
            row = con.execute("SELECT undec, rva, size FROM symbols WHERE kind='function' AND "
                              "undec LIKE ? ESCAPE '\\' ORDER BY rva LIMIT 1",
                              ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
                              ).fetchone()
            thunk = con.execute("SELECT rva FROM symbols WHERE kind='function' AND undec LIKE ? "
                                "ESCAPE '\\' ORDER BY rva LIMIT 1",
                                (f"%{prefix}{cls}::exec{func}(%".replace("_", "\\_"),)).fetchone()
            if row or thunk:
                res = {"symbol": row[0] if row else None,
                       "rva": f"{row[1]:#x}" if row else None,
                       "exec_thunk": f"{thunk[0]:#x}" if thunk else None}
                break
        _native_cache[key] = res
        return res
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "server"
    print(import_pdb(which, force="--force" in sys.argv, log=lambda m: print(m, flush=True)))
