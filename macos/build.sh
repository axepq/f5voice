#!/bin/zsh
# Собирает F5Voice.app в $F5VOICE_HOME (по умолчанию ~/.f5voice).
# Подпись ad-hoc с явным designated requirement по идентификатору бандла:
# так TCC узнаёт приложение после пересборки и не просит разрешения заново.
set -euo pipefail
DIR="${0:A:h}"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
APP="$HOME_DIR/F5Voice.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$DIR/Info.plist" "$APP/Contents/Info.plist"
cp "$DIR/F5Voice.icns" "$APP/Contents/Resources/F5Voice.icns"
swiftc -O -target arm64-apple-macosx14.0 -framework Cocoa -framework AVFoundation \
    "$DIR/main.swift" -o "$APP/Contents/MacOS/F5Voice"
codesign --force --sign - -r '=designated => identifier "com.alex.f5voice"' "$APP" 2>&1 \
    | grep -v 'replacing existing signature' || true
echo "собрано: $APP"
