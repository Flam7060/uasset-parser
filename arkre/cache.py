"""
Кеш разборов .uasset по sha256 содержимого и версии парсера.

Ключ — sha256 файла, а не версия мода: моддеры бампают workshop без смены
внутреннего номера и наоборот. Версия парсера (`parser_ver`) входит в путь:
после правки декодера старые разборы не используются.

    .cache/
      v<parser_ver>/by_hash/<sha256>.json   AssetView
      v<parser_ver>/by_hash/<sha256>.txt    полный текстовый дамп для CLI
      xref.sqlite  pdb/  vanilla_files.json
      index.json                            текущие хеши ассетов модов
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import time
from typing import Optional

from . import config

BY_HASH = "by_hash"
INDEX_NAME = "index.json"
_PARSER_SOURCES = ("uasset", "arkre/analysis.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=1)
def parser_ver() -> str:
    """Первые 12 hex sha256 от исходников uasset/*.py и arkre/analysis.py."""
    h = hashlib.sha256()
    files = []
    for entry in _PARSER_SOURCES:
        full = os.path.join(config.REPO, entry)
        if os.path.isdir(full):
            files += sorted(os.path.join(full, n) for n in os.listdir(full) if n.endswith(".py"))
        else:
            files.append(full)
    for f in files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def root() -> str:
    return config.cache_dir()


def version_dir(ver: Optional[str] = None) -> str:
    return os.path.join(root(), f"v{ver or parser_ver()}", BY_HASH)


def _path(sha: str, ext: str) -> str:
    return os.path.join(version_dir(), f"{sha}.{ext}")


def _atomic_write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def has_dump(sha: str) -> bool:
    return os.path.isfile(_path(sha, "txt"))


def read_dump(sha: str) -> Optional[str]:
    p = _path(sha, "txt")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write_dump(sha: str, text: str) -> str:
    return _atomic_write(_path(sha, "txt"), text)


def read_view(sha: str) -> Optional[dict]:
    p = _path(sha, "json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_view(sha: str, view: dict) -> str:
    return _atomic_write(_path(sha, "json"), json.dumps(view, ensure_ascii=False))


def gc(dry_run: bool = False) -> list[str]:
    """Удаляет каталоги кеша от других версий парсера. Возвращает удалённые."""
    removed = []
    if not os.path.isdir(root()):
        return removed
    current = f"v{parser_ver()}"
    for name in sorted(os.listdir(root())):
        full = os.path.join(root(), name)
        if os.path.isdir(full) and name.startswith("v") and name != current:
            removed.append(full)
            if not dry_run:
                shutil.rmtree(full)
    # кеш прежней раскладки (.cache/by_hash/) — тоже чужая версия
    legacy = os.path.join(root(), BY_HASH)
    if os.path.isdir(legacy):
        removed.append(legacy)
        if not dry_run:
            shutil.rmtree(legacy)
    return removed


# --- index.json: вычисляемое состояние сканера -----------------------------

def index_path() -> str:
    return os.path.join(root(), INDEX_NAME)


def load_index() -> dict:
    p = index_path()
    if not os.path.isfile(p):
        return {"version": 2, "updated_at": None, "mods": {}}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_index(idx: dict) -> None:
    idx["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _atomic_write(index_path(), json.dumps(idx, ensure_ascii=False, indent=2, sort_keys=True))
