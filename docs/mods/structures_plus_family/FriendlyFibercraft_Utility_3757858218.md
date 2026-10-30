---
title: "Friendly Fibercraft | Utility (3757858218) — CryoBreeder: разбор для arkmultitool"
workshop_ids: [3757858218]
status: draft
scope: cryobreeder-only
created: 2026-10-30
reviewer: Relay
---

# Friendly Fibercraft | Utility (3757858218): CryoBreeder

Разбор одной подсистемы мода — **CryoBreeder**, функциональный аналог
`BP_Propagator` из classic S+. На момент коммита в arkmultitool этот
крио-бридер не регистрируется в `src/game/features/mods/registry.hpp`,
поэтому его UClass'ы не попадают в `snapshot.mod_instances` и ни одна
feature его не видит.

Остальные подсистемы FF Utility (Mutator, ClonerV2, LoadoutStorage,
EndGame, PingSystem, Watermark) — вне скоупа этого отчёта.

## 1. Карта классов

| Роль | Game path | Native UClass |
| --- | --- | --- |
| Структура (actor) | `/Game/Mods/Friendly_Utility/Propagator/BP_CryoBreeder` | `BP_CryoBreeder_C` |
| Buff на игроке (видимость UI) | `/Game/Mods/Friendly_Utility/Propagator/Buffs/Buff_DG_Cryobreeder` | `Buff_DG_Cryobreeder_C` |
| Инвентарь | `/Game/Mods/Friendly_Utility/Propagator/PrimalInventory_Cryobreeder` | `PrimalInventory_Cryobreeder_C` |
| Яйцо | `/Game/Mods/Friendly_Utility/Propagator/PrimalItem_CryobreederEgg` | `PrimalItem_CryobreederEgg_C` |

Родители:

* `BP_CryoBreeder_C` ← `StructureItemContainerBaseBP_C` ← `APrimalStructureItemContainer`.
  Это обычный structure-с-инвентарём: наш reader найдёт его через проход
  по акторам уровня, как и любые structures.
* `Buff_DG_Cryobreeder_C` ← `Buff_Base_C` ← `UPrimalBuff`. Висит на пешке,
  пока окно CryoBreeder открыто (buff создаётся через стандартный путь
  Buff_Base, аналогично `Buff_StructuresPlus_Propagator_C` в classic S+).

## 2. Поля `BP_CryoBreeder_C`, которые нам нужны

Все offset'ы читаются через `property_meta(class, L"<name>")` — рефлексия
UProperty, offset'ы к пересборке мода не хардкодить.

| Поле | Тип | Что это | Флаги |
| --- | --- | --- | --- |
| `DinoList` | `TArray<FDG_Cryobreeder_DinoData>` | Основной массив дино, посаженных в бридер. **Это аналог S+ DinoList.** | Edit, SaveGame |
| `ReplicatedMatingData` | `FDG_Cryobreeder_RepData` | Реплицируемое состояние брачения (OnRep_ReplicatedMatingData) | Edit, Net, SaveGame, RepNotify |
| `IsBreeding` | `bool` | Активен ли сейчас процесс | Edit, Net, SaveGame, RepNotify (OnRep_IsBreeding) |
| `InventorySlots` | `int32` | Сколько слотов открыто (RepNotify_InventorySlots) | Edit, Net, RepNotify |
| `MaleIndex` | `int32` | Какой слот выбран как «самец» | Edit, Net, SaveGame |
| `Cryopods` | `TArray<UPrimalItem*>` | Ссылки на предметы-криоподы внутри | Edit, non-Net (локально у клиента после `GetCryopods`) |
| `MatingSpeedMultiplier` | `float` | Множитель скорости брачения (конфиг) | Edit |
| `MatingIntervalMultiplier` | `float` | Множитель интервала (реплицируется) | Edit, Net |
| `PulseMax` | `int32` | Макс. мутаций (гейт в UI «Опции») | Edit |
| `TribeLimit` | `int32` | Лимит на команду | Edit |
| `DinoBlacklist` | `FString` | Чёрный список классов | Edit |
| `MutatableStats` | `TArray<EPrimalCharacterStatusValue>` | Какие статы мутируются | Edit |

Это всё — ровно те же роли, что у classic S+ `BP_Propagator`:
`DinoList`, `IsBreeding`, `MaleIndex`, `InventorySlots`,
`PulseMax` переименованы 1-в-1.

## 3. Поля `Buff_DG_Cryobreeder_C`

| Поле | Тип | Что это |
| --- | --- | --- |
| `Propagator` | `UPrimalStructureItemContainer*` | Ссылка на тот самый `BP_CryoBreeder_C`, окно которого открыто. **Единственное поле, которое нам реально надо — дальше читаем `DinoList` уже из structure.** |
| `ActiveWidget` | `UUserWidget*` | Текущий открытый виджет (`Widget_CryobreederOptions_C` или `Widge1t_CryobreederOverview_C`). Нужен редко. |

Жизненный цикл буфа:

* Создаётся на игроке при `BPContainerActivated` структуры (клиент).
* Удаляется через серверное `RemoveBuffEvent` + клиентское `BPDeactivated`.
* Пока буф висит — его `Propagator` указывает на валидный `BP_CryoBreeder_C`.

Это даёт тот же механизм разблокировки, что у classic S+:
reader помечает `Buff_DG_Cryobreeder_C` в `snapshot.mod_instances`,
feature видит запись и через `Propagator` доходит до структуры.

## 4. Серверные RPC CryoBreeder (контекст, не для подписи)

Отправка через ProcessEvent на `BP_CryoBreeder_C` или
`Buff_DG_Cryobreeder_C`:

| UFunction | Класс | Направление | Что делает |
| --- | --- | --- | --- |
| `BPServerHandleNetExecCommand` | `BP_CryoBreeder_C` | server reliable | 112-строчный диспетчер по `CommandName`: Start/Stop breed, pulse mutations, drop eggs, change settings. |
| `BPServerHandleNetExecCommand` | `Buff_DG_Cryobreeder_C` | server reliable | Проксирует вызовы к structure через буф. |
| `SetData_-_Breeding` | `Buff_DG_Cryobreeder_C` | server reliable | Клиент шлёт серверу обновлённые UI_Data. |
| `RemoveBuffEvent` | `Buff_DG_Cryobreeder_C` | server reliable | Снять буф с игрока (закрыть UI). |
| `CreateUI_-_Overview` | `Buff_DG_Cryobreeder_C` | **client** reliable | Сервер → клиент: прислать OverviewData для отрисовки |
| `CreateUI_-_Options` | `Buff_DG_Cryobreeder_C` | **client** reliable | Сервер → клиент: прислать UI_Data + PulseMax + PreventAll + ModCosts |

**Защита от Cheater() как у Kav OmniTool здесь не замечена** —
`BPServerHandleNetExecCommand` CryoBreeder не содержит обвязки
`SessionID` / `Cheater()` в разборе. Отправка собственных RPC в
`BP_CryoBreeder.BPServerHandleNetExecCommand` не триггерит кик по
модели Kav. Если планируется ещё раз пересвериться — читать
псевдокод `BPServerHandleNetExecCommand` целиком (112 строк в
`BP_CryoBreeder`, 17 в буфе).

## 5. Как детектить и читать (под реализацию в arkmultitool)

Короткая версия — чтение один-в-один с `mods::structures_plus::Feature`:

1. **Регистрация в [registry.hpp](../../arkmultitool/src/game/features/mods/registry.hpp)**: добавить мод с detect_class = `Buff_DG_Cryobreeder_C`.

   ```cpp
   {3757858218ULL, "friendly_fibercraft", "Friendly Fibercraft",
       L"Buff_DG_Cryobreeder_C"},
   ```

   Этого достаточно, чтобы reader клал buff в `snapshot.mod_instances`.

2. **Feature** (`mods/friendly_fibercraft/cryobreeder.{hpp,cpp}` или
   расширение существующей `structures_plus::Feature` — см. раздел 6):

   ```
   tick():
     for buff in snapshot.mod_instances where mod_id == 3757858218:
       propagator = read_property<uintptr_t>(buff.address, buff.class, L"Propagator")
       if propagator < 0x10000: continue
       propagator_class = read object_class(propagator)
       if propagator_class != cached BP_CryoBreeder_C: continue
       items = read TArray(DinoList) via property_meta("DinoList")
       is_breeding = read_property<bool>(..., L"IsBreeding")
       male_index = read_property<int32>(..., L"MaleIndex")
       ...
   ```

   `DG_Cryobreeder_DinoData` — тот же `StructProperty`, что
   `FPropagatorDinoData` у S+: `PrimalDinoCharacter` ptr, пол,
   `CanBreed`, pulse count, статы, цвета. Разбор полей struct'а —
   отдельная подзадача, читается через `asset_summary` на
   `/Game/Mods/Friendly_Utility/Propagator/Structs/DG_Cryobreeder_DinoData`.

3. **UI**: страница мода в сайдбаре (так же, как `page_structures_plus.cpp`),
   HUD-пины подсветки.

## 6. Подход к добавлению форков («пропагатор-подобное»)

Форки одного и того же: classic S+ (`Buff_StructuresPlus_Propagator_C`),
S+Kav (его `BP_Propagator`), SS (унаследовано у S+), FF Utility
(`Buff_DG_Cryobreeder_C`), CPlusMain (`Buff_CryoBreeder_CP_C`,
Workshop ID 3803950735 — виден в `asset_find`). Также замечены ещё
несколько CryoBreeder-подобных сборок. Каждый форк — свой UClass, свой
Workshop ID, но **одинаковая роль и один набор полей**: structure с
`DinoList`, buff с `Propagator`-ссылкой, серверный `BPServerHandleNetExecCommand`.

Три варианта архитектуры:

### 6.1. Текущий — mod_id × detect_class (как сейчас)

Каждый мод = одна строка в `registry.hpp`, одна per-mod `Feature`.
Явно, пишется легко, в UI сразу видно «S+Kav» или «FF Utility».
Минус: добавление форка = код + review. Пропустил сервер — не
работаем, как сейчас.

### 6.2. Multi-UClass per mod

`ModInfo` получает список `detect_class[]`. Остальное без изменений.
Поможет случаям вроде FF (один mod — Cryobreeder + Mutator + ClonerV2 +
PingSystem), но форки родственных модов всё равно требуют отдельных
строк.

### 6.3. Поведенческий интерфейс — «propagator-kinship»

Отдельная таблица — не список UClass'ов, а список **ролей**:

```cpp
struct PropagatorKinship {
    const wchar_t* buff_class_substring;      // "Buff_StructuresPlus_Propagator"
    const wchar_t* structure_field_on_buff;   // "Propagator" или "PropagatorRef"
    const wchar_t* dino_list_field;           // "DinoList" или "Dinos"
    std::uint64_t  workshop_id;               // для UI/логов
    const char*    display;                   // "S+ Propagator"
};
```

Reader при обходе актёров сверяет имя класса каждого баффа по substring
со списком. Одна общая `PropagatorFeature` читает поля по именам из
записи kinship — работает с любым UClass'ом из таблицы.

**Плюс**: добавление нового форка = одна строка таблицы без правок в
Feature. Код чтения общий, поддержка автоматически покрывает все
форки с теми же именами полей.

**Минус**: нужно знать имена полей по форку. S+Kav/FF/CPlusMain все
унаследованы от S+ API, имена вроде `DinoList` / `IsBreeding` /
`MaleIndex` одинаковы — проверено (FF: см. раздел 2). Поля с
другими именами потребуют отдельного маппинга в kinship.

**Минус-2**: substring-детект может ложно сработать на не-пропагатор
баффе с «Propagator» в имени. Текущие форки используют отличимые
substring'и (`StructuresPlus_Propagator`, `DG_Cryobreeder`, `CryoBreeder_CP`),
совпадений не ожидается.

### Рекомендация

**6.3 с fallback на 6.1**. Общая `PropagatorFeature` + kinship-таблица
покрывает все нынешние форки одной стройкой. Явные записи в
`registry.hpp` остаются для **именования** мода в UI (`"S+ classic"` vs
`"S+Kav"` vs `"FF Utility"` vs `"CPlusMain"`) — kinship ссылается на
`workshop_id`, чтобы брать `display_name` из registry. Разбор нового
форка (`asset_summary` + сравнение полей) становится preparation step,
а не code change.

## 7. Отчёты по родственным модам (TODO)

* `CPlusMain` (`3803950735`) — свой `BP_CryoBreeder_CP` + `Buff_CryoBreeder_CP`.
  Разобрать по тому же шаблону, добавить kinship-запись.
* `Super Structures` (`1999447172`) — своего CryoBreeder нет, использует
  классический `Buff_StructuresPlus_Propagator_C`. Отчёт не нужен,
  classic S+ путь покрывает.
* `Structures Kav` (`3461528683`) — классический `BP_Propagator`; отчёт
  `StructuresPlusMod_3461528683.md` закрыл OmniTool-часть; propagator —
  отдельная подзадача по тому же плану.
