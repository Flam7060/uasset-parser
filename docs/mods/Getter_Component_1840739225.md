---
title: "Getter Component — HWID Bans (1840739225)"
status: card
workshop_ids: [1840739225]
---

# Getter Component — HWID Bans (1840739225)

> **Карточка, security-разбора нет.** Собрано по `mod.info`/`modmeta.info` и MCP `ark-re` (`asset_summary`, `asset_function`, `mod_net_rpcs`, `pdb_symbols`). Входит в [коллекцию MESA](MESA_Collection_3826240151.md).

## Инфо

| Поле | Значение |
| --- | --- |
| Workshop ID | 1840739225 |
| Название в Workshop | Getter Component (обновлён 2023-03-07; описание — «Helper MOD») |
| Внутреннее имя (`mod.info`) | **`HWIDBans`** → `/Game/Mods/HWIDBans` |
| ModName (PrimalGameData) | HWID Bans — «Unique ID ban system for ARK» |
| ModType (`modmeta.info`) | 1 — обычный мод |
| Ассетов | 2 (`Actor_BP_HWIDBans`, `PrimalGameData_BP_HWIDBans`) |
| GUID | 95704847403403BBF0E5BB96B0D180AD |

## Что делает

Под нейтральным именем «Getter Component» в Workshop лежит **клиентская часть системы банов по железу**. Мод только собирает идентификатор устройства клиента и отправляет его на сервер. Решение о бане принимает серверный плагин: в блюпринте серверные обработчики пустые.

## Как сделано

- `PrimalGameData_BP_HWIDBans.ServerExtraWorldSingletonActorClasses` = `Actor_BP_HWIDBans` — серверный синглтон, реплицируется клиентам.
- На клиенте в `ReceiveBeginPlay` через 0.2 с, если это не сервер, вызывается `ROS_Notify_Client_Ready()` (NetServer).
- Сервер (плагин) отвечает `ROC_Get_HWID()` (NetClient). На клиенте `KismetSystemLibrary::GetUniqueDeviceId()` → `ROS_Server_Receive_HWID(HWID: String)` (NetServer).
- Тела `ROS_Notify_Client_Ready` и `ROS_Server_Receive_HWID` в BP пустые: их перехватывает серверный плагин (ArkApi-хук). `Start_Get_Client_HWID` на дедике сразу зовёт `ROC_Get_HWID`, то есть это точка входа для плагина.
- **Что за «HWID».** В UE4 4.x `GetUniqueDeviceId` = MD5 от строки MAC-адреса (`FGenericPlatformMisc::GetMacAddressString` ← `FWindowsPlatformMisc::GetMacAddress`). Оба символа есть в клиентском PDB, сама `GetUniqueDeviceId` заинлайнена в exec-thunk. Дизассемблированием это не проверялось, вывод сделан по исходникам UE4.

## Заметки

- Идентификатор целиком вычисляет клиент и шлёт строкой. Сервер не может проверить его подлинность, так что подмена тривиальна для модифицированного клиента.
- Под Proton MAC берётся из Wine (`GetAdaptersInfo`) — тот же источник MAC, что у BattlEye (см. память `reference_battleye_hwid_linux`), но не DMI, не machine-id и не диски.
