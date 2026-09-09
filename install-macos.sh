#!/usr/bin/env bash
# F5Voice для macOS на Apple Silicon: установка одной командой.
#   bash <(curl -fsSL --connect-timeout 20 https://raw.githubusercontent.com/axepq/f5voice/main/install-macos.sh)
# Можно и из клона репозитория: ./install-macos.sh
# Ставит Command Line Tools (если их нет), своё Python-окружение с mlx-whisper,
# скачивает модель, собирает приложение, включает автозапуск. Повторный запуск обновляет.
set -euo pipefail

REPO_URL="https://github.com/axepq/f5voice.git"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
SRC="$HOME_DIR/src"
LABEL="com.alex.f5voice"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

step() { printf '\n\033[36m▸ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

echo "F5Voice: установка для macOS начинается, это займёт несколько минут."
[[ "$(uname -s)" == Darwin ]] || fail "Это установщик для macOS. Linux — install-linux.sh, Windows — install-windows.ps1"
[[ "$(uname -m)" == arm64 ]] || fail "Нужен Mac на Apple Silicon (M1 и новее): mlx-whisper не работает на Intel"
OSV="$(sw_vers -productVersion)"
[[ "${OSV%%.*}" -ge 14 ]] || fail "Нужна macOS 14 или новее, сейчас $OSV"

if ! xcode-select -p >/dev/null 2>&1; then
    step "Нужны Command Line Tools от Apple (git, python, компилятор). Сейчас появится окно — нажми «Установить» и дождись конца, я подожду."
    xcode-select --install >/dev/null 2>&1 || true
    waited=0
    until xcode-select -p >/dev/null 2>&1; do
        sleep 10
        waited=$((waited + 10))
        [[ $waited -ge 3600 ]] && fail "Command Line Tools так и не появились. Поставь их и запусти команду снова."
    done
    sleep 5
fi
command -v swiftc >/dev/null 2>&1 || fail "не найден swiftc — переустанови Command Line Tools: xcode-select --install"

mkdir -p "$HOME_DIR"

# Откуда исходники: запущены из файла внутри клона — используем его,
# иначе (curl | bash) скачиваем репозиторий и перезапускаемся из него.
SELF="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SELF" && -f "$SELF" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd -P)"
fi
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/macos/main.swift" ]]; then
    if [[ "$SCRIPT_DIR" != "$(cd "$SRC" 2>/dev/null && pwd -P || true)" ]]; then
        if [[ -e "$SRC" && ! -L "$SRC" ]]; then
            fail "$SRC уже существует и это не ссылка. Убери его или запусти установщик оттуда."
        fi
        ln -sfn "$SCRIPT_DIR" "$SRC"
        step "Исходники: $SCRIPT_DIR (ссылка $SRC)"
    fi
else
    if git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
        step "Обновляю исходники F5Voice"
        git -C "$SRC" pull --ff-only || fail "не удалось обновить $SRC"
    else
        step "Скачиваю F5Voice"
        git clone --depth 1 "$REPO_URL" "$SRC.new" || fail "не удалось скачать исходники. Проверь доступ к github.com; если он закрыт, нужен VPN."
        rm -rf "$SRC"
        mv "$SRC.new" "$SRC"
    fi
    if [[ -r /dev/tty ]]; then exec bash "$SRC/install-macos.sh" </dev/tty; else exec bash "$SRC/install-macos.sh"; fi
fi

step "Python-окружение в $HOME_DIR/venv"
[[ -x "$HOME_DIR/venv/bin/python" ]] || python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install --upgrade pip | tail -1
step "Зависимости (mlx-whisper и остальное, около 500 МБ)"
"$HOME_DIR/venv/bin/pip" install -r "$SRC/macos/requirements.txt" || fail "pip не смог поставить зависимости"

[[ -f "$HOME_DIR/config.json" ]] || cp "$SRC/macos/config.example.json" "$HOME_DIR/config.json"
MODEL="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('model') or 'mlx-community/whisper-large-v3-turbo')")"

step "Модель $MODEL (первый раз около 1,5 ГБ, ниже будет прогресс)"
"$HOME_DIR/venv/bin/python" - "$MODEL" <<'PY' || fail "не удалось скачать модель $MODEL — проверь интернет и имя модели в $HOME_DIR/config.json"
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1])
PY

step "Прогрев Python-пакетов (первый импорт компилирует их, иначе первый запуск ждёт полминуты)"
"$HOME_DIR/venv/bin/python" -c "import mlx_whisper, numpy" >/dev/null 2>&1 || true

step "Проверка ядра"
(cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest | tail -1) || fail "самопроверка ядра не прошла"

step "Сборка приложения"
F5VOICE_HOME="$HOME_DIR" zsh "$SRC/macos/build.sh"
F5VOICE_HOME="$HOME_DIR" "$HOME_DIR/F5Voice.app/Contents/MacOS/F5Voice" --check | sed 's/^/    /'

if [[ -n "${F5VOICE_NO_SERVICE:-}" ]]; then
    printf '\n\033[32mСобрано без запуска службы (F5VOICE_NO_SERVICE).\033[0m\n'
    exit 0
fi

step "Служба автозапуска"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# Старая служба выгружается не мгновенно; bootstrap того же имени в этот момент даёт EIO.
for _ in $(seq 1 40); do
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
    sleep 0.25
done
sed "s|@HOME_DIR@|$HOME_DIR|g" "$SRC/macos/launchagent.plist.template" > "$PLIST"
for attempt in 1 2 3 4 5; do
    if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then break; fi
    [[ $attempt -eq 5 ]] && fail "не удалось запустить службу: launchctl bootstrap gui/$(id -u) $PLIST"
    sleep 1
done

HOTKEY="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('hotkey') or 'F5')")"
printf '\n\033[32mГотово.\033[0m F5Voice запущен и будет стартовать при входе в систему.\n'
echo "Сейчас macOS спросит два разрешения (один раз):"
echo "  1. Микрофон — нажать «Разрешить»."
echo "  2. Универсальный доступ — Системные настройки → Конфиденциальность и безопасность → включить F5Voice."
echo "Потом: $HOTKEY — запись, ещё раз $HOTKEY — текст в активном поле, Esc — отмена."
echo "Настройки: $HOME_DIR/config.json (клавиша, языки, модель), лог: $HOME_DIR/f5voice.log"
