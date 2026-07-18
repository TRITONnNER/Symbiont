#!/usr/bin/env python3
"""db_backup.py — консистентный снимок РЕАЛЬНОЙ БД Симбионта (SQLite online backup).

Безопасно на ЖИВОЙ базе (WAL): использует sqlite3 backup API — снимок транзакционно
целостный, запись не блокируется, полу-записанного файла не бывает.

Запуск (из каталога с symbiont_state.db, обычно /var/lib/symbiont):
    python db_backup.py                 # → ./backups/symbiont-YYYYMMDD-HHMMSS.db
    python db_backup.py /path/backups   # каталог назначения
Держит последние SYMBIONT_BACKUP_KEEP снимков (по умолчанию 14). Ставится в cron/таймер
(см. deploy-backend.sh) для ежедневного бэкапа.
"""
import os, sys, glob, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import persist

KEEP = int(os.environ.get("SYMBIONT_BACKUP_KEEP", "14"))


def main():
    dest_dir = sys.argv[1] if len(sys.argv) > 1 else "backups"
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dest = os.path.join(dest_dir, f"symbiont-{stamp}.db")
    persist.backup(dest)
    size = os.path.getsize(dest)

    snaps = sorted(glob.glob(os.path.join(dest_dir, "symbiont-*.db")))
    removed = 0
    for old in snaps[:-KEEP]:
        try:
            os.remove(old); removed += 1
        except OSError:
            pass
    src = persist.db_path()
    print(f"[backup] {src} → {os.path.abspath(dest)} ({size} байт); "
          f"храним {min(len(snaps), KEEP)}, удалено старых: {removed}")


if __name__ == "__main__":
    main()
