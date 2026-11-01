---
title: "Pickup Gun (with Anti-Dupe) (2142974617)"
status: card
workshop_ids: [2142974617]
---

# Pickup Gun (with Anti-Dupe) (2142974617)

> **Карточка, полного security-разбора нет.** Собрано по `mod.info`/`modmeta.info`, описанию Workshop и MCP `ark-re` (`mod_net_rpcs`, `mod_ini_keys`, `mod_audit`, `asset_function`). Находки ниже — по байткоду, вживую не проверялись. Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 2142974617 |
| Название в Workshop | Pickup Gun (with Anti-Dupe) (обновлён 2020-08-06, автор RevysMods) |
| Внутреннее имя (`mod.info`) | `Xtinction` → `/Game/Mods/Xtinction`; карта в `mod.info` — `PickupGun` |
| ModType (`modmeta.info`) | 1 — обычный мод |
| PrimalGameData | `/Game/Mods/Xtinction/PrimalGameData_BP_PickupGun` |
| Ассетов | 10 |
| GUID | 6B2AE4684CAFD90C2DEB35AA45245016 |

## Что делает

Пушка подбора и сноса построек «как в S+», но с защитой от дюпа. ПКМ переключает режим pickup/demolish, ЛКМ — выстрел. Учитывает ранг активации построек в трайбе. Админ может сносить чужое (`AllowAdminToDemoAnyTribeWithDemoGun`). Список классов, которые нельзя подбирать, — `NotAllowedItemClasses`.

## Как сделано

- Оружие `BP_Pub_PickupGun` (наследник `ShooterWeapon_Instant`). Клиент делает трассировку `DoLineTrace` и шлёт `PickupEvent(Target)` / `DemolishEvent(Target)`. Сервер проверяет `IsPickupAllowed` / `IsDemolishAllowed` (команда, ранг, повреждённость, список запретов), затем `HandlePickup`: удаляет постройку, кладёт её предмет (`ConsumesPrimalItem`) в инвентарь и пишет в лог трайба «{Player} picked up: {Structure}».
- **«Анти-дюп».** При экипировке (`BPStartEquippedNotify`) клиент вызывает `ROS_AntiDupeWeaponDrop(Inv, ItemID)`. Сервер пишет «Dont try to cheese the Anti-Dupe!…» и делает `Inv.RemoveItem(ItemID)`. Плюс клиентский баф `Anti-PickupDupe_AWF`.
- Ini: `[PickupGun] AllowAdminToDemoAnyTribeWithDemoGun`, `NotAllowedItemClasses` (пути через `;`).

## На что смотреть

| Где | Что видно в байткоде | Возможное влияние |
| --- | --- | --- |
| `BP_Pub_PickupGun` · `ROS_AntiDupeWeaponDrop(Inv, ItemID)` | Сервер удаляет предмет `ItemID` из инвентаря `Inv`, который **прислал клиент**. Нет проверки, что `Inv` принадлежит владельцу пушки и что `ItemID` — сама пушка | Модифицированный клиент удаляет предметы из чужих реплицированных инвентарей, если знает их `ItemID` (грифинг) |
| `PickupEvent(Target)` → `HandlePickup` | `mod_audit` D5: недоверенный `Target` по цепочке доходит до `BPIncrementItemTemplateQuantity`; проверки находятся в `IsPickupAllowed`, а `HandlePickup` их не повторяет | Надо прочитать связку `PickupEvent` → `IsPickupAllowed` → `HandlePickup` целиком |
| `IsPickupAllowed` / `IsDemolishAllowed` | D6: решение строится на `TargetingTeam` и `GetOwnerController` | Пограничные случаи: ноглин, смена трайба |
