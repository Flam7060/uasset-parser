---
kind: game-analysis            # разбор САМОЙ ИГРЫ (нативный код), не мод
title: Нативная авторитетность — движение, выстрелы, дюп
scope: ShooterGameServer.pdb + ShooterGameServer.exe (сервер)
method: PDB-символы/раскладки + граф вызовов из машкода (arkre.native_calls, статическое чтение .exe с диска); процесс игры не трогался
status: draft
reviewed: 2026-11-01
confidence: структурные факты по графу вызовов; «где именно порог/кламп» внутри листаемых функций — кандидат на стенд
related: native_inventory_multiuse_trust.md
---

# Разбор самой игры: движение, выстрелы, дюп

> Продолжение [native_inventory_multiuse_trust.md](native_inventory_multiuse_trust.md). Там разобраны
> инвентарь/MultiUse/перенос. Здесь — три области из запроса: **передвижение, выстрелы, дюп**.
> Всё читалось **статически с диска** (`arkre.native_calls` по машкоду `ShooterGameServer.exe`),
> игра не запускалась и не инжектилась. Это разбор **защищённости** (где сервер проверяет клиента),
> а не инструкция по эксплуатации: находки оформлены как «гейт есть / гейт тонкий → проверить на
> стенде → как детектить/митигировать».

## Коротко

По всем трём областям базовый нативный сервер **авторитетен и проверяет клиента**, а не доверяет на
слово:

- **Движение** — временные метки клиента валидируются (`VerifyClientTimeStamp`/`ProcessClientTimeStamp`),
  координаты санируются (`_isnan`/`_finite`), рассинхрон правится коррекцией с порогом из
  `AGameNetworkManager`. Это антиспидхак/антител-порт на нативном уровне.
- **Выстрелы** — сервер **сам перетрассирует** каждый хитскан (`LineTraceSingle`/`SweepSingle`), сам
  считает разброс и точку выстрела; снаряды — сервер **клампит** клиентские точку и направление
  (`UVictoryCore::ClampLocation`/`ClampRotAxis`) и гейтит `ValidateFire`.
- **Дюп** — базовые пути `ServerSplitItemStack` и `ServerAddItemToArkInventory` структурно корректны
  (источник ищется в своём компоненте, порядок «уменьшить → создать»), но **точка клампа количества**
  спрятана внутри `IncrementItemQuantity`/`AddItem` — это остаточный кандидат на стенд, как и
  атомарность кластерной выгрузки.

Вывод тот же, что и по инвентарю: **наивных дыр в базовых путях нет**; реальные риски — пороги в
конфиге (`AGameNetworkManager`), окно лаг-компенсации, и BP/мод-оверрайды, которые эти гейты
подменяют или зовут мимо.

---

## 1. Передвижение

### Что происходит
Клиент шлёт `ServerMove(timestamp, accel, clientLoc, …)` (и варианты Dual/WithRotation/Old/OnlyRotation —
их много, см. зацепки). Разобран базовый `ServerMove_Implementation` (`0x23ab530`).

Граф вызовов (факты из машкода):

| Вызов | Что значит для защиты |
| --- | --- |
| `VerifyClientTimeStamp(float, FNetworkPredictionData_Server_Character&)` (`0x23aae40`, 80 б) | **антиспидхак-ядро.** Крошечная функция без под-вызовов — чистая арифметика: проверяет правдоподобность присланной клиентом `delta`-метки времени. Через неё клиент не может «ускорить время» и получить лишние шаги симуляции. |
| `ProcessClientTimeStamp(...)` (`0x23ab040`) | принимает/обновляет метку после верификации. |
| `MSVCR120!_isnan` ×11, `_finite` ×6 | каждая компонента присланных векторов проверяется на NaN/inf → мусорные координаты (телепорт «в никуда», креш-пакеты) отсекаются. |
| `ConvertFromRelativeToAttachedParent`, `ConvertToAbsAccel`, `GetMovementBaseTransform` | пересчёт относительно базы (лифт/платформа/дино) — своей математикой, не клиентской. |
| `SetWorldLocation` | применение позиции **после** проверок. |

Коррекция рассинхрона — `ServerMoveHandleClientError` (`0x23abce0`):

| Вызов | Что значит |
| --- | --- |
| `AGameNetworkManager::WithinUpdateDelayBounds(PC, double)` (`0x2415520`) | частота апдейтов клиента в допуске (антифлуд). |
| `GetDefault<AGameNetworkManager>()` ×2 | **пороги берутся из CDO `AGameNetworkManager`** — т.е. допуск ошибки позиции конфигурируемый. Именно здесь живёт «насколько сервер прощает расхождение», прежде чем откатить (`SetWorldLocation` назад). |

### Остаточный риск (кандидат)
- Антиспидхак **есть** и работает на метке времени — подделать «быстрее» в базовом пути нельзя.
- Реальная поверхность — **пороги `AGameNetworkManager`** (`MOVESERVER*`, `ClientNetSendMoveDeltaTime`,
  `*ErrorTolerance`): слишком щедрые значения в конфиге/моде расширяют допуск телепорта/рубер-бэндинга.
  Это **настройка**, а не дыра кода.
- Дино-движение идёт через отдельный `ServerMoveHandleClientErrorForDinos` (`0x23aa5b0`) — тот же каркас,
  отдельная ветка (стоит проверить, не мягче ли пороги для ездовых).

### Детект / митигация
- Спидхак/телепорт виден **внешним** мониторингом (как у вас в `ext_dump.py`): дельты позиции между
  тиками > физмакс скорости класса, при нормальном пинге. Это читается снаружи, без инжекта.
- Не трогать `AGameNetworkManager`-пороги в сторону ослабления; если мод их двигает — это находка.

---

## 2. Выстрелы

Две модели оружия, обе авторитетны по-разному.

### 2a. Хитскан (`AShooterWeapon_Instant`)
`ServerNotifyShotOrigin_Implementation` (`0xf02040`) → `ServerProcessShotsInternal` (`0xf02bb0`, ~30 КБ).

Факты:
- Клиент присылает origin и массив `FHitResult`. Сервер **не принимает хиты на веру**: в самом
  `ServerNotifyShotOrigin` стоит `sqrtf` — **проверка дистанции** клиентского origin против
  **серверного** `GetFireLocation()`; затем сервер делает **свои** `LineTraceSingle`/`SweepSingle`.
- `ServerProcessShotsInternal` пересобирает геометрию выстрела сам: своя точка `GetFireLocation()`,
  свой разброс `GetCurrentSpread()` + `FRandomStream` (сервер держит сид, клиент не диктует куда легло),
  тригонометрия конуса (`acosf`/`cosf`/`sinf`/`RotateAngleAxis`), затем перетрассировка и урон через
  `ProcessInstantHit_Confirmed` (имя говорит само — «подтверждённый» хит).
- Читается глобал `GIsOfficialServer` и `bHackDontUseAmmo` — античит-ветки/дебаг-флаги (на бою off).

**Остаточный риск:** главный нативный компромисс хитскана — **окно лаг-компенсации**: сервер перетрассирует,
но с поправкой на пинг. Чем шире окно, тем больше «я попал из-за угла/с опозданием» становится валидным.
Это внутри 30-КБ тела (порог/окно) — кандидат на стенд, а не подтверждённая дыра. Аимбот-направление
нативом **не гейтится напрямую** (клиент и так крутит камеру) — ловится только статистикой попаданий.

### 2b. Снаряды (`AShooterWeapon_Projectile`)
`ServerFireProjectile_Implementation` (`0xf1c090`). Здесь сервер жёстче:

| Вызов | Что значит для защиты |
| --- | --- |
| `AShooterWeapon::ValidateFire()` (`0xed15a0`) | **гейт выстрела** (можно ли вообще стрелять: состояние, перезарядка и т.п.). |
| `UVictoryCore::ClampLocation(...)` (`0x11d1b30`) ×2 | **клиентская точка выстрела клампится** к серверной `GetShootingCameraLocation()`/`GetMuzzleLocation()` в пределах радиуса. Произвольный «выстрел из точки на другом конце карты» срезается. |
| `UVictoryCore::ClampRotAxis(...)` (`0x11d1910`) ×2 | **направление клампится** по осям — инъекция произвольного угла ограничена. |
| `GetShootingCameraLocation`/`GetMuzzleLocation` | авторитетные серверные позиции, не клиентские. |
| `bProjectileHackDontUseAmmo` (read) | дебаг/античит-флаг, на бою off. |

**Остаточный риск:** пороги клампа (радиус `ClampLocation`, пределы `ClampRotAxis`) — конфиг/класс-зависимые;
дыра была бы, только если класс оружия (BP-оверрайд) ослабил их. База — клампит.

### Детект / митигация
- Аимбот/«через стены» на хитскане — не ловится кодом, только **серверной статистикой**: аномальная доля
  хедшотов/попаданий сквозь геометрию. Это внешний анализ логов урона, не правка натива.
- Любой мод-класс оружия, переопределяющий `ValidateFire`/геометрию или зовущий урон мимо
  `ProcessInstantHit_Confirmed`, — приоритетная цель для `mod_search` (символы ниже).

---

## 3. Дюп

Базовый перенос/транзит разобран в предыдущем отчёте (кламп по указателю, порядок «удалить→добавить»,
кулдаун на PC). Здесь — две **новые** нативные поверхности, классически дюп-склонные.

### 3a. Split стака — `ServerSplitItemStack_Implementation` (`0x75b7f0`)
Порядок (факт из графа):
1. `FindItem(id)` **в этом же компоненте** → чужой предмет по ID не разделить.
2. `IncrementItemQuantity(int, …)` (`0x7c32b0`) — **уменьшение источника**.
3. `GetItemNetInfo` → `GenerateItemID` (**новый** ID) → `CreateItemFromNetInfo` → `AddItem` — создание
   второго стака с собственным сетевым ID.

Структурно верно: сначала снять с источника, потом создать новый стак; новый получает свежий `GenerateItemID`
(не копия ID — значит не «две записи на один предмет»).

**Остаточный риск (кандидат на стенд):** клиентский `int` (сколько отделить) уходит в
`IncrementItemQuantity`. **Точка клампа — внутри `IncrementItemQuantity`** (должна флорить источник по 0 и
не давать снять больше стака), а количество нового стака должно равняться **фактически снятому**, а не
запрошенному. Если `IncrementItemQuantity` не клампит, а новый стак берёт запрошенное число — это
split-дюп. Проверка: отделить от стака заведомо больше, чем в нём есть, и посмотреть сумму. Чинить (если
подтвердится) — только серверный плагин/натив, настройкой не закрыть.

### 3b. Выгрузка в облачный ARK-инвентарь — `ServerAddItemToArkInventory_Implementation` (`0xdfd760`)
Гейты, которые **есть** (факт из графа):
- `AShooterGameState::IsClusterServer()` ×2 — путь живёт только на кластере; на одиночном приватном
  сервере, скорее всего, ранний выход (у вас зависит от кластер-конфига).
- `GetPlayerInventory()` ×6 — работа идёт с **реальным инвентарём игрока**, а не только с присланным
  указателем `UPrimalInventoryComponent*` (хороший признак — указатель клиента не главный источник правды).
- `AllowAddingToArkTribute()` — гейт допуска к трибьюту.
- `HasReachedTributeItemsLimit(int)` — кап на число предметов в облаке.
- `ServerAddToArkTributeInventory(...)` — собственно добавление в облако.

**Остаточный риск (кандидат на стенд):** классический дюп кластер-переноса — **неатомарность**: предмет
добавлен в трибьют, но не снят с локального инвентаря (или наоборот). В графе виден `AddItem`/
`IncrementItemTemplateQuantity` (сторона добавления) и трибьют-добавление, но **снятия с источника явным
символом не видно** — оно либо внутри `ServerAddToArkTributeInventory`, либо через vtable-индирект. Проверить
на стенде: что остаётся в локальном инвентаре после успешной выгрузки; оборвать соединение между добавлением
в облако и снятием. (То же семейство рисков — у `ServerUploadCurrentCharacterAndItems`/`ServerUploadDino`/
`ServerRequestDownloadDino`: загрузка/выгрузка персонажа и дино — самые дюп-склонные места ARK.)

### Связь с вашим кейсом капа 130
Дюп малышей на вашем сервере ([[project_baby_dupe_cap130]]) нативом **не объясняется** — нативное
вылупление спавнит одного. Как и в прошлом отчёте: копию делает серверный плагин капа, а ловушка Dino
Storage ловит оригинал раньше удаления. Нативные дюп-поверхности выше — **другой** класс (предметы), к
дюпу малышей отношения не имеют.

---

## Сводка находок

| # | Область | Нативный гейт | Статус | Остаточный риск |
| --- | --- | --- | --- | --- |
| M1 | движение/спидхак | `VerifyClientTimeStamp` (80 б, инлайн-арифметика) | **гейт есть** | пороги `AGameNetworkManager` в конфиге/моде |
| M2 | движение/телепорт | `_isnan`/`_finite` на векторах + коррекция с порогом | **гейт есть** | допуск ошибки позиции (конфиг); отдельная ветка дино |
| S1 | хитскан | сервер перетрассирует (`LineTraceSingle`/`SweepSingle`), свой разброс+сид, `sqrtf` на origin | **гейт есть** | ширина окна лаг-компенсации (внутри тела); аимбот — только статистикой |
| S2 | снаряды | `ValidateFire` + `ClampLocation` + `ClampRotAxis` | **гейт есть** | пороги клампа; BP-оверрайд класса оружия |
| D1 | split-дюп | `FindItem` в своём компоненте, порядок «снять→создать», новый ID | **структура верна** | кламп количества внутри `IncrementItemQuantity`; кол-во нового стака = снятому? |
| D2 | кластер-выгрузка | `IsClusterServer`/`AllowAddingToArkTribute`/`HasReachedTributeItemsLimit` | **гейты есть** | атомарность «снять с локала ↔ добавить в облако» |

## Нативные зацепки (символы, server PDB)

Движение:
- `UCharacterMovementComponent::ServerMove_Implementation` — `0x23ab530`
- `…::ServerMoveHandleClientError` — `0x23abce0`; `…ForDinos` — `0x23aa5b0`
- `…::VerifyClientTimeStamp` — `0x23aae40`; `ProcessClientTimeStamp` — `0x23ab040`
- `AGameNetworkManager::WithinUpdateDelayBounds` — `0x2415520`
- варианты: `ServerMoveDual/WithRotation/Old/OnlyRotation(+Dual)` — см. `pdb_symbols "*ServerMove*"`

Выстрелы:
- `AShooterWeapon_Instant::ServerNotifyShotOrigin_Implementation` — `0xf02040`; `ServerNotifyShot` — `0xf02000`
- `AShooterWeapon_Instant::ServerProcessShotsInternal` — `0xf02bb0`; `ProcessInstantHit_Confirmed` — `0xf0a3a0`
- `AShooterWeapon::ValidateFire` — `0xed15a0`
- `AShooterWeapon_Projectile::ServerFireProjectile_Implementation` — `0xf1c090`; `ServerFireProjectileEx` — `0xf1dfb0`
- `UVictoryCore::ClampLocation` — `0x11d1b30`; `ClampRotAxis` — `0x11d1910`
- баллиста: `AShooterCharacter::ServerNotifyBallistaShot` — `0xad20d0`; `ServerFireBallistaProjectile` — `0xb12960`

Дюп (предметы):
- `UPrimalInventoryComponent::ServerSplitItemStack_Implementation` — `0x75b7f0`
- `…::IncrementItemQuantity` — `0x7c32b0`; `AddItem` — `0x734330`; `GenerateItemID` — `0x7d9bc0`; `CreateItemFromNetInfo` — `0x790960`
- `…::ServerMergeItemStack`/`ServerForceMergeItemStack` — `0x75bde0` / `0x75c2a0`
- `AShooterPlayerController::ServerAddItemToArkInventory_Implementation` — `0xdfd760`; `ServerAddItemFromArkInventory` — `0xdc5480`
- `…::ServerUploadCurrentCharacterAndItems` — `0xdc7850`; `ServerUploadDino` — `0xdd8f40`; `ServerRequestDownloadDino` — `0xe06970`
- `UPrimalInventoryComponent::ServerAddToArkTributeInventory` — `0x76fd90`; `AllowAddingToArkTribute` — `0x747760`

## Ограничения метода

- `native_calls` даёт **рёбра графа вызовов и обращения к глобалам** (глубина 1), а не полную логику:
  «есть ли порог и каков он» внутри листаемой функции (окно лаг-компенсации, кламп количества в
  `IncrementItemQuantity`) — это кандидат на стенд или на отдельный проход по телу.
- Виртуальные вызовы названы не все (где в точке вызова не восстановим тип — помечены `vtable слот N`).
- Разбирался только серверный бинарь; клиент — тем же кодом при необходимости.
- Процесс игры не трогался: всё статическое чтение `.exe`/PDB с диска.
