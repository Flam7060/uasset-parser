# Отчёт: разбор мода `/Game/Mods/AC` (ARK SE)

Дата: 2026-10-17. Инструмент: `parse_uasset.py` (каталог `uasset-parser`).
Расшифровка каждого ассета лежит рядом: `decoded/<имя>.txt`.

## 1. Результат прогона

| Ассет | Экспортов | Функций с кодом | Строк в дампе | Итог |
|---|---|---|---|---|
| Actor_BP_AC | 28 | 6 | 378 | ok |
| Buff_AC | 257 | 14 | 2730 | ok |
| DetectionPacket | 6 | — (структура) | 67 | ok |
| GunCheck | 5 | — (структура) | 63 | ok |
| PrimalDinoCharacter_BP_AC | 48 | 6 | 628 | ok |
| PrimalGameData_BP_AC | 5 | 0 (убер-граф пуст) | 105 | ok |
| ShooterWeapon_BP_AC | 13 | 4 | 197 | ok |

- Все 7 файлов разобраны полностью, код возврата 0, в stderr пусто.
- В каждой из 30 функций с байткодом пересчитанный размер в памяти совпал с размером из заголовка функции. Неизвестных опкодов и недочитанных байт нет.
- Единственное поле с неизвестным смыслом — u32 = 1 в конце каждого экспорта `Blueprint` (поле ARK). Оно выводится как `ark u32`.

## 2. Что это за мод

Клиентский античит с маскировкой:
- В `PrimalGameData_BP_AC` мод представляется как `ModName = "AdminUI"`, `ModDescription = "AdminUI"`.
- Каналы связи названы `'OnClientMovement'` и `'AC_PACKET'`.
- Типы пакетов XOR-ятся константой 14973, к ним добавлены случайные числа и мусорные константы.

Как работает:
- Сервер вешает каждому игроку скрытый постоянный бафф `Buff_AC`.
- Бафф на стороне клиента собирает параметры персонажа, оружия и материалов.
- Собранное отправляется на сервер через `ShooterPlayerController::ClientSendNetExecCommandToServer(obj, command, BPNetExecParams, false)`.
- В `BPNetExecParams` 3 int, 3 float, 3 объекта и 1 строка.

**Серверного обработчика в этих ассетах нет.** Ни один файл не разбирает `OnClientMovement`/`AC_PACKET`. Приём и решение о бане сделаны где-то ещё: в другом ассете, в серверном плагине или в нативном коде.

## 3. Компоненты

### PrimalGameData_BP_AC — точка входа
- В `ServerExtraWorldSingletonActorClasses` прописаны `Actor_BP_AC_C`, `ShooterWeapon_BP_AC_C` и `PrimalDinoCharacter_BP_AC_C`. Сервер создаёт по одному экземпляру каждого.
- Собственной логики нет: убер-граф пуст.

### Actor_BP_AC — раздача баффа (сервер)
- `ReceiveBeginPlay`: ждёт 0.2 с (`Delay`), берёт `GameMode`, приводит к `ShooterGameMode` и подписывается на `OnCharacterPossessedByPlayer`.
- `OnCharacterPossessedByPlayer_Event(TheCharacter, ByPlayerController)`: если у персонажа нет `Buff_AC_C` (`HasBuff`), вызывает `PrimalBuff::StaticAddBuff(Buff_AC_C, TheCharacter, …)`.
- `OnPreSpawnedDino_Event`: только печатает "dino spawn".
- `AllowManualMultiUseActivation` всегда возвращает `false`.

### Buff_AC — сами проверки (клиент)
Свойства CDO: `bHideBuffFromHUD`, `bIsBuffPersistent`, `bPersistentBuffSurvivesLevelUp` — бафф невидим и переживает перезаход и повышение уровня.

Через убер-граф подключены 4 события:

| Событие | Что собирается и куда уходит |
|---|---|
| `ReceiveBeginPlay` | Контроллер → `ShooterCharacter` → `PlayerState`. Подписка на `NotifyWeaponFired` и `OnThirdPersonCameraView`. Проверка `AdditionalMaxUseDistance > 400`: если да — пакет, иначе печать "false". |
| `OnThirdPersonCameraView_Event` | Собирает `DetectionPacket` {MAC = `GetUniqueDeviceId()`, MaxCamOrbit = `OrbitCamMaxZoomLevel`, контроллер} → `SendDetection(packet, 1027)` |
| `NotifyWeaponFired_Event(Weapon)` | Порог: `GlobalFireCameraShakeScale < 0.6`, `AimDriftYawFrequency < 0.4` или `AimDriftPitchFrequency < 0.4` → пакет с `CurrentFiringSpread`, `GlobalFireCameraShakeScale`, `InstantConfig.WeaponSpread` и строкой `"||" + DeviceId`. Затем на каждый выстрел — пакет с `WeaponConfig.TimeBetweenShots` (скорострельность). Для оружия с "Multi" в имени выполняется более короткая ветка. |
| `MovementDone` (Net, NetClient — RPC на клиент) | Полный скан, см. ниже |

Скан в `MovementDone`:
- **Идентификаторы.** `PlayerState.GetUniqueIdString()` (Steam ID) и `GetUniqueDeviceId()` — два пакета с типом 44983.
- **Оружие.** `AimDriftYaw/Pitch`, `GlobalFireCameraShakeScale`, `CurrentFiringSpread` умножаются на 100, XOR-ятся с 6972 и суммируются в контрольное число (минус 186). Уходят с типом 49436.
- **Камера.** `OrbitCamMaxZoomLevel` XOR 25818, контрольное поле `−2737`, тип 73424.
- **Дальность взаимодействия.** `AdditionalMaxUseDistance`×100, XOR 32140, `−3361`, тип 81330.
- **Материалы** (защита от чамсов и прозрачных текстур), всё с типом 29310:
  - все `PrimalCharacter` с приведением к `PlayerPawnTest_C`: материалы `Mesh.SkeletalMesh` (MaterialInstanceConstant/Dynamic) — базовый материал, `BlendMode`, путь класса;
  - материалы `Mesh3P` своего оружия;
  - все `PrimalStructureTurret` → `MyStaticMesh.Materials` → `BlendMode` и путь класса.

Функции, которые внутри пакета никто не вызывает (их зовёт внешний код или это мёртвый код):

| Функция | Что отправляет |
|---|---|
| `SendDetection(DtcPacket, TickNum)` | Если `MaxCamOrbit ≥ 12`: тип 73424, floor(orbit), TickNum XOR 94624, строка `PlayerID + MAC`, канал `OnClientMovement`. Вызывается из события камеры. |
| `Heartbeat(Idx)` | 3 пакета `AC_PACKET` с типами 64353, 74925 и 73843. Значения float выводятся из floor(Idx), строки-константы вида `"E343DW434DA\|\|DA4342WDAW4"`. В конце печатает "Heartbeat done". |
| `OnTick(TickNumber)` | Тип 34253, случайное число, TickNumber XOR 94624, строка `"765237425 \|\| FF:FFA4:35:5A"`. |
| `GunCheck(GunCheck)` | Парсит JSON из поля `config`, массив `ExcludeGuns` (разделитель "."), ищет в нём имя оружия. В зависимости от результата шлёт тип 64353 в `AC_PACKET` или в `OnClientMovement` (IntParam2 = 1). |
| `ExcludePlayerFromDetections()` | Тип 64352, случайные значения |
| `Set Player Detections(N)` | Тип 64352, IntParam2 = N |
| `BeginPlay()` | Пустой пакет с командой `'None'`, проверка оружия, печать "Valid" |
| `DoMove()` | Вызывает `MovementDone()` |

### PrimalDinoCharacter_BP_AC — дино (клиент)
- Подписывается на `OnCharacterPossessedByPlayer`. Если персонаж — дино, ещё и на `OnCharacterMovementUpdated`.
- При движении с валидным `Rider` проверяет `FlyingForce/Walking/Swimming/RiderRotationRateModifier > 30`.
- Если порог превышен: `AC_PACKET`, тип 43543, floor(FlyingForce…), флаг `Isvalid`, три модификатора и объект `Rider`.

### ShooterWeapon_BP_AC — заглушка
Только печатает "Weaponn start" (`ReceiveBeginPlay`) и "called" (`CheckTimer`).

### Структуры
- `DetectionPacket`: PlayerID (Str), MAC (Str), Object, MaxCamOrbit (Float), PlayerReference (PlayerController).
- `GunCheck`: config (Name, внутри JSON), character (ShooterCharacter), weapon, controller.

## 4. Таблица типов пакетов (IntParam1)

Часть типов записана как `SelectInt(X, X, true) XOR 14973`, это простая маскировка константы. Другие записаны открытым числом.

| Как в коде | Реальное значение | Откуда |
|---|---|---|
| 74925 ^ 14973 | 73424 | камера (`OrbitCamMaxZoomLevel`) |
| 67535 ^ 14973 | 81330 | `AdditionalMaxUseDistance` |
| 64353 ^ 14973 | 49436 | параметры оружия |
| 763563 ^ 14973 | 761046 | `TimeBetweenShots` на выстрел |
| 18435 ^ 14973 | 29310 | материалы / BlendMode |
| 38346 ^ 14973 | 44983 | UniqueId / DeviceId |
| 64353, 74925, 73843 | открыто | `Heartbeat` |
| 34253 | открыто | `OnTick` |
| 64352 | открыто | `ExcludePlayerFromDetections`, `Set Player Detections` |
| 64353 | открыто | `GunCheck` |
| 43543 | открыто | дино |

Наблюдение: в `Heartbeat` и `GunCheck` уходят те же сырые 64353 и 74925, которые в других местах предварительно XOR-ятся. То есть сервер получает одно и то же исходное число в двух видах.

## 5. Связь с HWID

Поле `MAC` в `DetectionPacket` и строки с `DeviceId` заполняются через `KismetSystemLibrary::GetUniqueDeviceId()`. В стоковом UE 4.x на Windows это MD5 от MAC-адреса сетевого адаптера, без серийников железа. Под Proton MAC берётся через `GetAdaptersInfo` из wine, то есть это реальный MAC интерфейса хоста. Плюс отправляется Steam ID (`GetUniqueIdString`).

Это вывод по стоковому движку: что именно `GetUniqueDeviceId` делает в бинарнике ARK, я не проверял.

## 6. Ограничения разбора

- Опкод `0x65` интерпретирован как строковый литерал ARK: текст плюс числовой ключ. Размеры сходятся, контекст (аргументы FString) подтверждает. В дампах выглядит как `"текст"/*#ключ*/`.
- Порядок выполнения внутри убер-графа восстанавливается по меткам и `push_flow`/`pop_flow`. Описание в разделе 3 сгруппировано по тому, что собирается, а не по точному порядку выполнения.
- Серверная часть античита в этих 7 файлах отсутствует.
