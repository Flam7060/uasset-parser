"""Рефлексия UE4 (ARK SE 358.26): имена, объекты, свойства. Источник — ext_dump.py."""
from __future__ import annotations

import re
import struct
import time
from typing import Optional

from . import LiveError
from .proc import Process, ok

# Смещения UObject/UStruct/UProperty (UE 4.5, ARK SE), сверены вживую на 358.26.
OBJ_FLAGS, OBJ_CLASS, OBJ_NAME, OBJ_OUTER = 0x8, 0x10, 0x18, 0x20
TEMPLATE_FLAGS = 0x10 | 0x20          # ClassDefaultObject | ArchetypeObject
STRUCT_SUPER, STRUCT_CHILDREN, STRUCT_SIZE = 0x30, 0x38, 0x40
FIELD_NEXT, PROP_ELEMENT_SIZE, PROP_OFFSET, PROP_EXTRA = 0x28, 0x34, 0x4C, 0x70

NAMES_PATTERN = "E8 ?? ?? ?? ?? 48 89 1D ?? ?? ?? ?? 48 8B C3 48 8B 5C 24 ?? 48 83 C4 28 C3 48 8B 5C 24 ??"
WORLD_PATTERN = ("48 89 4C 24 ?? 48 81 EC ?? ?? ?? ?? E8 ?? ?? ?? ?? 48 8B 8C 24 ?? ?? ?? ?? "
                 "E8 ?? ?? ?? ?? 48 8B 05 ?? ?? ?? ?? 48 8B 80 ?? ?? ?? ?? 48 83 78 ?? ?? 74 2C 48 8B 05 ??")

INDEX_TTL = 10.0        # секунд живёт индекс «класс → объекты»


def _pattern(text: str):
    return re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)]))
                               for t in text.split()), re.S)


class Reflection:
    def __init__(self, p: Process, object_array_rva: int):
        self.p = p
        self.object_array_rva = object_array_rva
        self.names: dict = {}
        self._props: dict = {}
        self._chain: dict = {}
        self._index: Optional[tuple] = None
        code_at, code = self._text()
        self.gnames = self._resolve_names(code_at, code)
        m = _pattern(WORLD_PATTERN).search(code)
        self.gworld = (code_at + m.start() + 80 + struct.unpack_from("<i", code, m.start() + 76)[0]
                       if m else 0)

    # --- адреса -----------------------------------------------------------------
    def _text(self):
        p = self.p
        pe = p.i32(p.base + 0x3C)
        count = struct.unpack("<H", p.rd(p.base + pe + 6, 2) or b"\0\0")[0]
        opt = struct.unpack("<H", p.rd(p.base + pe + 20, 2) or b"\0\0")[0]
        for i in range(count):
            s = p.rd(p.base + pe + 24 + opt + i * 40, 40)
            if s and s[:8].rstrip(b"\0") == b".text":
                size, rva = struct.unpack("<II", s[8:16])
                start = p.base + rva
                parts = []
                for off in range(0, size, 1 << 20):
                    n = min(1 << 20, size - off)
                    parts.append(p.rd(start + off, n) or b"\0" * n)
                return start, b"".join(parts)
        raise LiveError("В образе игры нет секции .text.")

    def _resolve_names(self, code_at: int, code: bytes) -> int:
        m = _pattern(NAMES_PATTERN).search(code)
        if not m:
            raise LiveError("Сигнатура GNames не найдена (другая сборка игры?).")
        glob = code_at + m.start() + 12 + struct.unpack_from("<i", code, m.start() + 8)[0]
        for cand in (self.p.u64(glob), glob):
            self.gnames = cand
            self.names.clear()
            if ok(cand) and self.fname(0) == "None":
                return cand
        raise LiveError("GNames не прошёл проверку: имя с индексом 0 не None.")

    # --- имена и объекты ---------------------------------------------------------
    def fname(self, index: int, number: int = 0) -> str:
        p = self.p
        if not 0 <= index < 50_000_000:
            return "?"
        if index not in self.names:
            chunk = p.u64(self.gnames + (index // 16384) * 8)
            entry = p.u64(chunk + (index % 16384) * 8) if ok(chunk) else 0
            raw = p.rd(entry + 16, 256) if ok(entry) else None
            if raw is None:
                return "?"
            if p.i32(entry) & 0x80000000:
                self.names[index] = raw.decode("utf-16-le", "replace").split("\0")[0]
            else:
                self.names[index] = raw.split(b"\0")[0].decode("utf-8", "replace")
        return self.names[index] + ("_%d" % (number - 1) if number > 0 else "")

    def name_at(self, a: int) -> str:
        b = self.p.rd(a, 8)
        return self.fname(*struct.unpack("<ii", b)) if b else "?"

    def objname(self, o: int) -> str:
        return self.name_at(o + OBJ_NAME) if ok(o) else ""

    def cls(self, o: int) -> int:
        return self.p.u64(o + OBJ_CLASS)

    def chain(self, c: int) -> list[str]:
        """Имена класса и предков в исходном регистре."""
        if c not in self._chain:
            out, cur = [], c
            for _ in range(48):
                if not ok(cur):
                    break
                out.append(self.objname(cur))
                cur = self.p.u64(cur + STRUCT_SUPER)
            self._chain[c] = out
        return self._chain[c]

    def full_name(self, o: int) -> str:
        parts, cur = [], o
        for _ in range(16):
            if not ok(cur):
                break
            parts.append(self.objname(cur))
            cur = self.p.u64(cur + OBJ_OUTER)
        return ".".join(reversed(parts))

    def package_of(self, o: int) -> str:
        cur = o
        for _ in range(16):
            nxt = self.p.u64(cur + OBJ_OUTER)
            if not ok(nxt):
                return self.objname(cur)
            cur = nxt
        return ""

    def props(self, s: int) -> dict:
        """Свойства класса/структуры с предками: имя → (поле, смещение, имя класса поля)."""
        if s not in self._props:
            out, cur = {}, s
            for _ in range(48):
                if not ok(cur):
                    break
                f, seen = self.p.u64(cur + STRUCT_CHILDREN), set()
                while ok(f) and f not in seen and len(seen) < 8192:
                    seen.add(f)
                    out.setdefault(self.objname(f), (f, self.p.i32(f + PROP_OFFSET),
                                                     self.objname(self.cls(f))))
                    f = self.p.u64(f + FIELD_NEXT)
                cur = self.p.u64(cur + STRUCT_SUPER)
            self._props[s] = out
        return self._props[s]

    def objects(self):
        p = self.p
        arr = p.base + self.object_array_rva
        chunks, count = p.u64(arr + 0x10), p.i32(arr + 0x24)
        for ci in range((count + 65535) // 65536):
            cp = p.u64(chunks + ci * 8)
            n = min(65536, count - ci * 65536)
            blob = p.rd(cp, n * 16) if ok(cp) else None
            if blob:
                for k in range(n):
                    ptr = struct.unpack_from("<Q", blob, k * 16)[0]
                    if ok(ptr):
                        yield ptr

    def object_count(self) -> int:
        return self.p.i32(self.p.base + self.object_array_rva + 0x24)

    def index(self) -> dict:
        """Класс (нижний регистр) → адреса живых объектов; общий для вызовов ≤ INDEX_TTL с."""
        now = time.time()
        if self._index and now - self._index[0] < INDEX_TTL:
            return self._index[1]
        by_class: dict = {}
        for o in self.objects():
            c = self.cls(o)
            if ok(c):
                by_class.setdefault(c, []).append(o)
        self._index = (now, by_class)
        return by_class

    def weak(self, a: int) -> int:
        """FWeakObjectPtr: индекс в массиве объектов → адрес."""
        p = self.p
        index = p.i32(a)
        if index < 0:
            return 0
        arr = p.base + self.object_array_rva
        cp = p.u64(p.u64(arr + 0x10) + (index // 65536) * 8)
        return p.u64(cp + (index % 65536) * 16) if ok(cp) else 0

    # --- значения ----------------------------------------------------------------
    def tarray(self, a: int) -> tuple[int, int]:
        return self.p.u64(a), self.p.i32(a + 8)

    def fstring(self, a: int, limit: int = 4096) -> str:
        data, n = self.tarray(a)
        if not ok(data) or not 0 < n <= limit:
            return ""
        return (self.p.rd(data, n * 2) or b"").decode("utf-16-le", "replace").split("\0")[0]

    def ftext(self, a: int) -> str:
        pt = self.p.u64(a + 0x18)
        if not ok(pt):
            pt = self.p.u64(self.p.u64(a) + 0x10)
        return self.fstring(pt) if ok(pt) else ""

    def ref(self, o: int) -> Optional[dict]:
        if not ok(o):
            return None
        return {"address": hex(o), "class": self.objname(self.cls(o)), "name": self.objname(o)}
