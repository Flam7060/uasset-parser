using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.UnrealTypes;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;

if (args.Length < 1)
{
    Console.Error.WriteLine("usage: UAssetDump <path.uasset> [filter]");
    return 1;
}

var path = args[0];
var filter = args.Length > 1 ? args[1].ToLowerInvariant() : null;

// ARK — кастомная UE4; перебираем все версии включая UNKNOWN (автодетект).
UAsset? asset = null;
var allVersions = Enum.GetValues<EngineVersion>();
// Сначала UNKNOWN (автодетект), потом остальные.
var ordered = allVersions.OrderBy(v => v == EngineVersion.UNKNOWN ? 0 : 1);
foreach (var ver in ordered)
{
    try
    {
        asset = new UAsset(path, ver);
        Console.Error.WriteLine($"OK: {ver}");
        break;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"FAIL: {ver} — {ex.Message.Split('\n')[0][..Math.Min(80, ex.Message.Split('\n')[0].Length)]}");
    }
}
if (asset == null) { Console.Error.WriteLine("Ни одна версия не подошла"); return 1; }

Console.WriteLine($"=== {Path.GetFileName(path)} ===");
Console.WriteLine($"Exports: {asset.Exports.Count}");
Console.WriteLine($"Names: {asset.GetNameMapIndexList().Count}");
Console.WriteLine();

// Дамп имён
Console.WriteLine("--- Name Map ---");
for (int i = 0; i < asset.GetNameMapIndexList().Count; i++)
{
    var name = asset.GetNameReference(i).ToString();
    if (filter == null || name.ToLowerInvariant().Contains(filter))
        Console.WriteLine($"  [{i}] {name}");
}
Console.WriteLine();

// Дамп экспортов
for (int i = 0; i < asset.Exports.Count; i++)
{
    var export = asset.Exports[i];
    Console.WriteLine($"--- Export [{i}] {export.ObjectName} (class: {export.GetExportClassType()?.ToString() ?? "?"}) ---");

    if (export is NormalExport normal)
    {
        foreach (var prop in normal.Data)
            DumpProperty(prop, "  ");
    }

    if (export is FunctionExport func && func.ScriptBytecode != null)
    {
        Console.WriteLine("  [Kismet Bytecode]");
        foreach (var expr in func.ScriptBytecode)
            DumpExpression(expr, "    ");
    }

    if (export is StructExport structExp)
    {
        Console.WriteLine($"  SuperStruct: {structExp.SuperStruct}");
        if (structExp.LoadedProperties != null)
        {
            foreach (var fp in structExp.LoadedProperties)
                Console.WriteLine($"  Property: {fp.SerializedType} {fp.Name}");
        }
    }

    Console.WriteLine();
}

return 0;

void DumpProperty(PropertyData prop, string indent)
{
    var typeStr = prop.PropertyType?.ToString() ?? "?";

    if (prop is StructPropertyData structProp)
    {
        Console.WriteLine($"{indent}{typeStr} {prop.Name} {{");
        foreach (var child in structProp.Value)
            DumpProperty(child, indent + "  ");
        Console.WriteLine($"{indent}}}");
    }
    else if (prop is ArrayPropertyData arrProp)
    {
        Console.WriteLine($"{indent}{typeStr} {prop.Name} [{arrProp.Value.Length}]");
        for (int j = 0; j < Math.Min(arrProp.Value.Length, 30); j++)
            DumpProperty(arrProp.Value[j], indent + "  ");
        if (arrProp.Value.Length > 30)
            Console.WriteLine($"{indent}  ... и ещё {arrProp.Value.Length - 30}");
    }
    else
    {
        Console.WriteLine($"{indent}{typeStr} {prop.Name} = {prop.RawValue}");
    }
}

void DumpExpression(KismetExpression expr, string indent)
{
    var token = expr.Token.ToString();

    switch (expr)
    {
        case EX_LocalVirtualFunction vf:
            Console.WriteLine($"{indent}CALL_VIRTUAL {vf.VirtualFunctionName}");
            if (vf.Parameters != null)
                foreach (var p in vf.Parameters) DumpExpression(p, indent + "  ");
            break;
        case EX_CallMath cm:
            Console.WriteLine($"{indent}CALL_MATH stack={cm.StackNode}");
            if (cm.Parameters != null)
                foreach (var p in cm.Parameters) DumpExpression(p, indent + "  ");
            break;
        case EX_FinalFunction ff:
            Console.WriteLine($"{indent}CALL_FINAL stack={ff.StackNode}");
            if (ff.Parameters != null)
                foreach (var p in ff.Parameters) DumpExpression(p, indent + "  ");
            break;
        case EX_VirtualFunction vfn:
            Console.WriteLine($"{indent}VIRTUAL_FN {vfn.VirtualFunctionName}");
            if (vfn.Parameters != null)
                foreach (var p in vfn.Parameters) DumpExpression(p, indent + "  ");
            break;
        case EX_Context ctx:
            Console.WriteLine($"{indent}CONTEXT:");
            DumpExpression(ctx.ObjectExpression, indent + "  obj=");
            DumpExpression(ctx.ContextExpression, indent + "  ctx=");
            return;
        case EX_Let let:
            Console.WriteLine($"{indent}LET:");
            DumpExpression(let.Variable, indent + "  var=");
            DumpExpression(let.Expression, indent + "  expr=");
            return;
        case EX_LetObj letObj:
            Console.WriteLine($"{indent}LET_OBJ:");
            DumpExpression(letObj.VariableExpression, indent + "  var=");
            DumpExpression(letObj.AssignmentExpression, indent + "  expr=");
            return;
        case EX_LocalVariable lv:
            Console.WriteLine($"{indent}LOCAL_VAR {lv.Variable}");
            break;
        case EX_InstanceVariable iv:
            Console.WriteLine($"{indent}INSTANCE_VAR {iv.Variable}");
            break;
        case EX_ObjectConst oc:
            Console.WriteLine($"{indent}OBJ_CONST {oc.Value}");
            break;
        case EX_StringConst sc:
            Console.WriteLine($"{indent}STRING \"{sc.Value}\"");
            break;
        case EX_IntConst ic:
            Console.WriteLine($"{indent}INT {ic.Value}");
            break;
        case EX_FloatConst fc:
            Console.WriteLine($"{indent}FLOAT {fc.Value}");
            break;
        case EX_SetArray sa:
            Console.WriteLine($"{indent}SET_ARRAY:");
            foreach (var el in sa.Elements)
                DumpExpression(el, indent + "  ");
            return;
        case EX_StructMemberContext smc:
            Console.WriteLine($"{indent}STRUCT_MEMBER {smc.StructMemberExpression}");
            DumpExpression(smc.StructExpression, indent + "  struct=");
            return;
        case EX_SetSet ss:
            Console.WriteLine($"{indent}SET_SET:");
            foreach (var el in ss.Elements)
                DumpExpression(el, indent + "  ");
            return;
        default:
            Console.WriteLine($"{indent}{token}");
            break;
    }
}
