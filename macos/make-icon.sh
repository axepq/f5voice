#!/bin/zsh
# Генерирует иконки из macos/icon.swift: F5Voice.icns (macOS), python/F5Voice.png и .ico (Windows/Linux).
# Нужны системные swiftc, sips, iconutil и python3 с Pillow (для .ico).
set -euo pipefail
DIR="${0:A:h}"
TMP="$(mktemp -d)"
swiftc -O "$DIR/icon.swift" -o "$TMP/mkicon" 2>&1 | grep -v warning || true
"$TMP/mkicon" "$TMP/icon-1024.png" >/dev/null
mkdir -p "$TMP/F5Voice.iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon-1024.png" --out "$TMP/F5Voice.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$TMP/icon-1024.png" --out "$TMP/F5Voice.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/F5Voice.iconset" -o "$DIR/F5Voice.icns"
sips -z 256 256 "$TMP/icon-1024.png" --out "$DIR/../python/F5Voice.png" >/dev/null
PY=python3
for candidate in "${F5VOICE_HOME:-$HOME/.f5voice}/venv/bin/python" python3; do
    if "$candidate" -c "import PIL" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
"$PY" - "$TMP/icon-1024.png" "$DIR/../python/F5Voice.ico" <<'PYEOF'
import sys
from PIL import Image
img = Image.open(sys.argv[1]).convert("RGBA")
img.save(sys.argv[2], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
PYEOF
rm -rf "$TMP"
echo "иконки обновлены: $DIR/F5Voice.icns, python/F5Voice.png, python/F5Voice.ico"
