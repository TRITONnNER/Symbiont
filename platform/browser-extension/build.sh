#!/usr/bin/env bash
# Собирает расширение: вшивает мост и флаги из webapp/, генерит иконки.
# Запускать из каталога расширения:  bash build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WEBAPP="$(cd "$HERE/../../webapp" && pwd)"

mkdir -p "$HERE/vendor/flags"

# Мост (SYM_API/SYM_BRIDGE) — тот же, что в оболочке, чтобы не разъезжались.
cp "$WEBAPP/symbiont-bridge.js" "$HERE/vendor/symbiont-bridge.js"

# Флаги стран (для списка узлов в попапе) — оффлайн, без CDN.
cp "$WEBAPP"/flags/*.png "$HERE/vendor/flags/"

# Иконки расширения.
python3 "$HERE/make_icons.py"

echo "OK: vendor/symbiont-bridge.js, vendor/flags/ ($(ls "$HERE"/vendor/flags | wc -l) шт.), icons/"
echo "Загрузите каталог как распакованное расширение в chrome://extensions (Developer mode)."
