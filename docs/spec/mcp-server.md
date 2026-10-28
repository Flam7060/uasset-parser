# ark-re: MCP-сервер для разбора модов ARK

_Спецификация · черновик · 2026-10-28_

Сервер отдаёт ИИ-агенту то, что сейчас делается вызовами `parse_uasset.py` и
`scan_mods.py` из bash: статус модов, чтение ассетов по одной функции, поиск
ссылок по моду, сетевые RPC, ini-ключи, CDO-дефолты, нативные символы из PDB.
Отдельной фазой — живое чтение памяти игры снаружи, через `/proc/<pid>/mem`.

Аудитория: тот, кто будет реализовывать (в том числе следующая сессия ИИ), и
владелец репо для сверки решений.

## 1. Принятые решения

| Вопрос | Решение |
| --- | --- |
| Регистрация | User scope (`~/.claude.json`), доступен из любого проекта. `~/.claude.json` — обычный файл, не из nix |
| Запуск | `uv run --script server.py`, зависимости в шапке скрипта (PEP 723). Правки парсера подхватываются перезапуском сессии, без пересборки |
| CLI | Остаются тонкими обёртками над общим ядром; используются для отладки парсера |
| Baseline разбора | Во frontmatter отчёта (`workshop_ids`, `covered_assets`, `mod_hash`), под git |
| Раскладка docs/ | Всё в `docs/mods/`, статус документа во frontmatter; отчёты из корня `docs/` переносятся `git mv` |
| Права на диск | Пишет `.cache/` и frontmatter отчётов (`mark_reviewed`). Текст отчётов и таблицы README не трогает |
| Инструменты v1 | Индекс модов, чтение ассета, поиск по моду (xref + текст), сетевые RPC, ini-ключи, CDO-дефолты |
| Тело функции | Псевдокод текстом (как в CLI) + метаданные JSON; дерево Kismet — по флагу |
| Источники | Моды, ванильный `Content` по запросу (без полного индекса), `ShooterGameServer.pdb` и `ShooterGame.pdb` |
| PDB-движок | `llvm-pdbutil` (есть в nix: `llvm-21.1.8`), разовый импорт в SQLite |
| Поиск | Индекс перекрёстных ссылок из Kismet (call/read/write/…) + запасной текстовый поиск по псевдокоду |
| Живая память | Отдельная фаза 2. Только `O_RDONLY` + `pread` на `/proc/<pid>/mem`. По умолчанию выключено (`ARK_LIVE=1`) |

Не вошло в v1, но спроектировано так, чтобы добавить без переделок: diff версий
мода для `stale` (раздел 13).

## 2. Границы

Сервер **не**:

- пишет в процесс игры, не делает ptrace-attach, не запускает ничего под Proton/wine,
  не трогает префикс игры;
- меняет файлы игры и модов (`Content/`, `Mods/`) — только читает;
- пишет текст отчётов и таблицы в `docs/README.md` / `docs/mods/README.md`;
- коммитит в git.

## 3. Архитектура

```
uasset-parser/
  uasset/              парсер (как есть; API не ломаем)
  arkre/               ядро сервисов — только stdlib
    config.py          пути и флаги (env → дефолты)
    assets.py          резолв адреса ассета, поиск файла по имени, маппинг /Game/Mods/<Name> → workshop_id
    analysis.py        ассет → AssetView (функции, псевдокод, CDO, xrefs, rpc, ini) + кеш
    cache.py           перенос uasset/cache.py; версия парсера в ключе
    xref.py            SQLite-индекс ссылок
    mods.py            сканер Content/Mods, статусы
    reports.py         frontmatter: чтение/запись, свой парсер подмножества YAML
    pdb.py             llvm-pdbutil → SQLite, запросы по типам/символам
    live/              фаза 2
  server.py            MCP (FastMCP, stdio); единственный файл с внешней зависимостью `mcp`
  parse_uasset.py      CLI → arkre
  scan_mods.py         CLI → arkre
  tests/
```

Принципы:

- **Ядро без зависимостей.** `arkre/*` — только stdlib (`sqlite3`, `json`, `hashlib`,
  `subprocess`), чтобы CLI работали системным `python3` без uv. Поэтому frontmatter
  разбирается своим парсером подмножества YAML (скаляры, inline-списки, вложенные map
  на 2 уровня), а не pyyaml.
- **server.py — тонкий.** Каждый инструмент: валидация аргументов → вызов функции
  ядра → форматирование ответа. Логики анализа в server.py нет.
- **Ошибки разбора видны всегда.** Если у ассета/функции есть нераспознанные байты или
  несошедшийся размер Kismet (`render.problems`), это поле `problems` в каждом ответе,
  где ассет участвовал. Молча отдавать неполный псевдокод нельзя.

### Конфигурация

Переменные окружения задаются в регистрации MCP; у всех есть дефолты.

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `ARK_GAME_DIR` | `~/.local/share/Steam/steamapps/common/ARK` | корень игры |
| `ARK_MODS_DIR` | `$ARK_GAME_DIR/ShooterGame/Content/Mods` | моды |
| `ARK_PDB_SERVER` | `$ARK_GAME_DIR/ShooterGame/Binaries/Win64/ShooterGameServer.pdb` | серверный PDB |
| `ARK_PDB_CLIENT` | `…/Win64/ShooterGame.pdb` | клиентский PDB (нужен фазе 2) |
| `ARK_PDBUTIL` | `llvm-pdbutil` из PATH | путь к llvm-pdbutil |
| `ARK_RE_DOCS` | `<repo>/docs` | отчёты |
| `ARK_RE_CACHE` | `<repo>/.cache` | кеш |
| `ARK_LIVE` | не задано | `1` — регистрировать live-инструменты (фаза 2) |

Регистрация:

```bash
claude mcp add --scope user ark-re \
  -- uv run --script /home/flam7060/code/ark/uasset-parser/server.py
```

## 4. Адресация ассетов

Везде, где инструмент принимает `asset`, допустимы три формы:

1. Абсолютный путь к `.uasset`.
2. Игровой путь `/Game/...` (с `.Name` или без): `/Game/PrimalEarth/Dinos/Rex/Rex_Character_BP`.
   `/Game/Mods/<ModName>/…` маппится в `Content/Mods/<workshop_id>/…` по пути мода из `.mod`
   (первая FString), остальное — в ванильный `ShooterGame/Content/…`.
3. `<workshop_id>:<путь внутри мода или имя файла>`: `731604991:BP_Propagator`. Если имя
   неоднозначно, это ошибка со списком кандидатов.

Импорты в ответах отдаются игровыми путями, так что ИИ может сразу открыть
родительский класс или вызываемый ассет. Ванильный `Content` (~105 тыс. `.uasset`)
полностью не разбирается: файл парсится при первом обращении. Для поиска по имени
нужен только список путей (`os.walk` один раз, кеш списка с mtime каталогов).

## 5. Кеш

```
.cache/
  v<parser_ver>/by_hash/<sha256>.json   AssetView ассета (структура, см. ниже)
  v<parser_ver>/by_hash/<sha256>.txt    полный текстовый дамп для CLI (как сейчас)
  xref.sqlite                            ссылки; строки привязаны к (parser_ver, sha256)
  pdb/<pdb_sha256>.sqlite                импорт PDB
  index.json                             состояние сканера: текущие хеши ассетов модов
  vanilla_files.json                     список путей ванильного Content
```

- `parser_ver` — первые 12 hex от sha256 конкатенации исходников `uasset/*.py` и
  `arkre/analysis.py`. После исправления декодера старые разборы не используются.
  Чистка старых версий — `scan_mods.py cache-gc`, не автоматически.
- **AssetView** — то, что нужно инструментам, без повторного парса: список экспортов с
  типами и флагами; для каждой функции — флаги, параметры, псевдокод построчно с
  меткой/оффсетом каждой строки, вызовы, `problems`; CDO; события → метки ubergraph;
  rpc; ini-сайты. Дерево Kismet (`tree=true`) не кешируется: это перепарс одного ассета.
- Запись атомарная (`.tmp` + `os.replace`), как сейчас.
- `.cache/index.json` хранит только вычисляемое состояние (текущие хеши). Baseline
  (что покрыто отчётом) — во frontmatter, при удалении `.cache` не теряется.

## 6. Отчёты и baseline

### Раскладка

Все документы — в `docs/mods/`. Один файл на мод или на семейство модов.
Статус во frontmatter:

| `status` | Что это | Считается разбором |
| --- | --- | --- |
| `template` | Пустой шаблон из `.mod` | нет |
| `card` | Заполнены назначение/функции, security-разбора нет | нет |
| `draft` | Разбор начат | да |
| `done` | Разбор завершён | да |

У мода может быть несколько документов: карточка и общий отчёт семейства.
`mod_status` возвращает все.

### Frontmatter

```yaml
---
title: "Structures Plus (S+): отчёт по Propagator"
status: done
workshop_ids: [731604991]
reviewed: 2026-10-26
mod_hash:
  731604991: 9c1e44a0b7d2
covered_assets:
  731604991:
    Misc/Propagator/BP_Propagator.uasset: 3f9a1c0b2d4e
    Misc/Propagator/SP_PropagatorUIData.uasset: 51aa08c3e9f0
---
```

- Хеши — первые 12 hex sha256: коротко и достаточно, чтобы заметить изменение.
- `mod_hash` — sha256 по отсортированным строкам `rel_path:sha256` всех ассетов мода.
- `covered_assets` — ассеты, на которые отчёт опирается. По умолчанию `mark_reviewed`
  пишет все ассеты мода, но можно передать список: тогда обновление мода, не задевшее
  эти ассеты, не делает отчёт устаревшим.

### Статус мода

| Статус | Условие |
| --- | --- |
| `missing` | нет документа `draft`/`done` с этим id |
| `unreviewed` | документ есть, `covered_assets` для id пуст |
| `fresh` | все `covered_assets` на месте с теми же хешами |
| `stale` | хотя бы один покрытый ассет изменился или исчез |

Отдельный признак `drifted: true` — `mod_hash` изменился, но покрытые ассеты нет
(мод обновлялся, отчёта это не касается).

### Миграция (шаг 0)

- `git mv` из корня `docs/`: `DinoStorage2_1609138312.md`, `ArkFun_Family_20261025.md`,
  `AC_mod.md` → `docs/mods/`. `AC_mod.md` — не workshop-мод: `workshop_ids: []`,
  ассеты из корня репо.
- Проставить frontmatter всем 48 файлам `docs/mods/`: `template` / `card` (4 ArkFun-карточки) /
  `done` (S+, Dino Storage, ArkFun Family, AC).
- Поправить ссылки в `docs/README.md` и `docs/mods/README.md`.
- `git rm --cached` для `uasset/__pycache__/` и `decoded/` (уже в `.gitignore`) — отдельным коммитом.

## 7. Инструменты v1

Общие соглашения — в разделе 9. Имена в MCP: `mcp__ark-re__<tool>`.

### Индекс модов

**`mods_scan(mods_dir?)`** — обойти `Content/Mods`, пересчитать хеши (sha256 по
файлу, пропуская файлы с неизменными size+mtime), обновить `index.json`.
Возвращает сводку: всего модов, по статусам, какие моды изменились с прошлого скана.

**`mods_list(status?, query?, limit=50, offset=0)`** — строки
`{workshop_id, name, status, drifted, assets, docs[]}`. `query` — подстрока имени.

**`mod_status(workshop_id)`** — имя, внутренний путь мода (`/Game/Mods/<Name>`),
число ассетов, документы с их статусами, изменённые покрытые ассеты (для `stale`),
дата последнего разбора.

**`mark_reviewed(workshop_id, report, assets?)`** — записать в frontmatter `report`
`covered_assets`, `mod_hash`, `reviewed`. Тело файла не меняется, порядок остальных
ключей frontmatter сохраняется. Заодно гарантирует, что AssetView покрытых ассетов
лежит в кеше: это нужно будущему diff.

### Чтение ассета

**`asset_summary(asset, imports="classes")`**

- родительский класс (игровым путём), сгенерированный класс, CDO;
- функции: имя, флаги, сигнатура `(Param: Type, …) -> Ret`, число строк псевдокода;
- события и их метки в ubergraph (`ReceiveBeginPlay → L_0x1A40`);
- переменные класса: тип, флаги (`Net`, `RepNotify`, `Edit`…);
- импорты: `classes` — только классы/пакеты, `full` — всё, `none`;
- `problems`.

**`asset_function(asset, function, entry?, from_line=0, max_lines=400, tree=false)`**

Два блока контента:

1. JSON-мета: флаги, параметры, локальные переменные, `calls[]` (цель игровым путём,
   `final`/`virtual`, а при загруженном PDB — `native: {symbol, rva, exec_thunk}`),
   `reads[]`/`writes[]` свойств, `problems`, `total_lines`, `truncated`.
2. Псевдокод тем же рендером, что CLI; с `tree=true` — дерево узлов Kismet с
   оффсетами диск/память.

`entry` нужен для `ExecuteUbergraph_*`: имя события → псевдокод с его метки. Без
этого ubergraph крупного БП не влезает в ответ.

**`asset_defaults(asset, props?, resolve_parents=false)`** — свойства CDO
(`Default__X`) с типами, структуры и массивы раскрыты. В ассете лежат только
переопределённые значения. С `resolve_parents=true` сервер поднимается по
BP-родителям (мод → ваниль) и сливает значения, помечая, откуда каждое. Нативных
родителей (`/Script/…`) в `.uasset` нет: значение помечается `native default — нет в
ассете`.

**`asset_find(name, scope="all", limit=50)`** — поиск файла по имени (glob,
без учёта регистра) в модах и/или ванили (`mods` / `vanilla` / `all`).
Возвращает игровой путь, файловый путь, workshop_id.

### Поиск по моду

**`mod_search(scope, query, mode="xref", kinds?, limit=50, offset=0)`**

- `scope` — workshop_id, адрес ассета или список таких значений.
- `mode="xref"` — `query` трактуется как символ (имя функции/свойства/класса, можно
  `Class:Member`). Ищется в `xref.sqlite`. `kinds` ⊂ `call | read | write | cast | ref`.
- `mode="text"` — подстрока или регэксп (`/…/`) по строкам псевдокода.
- Строка результата: `{asset, function, kind, label, line}`, где `line` — строка
  псевдокода с совпадением.

Ассеты scope, которых нет в кеше, сначала разбираются. На первом поиске по большому
моду это заметно; дальше всё из кеша.

Как строятся xrefs (из обхода выражений, `render._walk`):

| Kind | Источник в Kismet |
| --- | --- |
| `call` | `FinalFunction`, `VirtualFunction` (и их `Local*`/`Math`-варианты), цель — функция из импорта или своего ассета |
| `read` | `InstanceVariable` / `LocalVariable` / `DefaultVariable` и обращение через `Context` не в левой части `Let*` |
| `write` | левая часть `Let*` |
| `cast` | `DynamicCast` / `MetaCast` / `ObjectToInterfaceCast` |
| `ref` | `ObjectConst`, классы и ассеты в аргументах |

### Безопасность

**`mod_net_rpcs(workshop_id)`** — по каждому ассету:

- функции с `Net`: направление (`server` / `client` / `multicast`), `reliable`,
  параметры с типами, `RepOffset`;
- переопределения известных серверных точек входа ARK, вызываемых по запросу
  клиента. Подтверждено: `BPServerHandleNetExecCommand` (S+). Для него извлекаются
  имена команд — `NameConst`, с которыми сравнивается аргумент команды. Список точек
  входа — в `arkre/analysis.py`, пополняется и сверяется по PDB (шаг 3);
- `client_callers` — где в моде клиентский код вызывает эти RPC (из xref).

**`mod_ini_keys(workshop_id)`** — ключи, которые мод читает из конфигов:
`{section, key, getter, default, sites[]}`. Сайт — `{asset, function, context}`, где
`context` — эвристика:

- `server_rpc` — функция серверная RPC или достижима из неё / из серверной точки входа
  внутри мода по графу вызовов;
- `widget` — класс ассета наследуется от `UserWidget`;
- `client` — `NetClient` / `NetMulticast` или достижима только из них;
- `unknown`.

Именно `server_rpc` против `widget` ловит класс находок «лимит только в UI», как
`PulseMax` / `PropagatorDisableDinoMods` в S+. Список геттеров (семейство `Get*Option`
у GameMode и аналоги) фиксируется на шаге 2 по реальным вызовам в S+ и ArkFun;
регрессионный тест — S+.

## 8. PDB

Оба PDB импортируются один раз в `.cache/pdb/<sha256>.sqlite` из вывода
`llvm-pdbutil dump` (типы, field list, public/global символы с section:offset →
RVA по заголовкам секций, процедуры с размером). Импорт потоковый, построчно.
**До реализации** — замерить время и объём текста на `ShooterGameServer.pdb` (370 МБ).
Если разовый импорт неприемлем, запасной путь — `llvm-pdbutil pretty -class-definitions=layout`
по одному типу на запрос с кешем результата.

**`pdb_type(name, pdb="server")`** — размер, базовые классы, члены
`{offset, size, type, name}`, bitfield-ы. Точное совпадение имени; если не найдено —
ближайшие имена.

**`pdb_symbols(query, kind="function", pdb="server", limit=50)`** — glob по
декорированному/недекорированному имени: `{name, rva, size, signature}`.

**BP → натив** (встроено в `asset_function`): вызов функции из импорта
`/Script/ShooterGame.PrimalDinoCharacter:Foo` ищется как `APrimalDinoCharacter::Foo` /
`UPrimalDinoCharacter::Foo` и `…::execFoo`. Результат — в `calls[].native`.

Серверный PDB — для анализа серверной логики. Клиентский — для фазы 2: RVA функций и
глобалов сервера к `ShooterGame.exe` не подходят. Раскладки классов совпадают,
скорее всего, но не гарантированно (`WITH_SERVER_CODE`): фаза 2 сверяет их с
рефлексией.

## 9. Ответы, лимиты, ошибки

- Списки — с `limit`/`offset` и полем `total`.
- Текст ответа ограничен ~20 тыс. символов. При обрезке — `truncated: true` и
  подсказка, каким параметром получить продолжение.
- Ошибки — ошибкой инструмента с понятным текстом на русском. «Ассет не найден» —
  с кандидатами из `asset_find`. «Нет PDB / llvm-pdbutil» — с тем, что задать в конфиге.
- Описания инструментов — короткие, на русском; в них — когда звать этот инструмент
  и чем он отличается от соседних (`mod_search` vs `asset_function`).

## 10. CLI

- `parse_uasset.py` — прежний интерфейс и вывод (байт-в-байт для полного дампа),
  внутри через `arkre.analysis` и кеш.
- `scan_mods.py` — `scan`, `list`, `mark-reviewed` как сейчас, но baseline во
  frontmatter. Новое: `cache-gc`.
- CLI нужен, чтобы проверить правку парсера без перезапуска сессии с MCP.

## 11. Фаза 2: живое чтение памяти

Включается только при `ARK_LIVE=1` в окружении сервера: без флага live-инструменты не
регистрируются, и ИИ их не видит.

### Правила безопасности (обязательные)

1. Доступ к процессу — **только** `os.open('/proc/<pid>/mem', O_RDONLY)` + `os.pread`,
   карта — чтение `/proc/<pid>/maps`. Так уже работает `arkmultitool/tools/ext_dump.py`.
2. Запрещено: `ptrace(ATTACH/SEIZE)`, `process_vm_writev`, открытие `mem` на запись,
   сигналы процессу, запуск чего-либо под Proton/wine, файлы в префиксе игры.
   Проверяется тестом: grep по `arkre/live/` на запрещённые вызовы и флаги open.
3. PID — только по `/proc/*/cmdline`, оканчивающемуся на `ShooterGame.exe`. Если
   процессов несколько — ошибка со списком, без угадывания.
4. Сборка проверяется до первого чтения: база модуля из `maps`, PE `TimeDateStamp` и
   CodeView GUID/age из образа в памяти сверяются с `ShooterGame.pdb`. Не совпало —
   отказ: смещения из PDB были бы чужими.
5. Каждое чтение ограничено по размеру (≤ 1 МБ за вызов, ≤ N объектов за ответ).
   Неудачный `pread` — это `null` в ответе, а не исключение и не повтор в цикле.

Код чтения (`Game.rd`, GNames, GUObjectArray, рефлексия свойств) переносится из
`ext_dump.py` в `arkre/live/` с указанием источника; `arkmultitool` не трогаем и
кросс-репо импортов не делаем. Адреса глобалов — сигнатурами, как в `ext_dump.py`;
RVA — только из клиентского PDB после проверки п. 4.

### Инструменты

| Инструмент | Что делает |
| --- | --- |
| `live_status()` | PID, база модуля, проверка сборки, найдены ли GNames/GUObjectArray/GWorld, число объектов |
| `live_instances(cls, include_subclasses=true, limit=50)` | живые объекты класса (`BP_Propagator_C` …): адрес, имя, outer, флаги; CDO/архетипы исключены |
| `live_object(ref, props?, depth=1)` | свойства объекта по рефлексии, включая массивы и структуры; `ref` — адрес или полное имя |
| `live_cdo(cls)` | CDO из памяти + diff со статическими дефолтами `asset_defaults(resolve_parents=true)` — видно, что поменяли ini/сервер |
| `live_snapshot(ref, label)` | снять состояние объекта (`.cache/live/<label>.json`) |
| `live_diff(label_a, label_b)` | какие поля изменились между снимками |
| `live_read(addr, size?, type?, path?)` | сырые байты (hex) или декод по типу из PDB; `path` — цепочка `+0x10->+0x38` |

## 12. Тесты и приёмка

Фикстуры — ассеты из корня репо (`Actor_BP_AC.uasset` и др.) и установленный S+.

- `parse_uasset.py` с кешем и без даёт байт-в-байт одинаковый вывод.
- Смена `parser_ver` делает старый кеш невидимым.
- Frontmatter: чтение → запись без изменений = тот же файл; `mark_reviewed` меняет
  только свои ключи.
- Статусы: `fresh` / `stale` / `drifted` на синтетическом моде во временном каталоге.
- Регрессия S+: `mod_net_rpcs` находит `BPServerHandleNetExecCommand` с командой
  `PropagatorStart`; `mod_ini_keys` показывает `PropagatorDisableDinoMods` только с
  контекстом `widget`; `mod_search(…, "PulseMax", kinds=["read"])` не находит чтений в
  серверном пути.
- Фаза 2: тест-запрет из правила 2; `live_status` без игры — понятная ошибка, а не
  исключение.
- Фреймворк — `unittest` из stdlib.

## 13. План работ

Каждый шаг — отдельный коммит (или несколько мелких), коммит — по просьбе владельца.

0. Миграция docs (раздел 6) + frontmatter; untrack `__pycache__`/`decoded`.
1. Ядро: `arkre/config`, `cache` с `parser_ver`, `analysis` (AssetView), `assets` (резолв,
   ваниль, `/Game/Mods`). CLI на ядре. Тесты кеша.
2. `reports` + `mods` (статусы из frontmatter), `xref.sqlite`, rpc, ini, CDO. Тесты S+.
3. `server.py` с инструментами разделов 7 и 9, регистрация, проверка в живой сессии.
4. PDB: замер `llvm-pdbutil`, импорт, `pdb_type`, `pdb_symbols`, BP → натив;
   сверка списка серверных точек входа.
5. Фаза 2 (`ARK_LIVE`).

Потом: `mod_diff(workshop_id)` — для `stale` показать, какие функции покрытых ассетов
изменились, по AssetView из кеша (baseline-версия сохранена благодаря `mark_reviewed`).
Генерация таблиц `docs/README.md` из frontmatter.

## 14. Открытые вопросы

- `llvm-pdbutil` не в PATH, лежит в `/nix/store/…-llvm-21.1.8`. Добавить `llvm` в
  home-manager или прописать `ARK_PDBUTIL` на `nix shell nixpkgs#llvm`?
- Точный список серверных точек входа ARK, кроме `BPServerHandleNetExecCommand`
  (multi-use, ServerRequest*…), — собрать по PDB на шаге 4.
- Геттеры ini-опций, которыми пользуются моды, — зафиксировать на шаге 2.
- Совпадают ли раскладки классов клиента и сервера — проверить на шаге 5 по рефлексии.
