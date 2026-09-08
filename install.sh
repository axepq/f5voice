#!/bin/zsh
# Установка F5Voice на macOS одной командой:
#   gh repo clone axepq/f5voice ~/.f5voice/src && ~/.f5voice/src/install.sh
# или из уже склонированной папки: ./install.sh
# Ставит venv с mlx-whisper, скачивает модель, собирает приложение, включает автозапуск.
set -euo pipefail

REPO_URL="https://github.com/axepq/f5voice.git"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
SRC="$HOME_DIR/src"
LABEL="com.alex.f5voice"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

step() { print -P "%F{cyan}▸%f $1"; }
fail() { print -P "%F{red}✗%f $1" >&2; exit 1; }

[[ "$(uname -s)" == Darwin ]] || fail "Это установщик для macOS. Windows и Linux — см. other/README.md"
[[ "$(uname -m)" == arm64 ]] || fail "Нужен Mac на Apple Silicon: mlx-whisper не работает на Intel"
OSV="$(sw_vers -productVersion)"
[[ "${OSV%%.*}" -ge 14 ]] || fail "Нужна macOS 14 или новее, сейчас $OSV"

if ! xcode-select -p >/dev/null 2>&1 || ! command -v swiftc >/dev/null 2>&1; then
    step "Нужны Command Line Tools (swiftc, git, python3). Запускаю их установку — подтверди в окне и запусти этот скрипт снова."
    xcode-select --install 2>/dev/null || true
    exit 1
fi

mkdir -p "$HOME_DIR"

# Исходники: запуск из клона репозитория или скачать.
SCRIPT_DIR="${0:A:h}"
if [[ -f "$SCRIPT_DIR/macos/main.swift" ]]; then
    if [[ "$SCRIPT_DIR" != "${SRC:A}" ]]; then
        if [[ -e "$SRC" && ! -L "$SRC" ]]; then
            fail "$SRC уже существует и это не ссылка. Убери его или запусти install.sh оттуда."
        fi
        ln -sfn "$SCRIPT_DIR" "$SRC"
        step "Исходники: $SCRIPT_DIR (ссылка $SRC)"
    fi
else
    if [[ -d "$SRC/.git" ]]; then
        step "Обновляю исходники"
        git -C "$SRC" pull --ff-only
    else
        step "Скачиваю исходники"
        git clone --depth 1 "$REPO_URL" "$SRC"
    fi
fi

step "Python-окружение"
[[ -x "$HOME_DIR/venv/bin/python" ]] || python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install --quiet --upgrade pip
"$HOME_DIR/venv/bin/pip" install --quiet -r "$SRC/macos/requirements.txt"

[[ -f "$HOME_DIR/config.json" ]] || cp "$SRC/config.example.json" "$HOME_DIR/config.json"
MODEL="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('model') or 'mlx-community/whisper-large-v3-turbo')")"

step "Модель $MODEL (первый раз около 1,5 ГБ)"
"$HOME_DIR/venv/bin/python" - "$MODEL" <<'PY' || fail "не удалось скачать модель $MODEL — проверь интернет и имя модели в $HOME_DIR/config.json"
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1])
PY

step "Прогрев Python-пакетов (первый импорт компилирует их, иначе первый запуск ждёт полминуты)"
"$HOME_DIR/venv/bin/python" -c "import mlx_whisper, numpy" >/dev/null 2>&1 || true

step "Проверка ядра"
(cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest >/dev/null) || {
    (cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest) || fail "самопроверка ядра не прошла — см. вывод выше"
}

step "Сборка приложения"
F5VOICE_HOME="$HOME_DIR" "$SRC/macos/build.sh"
F5VOICE_HOME="$HOME_DIR" "$HOME_DIR/F5Voice.app/Contents/MacOS/F5Voice" --check | sed 's/^/    /'

step "Служба автозапуска"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# Старая служба выгружается не мгновенно; bootstrap того же имени в этот момент даёт EIO.
for _ in {1..40}; do
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
    sleep 0.25
done
sed "s|@HOME_DIR@|$HOME_DIR|g" "$SRC/macos/launchagent.plist.template" > "$PLIST"
for attempt in {1..5}; do
    if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then break; fi
    [[ $attempt -eq 5 ]] && fail "не удалось запустить службу: launchctl bootstrap gui/$(id -u) $PLIST"
    sleep 1
done

HOTKEY="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('hotkey') or 'F5')")"
print
print -P "%F{green}Готово.%f F5Voice запущен и будет стартовать при входе в систему."
print "Сейчас macOS спросит два разрешения (один раз):"
print "  1. Микрофон — нажать «Разрешить»."
print "  2. Универсальный доступ — Системные настройки → Конфиденциальность и безопасность → включить F5Voice."
print "Потом: $HOTKEY — запись, ещё раз $HOTKEY — текст в активном поле, Esc — отмена."
print "Настройки: $HOME_DIR/config.json (клавиша, языки, модель), лог: $HOME_DIR/f5voice.log"
