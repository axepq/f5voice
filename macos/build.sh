#!/bin/zsh
# Собирает F5Voice.app. Куда — F5VOICE_APP (установщик кладёт в /Applications),
# по умолчанию $F5VOICE_HOME/F5Voice.app (~/.f5voice). Сборка идёт во временный каталог
# и подменяет бандл целиком. Подпись ad-hoc с явным designated requirement по идентификатору
# бандла: так TCC узнаёт приложение после пересборки и переноса и не просит разрешения заново.
set -euo pipefail
DIR="${0:A:h}"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
APP="${F5VOICE_APP:-$HOME_DIR/F5Voice.app}"
STAGE="$HOME_DIR/build/F5Voice.app"
rm -rf "$STAGE"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"
cp "$DIR/Info.plist" "$STAGE/Contents/Info.plist"
cp "$DIR/F5Voice.icns" "$STAGE/Contents/Resources/F5Voice.icns"
swiftc -O -target arm64-apple-macosx14.0 -framework Cocoa -framework AVFoundation \
    "$DIR/main.swift" "$DIR/settings.swift" -o "$STAGE/Contents/MacOS/F5Voice"
codesign --force --sign - -r '=designated => identifier "com.alex.f5voice"' "$STAGE" 2>&1 \
    | { grep -v 'replacing existing signature' || true; }
(( pipestatus[1] == 0 )) || { echo "codesign не удался" >&2; exit 1; }
mkdir -p "${APP:h}"
rm -rf "$APP"
mv "$STAGE" "$APP"
echo "собрано: $APP"
