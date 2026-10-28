"""Формат ответов инструментов: компактный JSON с лимитом ~20 тыс. символов."""
from __future__ import annotations

import json

LIMIT = 20000


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _longest_list(obj: dict):
    best = None
    for k, v in obj.items():
        if isinstance(v, list) and v and (best is None or len(_dump(v)) > len(_dump(obj[best]))):
            best = k
    return best


def fit(obj, limit: int = LIMIT) -> str:
    """Сериализует; если не влезает — укорачивает самый длинный список и ставит
    `truncated` с подсказкой. Для не-dict обрезает текст."""
    text = _dump(obj)
    if len(text) <= limit:
        return text
    if not isinstance(obj, dict):
        return text[:limit] + "…[обрезано]"
    obj = dict(obj)
    obj["truncated"] = True
    total_hint = ""
    while len(_dump(obj)) > limit:
        key = _longest_list(obj)
        if key is None:
            return _dump(obj)[:limit] + "…[обрезано]"
        lst = obj[key]
        keep = len(lst) // 2
        obj[key] = lst[:keep]
        total_hint = (f"список `{key}` показан не целиком ({keep} из {len(lst)}): "
                      f"сузь запрос или листай через offset/limit")
        obj["hint"] = total_hint
    return _dump(obj)


def function_reply(result: dict, limit: int = LIMIT) -> str:
    """asset_function: два блока — JSON-мета и псевдокод; код режется по символам."""
    meta, code = result["meta"], result["code"]
    head = _dump(meta)
    room = max(limit - len(head) - 40, 2000)
    if len(code) > room:
        cut = code[:room].rsplit("\n", 1)[0]
        shown = cut.count("\n") + 1
        meta = dict(meta, truncated=True, next_from_line=meta["from_line"] + shown,
                    hint=f"код обрезан по размеру ответа; продолжение — from_line={meta['from_line'] + shown}")
        head, code = _dump(meta), cut
    return head + "\n\n" + code
