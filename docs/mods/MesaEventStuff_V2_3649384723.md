---
title: "MesaEventStuff V2 (3649384723)"
status: card
workshop_ids: [3649384723]
---

# MesaEventStuff V2 (3649384723)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info` и сводкам MCP `ark-re`. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 3649384723 |
| Название в Workshop | MesaEventStuff V2 (обновлён 2026-01-18; описание — «Mesa Event Mod») |
| Внутреннее имя (`mod.info`) | `MesaDungeon` → `/Game/Mods/MesaDungeon`; карта в `mod.info` — `DungeonSpawner` |
| ModType (`modmeta.info`) | 1 — обычный мод |
| PrimalGameData | `/Game/Mods/MesaDungeon/PrimalGameData_BP_DungeonSpawner` |
| Ассетов | 205 |
| GUID | 8C3B7FB04E4292D427F82DB5260EFBB1 |

## Что делает

Ивенты, которые админы запускают на серверах MESA:

- **Данжи.** `DungeonManager_BP`, `Dungeon_01_BP`, точки спавна, ящики `DungeonCrate_2_BP`/`DungeonCrate_Boss_BP` с набором `LootItemSet_DungeonBossCrate`, босс `MesaDungeonBoss_Character`, мобы `CorruptHuman_With*` и `FlameBat`. Таймер боя — ключ `[MesaEvents] DungeonTimer`.
- **Battle Royale** (в ассетах — «Fortnite»). `FortniteManager_BP`/`FortniteArena_BP`, летающие острова, сужающаяся зона, планер (`Buff_Glider`), ящики `MesaBR_SupplyCrate`, лава-урон, без урона от падения.
- **Ворота-проверки** по типу седока или существа (`HumanGate`, `CrouchGate`, `StegoGate`, `TrikeGate`, `CarboGate`).
- Сезонные предметы — копии ванильных: Valentine's, Easter Egg, Halloween-могила и пугало, тыква.

## Как сделано

- Цикл ивента: игрок получает «приглашение» (`Buff_MesaDungeonRequest` / `Buff_MesaBRRequest`) → принимает (`…_Accepted`) → таймеры телепорта и старта (`Buff_Mesa_TeleportTimer`, `Buff_Mesa_BeginTimer`) → бафы зоны (`Buff_InsideDungeon`/`Buff_InsideArena`) → возврат игроков.
- Сеть: 41 `NetServer`, 1 client, 7 multicast. Управляют менеджеры (`SetDungeonSpawn`, `BeginDungeon`, `BeginBattleRoyale`, `SpawnLoot`, `SpawnDinos`, `EndDungeon`, `ReturnPlayers`). Есть «самоубийственные» RPC (`KillMePls`, `KillOwner`, `KillDino` на бафах принятия) и `NotF2Usage` — видимо, наказание за F2-магазин внутри арены.
- `mod_audit`: 1 кандидат (D6).

## На что смотреть

Серверные RPC менеджеров (`SetDungeonSpawn`, `PreviewDungeonSpawn`, `RemoveSpawnPoint`, `BeginBattleRoyale`) стоит проверить на админ-гейт: из названий не видно, кто может их вызвать.
