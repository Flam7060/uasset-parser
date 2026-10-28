---
title: "Structures Plus (S+) (731604991): отчёт по Propagator"
status: done
workshop_ids: [731604991]
reviewed: 2026-10-26
---

# Structures Plus (S+) (731604991): отчёт по Propagator

_2026-10-26 · Skaqq_

## Сводка

Статический разбор S+ Propagator выявил одну логическую проблему доверия с двумя следствиями: серверный обработчик `PropagatorStart` применяет присланные клиентом настройки разведения без повторной проверки прав и без клампа значений. Оба серверных лимита — `PulseMax` и `PreventChanges` (`PropagatorDisableDinoMods`) — используются только в клиентском виджете, на серверном пути их чтения нет. Схема та же, что у Dino Storage: серверное событие доверяет своим аргументам и полагается на проверку в UI мода, поэтому модифицированный клиент, вызывающий событие напрямую, обходит ограничения. На живом сервере ничего не проверялось.

- **Средняя — сервер применяет клиентские `UI_Data` без проверки доступа и клампа.** `BPServerHandleNetExecCommand('PropagatorStart')` → `ApplyModifications` копирует клиентские `CurGender`, `CurCanBreed`, `PulseCount` в данные дино, проверяя лишь валидность индекса. Проверка доступа `SP_GetAccessLevel` есть только в построении меню; `PulseMax` и `PreventChanges` уходят только в клиентский виджет. Модифицированный клиент меняет пол, переразрешает размножение и шлёт `PulseCount` выше `PulseMax` в обход `PropagatorDisableDinoMods`.
- **Средняя — `PulseCount` задаёт число очков характеристики за одну мутацию.** В `DoMating_-_MutateStats` при `PulseCount>0` одно срабатывание добавляет в стат `PulseCount × 2` очков, ограниченных только потолком байта `255`, а не штатными `2` и не `PulseMax`.
- **Низкая, условная — доступ к чужому строению.** Гейтит ли нативный S+ net-exec dispatcher команду к пропагатору, к которому у игрока нет доступа, — в Blueprint не видно; это решает, кросс-тимовый вектор или только внутри своего племени.

Что **не** обходится (серверно-авторитетные ограничители): счётчик мутаций (`PropagatorRespectMutationLimit` + `MaxMutations`), стоимость (`ConsumeMatingCost` абортит `DoMating` при нехватке) и правило «ровно один самец». Находки, влияние и исправления — ниже.

## Объём и метод

Разобраны cooked-блюпринты S+ в том виде, как они ставятся из Steam Workshop (ID 731604991), утилитой `uasset-parser/parse_uasset.py` (name map, экспорты, Kismet-байткод в псевдокод). Нативные символы бридинга сверены в `ShooterGameServer.pdb`. На живом сервере ничего не запускалось.

| Ассет | Роль | Разобран |
| --- | --- | --- |
| `Misc/Propagator/BP_Propagator` | Вся логика разведения: серверный net-exec, применение UI-данных, мутации, стоимость, подбор пар | Полностью (ключевые функции) |
| `Misc/Propagator/SP_PropagatorUIData` | Структура, которую шлёт клиент: `Index`, `CurGender`, `CurCanBreed`, `PulseCount`, `PreventGenderChange`, `UsesGender` | Полностью |
| `Misc/Propagator/SP_PropagatorDinoData` / `SP_PropagatorEggData` | Состояние дино/яйца: `MaxMutations`, `MutationChance`, `PointsPerMutation`, `NumMutationRolls`, `ApplyMutationCount`, статы | Полностью |
| `_ComObjects/SPlus_Propagator_ComObject` | Обёртка `UI_Data` для `ObjParam1` net-exec `PropagatorStart`; на сервере её создаёт буф в `SetData_-_Breeding` | По ссылкам |
| `_Buffs/Buff_StructuresPlus_Propagator` | Клиентский UI-буф (`InitializeUI_-_Options` получает `PulseMax`/`PreventChanges`) | Частично |
| `Misc/GenomicsChamber/Buff_Mutator`, `Buff_AllowBreeding` | Усиление нативных мутаций / снятие запрета размножения на живом дино | Бегло |

Искались четыре типа проблем: вызываемые клиентом серверные события, доверяющие аргументам; отсутствие проверок владельца/племени на серверном пути; лимиты, живущие только в UI; и данные, которым сервер доверяет без перепроверки.

## Находки

В каждой строке — ассет и функция, что сервер делает с вводом клиента и чего может добиться модифицированный клиент. Достижимы, потому что клиент шлёт `UI_Data` Server RPC `SetData_-_Breeding` у своего буфа, а сервер сам заворачивает их в `SPlus_Propagator_ComObject` и вызывает `PropagatorStart`; ограничения UI мода на этом пути не применяются.

| # | Серьёзность | Ассет · функция | В чём проблема | Влияние на сервер |
| --- | --- | --- | --- | --- |
| 1 | Средняя | `BP_Propagator` — `BPServerHandleNetExecCommand('PropagatorStart')` → `ApplyModifications` (Net·Server) | Гейт вызова — только `IsValid(FromPC)` и `Array_Length(DinoList)>0 && bContainerActivated`. Затем `ApplyModifications` в цикле копирует из клиентского `UI_Data` значения `CurGender`, `CurCanBreed` и `PulseCount → ApplyMutationCount`, проверяя лишь `Array_IsValidIndex(DinoList, Index)`. `SP_GetAccessLevel` вызывается только в `BPGetMultiUseEntries` (меню, клиент). `PulseMax` и `PreventChanges` читаются только в `OpenUI → InitializeUI_-_Options`, то есть уходят клиентскому виджету. | Модифицированный клиент, отправив `PropagatorStart` со своим `UI_Data`, задаёт на сервере произвольные пол и флаг «может размножаться» и счётчик мутаций в обход того, что `PropagatorDisableDinoMods` прячет в UI и чем `PulseMax` ограничивает ползунок. |
| 2 | Средняя | `BP_Propagator` — `DoMating_-_MutateStats` | `ForceMutation = (ApplyMutationCount>0)`; `PointsAdded = Min( ForceMutation ? ApplyMutationCount*2 : floor(PointsPerMutation), 255 − текущие_очки_стата )`. Магнитуду очков за мутацию задаёт присланный `PulseCount`, предел — только байт `255`. | Одна форс-мутация впечатывает до `PulseCount*2` очков (при `PulseCount=127` — 254) в один стат вместо штатных 2. Сила мутации не ограничена `PulseMax`; на свежей линии под лимитом мутаций это бустит статы далеко за норму. |
| 3 | Низкая (условная) | нативный net-exec dispatcher → `BP_Propagator.BPServerHandleNetExecCommand` | Проверяет ли нативная маршрутизация S+ право `FromPC` на конкретное строение перед вызовом Blueprint — в cooked-блюпринте не видно (dispatcher нативный). Blueprint сам доступ не проверяет (находка 1). | Если dispatcher не гейтит доступ — команду можно навести на пропагатор чужого племени (кросс-тим). Если гейтит — вектор остаётся внутри своего племени/своего пропагатора, но обход `PulseMax`/`PreventChanges`/магнитуды (находки 1–2) сохраняется. |

### Паттерн: лимиты только в UI (сквозной)

Первопричина находок 1–2 одна и совпадает с Dino Storage: проверка и клампы живут в UMG-виджете, а серверное событие вызывается напрямую. Конкретно в Propagator:

| Защита | Где задаётся | Где проверяется | На серверном пути |
| --- | --- | --- | --- |
| `SP_GetAccessLevel >= 2` | `StructureFunctions_C` | `BPGetMultiUseEntries` (меню) | нет |
| `PulseMax` (кап пульсов) | конфиг (привязан к `MutatorBuffMaxStackCount`) | клиентский ползунок `Buff_StructuresPlus_Propagator` | нет |
| `PreventChanges` (`PropagatorDisableDinoMods`) | конфиг | клиентский виджет: прячет контролы | нет |
| `PulseCount → очки мутации` | клиент | — | не клампится, только Min(.,255) |

Серверно-авторитетными остаются лишь три проверки, и они в самом `DoMating`:

| Ограничитель | Механизм | Обходится? |
| --- | --- | --- |
| Счётчик мутаций | `(MaxMutations>FemaleMut OR MaxMutations>MaleMut) OR !ObeyMutationLimit`; наращивание `+1 ≤ MaxMutations` | Нет при `PropagatorRespectMutationLimit=true` и родителях на капе. При `=false` — число мутаций открыто. |
| Стоимость | `DoMating` → `ConsumeMatingCost`; при нехватке `pop_flow`. Цена = `(пол? X)+(разведение? Y)+(мутация? Z×PulseCount)` ед. `PropagatorModCostItemClass` из инвентаря пропагатора | Нет, если сервер задал ненулевую цену и класс предмета. Масштабируется линейно с `PulseCount`. |
| Один самец | `CategorizeDinos` при другом числе самцов очищает готовых самок, не стартует | Нет. |

### Практический итог

На серверах с `PropagatorRespectMutationLimit=false` (частая настройка буст-серверов) или на свежей линии под лимитом, и при дешёвой/бесплатной mod-цене, модифицированный клиент: меняет пол и переразрешает размножение в обход `PropagatorDisableDinoMods`; шлёт `PulseCount ≫ PulseMax` и впечатывает до 255 очков в стат за мутацию. Единственный серверно-авторитетный ограничитель магнитуды — `ObeyMutationLimit` (только число мутаций) и `ConsumeMatingCost` (предметы).

## Как Propagator разводит

Пропагатор **не вызывает нативный бридинг ARK** — читает байты дино из криоподов, сам считает статы/мутации/цвета в Blueprint (`DoMating_-_GetStats`/`GetAncestors`/`GetColors`/`MutateStats`) и спавнит `PrimalItem_PropagatorEgg`. Поэтому нативные кап `RandomMutationChance` и «2 очка на мутацию» к нему неприменимы — действуют только проверки самого S+.

- Cryopod в инвентаре фильтруются по одному классу дино.
- Для старта должен остаться **ровно один самец** (`CategorizeDinos`).
- Единственный самец используется для каждой готовой самки; пары обрабатываются автоматически по готовности и cooldown. Ручного выбора пары в графе нет.
- Наследование каждой характеристики и цвета выбирается случайно между родителями. Для нужного результата: один самец с желаемыми высокими статами + только нужные самки того же вида; разные отцовские линии — в отдельные Propagator.
- Множители `PropagatorMatingSpeedMultiplier` / `PropagatorMatingIntervalMultiplier` зажаты Blueprint в `0.01..100` (конфиг сервера).

## Второй механизм — GenomicsChamber / `Buff_Mutator`

Отдельно от пропагатора GenomicsChamber вешает на **живого** дино `Buff_Mutator`, который временно поднимает *нативные* параметры мутаций (`OriginalMutationChance`/`RandomMutationChance`, `OriginalMutationPointsPerChance`, `OriginalMutationRolls`), со стеком до `MutatorBuffMaxStackCount`. Ветки помечены `HasAuthority`, баф восстанавливает оригиналы в `ReceiveDestroyed`. То есть этот путь усиливает штатное спаривание ARK, а не считает мутации сам. Беглый просмотр подозрительного доверия клиенту не выявил — нужен отдельный разбор.

## Задействованные файлы

**Мод** (один Workshop-пакет на клиент и сервер, `Content/Mods/731604991/`): `Misc/Propagator/BP_Propagator`, `SP_PropagatorUIData`, `SP_PropagatorDinoData`, `SP_PropagatorEggData`, `_ComObjects/SPlus_Propagator_ComObject`, `_Buffs/Buff_StructuresPlus_Propagator`, `Misc/GenomicsChamber/Buff_Mutator`, `Buff_AllowBreeding`. Конфиг (`Game.ini`, секция S+): `PropagatorRespectMutationLimit`, `PropagatorDisableDinoMods`, `PropagatorModCostMutate`/`Gender`/`Breed`, `PropagatorModCostItemClass`, `PropagatorMatingSpeedMultiplier`, `PropagatorMatingIntervalMultiplier`, `MutatorBuffMaxStackCount`, `TribePropagatorLimit`.

**Игра** (докачивать не нужно, уже в `Binaries/Win64`): `ShooterGameServer.{exe,pdb}` — нативная серверная сборка. Нативный бридинг (его задействует GenomicsChamber и имитирует Propagator) — в `UPrimalDinoCharacter`: символы `EggRandomMutationsMale`/`Female`, `GestationEggRandomMutations*`, `MaxAllowedRandomMutations`, `RandomMutationChance`, `RandomMutationPointsPerChance`/`Rolls`, `UpdateBabyCuddling_Implementation`, `BabyAge`/`BabyAgeSpeed`, `BabyGestation*`. Нативный net-exec dispatcher, принимающий `BPServerHandleNetExecCommand`, — тоже здесь; его надо прочитать, чтобы закрыть находку 3.

## Рекомендации

Первопричина: проверка живёт в UMG-виджете, серверное событие вызывается напрямую. Серверный RPC должен перепроверять всё, считая клиента враждебным.

**Автору мода**

1. В `BPServerHandleNetExecCommand`/`ApplyModifications` проверять право `FromPC` на это строение и замок перед применением `UI_Data` (находки 1, 3).
2. Клампить `PulseCount` в `0..PulseMax` на сервере; проверять допустимость пола/индекса; соблюдать `PreventChanges` серверно, а не только в UI (находка 1).
3. Магнитуду очков за мутацию (`PointsAdded`) считать от проверенного значения с безопасным потолком, а не от присланного `PulseCount*2` (находка 2).

**Админам серверов**

- Нет INI, закрывающего находки 1–2 независимо от UI — они достижимы прямым вызовом события. Пока нет исправленной сборки, надёжная мера одна: не давать мод там, где это важно, либо запускать проверенную самостоятельно сборку.
- Выставить ненулевую `PropagatorModCost*` и реальный `PropagatorModCostItemClass`: это единственный серверный ограничитель, масштабирующийся с `PulseCount`.
- Держать `PropagatorRespectMutationLimit=true`: закрывает число мутаций (но не магнитуду очков и не смену пола/разведения).

## Приложение: оффсеты объектов 358.26 (живой разбор 2026-10-27)

Снимались внешним пробником `scratchpad/propagator_probe.py` на построенном пропагаторе игрока. Полезны C++-клиенту, который читает Propagator прямо из памяти без вызова net-exec.

- `Buff_StructuresPlus_Propagator_C` (super `Buff_Base_C`, size 2810):
  - `Propagator` ObjectProperty → `PrimalStructureItemContainer*` @ **+0x980** — **не реплицируется**; на клиенте его ставит только `InitializeUI_-_Options`, которая там выполняется не всегда (2026-10-27 на одном и том же сервере было и так, и так). Клиенту для отправки не нужен
  - параметры `CreateUI_-_Options` рядом с `UI_Data`: `PulseMax` i32 @ **+0xA58**, `PreventAll` bool @ **+0xA5C**, `ModCosts` FVector @ **+0xA60** (X — смена пола, Y — смена разведения, Z — за пульс; `CalcMatingCost` берёт FFloor), `ModCostItem` UClass* @ **+0xA70**
  - `K2Node_DynamicCast_AsSPlus_Propagator_ComObject_C` ObjectProperty → `SPlus_Propagator_ComObject_C*` @ **+0x9B8**
  - `K2Node_MakeStruct_BPNetExecParams` StructProperty (BPNetExecParams, 64 B) @ **+0x9C8**
  - `K2Node_CustomEvent_UI_Data2` TArray<SP_PropagatorUIData> @ **+0x998** — параметр `SetData_-_Breeding` (заполняется на сервере)
  - `K2Node_CustomEvent_UI_Data` TArray<SP_PropagatorUIData> @ **+0xA48** — параметр клиентского `CreateUI_-_Options`: `UI_Data`, присланные сервером при открытии окна (сервер строит их `GetUI_DataFromDinoData` по своему `DinoList`)
  - `SetData_-_Breeding(UI_Data)` — **Server RPC** (`Net|NetReliable|NetServer`, флаги 0x0C6000C0, ParmsSize 16 = один TArray по ссылке). `CreateUI_-_Options` — Client RPC (`NetClient`). `BPServerHandleNetExecCommand`/`BPClientHandleNetExecCommand` — обычные события (`Event|BlueprintEvent`), не RPC: вызов на клиенте исполняется локально и до сервера не доходит
  - `BPServerHandleNetExecCommand` parms: `FromPC` +0, `CommandName` FName +8, `ExecParams` BPNetExecParams +0x10, `ReturnValue` +0x50 (минимальный буфер 0x61 B)
  - `BPClientHandleNetExecCommand` parms аналогично, но с `ForPC` вместо `FromPC`
- `BP_Propagator_C` (super `StructureItemContainerBaseBP_C`, size 4097):
  - `bContainerActivated` (унаследован) @ **+0xAE8**, битовое поле: ByteMask **0x40** (байт целиком не читать), реплицируется
  - `TargetingTeam` (унаследован) @ **+0x218**
  - `DinoList` TArray<SP_PropagatorDinoData> @ **+0xE50** (элемент 288 B), **не реплицируется** (нет CPF_Net) — клиентская копия собирается клиентом сам
  - `MaleIndex` IntProperty @ **+0xE60**
  - `PulseMax` IntProperty @ **+0xF30** (клиентский cap из конфига)
  - `PreventChanges` BoolProperty @ **+0xF34** (`PropagatorDisableDinoMods`)
- `SP_PropagatorUIData` (UserDefinedStruct, PropertiesSize 44, MinAlignment 8 → **шаг элемента в TArray 48**; так его читает и `ApplyModifications`): `Index` i32 +0x0 (индекс в серверном `DinoList`), `Name` FString +0x8, `OrigGender` i32 +0x18, `CurGender` i32 +0x1C, `PreventGenderChange` bool +0x20, `UsesGender` bool +0x21, `OrigCanBreed` bool +0x22, `CurCanBreed` bool +0x23, `PulseCount` i32 +0x24, `MatingCost` i32 +0x28
- `SP_PropagatorDinoData` (UScriptStruct, size 288) — поля не раскрывались (под UI не нужны; обход в сервере читает её, не мы)
- Связь `UI_Data` ↔ криоподы (байткод, 2026-10-27): `GetDataFromInventory` кладёт в `DinoList` годные крио по порядку `Cryopods` (инвентарь), `GetUI_DataFromDinoData` ставит `Index` = номер в `DinoList`, `Name` = `DescName`. `ParseItemData` берёт из записи `'Dino'` предмета: `DescName` = strings[1] (у криопода «… - Lvl N (вид)»), `TameName` = strings[0], `NextMatingTime` = doubles[2] (у души `SoulTrap_DS_C` — doubles[0] + время − `ItemDurability`). Очки статов — из `CustomDataBytes` (клиенту не приходят). `DoMating_-_UpdateTimers` после спаривания пишет откат самки обратно в тот же предмет (`SetCustomItemData`, doubles[2] / doubles[0] у души) — откат из криопода живой. arkmultitool сопоставляет строки окна с криоподами по `DescName` (k-й одноимённый — k-й по инвентарю) и берёт дикие очки из записи `BA_Dino`/души
- `SP_PropagatorRepData` (72 B), `SP_PropagatorOverviewData` (36 B) — присутствуют, детали пока не нужны
- `BPNetExecParams` (UScriptStruct, size 64): `IntParam1..3` i32 +0/4/8, `FloatParam1..3` f32 +0xC/0x10/0x14, `ObjParam1..3` UObject* +0x18/0x20/0x28, `StringParam1` FString +0x30

Поля `SP_PropagatorUIData`: пол `0=None, 1=Male, 2=Female` (в комбобоксе виджета индекс = `CurGender − (UsesGender ? 1 : 0)`); `PreventGenderChange = NextMatingTime > GameTime` — дино на откате спаривания, замок только в виджете; `PulseCount` ← `ApplyMutationCount`.

Путь «Start Breeding» (байткод, сверено 2026-10-27): виджет проверяет «ровно один самец, ≥1 самка» среди строк с `CurCanBreed`, кладёт в `UI_Data` **только строки с `CurCanBreed`**, создаёт у себя ComObject и зовёт `Buff.BPClientHandleNetExecCommand('StartBreeding')` → `SetData_-_Breeding(ComObject.UI_Data)` (Server RPC). На сервере буф: `IsValid(Propagator)` → `ConstructObjectAutoNameBP(SPlus_Propagator_ComObject_C, self)` → `ComObject.UI_Data = UI_Data` → `Propagator.BPServerHandleNetExecCommand(GetOwnerController(), 'PropagatorStart', {ObjParam1 = ComObject})` → `ApplyModifications` → `CategorizeDinos` (старт только при одном самце). Модифицированному клиенту достаточно вызвать `SetData_-_Breeding` у своего буфа через ProcessEvent с правленой копией `UI_Data` — движок сам отправит RPC.

UFunction 358.26: `FunctionFlags` u32 @ **+0x90**, `NumParms` u8 @ +0x96, `ParmsSize` u16 @ **+0x98**, `ReturnValueOffset` u16 @ +0x9A.

## Ограничения

- **Только статика.** Находки — из чтения декомпилированного Kismet-байткода, не из вызова RPC на работающем сервере. Поток управления подтверждён трассировкой достижимого кода (`goto`/`if`/`push_flow`) до стока.
- **Открытый вопрос доступа.** Находка 3 условна: нативный S+ net-exec dispatcher и права/замки на сервере в рантайме не тестировались, поэтому кросс-тимовая применимость не установлена. Обход `PulseMax`/`PreventChanges`/магнитуды (находки 1–2) подтверждён на уровне Blueprint-байткода.
- **Источник `PulseMax`.** Привязка к `MutatorBuffMaxStackCount` определена по переиспользуемой переменной парсера; значение в UI верно, но связку с конкретным конфиг-ключом стоит перепроверить.
- **GenomicsChamber разобран бегло**; отдельный проход на доверие клиенту не делался.
