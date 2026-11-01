---
title: "Awesome SpyGlass! (1404697612)"
status: card
workshop_ids: [1404697612]
---

# Awesome SpyGlass! (1404697612)

> **Карточка, полного security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и MCP `ark-re` (`mod_net_rpcs`, `mod_ini_keys`, `asset_function`). Две находки ниже прочитаны по байткоду, но вживую не проверялись. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 1404697612 |
| Название в Workshop | Awesome SpyGlass! (обновлён 2020-07-02; ≈7.5 млн подписок) |
| Внутреннее имя (`mod.info`) | `AwesomeSpyglass` → `/Game/Mods/AwesomeSpyglass` |
| ModType (`modmeta.info`) | 1 — обычный мод |
| PrimalGameData | `/Game/Mods/AwesomeSpyglass/PrimalGameData_BP_AwesomeSpyglass` |
| Ассетов | 46 |
| GUID | 87CB9E1D490C5FA6D4F255B2952F8302 |

## Что делает

Подзорная труба с инфо-панелью о цели: дино (статы, очки, еда для приручения, бафы), игроки, постройки, яйца, лут, ящики снабжения, ульи. Есть прицел, обводка цели, GPS. Пользовательские настройки хранятся на клиенте, серверные (`Allow*`) — у админа.

## Как сделано

- Предмет `PrimalItem_AwesomeSpyGlass` → при использовании вешает `AwesomeSpyGlass_Buff` и зовёт client RPC `Make Widget`. Панель — `AwesomeSpyGlass_Main`.
- **Модель «клиент спрашивает, сервер отвечает».** Чего клиент не видит сам, он запрашивает серверными RPC на бафе:
  - `Request Character Stats(PrimalCharacter)` → `Push Character Stats To Client`;
  - `Request Food List(Dino)` → `Push Food List to Client`;
  - `Request Inventory Items(Actor, Limit)` → `Push Item List To Client`;
  - `Request Nearest Bee(Hive)` → `Push Bee To Client`.
- **Серверные настройки.** 11 ключей `[AwesomeSpyGlass]` (`AllowGPS`, `AllowCrosshair`, `AllowFoodList`, `AllowBuffList`, `AllowItemList`, `AllowOutline`, `Allow*Targeting`). Их читает `Load Server Settings` один раз и сохраняет в `AwesomeSpyGlassServerSettingsV2.sav`; дальше ini игнорируется. Меняются из игры через `Set Server Settings` и рассылаются всем через `Update Server Settings Globally` → `Refresh Settings On Client`.

## На что смотреть

| Где | Что видно в байткоде | Возможное влияние |
| --- | --- | --- |
| `AwesomeSpyGlass_Buff` · `Request Inventory Items(Actor, Limit)` | Сервер берёт у **любого** присланного актора `PrimalInventoryComponent`, собирает список (`Make Inventory List`, до `Limit` предметов — число от клиента) и отдаёт клиенту. Нет проверки дистанции, команды, типа цели и флага `AllowItemList` | Модифицированный клиент читает содержимое чужих хранилищ, дино и игроков, если актор ему реплицирован (в зоне релевантности). Для PvP это разведка баз |
| `AwesomeSpyGlass_Buff` · `Set Server Settings(Settings)` | Сервер сразу пишет присланную структуру в `AwesomeSpyGlassServerSettingsV2.sav` и рассылает всем. Проверки на админа на серверном пути нет: кнопка админ-настроек ограничена только в UI | Любой игрок может включить себе и всем, например, `AllowItemList` или таргетинг игроков, или выключить их |

`mod_audit` по этому моду ничего не выдал. Детекторы D1–D6 не покрывают утечку данных в ответе и изменение глобальных настроек без админ-гейта, поэтому обе находки сделаны вручную.
