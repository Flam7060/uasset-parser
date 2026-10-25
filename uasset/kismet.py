"""
Декодер Kismet-байткода UE4 4.x в варианте ARK SE.

Два адресных пространства. На диске указатель (UObject*/UProperty*) — 4 байта
FPackageIndex, FName — 8 байт (index, number). В памяти 64-битного клиента —
8 и 12 байт (FScriptName). Переходы (Jump, PushExecutionFlow, SkipOffsetConst,
skip в Context) адресуют память, поэтому декодер ведёт оба оффсета, а
итоговый размер в памяти сверяется с UStruct::BytecodeBufferSize — это
проверка того, что арность каждого опкода угадана верно.

Отличия ARK от стокового 4.x, найденные по ассетам:
  * EX_Let (0x0F) — без указателя на свойство, только var и expr;
  * EX_Context/ClassContext — после R-value указателя ещё u8 (тип свойства);
  * 0x64 — константа double (8 байт), а не LetValueOnPersistentFrame:
    нужна для CustomDataDoubles; размер одинаков на диске и в памяти;
  * 0x65 — нестандартный опкод с двумя строковыми подвыражениями
    (текст + числовой ключ); разбирается как пара выражений.
"""
from dataclasses import dataclass, field

from .reader import Reader, ParseError

PTR_DISK, PTR_MEM = 4, 8
NAME_DISK, NAME_MEM = 8, 12
MAX_EXPR_DEPTH = 256

EX_EndFunctionParms = 0x16
EX_EndStructConst = 0x30
EX_EndArray = 0x32
EX_EndOfScript = 0x53

# Операнды: p — указатель (FPackageIndex), n — FName, s — абсолютный адрес
# кода в памяти (u32), k — относительный размер пропуска (u32), e — подвыражение, P — параметры до EndFunctionParms,
# F — поля до EndStructConst, A — элементы до EndArray, i/f/d/b/h/q/Q — i32,
# f32, f64, u8, u16, i64, u64, c — ANSI C-строка, w — UTF-16 C-строка.
OPCODES = {
    0x00: ("LocalVariable", "p"),
    0x01: ("InstanceVariable", "p"),
    0x02: ("DefaultVariable", "p"),
    0x04: ("Return", "e"),
    0x06: ("Jump", "s"),
    0x07: ("JumpIfNot", "se"),
    0x09: ("Assert", "hbe"),
    0x0B: ("Nothing", ""),
    0x0F: ("Let", "ee"),
    0x12: ("ClassContext", "ekpbe"),
    0x13: ("MetaCast", "pe"),
    0x14: ("LetBool", "ee"),
    0x15: ("EndParmValue", ""),
    0x16: ("EndFunctionParms", ""),
    0x17: ("Self", ""),
    0x18: ("Skip", "ke"),
    0x19: ("Context", "ekpbe"),
    0x1A: ("Context_FailSilent", "ekpbe"),
    0x1B: ("VirtualFunction", "nP"),
    0x1C: ("FinalFunction", "pP"),
    0x1D: ("IntConst", "i"),
    0x1E: ("FloatConst", "f"),
    0x1F: ("StringConst", "c"),
    0x20: ("ObjectConst", "p"),
    0x21: ("NameConst", "n"),
    0x22: ("RotationConst", "fff"),
    0x23: ("VectorConst", "fff"),
    0x24: ("ByteConst", "b"),
    0x25: ("IntZero", ""),
    0x26: ("IntOne", ""),
    0x27: ("True", ""),
    0x28: ("False", ""),
    0x29: ("TextConst", "eee"),
    0x2A: ("NoObject", ""),
    0x2B: ("TransformConst", "ffffffffff"),
    0x2C: ("IntConstByte", "b"),
    0x2D: ("NoInterface", ""),
    0x2E: ("DynamicCast", "pe"),
    0x2F: ("StructConst", "piF"),
    0x30: ("EndStructConst", ""),
    0x31: ("SetArray", "eA"),
    0x32: ("EndArray", ""),
    0x34: ("UnicodeStringConst", "w"),
    0x35: ("Int64Const", "q"),
    0x36: ("UInt64Const", "Q"),
    0x38: ("PrimitiveCast", "be"),
    0x42: ("StructMemberContext", "pe"),
    0x43: ("LetMulticastDelegate", "ee"),
    0x44: ("LetDelegate", "ee"),
    0x45: ("LocalVirtualFunction", "nP"),
    0x46: ("LocalFinalFunction", "pP"),
    0x48: ("LocalOutVariable", "p"),
    0x4A: ("DeprecatedOp4A", ""),
    0x4B: ("InstanceDelegate", "n"),
    0x4C: ("PushExecutionFlow", "s"),
    0x4D: ("PopExecutionFlow", ""),
    0x4E: ("ComputedJump", "e"),
    0x4F: ("PopExecutionFlowIfNot", "e"),
    0x50: ("Breakpoint", ""),
    0x51: ("InterfaceContext", "e"),
    0x52: ("ObjToInterfaceCast", "pe"),
    0x53: ("EndOfScript", ""),
    0x54: ("CrossInterfaceCast", "pe"),
    0x55: ("InterfaceToObjCast", "pe"),
    0x5A: ("WireTracepoint", ""),
    0x5B: ("SkipOffsetConst", "s"),
    0x5C: ("AddMulticastDelegate", "ee"),
    0x5D: ("ClearMulticastDelegate", "e"),
    0x5E: ("Tracepoint", ""),
    0x5F: ("LetObj", "ee"),
    0x60: ("LetWeakObjPtr", "ee"),
    0x61: ("BindDelegate", "nee"),
    0x62: ("RemoveMulticastDelegate", "ee"),
    0x63: ("CallMulticastDelegate", "pP"),
    0x64: ("ARK_DoubleConst", "d"),
    0x65: ("ARK_LocString", "ee"),
}

_LIST_END = {"P": EX_EndFunctionParms, "F": EX_EndStructConst, "A": EX_EndArray}
_FIXED = {"i": ("i32", 4), "f": ("f32", 4), "d": ("f64", 8), "b": ("u8", 1), "h": ("u16", 2),
          "q": ("i64", 8), "Q": ("u64", 8)}


@dataclass
class Ptr:
    index: int
    name: str

    def __str__(self):
        return self.name


@dataclass
class Expr:
    op: int
    name: str
    disk: int                   # оффсет от начала байткода на диске
    mem: int                    # оффсет в памяти (цель переходов)
    args: list = field(default_factory=list)   # операнды в порядке спецификации
    end_mem: int = 0


class KismetError(ParseError):
    pass


class Decoder:
    def __init__(self, pkg, data, start, size):
        self.pkg = pkg
        self.r = Reader(data, start, start + size)
        self.base = start
        self.mem = 0
        self.depth = 0

    def _disk(self):
        return self.r.tell() - self.base

    def _ptr(self):
        at = self.r.tell()
        idx = self.r.i32()
        self.mem += PTR_MEM
        if not self.pkg.valid_index(idx):
            raise KismetError(f"указатель {idx} @ disk+{at - self.base:#x} вне таблиц пакета")
        return Ptr(idx, self.pkg.full_name(idx))

    def _name(self):
        v = self.pkg.read_fname(self.r)
        self.mem += NAME_MEM
        return v

    def expr(self):
        if self.depth > MAX_EXPR_DEPTH:
            raise KismetError(f"вложенность выражений > {MAX_EXPR_DEPTH} @ disk+{self._disk():#x}")
        disk, mem = self._disk(), self.mem
        op = self.r.u8()
        self.mem += 1
        spec = OPCODES.get(op)
        if spec is None:
            raise KismetError(f"неизвестный опкод {op:#04x} @ disk+{disk:#x} mem+{mem:#x}")
        e = Expr(op, spec[0], disk, mem)
        self.depth += 1
        try:
            for kind in spec[1]:
                e.args.append(self._operand(kind))
        finally:
            self.depth -= 1
        e.end_mem = self.mem
        return e

    def _operand(self, kind):
        r = self.r
        if kind == "p":
            return self._ptr()
        if kind == "n":
            return self._name()
        if kind in "sk":
            self.mem += 4
            return r.u32()
        if kind == "e":
            return self.expr()
        if kind in _LIST_END:
            items = []
            end = _LIST_END[kind]
            while True:
                if r.at_end():
                    raise KismetError(f"список без терминатора {end:#04x}")
                if r.data[r.tell()] == end:
                    r.u8()
                    self.mem += 1
                    return items
                items.append(self.expr())
        if kind in _FIXED:
            meth, size = _FIXED[kind]
            self.mem += size
            v = getattr(r, meth)()
            return round(v, 6) if kind in "fd" else v
        if kind == "c":
            before = r.tell()
            v = r.cstring()
            self.mem += r.tell() - before
            return v
        if kind == "w":
            before = r.tell()
            v = r.wcstring()
            self.mem += r.tell() - before
            return v
        raise AssertionError(f"спецификация операнда {kind!r}")


@dataclass
class Script:
    statements: list
    mem_size: int
    expected_mem: int
    error: str = ""
    tail: bytes = b""           # не разобранный остаток при ошибке

    @property
    def mem_ok(self):
        return not self.error and self.mem_size == self.expected_mem


def decode(pkg, data, start, disk_size, mem_size):
    """Разбирает весь скрипт. При ошибке возвращает то, что успели, и хвост."""
    d = Decoder(pkg, data, start, disk_size)
    stmts = []
    stmt_start = start
    try:
        while not d.r.at_end():
            stmt_start = d.r.tell()
            stmts.append(d.expr())
    except ParseError as ex:
        # хвост — с начала инструкции, на которой споткнулись, включая опкод
        return Script(stmts, d.mem, mem_size, str(ex), data[stmt_start:start + disk_size])
    s = Script(stmts, d.mem, mem_size)
    if d.mem != mem_size:
        s.error = (f"размер в памяти {d.mem} ≠ {mem_size} из заголовка UStruct: "
                   f"арность какого-то опкода угадана неверно")
    elif not stmts or stmts[-1].op != EX_EndOfScript:
        s.error = "скрипт не завершается EX_EndOfScript"
    return s


def jump_targets(stmts):
    """Все адреса памяти, на которые ссылается код."""
    targets = set()

    def walk(e):
        spec = OPCODES[e.op][1]
        for kind, a in zip(spec, e.args):
            if kind == "s":
                targets.add(a)
            elif kind == "e":
                walk(a)
            elif kind in _LIST_END:
                for x in a:
                    walk(x)
    for s in stmts:
        walk(s)
    return targets
