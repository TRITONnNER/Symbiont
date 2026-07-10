#!/usr/bin/env python3
"""Вшивает дизайн-оболочку (webapp/) в assets/webapp/ Flutter-приложения.

Flutter умеет грузить только из своих assets, поэтому оболочку нужно скопировать
внутрь пакета. Запускать после правок в webapp/:

    python tools/sync_webapp.py

Кросс-платформенно (Windows/macOS/Linux). Служебные файлы разработки не копируем.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))            # symbiont_build/
WEBAPP = os.path.abspath(os.path.join(ROOT, "..", "webapp"))
DEST = os.path.join(ROOT, "assets", "webapp")

SKIP = {"serve.py", "vendor_assets.py", "README.md"}


def main():
    if not os.path.isdir(WEBAPP):
        print("не найден webapp/: %s" % WEBAPP, file=sys.stderr)
        sys.exit(1)
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)

    n = 0
    for base, _dirs, files in os.walk(WEBAPP):
        rel = os.path.relpath(base, WEBAPP)
        target_dir = DEST if rel == "." else os.path.join(DEST, rel)
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            if rel == "." and f in SKIP:
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(target_dir, f))
            n += 1
    print("вшито файлов: %d → %s" % (n, DEST))
    print("не забудьте: flutter pub get && flutter run")


if __name__ == "__main__":
    main()
