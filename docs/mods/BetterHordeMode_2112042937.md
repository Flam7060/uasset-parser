---
title: "Better Horde Mode (2112042937)"
status: card
workshop_ids: [2112042937]
---

# Better Horde Mode (2112042937)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и MCP `ark-re` (`asset_defaults`, `asset_summary`, `mod_ini_keys`). Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 2112042937 |
| Название в Workshop | [Discontinued, QoL, Open Source] Better Horde Mode (обновлён 2020-07-13) |
| Внутреннее имя (`mod.info`) | `BetterHordeMode` → `/Game/Mods/BetterHordeMode` |
| ModType (`modmeta.info`) | 4 — расширение карты; в `mod.info` подкарта `/Game/Maps/TheIslandSubMaps/HordeModeFix` |
| PrimalGameData | `/Game/Mods/BetterHordeMode/PrimalGameData_BP_BetterHordeMode` |
| Ассетов | 5 |
| GUID | 6319003A41F0B509197C31BDA79B1818 |

## Что делает

QoL для OSD и элементных жил:

- через `BeaconDelay` (по умолчанию 60 с) последних «потерянных» дино события подсвечивает луч-маяк;
- по желанию — автоубийство дино, которые долго не атакуют (`DoKillWhenNotAttacking*`);
- событие не взрывается при уходе в стазис; вместо этого своя дальность самоуничтожения `EventAutoDestructionRange` (по умолчанию 75 000 против ванильных 25 000).

## Как сделано

- **Маяк.** `PrimalGameData.AdditionalDefaultBuffs` вешает `TrackingBuff/Buff_HordeDinos` на все corrupt-классы Extinction (Dilo, Arthro, Carno, Chalico, Dimorph, Giga, MegaRex, MegaTrike, Paracer, Ptero, Raptor, Rex, RockDrake, Spino, Stego, Trike, Fire Wyvern, Nameless). Баф по таймеру показывает `LostDino_Indicator` и проверяет условие автокилла.
- **Стазис.** Подуровень `HordeModeFix` (тип 4) кладёт в мир актор `BP_HordeEventOverride`. Раз в 30 с на сервере он перебирает `SupplyCrate_Base_Horde`, через `ServerOctreeOverlapActors` ищет игроков в `EventAutoDestructionRange` и не даёт событию самоуничтожиться.
- Ini `[BetterHordeMode]`: `PreventEventStasisAutoDestruction`, `EventAutoDestructionRange`, `DisableBeacon`, `BeaconDelay`, `DoKillWhenNotAttacking`, `DoKillWhenNotAttackingFor`, `DoKillCheckInterval`. RPC нет.

## Заметка

Та же логика (копии `BP_HordeEventOverride`, `Buff_HordeDinos`, те же ключи) встроена в [MESA OSD/VEIN](MesaMod3_3817736169.md). В коллекции оба мода, так что маяк и надзор за стазисом могут работать дважды.
