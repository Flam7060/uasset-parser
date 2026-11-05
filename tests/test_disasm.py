import os
import struct
import unittest

from arkre import config, disasm, pdb
from arkre.disasm import SymbolIndex

BASE = 0x140000000
FN = 0x1000           # тело тестовой функции
CALLEE = 0x2000
VT = 0x5000           # vftable класса Foo
VSLOT = 0x3000        # цель слота 2 vftable
IAT = 0x6000          # слот импорта


class FakePe:
    image_base = BASE

    def __init__(self, mem: dict):
        self.mem = mem

    def in_image(self, rva):
        return 0 <= rva < 0x10000

    def read(self, rva, size):
        return bytes(self.mem.get(rva + i, 0) for i in range(size))

    def imports(self):
        return {IAT: "KERNEL32.dll!Sleep"}


def rel32(op: bytes, at: int, target: int) -> bytes:
    return op + struct.pack("<i", target - (at + len(op) + 4))


def build():
    code = b""
    at = FN
    code += rel32(b"\xe8", at, CALLEE); at = FN + len(code)            # call CALLEE
    code += rel32(b"\xff\x15", at, IAT); at = FN + len(code)           # call [rip+IAT]
    code += rel32(b"\x48\x8d\x0d", at, VT); at = FN + len(code)        # lea rcx,[rip+VT]
    code += b"\xff\x51\x10"                                            # call [rcx+0x10] (слот 2)
    code += b"\x48\x31\xc9"                                            # xor rcx,rcx (сброс)
    code += b"\xff\x51\x08"                                            # call [rcx+8] — тип потерян
    code += b"\xff\xd0"                                                # call rax
    code += b"\xc3"
    mem = {FN + i: b for i, b in enumerate(code)}
    for i, b in enumerate(struct.pack("<Q", BASE + VSLOT)):
        mem[VT + 0x10 + i] = b
    rows = [("?Callee@@YAXXZ", "void Callee(void)", CALLEE, "function", 16),
            ("?Virt@Foo@@UEAAXXZ", "void Foo::Virt(void)", VSLOT, "function", 16),
            ("?Fn@@YAXXZ", "void Fn(void)", FN, "function", len(code)),
            ("??_7Foo@@6B@", "const Foo::`vftable'", VT, "data", 0)]
    return code, FakePe(mem), SymbolIndex(rows)


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        try:
            import iced_x86  # noqa: F401
        except ImportError:
            self.skipTest("нет iced-x86")

    def test_direct_import_virtual_and_indirect(self):
        code, pe, idx = build()
        r = disasm._analyze(code, FN, len(code), pe, idx)
        by_kind = {c["kind"]: c for c in r["calls"]}
        self.assertEqual(by_kind["direct"]["name"], "void Callee(void)")
        self.assertEqual(by_kind["import"]["name"], "KERNEL32.dll!Sleep")
        self.assertEqual(by_kind["virtual"]["name"], "void Foo::Virt(void)")
        kinds = sorted((i["kind"], i.get("slot")) for i in r["indirect"])
        self.assertEqual(kinds, [("reg", None), ("vtable", 1)])
        self.assertEqual(r["unresolved"], [])

    def test_resolve_offset(self):
        _, _, idx = build()
        self.assertEqual(idx.resolve(CALLEE + 0x4), "void Callee(void)+0x4")


def _real_available() -> bool:
    return (os.path.isfile(config.server_exe()) and os.path.isfile(config.pdb_server())
            and pdb.imported("server") is not None)


@unittest.skipUnless(_real_available(), "нет ShooterGameServer.exe/PDB (или PDB не импортирован)")
class RealBinaryTests(unittest.TestCase):
    def test_character_tick_has_named_calls(self):
        try:
            import iced_x86  # noqa: F401
        except ImportError:
            self.skipTest("нет iced-x86")
        r = disasm.native_calls("*AShooterCharacter::Tick(float)")
        self.assertTrue(r["calls"])
        self.assertTrue(any("FWeakObjectPtr::IsValid" in c["name"] for c in r["calls"]))
        self.assertTrue(r["indirect"] or any(c["kind"] == "virtual" for c in r["calls"]))


if __name__ == "__main__":
    unittest.main()
