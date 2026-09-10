# -*- coding: utf-8 -*-
"""История диктовок: последние тексты в ~/.f5voice/history.json (в файле старые → новые).
Пишут worker.py (macOS) и dictate.py (Windows/Linux), читают окна настроек."""
import json
import os
import time
from pathlib import Path

LIMIT = 30


def _read(path):
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return items if isinstance(items, list) else []


def load(path):
    """Записи, новые первыми: {"time", "text", "lang", "seconds"}."""
    return [i for i in reversed(_read(path)) if isinstance(i, dict) and i.get("text")]


def add(path, text, lang="", seconds=0.0, limit=LIMIT):
    text = (text or "").strip()
    if not text:
        return
    items = [i for i in _read(path) if isinstance(i, dict) and i.get("text")]
    items.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text, "lang": lang,
                  "seconds": round(float(seconds or 0), 2)})
    items = items[-max(int(limit), 1):]
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:  # через временный файл и replace: обрыв на записи не оставит битый JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
