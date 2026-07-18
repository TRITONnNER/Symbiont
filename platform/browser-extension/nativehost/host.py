#!/usr/bin/env python3
"""Native-messaging хост Симбионта.

Расширение браузера общается с ним по протоколу Chrome native messaging
(4 байта длины + JSON в stdin/stdout). Хост пересылает команды локальному
управляющему эндпоинту установленного приложения Симбионт, которое и держит
VPN-движок (winws / sing-box / byedpi).

Команды от расширения:
    {"cmd":"connect","node":"ams-03.symbiont.net"}
    {"cmd":"disconnect"}
    {"cmd":"status"}
Ответ хоста (и любые асинхронные события) — событие движка:
    {"conn":"connected","stage":"tunnel","ping":42}

Локальный контрол приложения по умолчанию: http://127.0.0.1:8611/control
(приложение поднимает его на loopback; наружу не выставляется).
"""
import sys
import json
import struct
import os
import urllib.request

APP_CONTROL = os.environ.get("SYMBIONT_CONTROL", "http://127.0.0.1:8611/control")


def _read():
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("=I", raw_len)
    # Спецификация native messaging: сообщение ≤1 МБ. Больший/нулевой префикс —
    # рассинхрон кадров или мусор: рвём цикл чисто, а не блокируемся на чтении гиганта.
    if length <= 0 or length > 1024 * 1024:
        return None
    data = sys.stdin.buffer.read(length)
    if len(data) < length:            # пайп закрылся раньше — не парсим огрызок
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}


def _write(obj):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _forward(cmd):
    """Переслать команду локальному контролу приложения, вернуть состояние движка."""
    try:
        req = urllib.request.Request(
            APP_CONTROL,
            data=json.dumps(cmd).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": "app_unreachable", "detail": str(e), "conn": "error"}


def main():
    while True:
        msg = _read()
        if msg is None:
            break
        cmd = msg.get("cmd")
        # Эхо-им _id запроса в ответ, чтобы попап сопоставил ответ со своей командой
        # (на одном порту живут и ответы, и незапрошенные engine-события).
        rid = msg.get("_id")
        if cmd not in ("connect", "disconnect", "status"):
            resp = {"ok": False, "error": "unknown_cmd"}
        else:
            resp = _forward(msg)
        if rid is not None and isinstance(resp, dict):
            resp["_id"] = rid
        _write(resp)


if __name__ == "__main__":
    main()
