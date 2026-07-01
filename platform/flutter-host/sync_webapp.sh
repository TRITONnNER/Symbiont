#!/usr/bin/env bash
# Копирует оболочку из webapp/ в assets/webapp/ этого пакета, чтобы вшить её в
# бинарник приложения (Flutter грузит только из своих assets).
#   bash sync_webapp.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WEBAPP="$(cd "$HERE/../../webapp" && pwd)"
DEST="$HERE/assets/webapp"

rm -rf "$DEST"
mkdir -p "$DEST"
# Копируем всё, кроме служебных файлов сервера/докусов.
cp -r "$WEBAPP"/. "$DEST"/
rm -f "$DEST/serve.py" "$DEST/vendor_assets.py" "$DEST/README.md"

echo "OK: оболочка вшита в assets/webapp ($(find "$DEST" -type f | wc -l) файлов)"
echo "Не забудьте перечислить подкаталоги в pubspec.yaml (см. pubspec.snippet.yaml)."
