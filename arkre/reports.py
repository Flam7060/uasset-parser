"""
Frontmatter отчётов docs/mods/*.md: чтение и точечная запись.

Свой разбор подмножества YAML, без pyyaml: ядро работает системным python3.
Поддержано: `ключ: скаляр`, inline-списки `[a, b]`, вложенные map по отступам.
Скаляры остаются строками, кроме элементов inline-списков из одних цифр
(это workshop_ids) — хеши из цифр иначе стали бы числами.

Запись не пересобирает файл: меняются только блоки заданных ключей, остальной
frontmatter и тело файла сохраняются побайтно.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import config

STATUS_REVIEWED = ("draft", "done")        # считаются разбором
STATUSES = ("template", "card", "draft", "done")


# --- разбор ----------------------------------------------------------------

def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _split_key(line: str) -> tuple[str, str]:
    """`ключ: значение` / `"ключ: с двоеточием": значение` → (ключ, остаток)."""
    s = line.strip()
    if s[:1] in "\"'":
        q = s[0]
        i = 1
        while i < len(s):
            if s[i] == "\\" and q == '"':
                i += 2
                continue
            if s[i] == q:
                break
            i += 1
        key = _unquote(s[:i + 1])
        rest = s[i + 1:]
        if not rest.startswith(":"):
            raise ValueError(f"нет двоеточия после ключа: {line!r}")
        return key, rest[1:].strip()
    m = re.match(r"^(.*?):(?:\s+(.*))?$", s)
    if not m:
        raise ValueError(f"не разобрать строку frontmatter: {line!r}")
    return m.group(1).strip(), (m.group(2) or "").strip()


def _scalar(v: str):
    if v.startswith("[") and v.endswith("]"):
        items = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        return [int(x) if x.isdigit() else _unquote(x) for x in items]
    return _unquote(v)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], pos: int, indent: int):
    out: dict = {}
    while pos < len(lines):
        line = lines[pos]
        if not line.strip() or line.lstrip().startswith("#"):
            pos += 1
            continue
        ind = _indent(line)
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"неожиданный отступ: {line!r}")
        key, rest = _split_key(line)
        pos += 1
        if rest:
            out[key] = _scalar(rest)
        else:
            nxt = pos
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines) and _indent(lines[nxt]) > indent:
                out[key], pos = _parse_block(lines, nxt, _indent(lines[nxt]))
            else:
                out[key] = {}
    return out, pos


def split(text: str) -> tuple[Optional[str], str]:
    """(frontmatter без разделителей | None, тело вместе с переводом строки после `---`)."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end < 0:
        if text.endswith("\n---"):
            return text[4:-4], ""
        return None, text
    return text[4:end], text[end + 5:]


def parse(text: str) -> dict:
    fm, _ = split(text)
    if fm is None:
        return {}
    return _parse_block(fm.split("\n"), 0, 0)[0]


# --- запись ----------------------------------------------------------------

def _needs_quote(s: str) -> bool:
    return (not s or s != s.strip() or s[0] in "\"'[{!&*#|>%@`-?:,"
            or ": " in s or " #" in s or s.endswith(":"))


def _fmt_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt_scalar(x) for x in v) + "]"
    s = str(v)
    return json.dumps(s, ensure_ascii=False) if _needs_quote(s) else s


def _fmt_key(k) -> str:
    return _fmt_scalar(str(k))


def dump_block(key: str, value, indent: int = 0) -> list[str]:
    pad = " " * indent
    if isinstance(value, dict):
        lines = [f"{pad}{_fmt_key(key)}:"]
        for k, v in value.items():
            lines += dump_block(k, v, indent + 2)
        return lines
    return [f"{pad}{_fmt_key(key)}: {_fmt_scalar(value)}"]


def set_keys(text: str, updates: dict) -> str:
    """Заменяет блоки ключей верхнего уровня; новые ключи — в конец frontmatter."""
    fm, body = split(text)
    if fm is None:
        raise ValueError("в файле нет frontmatter")
    lines = fm.split("\n")
    # границы блоков верхнего уровня
    blocks: list[tuple[str, int, int]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() and _indent(lines[i]) == 0 and not lines[i].startswith("#"):
            key, _ = _split_key(lines[i])
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > 0):
                j += 1
            # хвостовые пустые строки блоку не принадлежат
            k = j
            while k > i + 1 and not lines[k - 1].strip():
                k -= 1
            blocks.append((key, i, k))
            i = j
        else:
            i += 1
    pending = dict(updates)
    out: list[str] = []
    cursor = 0
    for key, a, b in blocks:
        if key in pending:
            out += lines[cursor:a]
            out += dump_block(key, pending.pop(key))
            cursor = b
    out += lines[cursor:]
    while out and not out[-1].strip():
        out.pop()
    for key, value in pending.items():
        out += dump_block(key, value)
    return "---\n" + "\n".join(out) + "\n---\n" + body


# --- документы -------------------------------------------------------------

@dataclass
class Report:
    path: str                       # абсолютный путь
    name: str                       # имя файла
    meta: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "template"))

    @property
    def workshop_ids(self) -> list[str]:
        return [str(x) for x in (self.meta.get("workshop_ids") or [])]

    @property
    def title(self) -> str:
        return str(self.meta.get("title", self.name))

    def covered(self, workshop_id: str) -> dict:
        cov = self.meta.get("covered_assets") or {}
        v = cov.get(str(workshop_id)) or {}
        return v if isinstance(v, dict) else {}

    def mod_hash(self, workshop_id: str) -> Optional[str]:
        mh = self.meta.get("mod_hash") or {}
        v = mh.get(str(workshop_id)) if isinstance(mh, dict) else None
        return str(v) if v is not None else None


def load(path: str) -> Report:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return Report(path, os.path.basename(path), parse(text))


def list_reports(reports_dir: Optional[str] = None) -> list[Report]:
    d = reports_dir or config.reports_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".md") and name != "README.md":
            out.append(load(os.path.join(d, name)))
    return out


def find_report(ref: str) -> Report:
    """Отчёт по имени файла (с `.md` или без) либо по пути."""
    cand = ref if os.path.isabs(ref) else os.path.join(config.reports_dir(), ref)
    for p in (cand, cand + ".md"):
        if os.path.isfile(p):
            return load(p)
    raise FileNotFoundError(f"Нет отчёта {ref} в {config.reports_dir()}")


def write_baseline(report: Report, workshop_id: str, covered: dict, mod_hash: str, reviewed: str) -> None:
    """Пишет `reviewed`, `mod_hash`, `covered_assets` для одного мода.

    Данные других модов того же отчёта сохраняются. Тело и прочие ключи
    frontmatter не меняются.
    """
    with open(report.path, "r", encoding="utf-8") as fh:
        text = fh.read()
    meta = parse(text)
    mh = dict(meta.get("mod_hash") or {})
    cov = {k: dict(v) for k, v in (meta.get("covered_assets") or {}).items()}
    mh[str(workshop_id)] = mod_hash
    cov[str(workshop_id)] = dict(sorted(covered.items()))
    new = set_keys(text, {"reviewed": reviewed,
                          "mod_hash": dict(sorted(mh.items())),
                          "covered_assets": dict(sorted(cov.items()))})
    if new != text:
        tmp = report.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, report.path)
