#!/usr/bin/env python3
"""Локальный запуск бэкенда для разработки/демо.

    python run_local.py            # → http://127.0.0.1:8600
    python run_local.py 9000       # свой порт

Веб-оболочка (webapp/) в «живом» режиме ходит сюда: задайте в ней
    window.SYM_CONFIG = { apiBase: 'http://127.0.0.1:8600', live: true }
(или отдавайте статику тем же origin — тогда apiBase:'' ). CORS уже настроен.
"""
import sys
import uvicorn

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8600
    uvicorn.run("server:app", host="127.0.0.1", port=port, log_level="info")
