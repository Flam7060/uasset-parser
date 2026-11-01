---
title: "Mesa The Island V10 (3561146931)"
status: card
workshop_ids: [3561146931]
---

# Mesa The Island V10 (3561146931)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и составу ассетов. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 3561146931 |
| Название в Workshop | Mesa The Island V10 (обновлён 2026-08-01; ≈1.1 ГБ) |
| Внутреннее имя (`mod.info`) | `MesaIslandExtension` → `/Game/Mods/MesaIslandExtension` |
| ModType (`modmeta.info`) | 4 — расширение карты (PrimalGameData не задан) |
| Ассетов | 374 в `Assets/` + 52 `.umap` в `Maps/TheIslandSubMaps` |
| GUID | F67B5DE845D272B3F042E3AFA20B8BA2 |

## Что делает

PvP-переделка ванильного The Island для серверов MESA: изменённый рельеф и пещеры, новые пещеры, «Луна».

## Как сделано

- Расширение карты (тип 4): в `mod.info` перечислены 55 записей (53 уникальных подуровня) `/Game/Maps/TheIslandSubMaps/*`, которые игра подгружает в The Island.
- **Замены ванильного.** Часть подуровней названа как ванильные (`A2_Near_WIP`, `B4_Far_WIP`, `E3_Near`, `Cave_1…7`, `Cave_IceCaverns_B`, `Cave_SwampCaverns_A`, `Cave_Underwater_*`, `Cave_VolcanicCaverns`, `LandscapeSublevel`, `Oasis_Landscape`…). Эти **подменяют** ванильные уровни.
- **Новые уровни.** `Mesa_Island_Persistent`, `Mesa_Island_Moon`, пещеры `Mesa_Island_CrashCave`/`EastSnowCave`/`GreenObeCave`/`Lunar_Cave`/`NE_Cave`/`RexCave`, `Mesa_Castle_Cave`, `Mesa_PearlCave_01`.
- **Логика минимальная.** `CaveDMG_Changer_BP` (урон в пещерах), `DinoPreventorVolume_BP` (запрет дино в зонах), `Dynamic_PhysicsVolume_Hazard_Acid`, `Buff_MesaSwampCave`, `MesaGate_Terminal`. Сеть: только 2 multicast, ini-ключей нет.
