"""
Адресация ассетов и поиск файлов.

Три формы адреса (раздел 4 спеки):
    1. абсолютный путь к .uasset;
    2. игровой путь `/Game/...` (`.Name` на конце необязателен);
    3. `<workshop_id>:<путь внутри мода или имя файла>`.

`/Game/Mods/<ИмяМода>/…` маппится в `Content/Mods/<workshop_id>/…` по
внутреннему имени из `mod.info`; остальное — в ванильный `Content/`.
Ванильный Content (~105 тыс. файлов) целиком не разбирается: нужен только
список путей, он кешируется с mtime каталогов.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import struct
from dataclasses import dataclass
from typing import Optional

from . import cache, config


class AssetError(Exception):
    """Ошибка адресации; текст — на русском, уходит агенту как есть."""


@dataclass
class Mod:
    workshop_id: str
    dir: str
    internal: Optional[str]      # имя в /Game/Mods/<internal>
    name: Optional[str]          # человекочитаемое имя из .mod


def _fstrings(data: bytes, pos: int, count: int) -> list[str]:
    out = []
    for _ in range(count):
        if pos + 4 > len(data):
            break
        n = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if not 0 < n < 1024 or pos + n > len(data):
            break
        out.append(data[pos:pos + n].rstrip(b"\x00").decode("latin-1"))
        pos += n
    return out


def read_mod_meta(mods_dir: str, workshop_id: str) -> tuple[Optional[str], Optional[str]]:
    """(внутреннее имя, отображаемое имя). Источники: mod.info и .mod."""
    internal = display = None
    info = os.path.join(mods_dir, workshop_id, "mod.info")
    if os.path.isfile(info):
        with open(info, "rb") as fh:
            got = _fstrings(fh.read(4096), 0, 1)
        internal = got[0] if got else None
    mod = os.path.join(mods_dir, f"{workshop_id}.mod")
    if os.path.isfile(mod):
        with open(mod, "rb") as fh:
            data = fh.read()
        # u32 id, u32 0, FString имя, FString путь, … (карты, метаданные)
        got = _fstrings(data, 8, 1)
        display = got[0] if got else None
        if internal is None:
            m = re.search(rb"/Game/Mods/([^/\x00]+)/", data)
            internal = m.group(1).decode("latin-1") if m else None
    return internal, display


def list_mods(mods_dir: Optional[str] = None) -> list[Mod]:
    mods_dir = mods_dir or config.mods_dir()
    out = []
    if not os.path.isdir(mods_dir):
        return out
    for name in sorted(os.listdir(mods_dir)):
        full = os.path.join(mods_dir, name)
        if name.isdigit() and os.path.isdir(full):
            internal, display = read_mod_meta(mods_dir, name)
            out.append(Mod(name, full, internal, display or internal))
    return out


def get_mod(workshop_id: str) -> Mod:
    for m in list_mods():
        if m.workshop_id == str(workshop_id):
            return m
    raise AssetError(f"Мод {workshop_id} не найден в {config.mods_dir()}. "
                     f"Список установленных — mods_list.")


def _walk_uassets(root: str) -> list[str]:
    out = []
    for base, _, files in os.walk(root):
        for f in files:
            if f.endswith(".uasset"):
                out.append(os.path.relpath(os.path.join(base, f), root))
    return sorted(out)


# --- ванильный Content: список путей с кешем -------------------------------

def _dir_stamp(root: str) -> dict:
    stamp = {"": int(os.path.getmtime(root))}
    for n in sorted(os.listdir(root)):
        full = os.path.join(root, n)
        if os.path.isdir(full):
            stamp[n] = int(os.path.getmtime(full))
    return stamp


def vanilla_files() -> list[str]:
    """Относительные пути всех .uasset ванильного Content, без каталога Mods."""
    root = config.vanilla_content_dir()
    if not os.path.isdir(root):
        raise AssetError(f"Нет каталога игры {root}. Задай ARK_GAME_DIR в окружении MCP-сервера.")
    p = os.path.join(cache.root(), "vanilla_files.json")
    stamp = _dir_stamp(root)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("root") == root and cached.get("stamp") == stamp:
            return cached["files"]
    except (OSError, ValueError):
        pass
    files = []
    for base, dirs, fs in os.walk(root):
        if base == root:
            dirs[:] = [d for d in dirs if d != "Mods"]
        for f in fs:
            if f.endswith(".uasset"):
                files.append(os.path.relpath(os.path.join(base, f), root))
    files.sort()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"root": root, "stamp": stamp, "files": files}, fh)
    os.replace(tmp, p)
    return files


def mod_files(mod: Mod) -> list[str]:
    return _walk_uassets(mod.dir)


# --- разрешение адреса -----------------------------------------------------

@dataclass
class Resolved:
    file: str
    workshop_id: Optional[str]    # None — ваниль или вне модов
    rel: str                      # путь внутри мода / внутри Content
    game_path: str


def game_path(mod: Optional[Mod], rel: str) -> str:
    stem = rel[:-len(".uasset")] if rel.endswith(".uasset") else rel
    stem = stem.replace(os.sep, "/")
    if mod is None:
        return "/Game/" + stem
    return f"/Game/Mods/{mod.internal or mod.workshop_id}/{stem}"


def _candidates_msg(cands: list[Resolved]) -> str:
    return "\n".join(f"  {c.workshop_id or 'vanilla'}:{c.rel}  ({c.game_path})" for c in cands[:20])


def _by_name(mod: Mod, name: str) -> list[Resolved]:
    base = name[:-len(".uasset")] if name.endswith(".uasset") else name
    if "/" in base:
        rel = base + ".uasset"
        if os.path.isfile(os.path.join(mod.dir, rel)):
            return [Resolved(os.path.join(mod.dir, rel), mod.workshop_id, rel, game_path(mod, rel))]
        return []
    low = base.lower()
    return [Resolved(os.path.join(mod.dir, rel), mod.workshop_id, rel, game_path(mod, rel))
            for rel in mod_files(mod)
            if os.path.basename(rel)[:-len(".uasset")].lower() == low]


def resolve(asset: str) -> Resolved:
    asset = asset.strip()
    if not asset:
        raise AssetError("Пустой адрес ассета.")
    # 1. абсолютный путь
    if os.path.isabs(asset) and not asset.startswith("/Game/"):
        p = asset if asset.endswith(".uasset") else asset + ".uasset"
        if not os.path.isfile(p):
            raise AssetError(f"Файл не найден: {p}")
        return _classify_file(p)
    # 3. <workshop_id>:<…>
    m = re.match(r"^(\d+):(.+)$", asset)
    if m:
        mod = get_mod(m.group(1))
        hits = _by_name(mod, m.group(2))
        if not hits:
            raise _not_found(asset)
        if len(hits) > 1:
            raise AssetError(f"Имя «{m.group(2)}» неоднозначно в моде {mod.workshop_id}, "
                             f"уточни путём внутри мода:\n{_candidates_msg(hits)}")
        return hits[0]
    # 2. /Game/...
    if asset.startswith("/Game/"):
        path = asset[len("/Game/"):].split(".")[0]
        parts = path.split("/")
        if parts[0] == "Mods" and len(parts) >= 3:
            internal, rest = parts[1], "/".join(parts[2:])
            owners = [m for m in list_mods() if m.internal == internal]
            hits = []
            for mod in owners:
                rel = rest + ".uasset"
                if os.path.isfile(os.path.join(mod.dir, rel)):
                    hits.append(Resolved(os.path.join(mod.dir, rel), mod.workshop_id, rel,
                                         game_path(mod, rel)))
            if not hits:
                raise _not_found(asset)
            if len(hits) > 1:
                raise AssetError(f"Путь {asset} есть в нескольких модах с одним внутренним "
                                 f"именем «{internal}»; укажи `<workshop_id>:{rest}`:\n"
                                 f"{_candidates_msg(hits)}")
            return hits[0]
        p = os.path.join(config.vanilla_content_dir(), path + ".uasset")
        if os.path.isfile(p):
            return Resolved(p, None, path + ".uasset", "/Game/" + path)
        raise _not_found(asset)
    raise AssetError(f"Не понимаю адрес «{asset}». Допустимо: абсолютный путь к .uasset, "
                     f"`/Game/...` или `<workshop_id>:<путь или имя>`.")


def _classify_file(p: str) -> Resolved:
    p = os.path.abspath(p)
    mods_root = os.path.abspath(config.mods_dir()) + os.sep
    if p.startswith(mods_root):
        wid, _, rel = p[len(mods_root):].partition(os.sep)
        if wid.isdigit():
            mod = Mod(wid, os.path.join(mods_root, wid), *(read_mod_meta(config.mods_dir(), wid)[:1]), None)
            return Resolved(p, wid, rel, game_path(mod, rel))
    content = os.path.abspath(config.vanilla_content_dir()) + os.sep
    if p.startswith(content):
        rel = p[len(content):]
        return Resolved(p, None, rel, game_path(None, rel))
    return Resolved(p, None, os.path.basename(p), os.path.basename(p))


def _not_found(asset: str) -> AssetError:
    base = re.split(r"[/:]", asset)[-1].split(".")[0]
    try:
        hits = find(f"*{base}*", scope="all", limit=10)
    except AssetError:
        hits = []
    msg = f"Ассет не найден: {asset}."
    if hits:
        msg += " Похожие (asset_find):\n" + _candidates_msg(hits)
    return AssetError(msg)


# --- поиск по имени --------------------------------------------------------

def find(name: str, scope: str = "all", limit: int = 50) -> list[Resolved]:
    """glob по имени файла (без учёта регистра); `scope`: mods | vanilla | all."""
    if scope not in ("mods", "vanilla", "all"):
        raise AssetError("scope: mods, vanilla или all.")
    pat = name.lower()
    pat = pat[:-len(".uasset")] if pat.endswith(".uasset") else pat
    if not any(c in pat for c in "*?["):
        pat = f"*{pat}*"
    out: list[Resolved] = []

    def match(rel: str) -> bool:
        return fnmatch.fnmatchcase(os.path.basename(rel)[:-len(".uasset")].lower(), pat)

    if scope in ("mods", "all"):
        for mod in list_mods():
            for rel in mod_files(mod):
                if match(rel):
                    out.append(Resolved(os.path.join(mod.dir, rel), mod.workshop_id, rel,
                                        game_path(mod, rel)))
                    if len(out) >= limit:
                        return out
    if scope in ("vanilla", "all"):
        root = config.vanilla_content_dir()
        for rel in vanilla_files():
            if match(rel):
                out.append(Resolved(os.path.join(root, rel), None, rel, game_path(None, rel)))
                if len(out) >= limit:
                    return out
    return out
