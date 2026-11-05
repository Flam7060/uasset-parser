"""native_calls: граф вызовов нативного символа по машкоду .exe (статическое чтение с диска).

Берём (rva, size) символа из PDB-базы, читаем тело из .exe, дизассемблируем iced-x86 и
отдаём структурные факты: прямые вызовы, импорты (IAT), виртуальные вызовы (названы,
если в функции регистр загружен из известной vftable), обращения к глобалам.
Процесс игры не трогаем.
"""
from __future__ import annotations

import bisect
import os
import sqlite3
import struct
from typing import Optional

from . import config, pdb
from .pdb import PdbError

_DATA_SLACK = 0x20      # допуск оффсета для глобалов (поле внутри символа)
_MAX_OFFSET = 0x100     # за пределами размера символа дальше этого оффсета цель считаем безымянной
_NOISE = ("__real@", "__xmm@", "__ymm@", "??_C@", "_real@")   # константы и строковые литералы — не глобалы


def _iced():
    try:
        import iced_x86
    except ImportError:
        raise PdbError("нет iced-x86: добавь его в зависимости (uv run --with iced-x86) "
                       "или в inline-deps server.py.") from None
    return iced_x86


# --- PE ----------------------------------------------------------------------------

class Pe:
    """Минимальный разбор PE32+: ImageBase, секции, каталог импорта."""

    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise PdbError(f"Нет файла {path}. Задай ARK_SERVER_EXE / ARK_CLIENT_EXE.")
        self.path = path
        with open(path, "rb") as f:
            head = f.read(0x1000)
            if head[:2] != b"MZ":
                raise PdbError(f"{path}: не PE-файл.")
            lfanew = struct.unpack_from("<I", head, 0x3C)[0]
            if lfanew + 0x18 > len(head):
                f.seek(lfanew)
                head = f.read(0x1000)
                lfanew = 0
            if head[lfanew:lfanew + 4] != b"PE\0\0":
                raise PdbError(f"{path}: нет сигнатуры PE.")
            nsec, = struct.unpack_from("<H", head, lfanew + 6)
            opt_size, = struct.unpack_from("<H", head, lfanew + 20)
            opt = lfanew + 24
            if struct.unpack_from("<H", head, opt)[0] != 0x20B:
                raise PdbError(f"{path}: ожидается PE32+ (x64).")
            self.image_base, = struct.unpack_from("<Q", head, opt + 24)
            ndir, = struct.unpack_from("<I", head, opt + 108)
            self.import_dir = struct.unpack_from("<II", head, opt + 112 + 8) if ndir > 1 else (0, 0)
            sec_off = opt + opt_size
            need = sec_off + nsec * 40
            if need > len(head):
                raise PdbError(f"{path}: таблица секций за пределами заголовка.")
            self.sections = []
            for i in range(nsec):
                name, vsize, va, rawsize, rawptr = struct.unpack_from("<8sIIII", head, sec_off + i * 40)
                self.sections.append((name.rstrip(b"\0").decode("latin-1"), va, vsize, rawptr, rawsize))
        self._imports: Optional[dict] = None

    def rva2off(self, rva: int) -> Optional[int]:
        for _, va, vsize, rawptr, rawsize in self.sections:
            if va <= rva < va + max(vsize, rawsize):
                off = rva - va
                return rawptr + off if off < rawsize else None
        return None

    def in_image(self, rva: int) -> bool:
        return any(va <= rva < va + vsize for _, va, vsize, _, _ in self.sections)

    def read(self, rva: int, size: int) -> bytes:
        off = self.rva2off(rva)
        if off is None:
            raise PdbError(f"RVA {rva:#x} вне секций {self.path}.")
        with open(self.path, "rb") as f:
            f.seek(off)
            return f.read(size)

    def _cstr(self, rva: int) -> str:
        data = self.read(rva, 256)
        return data.split(b"\0", 1)[0].decode("latin-1")

    def imports(self) -> dict:
        """RVA слота IAT → `dll!имя` (или `dll!#ординал`)."""
        if self._imports is not None:
            return self._imports
        out: dict = {}
        rva, size = self.import_dir
        if rva and size and self.rva2off(rva) is not None:
            desc_rva = rva
            while True:
                oft, _, _, name_rva, ft = struct.unpack("<IIIII", self.read(desc_rva, 20))
                if not name_rva:
                    break
                dll = self._cstr(name_rva)
                lookup = oft or ft
                i = 0
                while True:
                    thunk, = struct.unpack("<Q", self.read(lookup + i * 8, 8))
                    if not thunk:
                        break
                    if thunk >> 63:
                        label = f"#{thunk & 0xFFFF}"
                    else:
                        label = self._cstr((thunk & 0x7FFFFFFF) + 2)
                    out[ft + i * 8] = f"{dll}!{label}"
                    i += 1
                desc_rva += 20
        self._imports = out
        return out


_pe_memo: dict = {}


def _pe(path: str) -> Pe:
    st = os.stat(path) if os.path.isfile(path) else None
    key = (path, st.st_size, st.st_mtime_ns) if st else (path,)
    if key not in _pe_memo:
        _pe_memo[key] = Pe(path)
    return _pe_memo[key]


# --- карта символов ----------------------------------------------------------------

class SymbolIndex:
    """Отсортированные по RVA символы функций и данных; поиск «ближайший ≤ target»."""

    def __init__(self, rows: list):
        self.funcs = self._build(r for r in rows if r[3] == "function")
        self.data = self._build(r for r in rows if r[3] == "data")
        # vftable: декорированное имя `??_7Класс@@6B@`; ключ — RVA начала
        self.vtables = {r[2]: r[1] or r[0] for r in rows if r[0].startswith("??_7")}

    @staticmethod
    def _build(rows) -> tuple:
        items = sorted(((r[2], r[1] or r[0], r[4]) for r in rows), key=lambda x: x[0])
        return [i[0] for i in items], items

    @staticmethod
    def _near(index: tuple, rva: int) -> Optional[tuple]:
        keys, items = index
        i = bisect.bisect_right(keys, rva) - 1
        if i < 0:
            return None
        base, name, size = items[i]
        off = rva - base
        return (name, off) if off < max(size, 1) or off <= _MAX_OFFSET else None

    def resolve(self, rva: int, prefer: str = "function") -> Optional[str]:
        order = (self.funcs, self.data) if prefer == "function" else (self.data, self.funcs)
        for index in order:
            hit = self._near(index, rva)
            if hit:
                return hit[0] if hit[1] == 0 else f"{hit[0]}+{hit[1]:#x}"
        return None

    def data_at(self, rva: int) -> Optional[str]:
        """Имя глобала, если rva внутри/в начале data-символа (без размеров — по ближайшему)."""
        hit = self._near(self.data, rva)
        if hit is None or hit[1] > _DATA_SLACK:
            return None   # у публичных символов нет размеров: дальний «ближайший» — это уже чужая область
        return hit[0] if hit[1] == 0 else f"{hit[0]}+{hit[1]:#x}"


_index_memo: dict = {}


def _symbol_index(which: str) -> tuple:
    db = pdb.import_pdb(which)
    key = (db, os.stat(db).st_mtime_ns)
    if key not in _index_memo:
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT name, undec, rva, kind, size FROM symbols").fetchall()
        finally:
            con.close()
        _index_memo[key] = SymbolIndex(rows)
    return _index_memo[key], db


def _find_symbol(db: str, symbol: str) -> tuple:
    """(имя, rva, size) по точному декорированному/раскрытому имени либо единственному glob-совпадению."""
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT name, undec, rva, size FROM symbols WHERE kind='function' "
                           "AND (name=? OR undec=?) ORDER BY rva", (symbol, symbol)).fetchall()
        if not rows:
            pat = pdb._like_pattern(symbol)
            rows = con.execute("SELECT name, undec, rva, size FROM symbols WHERE kind='function' "
                               "AND (undec LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\') "
                               "ORDER BY rva LIMIT 6", (pat, pat)).fetchall()
    finally:
        con.close()
    if not rows:
        raise PdbError(f"Символ функции {symbol!r} не найден в PDB (pdb_symbols поможет с поиском).")
    uniq = {r[2]: r for r in rows}   # алиасы на одном RVA — один символ
    if len(uniq) > 1:
        names = "; ".join(f"{r[1] or r[0]} @ {r[2]:#x}" for r in list(uniq.values())[:5])
        raise PdbError(f"Символ {symbol!r} неоднозначен ({len(uniq)}+): {names}. Уточни имя.")
    name, undec, rva, size = next(iter(uniq.values()))
    return undec or name, rva, size


# --- разбор тела -------------------------------------------------------------------

def _analyze(code: bytes, rva: int, size: int, pe: Pe, idx: SymbolIndex, listing: Optional[list] = None) -> dict:
    _iced()
    from iced_x86 import Code, Decoder, FlowControl, Formatter, FormatterSyntax, InstructionInfoFactory, \
        OpAccess, OpKind, Register

    base = pe.image_base
    dec = Decoder(64, code, ip=base + rva)
    fmt = Formatter(FormatterSyntax.INTEL) if listing is not None else None
    info = InstructionInfoFactory()
    imports = pe.imports()
    end = rva + size

    calls: dict = {}
    reads: dict = {}
    writes: dict = {}
    indirect: list = []
    unresolved: list = []
    vt_reg: dict = {}    # регистр → RVA vftable, из которой он загружен в этой функции

    def add_call(target_rva: int, kind: str, site: int, name: Optional[str] = None):
        if name is None and not pe.in_image(target_rva):
            return   # мусор от линейного прохода по хвосту/данным в коде
        key = (target_rva, kind)
        if key in calls:
            calls[key]["count"] += 1
            return
        name = name or idx.resolve(target_rva)
        calls[key] = {"rva": f"{target_rva:#x}", "name": name or f"sub_{target_rva:#x}",
                      "kind": kind, "site": f"{site:#x}", "count": 1}
        if name is None:
            unresolved.append({"rva": f"{target_rva:#x}", "note": "цель вне известных символов"})

    def vtable_name(vrva: int) -> str:
        return idx.vtables.get(vrva, f"vftable_{vrva:#x}")

    for ins in dec:
        site = ins.ip - base
        if site >= end:
            break
        if fmt is not None:
            listing.append(f"{site:#x}\t{fmt.format(ins)}")

        fc = ins.flow_control
        is_call = fc == FlowControl.CALL or fc == FlowControl.INDIRECT_CALL
        is_jmp = fc == FlowControl.UNCONDITIONAL_BRANCH or fc == FlowControl.INDIRECT_BRANCH

        if fc == FlowControl.CALL or fc == FlowControl.UNCONDITIONAL_BRANCH:
            target = ins.near_branch_target - base
            if is_call:
                add_call(target, "direct", site)
            elif not (rva <= target < end):
                add_call(target, "tail", site)   # jmp в другую функцию = хвостовой вызов
        elif fc in (FlowControl.INDIRECT_CALL, FlowControl.INDIRECT_BRANCH) and (is_call or is_jmp):
            kind0 = ins.op0_kind
            if kind0 == OpKind.MEMORY and ins.is_ip_rel_memory_operand:
                slot = ins.ip_rel_memory_address - base
                if slot in imports:
                    add_call(slot, "import" if is_call else "tail_import", site, imports[slot])
                else:
                    indirect.append({"site": f"{site:#x}", "kind": "reg",
                                     "name": idx.data_at(slot) or f"[{slot:#x}]"})
            elif kind0 == OpKind.MEMORY and ins.memory_base != Register.NONE and ins.memory_index == Register.NONE:
                disp = ins.memory_displacement
                reg = ins.memory_base
                vrva = vt_reg.get(reg)
                if vrva is not None and disp % 8 == 0 and disp >= 0:
                    try:
                        slot_va, = struct.unpack("<Q", pe.read(vrva + disp, 8))
                    except (PdbError, struct.error):
                        slot_va = 0
                    target = slot_va - base
                    if slot_va and pe.in_image(target):
                        add_call(target, "virtual" if is_call else "tail", site)
                    else:
                        indirect.append({"site": f"{site:#x}", "kind": "vtable", "slot": disp // 8,
                                         "name": f"{vtable_name(vrva)} слот {disp // 8} (чисто виртуальный/пуст)"})
                else:
                    indirect.append({"site": f"{site:#x}", "kind": "vtable", "slot": disp // 8,
                                     "offset": disp})
            elif kind0 == OpKind.REGISTER:
                indirect.append({"site": f"{site:#x}", "kind": "reg"})
            else:
                indirect.append({"site": f"{site:#x}", "kind": "reg"})
            if is_call:
                # после вызова volatile-регистры потеряны
                for r in (Register.RAX, Register.RCX, Register.RDX, Register.R8, Register.R9,
                          Register.R10, Register.R11):
                    vt_reg.pop(r, None)
            continue

        if is_call:
            for r in (Register.RAX, Register.RCX, Register.RDX, Register.R8, Register.R9,
                      Register.R10, Register.R11):
                vt_reg.pop(r, None)
            continue
        if is_jmp:
            continue

        # данные и отслеживание регистров с vftable
        if ins.is_ip_rel_memory_operand:
            addr = ins.ip_rel_memory_address - base
            ii = info.info(ins)
            name = idx.data_at(addr)
            if ins.op0_kind == OpKind.REGISTER and addr in idx.vtables and (
                    ins.code in (Code.LEA_R64_M, Code.MOV_R64_RM64)):
                vt_reg[ins.op0_register] = addr
            if name is not None and not name.startswith(_NOISE):
                for m in ii.used_memory():
                    if m.base != Register.NONE or m.index != Register.NONE:
                        continue   # нужен именно rip-относительный операнд (iced даёт абсолютный адрес)
                    acc = m.access
                    entry = {"rva": f"{addr:#x}", "name": name}
                    if acc in (OpAccess.READ, OpAccess.COND_READ, OpAccess.READ_WRITE, OpAccess.READ_COND_WRITE):
                        reads.setdefault(addr, entry)
                    if acc in (OpAccess.WRITE, OpAccess.COND_WRITE, OpAccess.READ_WRITE, OpAccess.READ_COND_WRITE):
                        writes.setdefault(addr, entry)
                    if acc == OpAccess.NO_MEM_ACCESS:   # lea: берётся адрес глобала
                        reads.setdefault(addr, {**entry, "note": "адрес (lea)"})
            continue

        # перезапись отслеживаемого регистра чем-то другим сбрасывает привязку
        if ins.op0_kind == OpKind.REGISTER and vt_reg:
            for r in list(vt_reg):
                if r == ins.op0_register or _same_family(r, ins.op0_register):
                    vt_reg.pop(r, None)

    return {
        "calls": sorted(calls.values(), key=lambda c: int(c["site"], 16)),
        "reads": sorted(reads.values(), key=lambda c: int(c["rva"], 16)),
        "writes": sorted(writes.values(), key=lambda c: int(c["rva"], 16)),
        "indirect": indirect,
        "unresolved": unresolved,
    }


def _full(reg):
    from iced_x86 import Register, RegisterInfo
    return RegisterInfo(reg).full_register


def _same_family(a, b) -> bool:
    """Подрегистры одного 64-битного (eax/ax/al ↔ rax)."""
    return _full(a) == _full(b)


# --- API ---------------------------------------------------------------------------

def native_calls(symbol: str, pdb_name: str = "server", listing_path: Optional[str] = None) -> dict:
    """Прямые вызовы, импорты, виртуальные вызовы и глобалы одного нативного символа (глубина 1)."""
    if pdb_name not in ("server", "client"):
        raise PdbError("pdb_name: server или client.")
    exe = config.server_exe() if pdb_name == "server" else config.client_exe()
    pe = _pe(exe)
    idx, db = _symbol_index(pdb_name)
    name, rva, size = _find_symbol(db, symbol)
    if size <= 0:
        raise PdbError(f"У символа {name} неизвестен размер — разбирать нечего.")
    code = pe.read(rva, size)
    listing: Optional[list] = [] if listing_path else None
    facts = _analyze(code, rva, size, pe, idx, listing)
    if listing_path:
        with open(listing_path, "w", encoding="utf-8") as f:
            f.write(f"; {name} rva={rva:#x} size={size}\n" + "\n".join(listing) + "\n")
    out = {"symbol": name, "rva": f"{rva:#x}", "size": size, "size_approx": True,
           "pdb": pdb_name, **facts}
    if listing_path:
        out["listing"] = listing_path
    return out


def main(argv: Optional[list] = None) -> None:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="граф вызовов нативного символа")
    ap.add_argument("symbol")
    ap.add_argument("--pdb", default="server", choices=("server", "client"))
    ap.add_argument("--listing", help="записать сырой дизасм в этот файл (в stdout не идёт)")
    a = ap.parse_args(argv)
    print(json.dumps(native_calls(a.symbol, a.pdb, a.listing), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
