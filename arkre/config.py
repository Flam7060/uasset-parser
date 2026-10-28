"""Пути и флаги из окружения; у всех есть дефолты."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name: str, default: str) -> str:
    return os.path.expanduser(os.environ.get(name) or default)


def game_dir() -> str:
    return _env("ARK_GAME_DIR", "~/.local/share/Steam/steamapps/common/ARK")


def mods_dir() -> str:
    return _env("ARK_MODS_DIR", os.path.join(game_dir(), "ShooterGame", "Content", "Mods"))


def vanilla_content_dir() -> str:
    return os.path.join(game_dir(), "ShooterGame", "Content")


def pdb_server() -> str:
    return _env("ARK_PDB_SERVER",
                os.path.join(game_dir(), "ShooterGame", "Binaries", "Win64", "ShooterGameServer.pdb"))


def pdb_client() -> str:
    return _env("ARK_PDB_CLIENT",
                os.path.join(game_dir(), "ShooterGame", "Binaries", "Win64", "ShooterGame.pdb"))


def pdbutil() -> str:
    return _env("ARK_PDBUTIL", "llvm-pdbutil")


def docs_dir() -> str:
    return _env("ARK_RE_DOCS", os.path.join(REPO, "docs"))


def reports_dir() -> str:
    return os.path.join(docs_dir(), "mods")


def cache_dir() -> str:
    return _env("ARK_RE_CACHE", os.path.join(REPO, ".cache"))


def live_enabled() -> bool:
    return os.environ.get("ARK_LIVE") == "1"
