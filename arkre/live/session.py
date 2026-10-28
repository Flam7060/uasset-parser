"""Сессия живого чтения: процесс, проверка сборки, рефлексия — создаются лениво."""
from __future__ import annotations

import os
import re
from typing import Optional

from .. import config, pdb
from . import LiveError
from .proc import Process, find_pids, read_build
from .reflect import Reflection

_state: dict = {}


def _pdb_summary(path: str) -> dict:
    """GUID и Age клиентского PDB (llvm-pdbutil dump -summary, доли секунды)."""
    meta = {}
    for line in pdb._stream(["dump", "-summary", path]):
        s = line.strip()
        if s.startswith("GUID:"):
            meta["guid"] = s.split(":", 1)[1].strip().upper()
        elif s.startswith("Age:"):
            meta["age"] = int(s.split(":", 1)[1])
    return meta


def verify_build(p: Process) -> dict:
    """Правило 4: образ в памяти должен соответствовать ShooterGame.pdb, иначе отказ."""
    build = read_build(p)
    try:
        src = pdb.pdb_path("client")
        want = _pdb_summary(src)
    except pdb.PdbError as ex:
        raise LiveError(f"Не могу проверить сборку игры: {ex}") from None
    if not build["guid"] or build["guid"] != want.get("guid") or build["age"] != want.get("age"):
        raise LiveError(
            f"Сборка игры не совпадает с ShooterGame.pdb: в памяти GUID {build['guid']} age {build['age']}, "
            f"в PDB GUID {want.get('guid')} age {want.get('age')}. Смещения из PDB были бы чужими — "
            f"чтение отключено. Обнови клиентский PDB (ARK_PDB_CLIENT) под запущенную игру.")
    build["pdb"] = src
    return build


def _object_array_rva() -> int:
    """RVA GUObjectArray — только из клиентского PDB (после проверки сборки)."""
    pdb.import_pdb("client")
    r = pdb.pdb_symbols("?GUObjectArray@@*", kind="data", pdb="client", limit=5)
    for it in r["items"]:
        if it["decorated"].startswith("?GUObjectArray@@"):
            return int(it["rva"], 16)
    raise LiveError("В клиентском PDB нет символа GUObjectArray.")


def session() -> tuple[Process, Reflection, dict]:
    """(процесс, рефлексия, сведения о сборке); пересоздаётся, если игра перезапущена."""
    pids = find_pids()
    if len(pids) == 1 and _state.get("pid") == pids[0]:
        return _state["proc"], _state["reflect"], _state["build"]
    if _state.get("proc"):
        try:
            _state["proc"].close()
        except OSError:
            pass
        _state.clear()
    p = Process()
    try:
        build = verify_build(p)
        rva = _object_array_rva()
        refl = Reflection(p, rva)
    except BaseException:
        p.close()
        raise
    build["object_array_rva"] = hex(rva)
    _state.update(pid=p.pid, proc=p, reflect=refl, build=build)
    return p, refl, build
