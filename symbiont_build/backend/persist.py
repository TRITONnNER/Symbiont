#!/usr/bin/env python3
"""
persist.py — состояние сервиса в РЕАЛЬНОЙ базе данных (SQLite, WAL).

Интерфейс тот же (jload/jsave/exists), поэтому вся логика поверх НЕ меняется, но
теперь всё состояние (аккаунты, сессии, ключи, узлы, платежи, health, …) живёт в
одном файле `symbiont_state.db`: каждый прежний «json-файл» — строка key→JSON.
Плюсы против голых JSON-файлов:
  • атомарные транзакции и WAL (конкурентное чтение) — реальная БД, не набор файлов;
  • один файл легко бэкапить/переносить/читать SQL-инструментами;
  • нет полу-записанных файлов при падении.

Бесшовный переход: если ключа ещё нет в БД, но рядом лежит СТАРЫЙ json-файл <key>,
он один раз импортируется в БД (существующие развёртывания не теряют данные).

Путь к БД — из окружения SYMBIONT_DB (по умолчанию symbiont_state.db) в текущем
рабочем каталоге, как раньше лежали json-файлы (тесты через os.chdir изолируются).
"""
from __future__ import annotations
import json, logging, os, sqlite3, threading

_DB_NAME = os.environ.get("SYMBIONT_DB", "symbiont_state.db")
_lock = threading.Lock()
_conns: dict[str, sqlite3.Connection] = {}   # cwd-путь БД → соединение
_log = logging.getLogger("symbiont.backend")


def _conn() -> sqlite3.Connection:
    path = os.path.join(os.getcwd(), _DB_NAME)
    c = _conns.get(path)
    if c is None:
        c = sqlite3.connect(path, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")   # ждём до 5с при блокировке (напр. параллельный бэкап-процесс), а не мгновенный "database is locked"
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        c.commit()
        _conns[path] = c
    return c


def _legacy_import(c: sqlite3.Connection, key: str) -> bool:
    """Разовый импорт старого json-файла <key> в БД. Повреждённый файл не теряем —
    откладываем в <key>.corrupt. Возвращает True, если импорт удался."""
    if not os.path.exists(key):
        return False
    try:
        with open(key, encoding="utf-8") as f:
            data = f.read()
        json.loads(data)                      # валидируем перед импортом
    except (OSError, json.JSONDecodeError) as e:
        try:
            os.replace(key, key + ".corrupt")
            _log.error("ПОВРЕЖДЁН legacy-стор %s (%s) → отложен в %s.corrupt", key, e, key)
        except OSError:
            _log.error("ПОВРЕЖДЁН legacy-стор %s (%s)", key, e)
        return False
    with _lock:
        c.execute("INSERT OR IGNORE INTO kv(k, v) VALUES(?, ?)", (key, data))
        c.commit()
    return True


def jload(path: str, default):
    c = _conn()
    with _lock:
        row = c.execute("SELECT v FROM kv WHERE k=?", (path,)).fetchone()
    if row is None and _legacy_import(c, path):
        with _lock:
            row = c.execute("SELECT v FROM kv WHERE k=?", (path,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError as e:
        _log.error("ПОВРЕЖДЁНА запись стора %s (%s) — возвращаю default", path, e)
        return default


def jsave(path: str, obj) -> None:
    c = _conn()
    data = json.dumps(obj, ensure_ascii=False)
    with _lock:
        c.execute("INSERT INTO kv(k, v) VALUES(?, ?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (path, data))
        c.commit()   # коммит = durability (WAL)


def exists(path: str) -> bool:
    """Есть ли такой ключ в БД (с учётом бесшовного импорта legacy-файла).
    Заменяет прежние os.path.exists('<key>.json') на уровне состояния."""
    c = _conn()
    with _lock:
        row = c.execute("SELECT 1 FROM kv WHERE k=?", (path,)).fetchone()
    if row:
        return True
    return _legacy_import(c, path)


def db_path() -> str:
    """Абсолютный путь к файлу БД (для бэкапа/переноса/SQL-инструментов)."""
    return os.path.join(os.getcwd(), _DB_NAME)


def stats() -> dict:
    """Снимок состояния БД для панели оператора: путь, размер, режим журнала,
    число ключей и размер каждого ключа в байтах. Доказывает, что это РЕАЛЬНАЯ
    база (WAL), а не набор json-файлов."""
    c = _conn()
    with _lock:
        rows = c.execute("SELECT k, length(v) FROM kv ORDER BY k").fetchall()
        jm = c.execute("PRAGMA journal_mode").fetchone()[0]
    p = db_path()
    size = os.path.getsize(p) if os.path.exists(p) else 0
    wal = p + "-wal"
    return {"path": p, "size_bytes": size,
            "wal_bytes": os.path.getsize(wal) if os.path.exists(wal) else 0,
            "journal_mode": jm, "keys": len(rows),
            "bytes_by_key": {k: n for k, n in rows}}


def backup(dest_path: str) -> str:
    """Консистентный ОНЛАЙН-бэкап БД в dest_path (sqlite3 backup API). Безопасно на
    живой WAL-базе: не блокирует запись и не даёт полу-записанного файла — снимок
    транзакционно целостный. Возвращает путь готового файла."""
    src = _conn()
    dst = sqlite3.connect(dest_path)
    try:
        with _lock:
            src.backup(dst)
    finally:
        dst.close()
    return dest_path
