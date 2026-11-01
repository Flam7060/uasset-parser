---
title: "C+ Structures (3469918803)"
status: card
workshop_ids: [3469918803]
---

# C+ Structures (3469918803)

> **Карточка, security-разбора нет.** C+ — форк [Structures Plus (S+)](Structures_Plus_S_731604991.md). Здесь только то, чем C+ отличается от S+. Сравнение построено семантическим диффом псевдокода всех различающихся BP (ядро MCP `ark-re`: `asset_summary` + `asset_function`). Входит в [коллекцию MESA](../MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 3469918803 |
| Название в Workshop | C+ Structures (обновлён 2026-05-16; поддерживает srdevs.net) |
| Внутреннее имя (`mod.info`) | `StructuresPlusMod` → `/Game/Mods/StructuresPlusMod` (как у S+, поэтому замена «на лету» совместима с сейвами) |
| ModType (`modmeta.info`) | 1 — обычный мод |
| PrimalGameData | `/Game/Mods/StructuresPlusMod/PrimalGameData_StructuresPlusMod` |
| Ассетов | 2242 (тот же набор путей, что у S+ 731604991) |
| GUID | FF587DBE467EC29D67EE6C9F69ADD52B |

## Что делает (по Workshop)

Замена S+ без ремапа: тот же путь `/Game/Mods/StructuresPlusMod`, сохранения переносятся. Заявлено три изменения: фикс дюпа Inventory Pull на дедикейтед-хранилищах, снятие ограничения по весу (для серверов с бесконечным весом) и уменьшение пакета с 61 до 34 МБ.

## Что реально поменяно относительно S+

Байтами отличаются 1070 из 2243 ассетов, но почти всё это пересборка: имена узлов, GUID пакета, «S+» → «C+» в `DescriptiveName`. Настоящая разница в коде есть в 32 ассетах, из них по логике значимы:

| Ассет · функция | S+ | C+ |
| --- | --- | --- |
| `_Scripts/PullFunctions` · `SP_HandleResourcePull` | Забирает `QtyToTake` из хранилища со счётчиком (`IsStructureUsingItemCounts`) без ограничения и столько же вычитает из счётчика | `Clamp(QtyToTake, 0, CurQty)` и в выдаче, и в списании. **Это и есть фикс дюпа pull** из дедик-хранилищ |
| `_Scripts/PullFunctions` · `SP_AdjustQty_-_Slots_&_Weight` | Ограничивает pull свободными слотами и весом инвентаря персонажа | Ветка веса удалена, остались только слоты |
| `_Scripts/InventoryFunctions` · `SP_GetCapacityForItem` | Для персонажа считает вместимость по оставшемуся весу | Расчёт по весу удалён |
| `Misc/Propagator/BP_Propagator` · `ParseDino` | Берёт `RandomMutationChance/GivePoints/Rolls` из дино | Константы: шанс 0.1, очки 2.0, броски 0 |
| `Misc/Propagator/BP_Propagator` · `DoMating_-_MutateStats` | Число бросков — `NumMutationRolls` самки (или 1 при `ForceMutation`); проверка лимита с `ObeyMutationLimit` | Бросков всегда 1 (`SelectInt(1, 1, ForceMutation)`); проверка лимита переписана, отдельно не разбиралась |
| `Misc/Teleporter/BP_TeleporterPlus` | Дальность small/medium/large (300 / 1000 / 2000) | Все варианты дальности = 200, пункты меню «Disabled»; уровни доступа перестроены |

Остальные отличия — перечисления (`SP_Enum_Aggregator*`, `SP_Enum_GlassTransparency`), анимации и мелочи в проводах, лестницах, трубах, шипах. Логики там нет или она тривиальна.

## Сеть и настройки (как у S+)

110 `NetServer` RPC, 47 client, 56 multicast, 64 переопределения серверных точек входа (`BPServerHandleNetExecCommand` у станций, дверей, мульти/омнитула, авто-крафтера…). Ini: 274 ключа в секции `[StructuresPlus]`.

## На что смотреть

- Находки по Propagator из отчёта S+ (сервер применяет клиентские `UI_Data` без проверки доступа и клампа) к C+ применимы: точки входа те же, менялась только мутационная часть.
- Фикс pull закрывает только расхождение со счётчиком структуры (`IsStructureUsingItemCounts`). Обычные инвентари и `Msa_PullFunctions` из MESA им не затронуты.
