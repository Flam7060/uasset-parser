# Fun24 community mod (3751710508)

> **Покрыт общим отчётом:** [семейство ArkFun](../ArkFun_Family_20261025.md). Пользовательские `NetServer` RPC в проверенных игровых блюпринтах не найдены.

## Инфо (из локального `3751710508.mod`)

| Поле | Значение |
| --- | --- |
| Workshop ID | 3751710508 |
| Название | Fun24 community mod |
| Тип (ModType) | GameModBaseAsset |
| Категория | геймплей (PrimalGameData) |
| GUID | E9726D4F4E140F1C7F895C8EE9A1E46E |
| Steam Workshop | https://steamcommunity.com/sharedfiles/filedetails/?id=3751710508 |

## Назначение и функции

Community mod добавляет оружие, крупные строительные детали и наборы косметики. В пакете также находятся сезонные ресурсы, общие с AF24 ServerMod.

| Функция/содержимое | Цель | Основные ассеты |
| --- | --- | --- |
| Boomerang | Отдельный предмет оружия и связанный projectile | `WeapBoomerang_AF24`, `ProjBoomerang_AF24`, предмет и engram entry |
| Grappling hook | Крюк/снаряд для перемещения с использованием арбалета; длина каната до 7 000 UU, в PvE зацеп ограничен союзниками | `WeapCrossbow_GrapplingHook_ABER`, `ProjGrapHook`, ammo и engram entry |
| Spear | Дополнительный вариант копья и снаряда | `WeapSpear_AF24`, `ProjSpear_AF24`, предмет и engram entry |
| Большие дверные рамы Metal и Tek | Дополнительные крупные строительные детали | `BP_DoubleDoorframe_large_Metal`, `BP_DoubleDoorframe_large_Tek`, предметы и engram entries |
| Костюмы и броня | Косметика для персонажа и Giga: Gen1 Avatar, Tek suit, skeleton и lava Giga | `skins/CORRUPTED`, `skins/Gen2_suit`, `skins/SKELET_LAVA`, `skins/SKELET_MATAL`, `skins/EXP_GIGANT` |
| Сезонные предметы/декорации | Halloween, Easter, Christmas и Valentine ресурсы | `PrimalEarth/...` сезонные assets |

Описания Boomerang, Grappling Hook и Spear ограничены обнаруженными предметами, оружием и projectile assets; поведение возврата/зацепа не оценивалось в игре.

В проверенных projectile assets бумеранг имеет 30 прямого урона, копьё — 50, а крюк не наносит урон. Аномально высокого урона, автоматизации боя/фарма или пользовательских `NetServer` RPC в проверенных игровых Blueprint не обнаружено. `bImpactPvEOnlyAlly=true` указывает на союзный зацеп крюком в PvE; runtime-поведение этим статическим анализом не проверялось.

## Результат

Детальный объём, метод и ограничения приведены в [общем отчёте семейства ArkFun](../ArkFun_Family_20261025.md). Это не подтверждает отсутствие игровых или балансных ошибок, не связанных с пользовательскими сетевыми событиями.
