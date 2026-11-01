---
title: Structures Plus family — index
status: draft
created: 2026-10-30
---

# S+ family — index

Форки одного и того же мода: classic S+ и его потомки. Все переупаковывают
одинаковую роль «пропагатор-подобный контейнер с DinoList», часть добавляет
своё. Собираем здесь, чтобы:

1. не писать по one-off отчёту на каждый форк,
2. поддерживать общую `PropagatorFeature` (см. `arkmultitool/docs/PROPAGATOR_FAMILY_SPEC.md`)
   через kinship-таблицу,
3. унаследовать баги оригинала (например, находки `#1..#7` S+Kav) на новые
   форки, которые их не фиксили.

| Workshop ID | Имя | Статус отчёта |
| --- | --- | --- |
| 731604991  | Structures Plus (classic) | [Propagator + OmniTool](./Structures_Plus_S_731604991.md) |
| 1999447172 | Super Structures | TODO (без своего propagator — наследует classic) |
| 3469918803 | C+ Structures | [карточка-дифф от classic](./C_Structures_3469918803.md) |
| 3461528683 | Structures Kav | [частично — OmniTool](./StructuresPlusMod_3461528683.md), propagator TODO |
| 3757858218 | Friendly Fibercraft \| Utility | [CryoBreeder](./FriendlyFibercraft_Utility_3757858218.md) |
| 3803950735 | CPlusMain | TODO (BP_CryoBreeder_CP) |

## Соглашения отчёта

Каждый форк документируется минимально по форме FF-отчёта: карта классов,
поля structure и buff, список серверных RPC с направлением, замечания про
Cheater()/SessionID если есть. Поля чтения дают готовую запись kinship для
`arkmultitool/src/game/features/mods/propagator_kinship.hpp`.

## Унаследованные баги оригинала

Форки часто ничего не фиксят из того, что было в classic S+. Если находка
первой зафиксирована в classic-отчёте — ставим в его таблице статус
«присутствует в <форке>» без копирования описания. Таблица в Kav-отчёте
(«Статус в Kav») — пример такой разметки.

**Известный разрыв конвенции:** находка #7 Kav-отчёта (OmniTool Basic Transfer
— `SanityCheckBasicTransfer` не проверяет источник) была зафиксирована первой
в форке, хотя баг — байт-в-байт из classic. В classic-отчёт задним числом
добавлен второй, независимый путь к тому же гейту (`SetSessionVars` вместо
`FiredEvent`/`Target`); сам факт отсутствия проверки источника остаётся
описан только в Kav-отчёте (там же фикс). Не переносить описание — читать по
кросс-ссылке.
