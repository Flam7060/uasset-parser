#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""
ark-re: MCP-сервер разбора модов ARK (stdio).

Единственный файл с внешней зависимостью `mcp`. Инструмент — это валидация
аргументов, вызов функции ядра `arkre` и форматирование ответа; логики
анализа здесь нет. Регистрация:

    claude mcp add --scope user ark-re \
      -- uv run --script /home/flam7060/code/ark/uasset-parser/server.py
"""
import functools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from arkre import asset_tools, assets, config, mods, pdb, respond, security, xref  # noqa: E402

mcp = FastMCP("ark-re")

_USER_ERRORS = (assets.AssetError, asset_tools.ToolError, mods.ModError, pdb.PdbError, ValueError)


def tool(fn):
    """Регистрирует инструмент; ожидаемые ошибки уходят агенту текстом на русском."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _USER_ERRORS as ex:
            raise RuntimeError(str(ex)) from None
    return mcp.tool()(wrapper)


# --- индекс модов ------------------------------------------------------------

@tool
def mods_scan(mods_dir: str | None = None) -> str:
    """Обойти Content/Mods, пересчитать хеши ассетов (неизменные файлы пропускаются)
    и обновить индекс. Зови в начале работы или после обновления модов: вернёт
    число модов, разбивку по статусам отчётов и какие моды изменились."""
    return respond.fit(mods.scan(mods_dir))


@tool
def mods_list(status: str | None = None, query: str | None = None,
              limit: int = 50, offset: int = 0) -> str:
    """Список установленных модов со статусом разбора (missing / unreviewed / fresh /
    stale) и признаком drifted. `status` — фильтр, `query` — подстрока имени или id.
    Прежде чем разбирать мод, проверь здесь, нет ли свежего отчёта."""
    return respond.fit(mods.mods_list(status, query, limit, offset))


@tool
def mod_status(workshop_id: str) -> str:
    """Подробный статус одного мода: имя, игровой путь /Game/Mods/<Имя>, число
    ассетов, документы-отчёты и их состояние, изменённые покрытые ассеты (для stale),
    дата последнего разбора."""
    return respond.fit(mods.mod_status(workshop_id))


@tool
def mark_reviewed(workshop_id: str, report: str, assets: list[str] | None = None) -> str:
    """Зафиксировать текущие хеши ассетов мода как покрытые отчётом: пишет `reviewed`,
    `mod_hash` и `covered_assets` во frontmatter файла `report` (имя в docs/mods/).
    Тело отчёта не меняется. `assets` — пути внутри мода; без него — все ассеты.
    Звать после того, как отчёт написан или обновлён."""
    return respond.fit(mods.mark_reviewed(workshop_id, report, assets))


# --- чтение ассета -----------------------------------------------------------

@tool
def asset_summary(asset: str, imports: str = "classes") -> str:
    """Обзор ассета: родитель, функции с сигнатурами, события и их метки в убер-графе,
    переменные класса, импорты (`classes` / `full` / `none`). Начинай с него, потом
    asset_function. `asset` — абсолютный путь, `/Game/...` или `<workshop_id>:<путь/имя>`."""
    return respond.fit(asset_tools.asset_summary(asset, imports))


@tool
def asset_function(asset: str, function: str, entry: str | None = None,
                   from_line: int = 0, max_lines: int = 400, tree: bool = False) -> str:
    """Тело одной функции: JSON-мета (флаги, параметры, вызовы, чтение/запись свойств,
    problems) и псевдокод. Для `ExecuteUbergraph_*` укажи `entry` — имя события: вернётся
    код от его метки. `tree=true` добавляет дерево узлов Kismet с оффсетами. Длинный код
    листается через from_line. Для поиска по многим ассетам используй mod_search."""
    return respond.function_reply(
        asset_tools.asset_function(asset, function, entry, from_line, max_lines, tree))


@tool
def asset_defaults(asset: str, props: list[str] | None = None,
                   resolve_parents: bool = False) -> str:
    """Значения свойств CDO (Default__X) с типами; в ассете лежат только переопределённые.
    `resolve_parents=true` поднимается по BP-родителям (мод → ваниль) и сливает значения
    с пометкой источника; нативные дефолты (/Script/…) в .uasset отсутствуют."""
    return respond.fit(asset_tools.asset_defaults(asset, props, resolve_parents))


@tool
def asset_find(name: str, scope: str = "all", limit: int = 50) -> str:
    """Найти файл ассета по имени (glob, без регистра) в модах (`mods`), ванили
    (`vanilla`) или везде (`all`). Возвращает игровой путь, файл и workshop_id."""
    return respond.fit(asset_tools.asset_find(name, scope, limit))


# --- поиск и безопасность ----------------------------------------------------

@tool
def mod_search(scope: str | list[str], query: str, mode: str = "xref",
               kinds: list[str] | None = None, limit: int = 50, offset: int = 0) -> str:
    """Поиск по ассетам мода. `scope` — workshop_id, адрес ассета или их список.
    mode=`xref`: query — символ (функция/свойство/класс, можно `Class:Member`), kinds ⊂
    call|read|write|cast|ref; точно и быстро по индексу. mode=`text`: подстрока или
    /регэксп/ по строкам псевдокода. Для чтения одной функции — asset_function."""
    return respond.fit(xref.mod_search(scope, query, mode, kinds, limit, offset))


@tool
def mod_net_rpcs(workshop_id: str) -> str:
    """Сетевые точки входа мода: функции с Net (направление, reliable, параметры),
    переопределения серверных точек входа ARK (BPServerHandleNetExecCommand и имена
    его команд) и места, где клиентский код мода их вызывает. Первый шаг security-разбора."""
    return respond.fit(security.net_rpcs(workshop_id))


@tool
def mod_ini_keys(workshop_id: str) -> str:
    """Ключи ini, которые читает мод: секция, ключ, геттер, умолчание, места чтения
    с контекстом (server_rpc / widget / client / unknown) и потребители значения.
    `server_consumed=false` — лимит не доходит до серверного пути (проверяется только в UI)."""
    return respond.fit(security.ini_keys(workshop_id))


# --- PDB ---------------------------------------------------------------------

@tool
def pdb_type(name: str, pdb_name: str = "server") -> str:
    """Раскладка нативного класса/структуры из PDB: размер, базовые классы, поля
    {offset, size, type, name}, битовые поля. Нужно точное имя (APrimalDinoCharacter,
    FBPNetExecParams); если не найдено — вернутся ближайшие. `pdb_name`: server | client.
    Первый вызов делает разовый импорт PDB в SQLite (~30 с)."""
    return respond.fit(pdb.pdb_type(name, pdb_name))


@tool
def pdb_symbols(query: str, kind: str = "function", pdb_name: str = "server",
                limit: int = 50) -> str:
    """Поиск символов в PDB по имени (glob, декорированное или нет): имя, RVA, размер
    (оценка по соседнему символу). `kind`: function | data | any. Чтобы найти нативную
    реализацию вызова из BP, смотри calls[].native в asset_function."""
    return respond.fit(pdb.pdb_symbols(query, kind, pdb_name, limit))


if __name__ == "__main__":
    mcp.run()
