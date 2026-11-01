---
title: "Mesa City V7 (3421508193)"
status: card
workshop_ids: [3421508193]
---

# Mesa City V7 (3421508193)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и сводкам MCP `ark-re`. Имя файла историческое: внутреннее имя мода — `Hollow_Terra`. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 3421508193 |
| Название в Workshop | Mesa City V7 (обновлён 2026-10-09; ≈860 МБ; автор Sir.Caveman) |
| Внутреннее имя (`mod.info`) | `Hollow_Terra` → `/Game/Mods/Hollow_Terra`; карта `MesaPrime` |
| ModType (`modmeta.info`) | 2 — самостоятельная карта (PrimalGameData не задан) |
| Ассетов | 905 в `Assets/` + 46 `.umap` в корне |
| GUID | DD2F61C842DE82081CA53793B56B3C0E |

## Что делает

Собственная PvP-карта MESA с OSD (описание: «Custom PvP OSD Map for Mesa Servers»). Карта Extinction-типа: на ней работают OSD и жилы из [MESA OSD/VEIN](MesaMod3_3817736169.md), включая ежедневный Tek-дроп. На карте есть ивент-локации:

- **Military Airport** и **Military Bunker.** Ворота открываются ключ-картами (Red/Green keycards из OSD), внутри Tek/угольные/патронные/деревянные ящики с наборами `Mesa_LootItemSet_SupplyDrop_*`, на входе терминалы.
- **Demon Ruin.** Ивент с «демоническими» сундуками (`RedDemonicChest_BP`, `TekDemonicChest_BP`, `EpicDemonChest`).
- **Dungeon.** `MesaDungeonManager_BP`: босс `MesaDungeonBoss_Character`, мобы `MesaHuman_With*`, урон по биому, PvP-урон в данже.
- **Cargo Ship.** Ящик `SupplyCrate_CargoShip`; плюс мини-босс (`SupplyCrate_MiniBoss_Mesa`), свои пещерные ящики, кастомный `HordeCrateManager_Custom*` (спавн OSD), `TributeTerminal_Mesa_NoDino`, костяной рекс `Bone_MesaRex`.

## Как сделано

- **Уровни.** Персистентный `MesaPrime.umap` + сетка подуровней `A0…Z4_Far/Near`, `Landscape_Main`, `PersistentGameplaySublevel` и `PGS_*` (биомы, спавны дино, ящики). Отдельные подуровни: `Bunker`, `Dungeon_Cave`, `LavaGolemCave`, `LootCave`, `RuinCave`, `BossMain`, `BossCaveCenter`.
- **Ивенты.** Каждый ивент — менеджер-актор на карте с серверными RPC (`Start*`, `OpenGate`, `SpawnLoot`, `*End`, `NotifyServer*`) и бафом «внутри зоны» (`Buff_InsideAirport`/`Bunker`/`Ruin`). Всего 30 `NetServer`, 3 client, 12 multicast.
- **Телепорты.** `ReturnTerminal` («Teleport Back»). `Buff_PvPCheck.TPMe` — отложенный на 9.4 с телепорт, отменяется при получении урона.
- **Настройки** `[BrokenTerra]`: `BossHP`, `BossDMG`, `CorpseTimer`, `CorpseCacheTimer`, `DungeonTimer`.
- `mod_audit`: 1 кандидат (D6).
