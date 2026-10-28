"""
Кеш разборов .uasset по sha256 содержимого.

Ключ — sha256 файла, а не версия мода: моддеры часто бампают workshop без
смены внутреннего номера, и наоборот. Хеш переживает любые такие игры.

Структура:
    .cache/
        by_hash/<sha256>.txt   — текстовый дамп parse_uasset (все секции)
        index.json             — карта workshop_id → ассеты, их хеши, отчёт

index.json используется сканером и ИИ: по нему видно какие моды разобраны,
какой отчёт покрывает какие хеши, не устарел ли отчёт после обновления мода.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

CACHE_DIRNAME = ".cache"
BY_HASH = "by_hash"
INDEX_NAME = "index.json"


def _parser_root() -> str:
    # uasset/cache.py → uasset-parser/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_root() -> str:
    return os.path.join(_parser_root(), CACHE_DIRNAME)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_path(sha: str) -> str:
    return os.path.join(cache_root(), BY_HASH, f"{sha}.txt")


def has_dump(sha: str) -> bool:
    return os.path.isfile(dump_path(sha))


def read_dump(sha: str) -> Optional[str]:
    p = dump_path(sha)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write_dump(sha: str, text: str) -> str:
    """Атомарно пишет дамп. Возвращает путь."""
    p = dump_path(sha)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, p)
    return p


# --- index.json ---------------------------------------------------------

def index_path() -> str:
    return os.path.join(cache_root(), INDEX_NAME)


def load_index() -> dict:
    p = index_path()
    if not os.path.isfile(p):
        return {"version": 1, "updated_at": None, "mods": {}}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_index(idx: dict) -> None:
    idx["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = index_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)
