"""
log_setup.py — единая настройка логирования бэкенда «Симбионта».

Цель: чтобы по логам было ПОНЯТНО, что случилось и как чинить. Пишем сразу в два
места: stdout (его собирает systemd/journalctl) и файл с ротацией (удобно приложить
в поддержку или разобрать инцидент постфактум).

Управление через окружение:
  SYMBIONT_LOG_LEVEL  — DEBUG | INFO | WARNING | ERROR   (по умолчанию INFO)
  SYMBIONT_LOG_FILE   — путь к файлу лога (по умолчанию symbiont-backend.log)

Разбор лога:
  журнал systemd:  journalctl -u symbiont-backend -f
  файл:            tail -f symbiont-backend.log
Каждая строка: время · уровень · логгер · сообщение. Уровень WARNING+ — то, на что
стоит смотреть в первую очередь (4xx/5xx, отказ подписи, отвал узла и т.п.).
"""
from __future__ import annotations
import logging
import os
from logging.handlers import RotatingFileHandler

_FMT = logging.Formatter(
    "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup(name: str = "symbiont") -> logging.Logger:
    """Идемпотентно настроить и вернуть логгер (stdout + ротируемый файл)."""
    logger = logging.getLogger(name)
    if logger.handlers:                       # уже настроен (повторный import)
        return logger
    level = os.environ.get("SYMBIONT_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))

    sh = logging.StreamHandler()
    sh.setFormatter(_FMT)
    logger.addHandler(sh)

    path = os.environ.get("SYMBIONT_LOG_FILE", "symbiont-backend.log")
    try:
        fh = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(_FMT)
        logger.addHandler(fh)
    except Exception as e:                     # нет прав на файл — не падаем, только stdout
        logger.warning("файл лога недоступен (%s): %s — пишу только в stdout", path, e)

    logger.propagate = False
    return logger
