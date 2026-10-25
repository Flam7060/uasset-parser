"""
Текстовый вывод: таблицы пакета, свойства, определения UProperty и
Kismet-псевдокод с метками переходов (адреса — в памяти, как в самом коде).
"""
import json

from .properties import Raw, ObjRef, StructValue, ArrayValue
from . import kismet

FUNC_FLAGS = [
    (0x00000001, "Final"), (0x00000002, "RequiredAPI"), (0x00000004, "BlueprintAuthorityOnly"),
    (0x00000008, "BlueprintCosmetic"), (0x00000040, "Net"), (0x00000080, "NetReliable"),
    (0x00000100, "NetRequest"), (0x00000200, "Exec"), (0x00000400, "Native"),
    (0x00000800, "Event"), (0x00001000, "NetResponse"), (0x00002000, "Static"),
    (0x00004000, "NetMulticast"), (0x00010000, "MulticastDelegate"), (0x00020000, "Public"),
    (0x00040000, "Private"), (0x00080000, "Protected"), (0x00100000, "Delegate"),
    (0x00200000, "NetServer"), (0x00400000, "HasOutParms"), (0x00800000, "HasDefaults"),
    (0x01000000, "NetClient"), (0x02000000, "DLLImport"), (0x04000000, "BlueprintCallable"),
    (0x08000000, "BlueprintEvent"), (0x10000000, "BlueprintPure"), (0x40000000, "Const"),
]

CPF_FLAGS = [
    (0x1, "Edit"), (0x2, "ConstParm"), (0x4, "BlueprintVisible"), (0x8, "ExportObject"),
    (0x10, "BlueprintReadOnly"), (0x20, "Net"), (0x40, "EditFixedSize"), (0x80, "Parm"),
    (0x100, "OutParm"), (0x200, "ZeroConstructor"), (0x400, "ReturnParm"),
    (0x800, "DisableEditOnTemplate"), (0x2000, "Transient"), (0x4000, "Config"),
    (0x10000, "DisableEditOnInstance"), (0x20000, "EditConst"), (0x40000, "GlobalConfig"),
    (0x80000, "InstancedReference"), (0x200000, "DuplicateTransient"),
    (0x400000, "SubobjectReference"), (0x1000000, "SaveGame"), (0x2000000, "NoClear"),
    (0x8000000, "ReferenceParm"), (0x10000000, "BlueprintAssignable"), (0x20000000, "Deprecated"),
    (0x40000000, "IsPlainOldData"), (0x80000000, "RepSkip"), (0x100000000, "RepNotify"),
    (0x200000000, "Interp"), (0x400000000, "NonTransactional"),
]

RF_FLAGS = [
    (0x1, "Public"), (0x2, "Standalone"), (0x4, "Native"), (0x8, "Transactional"),
    (0x10, "ClassDefaultObject"), (0x20, "ArchetypeObject"), (0x40, "Transient"),
]

CPF_Parm, CPF_OutParm, CPF_ReturnParm = 0x80, 0x100, 0x400

# ECastToken UE4 4.x — только встречающиеся в BP
PRIMITIVE_CASTS = {0x46: "ObjectToInterface", 0x47: "ObjectToBool", 0x49: "InterfaceToBool"}


def flag_str(value, table):
    names, rest = [], value
    for bit, name in table:
        if value & bit:
            names.append(name)
            rest &= ~bit
    if rest:
        names.append(f"{rest:#x}")
    return "|".join(names) if names else "0"


def label(addr):
    return f"L_{addr:04x}"


def quote(s):
    return json.dumps(s, ensure_ascii=False)


class Context:
    """Общие данные для вывода одного пакета."""

    def __init__(self, pkg, bodies):
        self.pkg = pkg
        self.bodies = bodies
        self.ubergraph_entries = self._collect_entries()

    def body_of(self, index):
        if index > 0 and index - 1 < len(self.bodies):
            return self.bodies[index - 1]
        return None

    def short(self, index):
        return self.pkg.obj_name(index)

    def _collect_entries(self):
        """Точки входа убер-графа: событие-заглушка вызывает
        ExecuteUbergraph_X(IntConst N) — N становится меткой в убер-графе."""
        entries = {}
        for b in self.bodies:
            if not b.script:
                continue
            fn = self.pkg.exports[b.index].object_name
            for st in b.script.statements:
                for call in _walk(st):
                    if call.name in ("VirtualFunction", "LocalVirtualFunction",
                                     "FinalFunction", "LocalFinalFunction"):
                        target = call.args[0]
                        tname = target if isinstance(target, str) else self.short(target.index)
                        params = call.args[1]
                        if tname.startswith("ExecuteUbergraph") and params and params[0].name == "IntConst":
                            entries.setdefault(params[0].args[0], []).append(fn)
        return entries


def _walk(e):
    yield e
    spec = kismet.OPCODES[e.op][1]
    for kind, a in zip(spec, e.args):
        if kind == "e":
            yield from _walk(a)
        elif kind in "PFA":
            for x in a:
                yield from _walk(x)


# --- выражения -------------------------------------------------------------

class ExprFormatter:
    def __init__(self, ctx):
        self.ctx = ctx

    def var(self, p):
        return self.ctx.short(p.index)

    def args(self, items):
        return ", ".join(self.fmt(x) for x in items)

    def call(self, name, params):
        s = f"{name}({self.args(params)})"
        if name.startswith("ExecuteUbergraph") and params and params[0].name == "IntConst":
            s += f"  /* -> {label(params[0].args[0])} */"
        return s

    def fmt(self, e):
        n, a = e.name, e.args
        if n in ("LocalVariable", "InstanceVariable", "DefaultVariable", "LocalOutVariable"):
            return self.var(a[0])
        if n in ("Let", "LetObj", "LetBool", "LetWeakObjPtr", "LetDelegate", "LetMulticastDelegate"):
            return f"{self.fmt(a[0])} = {self.fmt(a[1])}"
        if n in ("Context", "ClassContext"):
            return f"{self.fmt(a[0])}.{self.fmt(a[4])}"
        if n == "Context_FailSilent":
            return f"{self.fmt(a[0])}?.{self.fmt(a[4])}"
        if n in ("VirtualFunction", "LocalVirtualFunction"):
            return self.call(a[0], a[1])
        if n in ("FinalFunction", "LocalFinalFunction"):
            return self.call(self.var(a[0]), a[1])
        if n == "CallMulticastDelegate":
            return f"broadcast {self.call(self.var(a[0]), a[1])}"
        if n == "Return":
            inner = "" if a[0].name == "Nothing" else " " + self.fmt(a[0])
            return "return" + inner
        if n == "Jump":
            return f"goto {label(a[0])}"
        if n == "JumpIfNot":
            return f"if (!{self.fmt(a[1])}) goto {label(a[0])}"
        if n == "PushExecutionFlow":
            return f"push_flow {label(a[0])}"
        if n == "PopExecutionFlow":
            return "pop_flow"
        if n == "PopExecutionFlowIfNot":
            return f"if (!{self.fmt(a[0])}) pop_flow"
        if n == "ComputedJump":
            return f"goto *{self.fmt(a[0])}"
        if n == "SkipOffsetConst":
            return label(a[0])
        if n == "Skip":
            return f"skip({self.fmt(a[1])})"
        if n == "Assert":
            return f"assert({self.fmt(a[2])})  /* line {a[0]} */"
        if n == "IntConst" or n in ("ByteConst", "IntConstByte", "Int64Const", "UInt64Const"):
            return str(a[0])
        if n in ("FloatConst", "ARK_DoubleConst"):
            return repr(a[0])
        if n == "StringConst":
            return quote(a[0])
        if n == "UnicodeStringConst":
            return "u" + quote(a[0])
        if n == "NameConst":
            return f"'{a[0]}'"
        if n == "ObjectConst":
            return a[0].name
        if n == "RotationConst":
            return f"Rotator({a[0]}, {a[1]}, {a[2]})"
        if n == "VectorConst":
            return f"Vector({a[0]}, {a[1]}, {a[2]})"
        if n == "TransformConst":
            return f"Transform(rot={a[0:4]}, pos={a[4:7]}, scale={a[7:10]})"
        if n == "TextConst":
            return f"Text({self.args(a)})"
        if n == "ARK_LocString":
            # второй операнд — числовой ключ (похоже на CRC32), в логике не участвует
            key = a[1].args[0] if a[1].name == "StringConst" else self.fmt(a[1])
            return f"{self.fmt(a[0])}/*#{key}*/"
        if n == "StructConst":
            return f"{self.var(a[0])}{{{self.args(a[2])}}}"
        if n == "SetArray":
            return f"{self.fmt(a[0])} = [{self.args(a[1])}]"
        if n == "StructMemberContext":
            return f"{self.fmt(a[1])}.{self.var(a[0])}"
        if n == "DynamicCast":
            return f"Cast<{self.var(a[0])}>({self.fmt(a[1])})"
        if n == "MetaCast":
            return f"ClassCast<{self.var(a[0])}>({self.fmt(a[1])})"
        if n in ("ObjToInterfaceCast", "CrossInterfaceCast"):
            return f"InterfaceCast<{self.var(a[0])}>({self.fmt(a[1])})"
        if n == "InterfaceToObjCast":
            return f"ObjectCast<{self.var(a[0])}>({self.fmt(a[1])})"
        if n == "PrimitiveCast":
            return f"{PRIMITIVE_CASTS.get(a[0], f'Cast_{a[0]:#04x}')}({self.fmt(a[1])})"
        if n == "InterfaceContext":
            return self.fmt(a[0])
        if n == "InstanceDelegate":
            return f"&self.{a[0]}"
        if n == "BindDelegate":
            return f"{self.fmt(a[1])}.Bind({self.fmt(a[2])}, '{a[0]}')"
        if n == "AddMulticastDelegate":
            return f"{self.fmt(a[0])} += {self.fmt(a[1])}"
        if n == "RemoveMulticastDelegate":
            return f"{self.fmt(a[0])} -= {self.fmt(a[1])}"
        if n == "ClearMulticastDelegate":
            return f"{self.fmt(a[0])}.Clear()"
        fixed = {"Self": "self", "True": "true", "False": "false", "NoObject": "null",
                 "NoInterface": "null", "IntZero": "0", "IntOne": "1", "Nothing": "nop",
                 "EndOfScript": "end_of_script"}
        if n in fixed:
            return fixed[n]
        return n.lower()


def render_script(ctx, script, out, indent, tree=False):
    fmt = ExprFormatter(ctx)
    stmt_addrs = {s.mem for s in script.statements}
    targets = kismet.jump_targets(script.statements)
    entries = ctx.ubergraph_entries if any(
        s.name == "ComputedJump" for s in script.statements) else {}
    labels = targets | set(entries)
    # при оборванном разборе часть инструкций не прочитана — граница не проверяема
    for t in sorted(labels) if not script.tail else ():
        if t not in stmt_addrs and t != script.mem_size:
            out.append(f"{indent}!! {label(t)} не попадает на границу инструкции")
    for s in script.statements:
        if s.mem in labels:
            note = f"  // вход из {', '.join(entries[s.mem])}" if s.mem in entries else ""
            out.append(f"{indent}{label(s.mem)}:{note}")
        out.append(f"{indent}  {s.mem:04x}  {fmt.fmt(s)}")
        if tree:
            render_tree(s, out, indent + "        ")
    if script.error:
        out.append(f"{indent}!! {script.error}")
        if script.tail:
            out.append(f"{indent}!! не разобрано {len(script.tail)} байт: {script.tail[:48].hex(' ')}")
    else:
        out.append(f"{indent}// mem {script.mem_size} = заголовок {script.expected_mem}, проверено")


def render_tree(e, out, indent):
    spec = kismet.OPCODES[e.op][1]
    scalars = []
    children = []
    for kind, a in zip(spec, e.args):
        if kind == "e":
            children.append(("", a))
        elif kind in "PFA":
            children.extend((f"{kind}{i}", x) for i, x in enumerate(a))
        elif kind == "s":
            scalars.append(label(a))
        elif kind in ("c", "w"):
            scalars.append(quote(a))
        else:
            scalars.append(str(a))
    out.append(f"{indent}[d{e.disk:04x} m{e.mem:04x}] {e.op:02x} {e.name} {' '.join(scalars)}".rstrip())
    for _, c in children:
        render_tree(c, out, indent + "  ")


# --- свойства --------------------------------------------------------------

def value_str(v):
    if isinstance(v, (Raw, ObjRef)):
        return str(v)
    if isinstance(v, str):
        return quote(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={value_str(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(value_str(x) for x in v) + "]"
    return str(v)


def render_props(props, out, indent):
    for p in props:
        idx = f"[{p.array_index}]" if p.array_index else ""
        typ = p.type
        if p.struct_name:
            typ += f"<{p.struct_name}>"
        elif p.enum_name and p.enum_name != "None":
            typ += f"<{p.enum_name}>"
        elif p.inner_type:
            typ += f"<{p.inner_type}>"
        note = f"  !! {p.note}" if p.note else ""
        render_value(f"{p.name}{idx}: {typ}", p.value, out, indent, note)


def render_value(head, v, out, indent, note=""):
    if isinstance(v, StructValue) and not v.native:
        out.append(f"{indent}{head} {{{note}")
        render_props(v.fields, out, indent + "  ")
        out.append(f"{indent}}}")
    elif isinstance(v, StructValue):
        out.append(f"{indent}{head} = {value_str(v.fields)}{note}")
    elif isinstance(v, ArrayValue):
        out.append(f"{indent}{head} [{len(v.items)}]{note}")
        for i, x in enumerate(v.items):
            render_value(f"[{i}]", x, out, indent + "  ")
    else:
        out.append(f"{indent}{head} = {value_str(v)}{note}")


# --- определения UProperty и структуры ------------------------------------

def property_type(ctx, body):
    f = body.fields
    cls = body.class_name
    for key in ("PropertyClass", "Struct", "SignatureFunction", "InterfaceClass", "Enum"):
        ref = f.get(key)
        if ref is not None and ref.index != 0:
            inner = ref.name.split(".")[-1]
            if cls == "ClassProperty" and f.get("MetaClass"):
                inner = f["MetaClass"].name.split(".")[-1]
            return f"{cls}<{inner}>"
    if cls == "ArrayProperty" and f.get("Inner"):
        ib = ctx.body_of(f["Inner"].index)
        return f"ArrayProperty<{property_type(ctx, ib) if ib else f['Inner'].name}>"
    return cls


def children(ctx, struct_body):
    """Поля структуры: Children → цепочка Next (только экспорты пакета)."""
    out = []
    ref = struct_body.fields.get("Children")
    seen = set()
    idx = ref.index if ref else 0
    while idx > 0 and idx not in seen:
        seen.add(idx)
        b = ctx.body_of(idx)
        if b is None:
            break
        out.append(b)
        nxt = b.fields.get("Next")
        idx = nxt.index if nxt else 0
    return out


def render_struct_members(ctx, body, out, indent):
    members = [b for b in children(ctx, body) if b.kind == "property"]
    if not members:
        return
    is_func = body.kind == "function"
    params = [b for b in members if is_func and b.fields.get("PropertyFlags", 0) & CPF_Parm]
    if is_func:
        sig = []
        ret = ""
        for b in params:
            fl = b.fields["PropertyFlags"]
            name = ctx.pkg.exports[b.index].object_name
            t = property_type(ctx, b)
            if fl & CPF_ReturnParm:
                ret = f" -> {t}"
            else:
                sig.append(f"{'out ' if fl & CPF_OutParm else ''}{name}: {t}")
        out.append(f"{indent}signature: ({', '.join(sig)}){ret}")
    title = "locals" if is_func else "members"
    rest = [b for b in members if b not in params]
    if rest:
        out.append(f"{indent}{title}:")
        for b in rest:
            fl = b.fields.get("PropertyFlags", 0)
            dim = b.fields.get("ArrayDim", 1)
            arr = f"[{dim}]" if dim != 1 else ""
            out.append(f"{indent}  {ctx.pkg.exports[b.index].object_name}{arr}: {property_type(ctx, b)}"
                       f"  [{flag_str(fl, CPF_FLAGS)}]")


# --- пакет -----------------------------------------------------------------

def render_header(pkg, out):
    s = pkg.summary
    out.append(f"=== {pkg.path} ({len(pkg.data)} байт) ===")
    out.append(f"FileVersionUE4={s.file_version_ue4} Licensee={s.file_version_licensee} "
               f"Legacy={s.legacy_version} UE3={s.legacy_ue3_version} Engine={s.engine_version}")
    out.append(f"PackageFlags={s.package_flags:#x} Guid={s.guid} HeaderSize={s.total_header_size:#x} "
               f"BulkDataStart={s.bulk_data_start:#x} Source={s.package_source:#x}")
    out.append(f"Names {s.name_count}@{s.name_offset:#x}  Imports {s.import_count}@{s.import_offset:#x}  "
               f"Exports {s.export_count}@{s.export_offset:#x}  Depends@{s.depends_offset:#x}")
    if s.custom_versions:
        out.append(f"CustomVersions: {s.custom_versions}")
    if any(s.ark_extra):
        out.append(f"ARK extra summary ints: {s.ark_extra}")
    out.append("")


def render_names(pkg, out, filt):
    out.append(f"--- Name Map ({len(pkg.names)}) ---")
    for i, n in enumerate(pkg.names):
        if filt is None or filt in n.lower():
            out.append(f"  [{i}] {n}")
    out.append("")


def render_imports(pkg, out):
    out.append(f"--- Imports ({len(pkg.imports)}) ---")
    for i, imp in enumerate(pkg.imports):
        out.append(f"  [{-i - 1}] {pkg.full_name(-i - 1)}  ({imp.class_package}.{imp.class_name})")
    out.append("")


def render_export_table(pkg, out):
    out.append(f"--- Exports ({len(pkg.exports)}) ---")
    for i, e in enumerate(pkg.exports):
        sup = f" super={pkg.full_name(e.super_index)}" if e.super_index else ""
        outer = f" outer={pkg.obj_name(e.outer_index)}" if e.outer_index else ""
        out.append(f"  [{i + 1}] {e.object_name}  class={pkg.export_class(e)}{sup}{outer}  "
                   f"flags={flag_str(e.object_flags, RF_FLAGS)}  {e.serial_size}@{e.serial_offset:#x}")
    out.append("")


def render_body(ctx, body, out, tree=False):
    pkg = ctx.pkg
    e = pkg.exports[body.index]
    out.append(f"--- [{body.index + 1}] {pkg.full_name(body.index + 1)} ({body.class_name}) ---")
    ind = "  "
    if body.props:
        out.append(f"{ind}properties:")
        render_props(body.props, out, ind + "  ")
    if body.guid:
        out.append(f"{ind}guid: {body.guid}")
    f = body.fields
    if body.kind == "function":
        sup = f" overrides {f['SuperStruct'].name}" if f["SuperStruct"].index else ""
        out.append(f"{ind}flags: {flag_str(f['FunctionFlags'], FUNC_FLAGS)}{sup}")
        if "RepOffset" in f:
            out.append(f"{ind}rep offset: {f['RepOffset']}")
    elif body.kind == "class":
        out.append(f"{ind}super: {f['SuperStruct'].name}  flags: {f['ClassFlags']:#x}  "
                   f"within: {f['ClassWithin'].name}  config: {f['ClassConfigName']}")
        out.append(f"{ind}generated by: {f['ClassGeneratedBy'].name}  CDO: {f['ClassDefaultObject'].name}  "
                   f"cooked: {f['bCooked']}")
        for iface in f["Interfaces"]:
            out.append(f"{ind}implements: {iface['Class'].name}")
        if f["FuncMap"]:
            out.append(f"{ind}functions: {', '.join(n for n, _ in f['FuncMap'])}")
    elif body.kind == "struct":
        out.append(f"{ind}struct flags: {f['StructFlags']:#x}")
    elif body.kind == "enum":
        out.append(f"{ind}values: {', '.join(f['Names'])}")
    elif body.kind == "property":
        out.append(f"{ind}{property_type(ctx, body)}  [{flag_str(f['PropertyFlags'], CPF_FLAGS)}]"
                   f"{'  ArrayDim=' + str(f['ArrayDim']) if f['ArrayDim'] != 1 else ''}"
                   f"{'  RepNotify=' + f['RepNotifyFunc'] if f['RepNotifyFunc'] != 'None' else ''}")
    elif body.kind == "blueprint":
        out.append(f"{ind}ark u32: {f['ArkBlueprintU32']}")
    if body.kind in ("function", "struct", "class"):
        render_struct_members(ctx, body, out, ind)
    if body.kind == "function" and not body.error and not body.script \
            and f.get("BytecodeDiskSize") == 0:
        out.append(f"{ind}bytecode: пусто")
    if body.script:
        out.append(f"{ind}bytecode: disk {f['BytecodeDiskSize']} / mem {f['BytecodeMemSize']}")
        render_script(ctx, body.script, out, ind + "  ", tree)
    if body.error:
        out.append(f"{ind}!! ошибка разбора: {body.error}")
    if body.trailing:
        out.append(f"{ind}!! не прочитано {len(body.trailing)} байт: {body.trailing[:48].hex(' ')}")
    if e.serial_size == 0:
        out.append(f"{ind}(пустой экспорт)")
    out.append("")


def problems(bodies):
    """Экспорты, которые разобраны не полностью — для кода возврата."""
    bad = []
    for b in bodies:
        if b.error or b.trailing or (b.script and not b.script.mem_ok):
            bad.append(b)
    return bad
