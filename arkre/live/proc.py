"""Процесс игры: PID, карта памяти, ограниченное чтение, проверка сборки."""
from __future__ import annotations

import os
import struct
from typing import Optional

from . import LiveError

MAX_READ = 1 << 20      # правило 5: не больше 1 МБ за одно чтение


def find_pids() -> list[int]:
    """PID процессов, у которых argv[0] оканчивается на ShooterGame.exe (правило 3)."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as fh:
                argv0 = fh.read().split(b"\0")[0]
        except OSError:
            continue
        if argv0.endswith(b"ShooterGame.exe"):
            out.append(int(d))
    return sorted(out)


def find_pid() -> int:
    pids = find_pids()
    if not pids:
        raise LiveError("Игра не запущена: нет процесса ShooterGame.exe.")
    if len(pids) > 1:
        raise LiveError(f"Несколько процессов ShooterGame.exe: {', '.join(map(str, pids))}. "
                        f"Угадывать не буду — оставь один.")
    return pids[0]


def ok(p: int) -> bool:
    """Правдоподобный пользовательский адрес x64."""
    return 0x10000 <= p < 0x7fffffffffff


class Process:
    def __init__(self, pid: Optional[int] = None):
        self.pid = pid or find_pid()
        try:
            self.fd = os.open(f"/proc/{self.pid}/mem", os.O_RDONLY)
        except OSError as ex:
            raise LiveError(f"Не открыть /proc/{self.pid}/mem на чтение: {ex}") from None
        self.base = self._module_base()

    def close(self) -> None:
        os.close(self.fd)

    def _module_base(self) -> int:
        try:
            with open(f"/proc/{self.pid}/maps") as fh:
                for line in fh:
                    if line.rstrip().endswith("ShooterGame.exe"):
                        return int(line.split("-")[0], 16)
        except OSError as ex:
            raise LiveError(f"Не прочитать карту памяти процесса {self.pid}: {ex}") from None
        raise LiveError("В карте памяти нет образа ShooterGame.exe.")

    # --- чтение: неудача — None, не исключение и не повтор ---------------------

    def rd(self, addr: int, n: int) -> Optional[bytes]:
        if n < 0 or n > MAX_READ:
            raise LiveError(f"Чтение {n} байт больше лимита {MAX_READ}.")
        if n == 0:
            return b""
        try:
            b = os.pread(self.fd, n, addr)
            return b if len(b) == n else None
        except (OSError, OverflowError):
            return None

    def u8(self, a: int) -> int:
        b = self.rd(a, 1)
        return b[0] if b else 0

    def u64(self, a: int) -> int:
        b = self.rd(a, 8)
        return struct.unpack("<Q", b)[0] if b else 0

    def i32(self, a: int) -> int:
        b = self.rd(a, 4)
        return struct.unpack("<i", b)[0] if b else 0

    def u32(self, a: int) -> int:
        b = self.rd(a, 4)
        return struct.unpack("<I", b)[0] if b else 0


# --- PE-образ в памяти и проверка сборки (правило 4) ------------------------------

def read_build(p: Process) -> dict:
    """TimeDateStamp и CodeView (GUID, age) из образа игры в памяти."""
    pe = p.i32(p.base + 0x3C)
    if p.rd(p.base + pe, 4) != b"PE\0\0":
        raise LiveError("Образ в памяти не похож на PE: смещение e_lfanew неверно.")
    timestamp = p.u32(p.base + pe + 8)
    opt = p.base + pe + 24
    magic = struct.unpack("<H", p.rd(opt, 2) or b"\0\0")[0]
    if magic != 0x20B:
        raise LiveError("Ожидается PE32+ (x64).")
    debug_rva, debug_size = struct.unpack("<II", p.rd(opt + 112 + 6 * 8, 8) or b"\0" * 8)
    guid = age = None
    for i in range(min(debug_size // 28, 16)):
        e = p.rd(p.base + debug_rva + i * 28, 28)
        if not e:
            continue
        typ, size, rva = struct.unpack_from("<I", e, 12)[0], struct.unpack_from("<I", e, 16)[0], \
            struct.unpack_from("<I", e, 20)[0]
        if typ == 2 and size >= 24:
            cv = p.rd(p.base + rva, 24)
            if cv and cv[:4] == b"RSDS":
                d1, d2, d3 = struct.unpack_from("<IHH", cv, 4)
                d4 = cv[12:20]
                guid = "{%08X-%04X-%04X-%s-%s}" % (d1, d2, d3, d4[:2].hex().upper(), d4[2:].hex().upper())
                age = struct.unpack_from("<I", cv, 20)[0]
                break
    return {"pe_timestamp": timestamp, "guid": guid, "age": age}
