---
title: "MESA Ragnarok Extension V15 (3599698299)"
status: card
workshop_ids: [3599698299]
---

# MESA Ragnarok Extension V15 (3599698299)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и составу ассетов. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 3599698299 |
| Название в Workshop | MESA Ragnarok Extension V15 (обновлён 2026-09-29; ≈1.2 ГБ; автор Sir.Caveman) |
| Внутреннее имя (`mod.info`) | `MesaRagnarokExtension` → `/Game/Mods/MesaRagnarokExtension` |
| ModType (`modmeta.info`) | 4 — расширение карты (PrimalGameData не задан) |
| Ассетов | 514 в `Assets/` + 53 `.umap` в `Maps/TheIslandSubMaps` |
| GUID | 70D5119F478BCB94F8BDF5B2752E48F7 |

## Что делает

PvP-переделка Ragnarok для серверов MESA: изменённые участки ландшафта, пещеры и данж, зоны без полётов, свои камни и паук.

## Как сделано

- Тот же приём, что у [Mesa The Island](Mesa_The_Island_V10_3561146931.md). В `mod.info` 58 записей (53 уникальных подуровня) `Ragnarok_*` (ландшафт, биомы, озёра, океан, пещеры `A1_Cave`, данж `A2_Dungeon`, Scotland, Volcano, Desert…) — это заменённые версии уровней Ragnarok. Плюс новый `Mesa_Rag_Persistent`.
- **Логика минимальная.** `NoFly_Zone_BP` и `NoFly_Zone_DesFix_BP` (запрет полётов), `DinoPreventorVolume_BP`, `PrisonSiren_BP`, свой `Mesa_BogSpider_Character_BP`, ресурсные камни `Mesa_MediumRock_BP`/`MesaSmallRock_BP`, клаттер `BP_Extinction_Clutter`/`BP_Redwood_Clutter`, кастомные пещеры в `Assets/CustomCaves`. RPC и ini-ключей нет.
