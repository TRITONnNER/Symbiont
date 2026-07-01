#!/usr/bin/env python3
"""
persist.py — крошечный атомарный JSON-стор для бэкенда. Состояние переживает
рестарт (аккаунты, диалоги, реестр ключей, узлы). Запись атомарна (temp+rename),
чтобы падение посреди записи не било файл. Для MVP-нагрузки этого достаточно;
на масштаб — заменить на SQLite, интерфейс тот же (jload/jsave).
"""
from __future__ import annotations
import json, os, tempfile, threading

_lock = threading.Lock()


def jload(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def jsave(path: str, obj) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # атомарная замена
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
