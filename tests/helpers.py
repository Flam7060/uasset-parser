"""Общие фикстуры: временный каталог игры с синтетическим модом из ассетов репо."""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FIXTURES = ["Actor_BP_AC", "Buff_AC", "DetectionPacket", "GunCheck",
            "PrimalDinoCharacter_BP_AC", "PrimalGameData_BP_AC", "ShooterWeapon_BP_AC"]


def fstring(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


class Sandbox:
    """Каталог игры + кеш + docs во временной папке; env указывает на них."""

    def __init__(self, workshop_id: str = "999001", internal: str = "FixMod"):
        self.root = tempfile.mkdtemp(prefix="arkre-test-")
        self.game = os.path.join(self.root, "ARK")
        self.mods = os.path.join(self.game, "ShooterGame", "Content", "Mods")
        self.cache = os.path.join(self.root, "cache")
        self.docs = os.path.join(self.root, "docs")
        self.reports = os.path.join(self.docs, "mods")
        os.makedirs(self.reports)
        os.makedirs(self.mods)
        self.workshop_id = workshop_id
        self.internal = internal
        self.add_mod(workshop_id, internal)
        self._saved = {k: os.environ.get(k) for k in
                       ("ARK_GAME_DIR", "ARK_MODS_DIR", "ARK_RE_CACHE", "ARK_RE_DOCS")}
        os.environ.update(ARK_GAME_DIR=self.game, ARK_MODS_DIR=self.mods,
                          ARK_RE_CACHE=self.cache, ARK_RE_DOCS=self.docs)

    def add_mod(self, workshop_id: str, internal: str, files=FIXTURES, subdir="Sub"):
        d = os.path.join(self.mods, workshop_id)
        os.makedirs(os.path.join(d, subdir), exist_ok=True)
        with open(os.path.join(d, "mod.info"), "wb") as fh:
            fh.write(fstring(internal) + struct.pack("<i", 0))
        for name in files:
            shutil.copy(os.path.join(REPO, name + ".uasset"), os.path.join(d, subdir, name + ".uasset"))
        return d

    def close(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)
