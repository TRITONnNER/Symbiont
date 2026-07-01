#!/usr/bin/env python3
"""
persist.py — крошечный атомарный JSON-стор для бэкенда. Состояние переживает
рестарт (аккаунты, диалоги, реестр ключей, узлы). Запись атомарна (temp+rename),
чтобы падение посреди записи не било файл. Для MVP-нагрузки этого достаточно;
на масштаб — заменить на SQLite, интерфейс тот же (jload/jsave).
"""
from __future__ import annotations
import json, logging, os, tempfile, threading

_lock = threading.Lock()
_log = logging.getLogger("symbiont.backend")


def jload(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        # НЕ теряем данные молча: битый файл откладываем в .corrupt и ГРОМКО в лог,
        # чтобы не «стартануть с пустого» и не затереть его при следующей записи.
        try:
            bad = path + ".corrupt"
            os.replace(path, bad)
            _log.error("ПОВРЕЖДЁН стор %s (%s) → сохранил как %s; стартую пустым. "
                       "Восстанови данные из %s вручную.", path, e, bad, bad)
        except OSError:
            _log.error("ПОВРЕЖДЁН стор %s (%s); не смог отложить копию", path, e)
        return default


def jsave(path: str, obj) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())   # данные на диск ДО rename (переживёт потерю питания)
            os.replace(tmp, path)      # атомарная замена
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
