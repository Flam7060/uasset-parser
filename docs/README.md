# Разборы модов (uasset-parser)

Отчёты по декомпиляции и security-разбору модов ARK. Хранятся здесь, чтобы не разбирать одни и те же моды повторно — перед разбором проверь, нет ли мода в таблице ниже.

## Для ИИ: быстрая навигация

Есть MCP-сервер `ark-re` (`server.py`, спека — [spec/mcp-server.md](spec/mcp-server.md)): статусы
модов, чтение ассетов по одной функции, поиск по моду, сетевые RPC, ключи ini, нативные символы из PDB.
Регистрация (один раз, user scope):

```bash
claude mcp add --scope user ark-re \
  -e ARK_PDBUTIL=/nix/store/…-llvm-21.1.8/bin/llvm-pdbutil \
  -- uv run --script /home/flam7060/code/ark/uasset-parser/server.py
```

Живое чтение памяти игры (`live_*`) включается только при `ARK_LIVE=1` в `-e`.

Без MCP то же делают CLI:

```bash
python3 scan_mods.py scan                          # хеши ассетов, статусы
python3 scan_mods.py list --status missing         # что ещё не разобрано
python3 scan_mods.py list --status stale           # мод обновился — отчёт устарел
python3 scan_mods.py mark-reviewed <id> --report <файл.md>   # зафиксировать baseline
python3 scan_mods.py cache-gc                      # убрать кеш старых версий парсера
python3 parse_uasset.py <файл.uasset>              # полный текстовый дамп
```

Baseline разбора лежит во frontmatter отчёта (`workshop_ids`, `reviewed`, `mod_hash`,
`covered_assets`), кеш (`.cache/`, gitignored) его не хранит. Статус отчёта — `status`:

| `status` | Что это | Считается разбором |
| --- | --- | --- |
| `template` | Пустой шаблон из `.mod` | нет |
| `card` | Заполнены назначение/функции, security-разбора нет | нет |
| `draft` | Разбор начат | да |
| `done` | Разбор завершён | да |

Статусы мода:

- `fresh` — покрытые ассеты на месте и с теми же хешами, перепроверять не нужно.
- `stale` — покрытый ассет изменился или исчез. Перечитай отчёт, подкрути под новую версию, затем `mark-reviewed`.
- `unreviewed` — документ есть, но baseline хешей ещё не зафиксирован.
- `missing` — нет документа `draft`/`done` с этим workshop_id.
- `drifted` (признак) — мод обновился, но покрытых отчётом ассетов это не коснулось.

Парсер кеширует разборы по sha256 файла и версии парсера (`.cache/v<ver>/by_hash/`):
повторный запуск на том же `.uasset` мгновенный, а правка декодера делает старый кеш
невидимым. `--no-cache` принудительно перепарсивает, `--cache-only` читает только из кеша.

| Мод | Workshop ID | Файл отчёта | Дата | Что внутри |
| --- | --- | --- | --- | --- |
| Dino Storage v2 | 1609138312 | [DinoStorage2_1609138312.md](mods/DinoStorage2_1609138312.md) | 2026-10-25 | Баги и эксплойты: 9 находок (2 высоких / 4 средних / 3 низких) в серверных RPC — SteamID-бэкдор, обход permission-гейтов, нет клампов/проверок. |
| Семейство ArkFun (4 мода) | 2388016936, 3287578775, 3162195052, 3751710508 | [ArkFun_Family_20261025.md](mods/ArkFun_Family_20261025.md) | 2026-10-25 | RPC-находки AF24/Ark Fun 24, Tribe Scan и отдельная баланс-заметка о сильной серверно-настраиваемой Tek-турели ArkFun. |
| `/Game/Mods/AC` | — | [AC_mod.md](mods/AC_mod.md) | 2026-10-17 | Прогон парсера по 7 ассетам (Actor/Buff/PrimalDino/Weapon и т.д.), все разобраны полностью. |
| Structures Plus (S+) | 731604991 | [mods/structures_plus_family/Structures_Plus_S_731604991.md](mods/structures_plus_family/Structures_Plus_S_731604991.md) | 2026-10-31 | Propagator: сервер применяет клиентские `UI_Data` без проверки доступа и клампа (`PulseMax`/`PreventChanges` только в UI). OmniTool: `HandleBasicTransfer`/`SanityCheckBasicTransfer` проверяет дистанцию только до цели, источник — только `IsValid`, а `SourceInv` ставится прямо из клиентского `ExecParams.ObjParam1`. `mod_audit` (219 срабатываний) даёт backlog по Converter/DedicatedStorage/Transmutator/ChibiDisplay/BlueprintMaker/ItemTranslocator/PullFunctions — не проверены построчно. |
| Коллекция MESA (32 мода) | 3826240151 | [mods/MESA_Collection_3826240151.md](mods/MESA_Collection_3826240151.md) | 2026-10-28 | Обзор коллекции серверов MESA: 21 карточка (`status: card`, без полного security-разбора) — что делает мод и как устроен. Кандидаты: SpyGlass отдаёт чужие инвентари и меняет серверные настройки без админа; Pickup Gun удаляет предметы из присланного клиентом инвентаря; C+ — фикс pull-дюпа S+ клампом; Getter Component = HWID-баны (MD5 MAC). |
| Items+ Companion | 2564546455 | [mods/Items_Companion_2564546455.md](mods/Items_Companion_2564546455.md) | 2026-10-29 | Клиентский мост серверного плагина Items+ (вес, `bPreventUpload`). RPC нет. Условно: после рестарта у предметов в хранилищах ванильный `bPreventUpload` (флаг не `SaveGame`, `ServerPostInit` правит только инвентари игроков). Конфиг на клиент приходит только при спавне пешки и принимается один раз. Конфиг сервера читается с клиента. |

## Разбор самой игры (не моды)

Нативная логика базовой игры из `ShooterGameServer.pdb` + ванильные ассеты (теперь парсятся). Помечены `kind: game-analysis`.

| Тема | Отчёт | Дата | Кратко |
| --- | --- | --- | --- |
| Инвентарь, MultiUse, доверие к клиенту | [game/native_inventory_multiuse_trust.md](game/native_inventory_multiuse_trust.md) | 2026-11-01 | Кандидаты по серверным RPC, которым клиент диктует индекс действия (`ServerMultiUse`→ сумка-трансмиттер), указатель чужого инвентаря (9× `Server*RemoteInventory`), количество/слот (дюп чертежей) и тайминг/атомарность TransferAll (овер-кап дюп станции брони). Тела (asm) не читались — кандидаты на стендовую проверку. |

## Очередь (шаблоны, не разобрано)

Шаблоны по остальным установленным модам — в [mods/](mods/) (индекс: [mods/README.md](mods/README.md)), по одному `.md` на мод с описанием из локального `.mod`/`mod.info`. Четыре ArkFun-шаблона отмечены как покрытые общим отчётом выше. При разборе мода заполняй его шаблон и переноси строку в таблицу «Разобрано».

## Как добавлять

1. Назвать файл по моду: `<Имя>_<WorkshopID>.md` (или `<имя>.md`, если ID нет).
2. Добавить строку в таблицу выше: мод, ID, ссылка, дата, краткое содержание.
