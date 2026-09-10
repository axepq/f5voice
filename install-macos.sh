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
APP_DIR="/Applications"
[[ -w /Applications ]] || APP_DIR="$HOME/Applications"
APP="$APP_DIR/F5Voice.app"

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
        if (( waited % 60 == 0 )); then
            echo "  жду Command Line Tools… $((waited / 60)) мин. Окна не было? В другом окне Терминала: xcode-select --install"
        fi
        [[ $waited -ge 3600 ]] && fail "Command Line Tools так и не появились. Поставь их и запусти команду снова."
    done
    sleep 5
fi
command -v swiftc >/dev/null 2>&1 || fail "не найден swiftc — переустанови Command Line Tools: xcode-select --install"

# Command Line Tools бывают разобранными: после обновления macOS в SDKs/ появляется новый SDK,
# а компилятор остаётся от прошлой версии, и swiftc падает на самом модуле Swift
# («SDK is built with Apple Swift version X, while this compiler is Y»). Проверяем на крошечном
# файле до сборки; если штатный SDK не подходит — берём из SDKs/ тот, что подходит компилятору.
SMOKE="$(mktemp -d)"
printf 'import Cocoa\nprint(NSApplication.shared.isRunning)\n' > "$SMOKE/smoke.swift"
swift_smoke() {
    local sdk=()
    [[ -n "${1:-}" ]] && sdk=(-sdk "$1")
    # ${sdk[@]+...}: в bash 3.2 пустой массив под set -u считается unbound
    swiftc ${sdk[@]+"${sdk[@]}"} -target arm64-apple-macosx14.0 "$SMOKE/smoke.swift" -o "$SMOKE/smoke" >"$SMOKE/log" 2>&1
}
if ! swift_smoke ""; then
    SDK_OK=""
    for sdk in $(printf '%s\n' /Library/Developer/CommandLineTools/SDKs/MacOSX[0-9]*.sdk | sort -rV); do
        [[ -d "$sdk" ]] || continue
        if swift_smoke "$sdk"; then SDK_OK="$sdk"; break; fi
    done
    if [[ -n "$SDK_OK" ]]; then
        export SDKROOT="$SDK_OK"
        step "Штатный SDK новее компилятора swiftc, собираю с ${SDK_OK##*/}"
        echo "  Чтобы это не повторялось, обнови Command Line Tools: Системные настройки → Основные → Обновление ПО,"
        echo "  либо: sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install"
    else
        tail -3 "$SMOKE/log" >&2
        rm -rf "$SMOKE"
        fail "swiftc не собирает даже пустую программу: компилятор и SDK в Command Line Tools не совпадают. Переустанови их: sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install — и запусти установку снова."
    fi
fi
rm -rf "$SMOKE"

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
    # Ввод с терминала — для sudo и вопросов; если терминала нет (автоматизация), без него.
    if { : </dev/tty; } 2>/dev/null; then exec bash "$SRC/install-macos.sh" </dev/tty; else exec bash "$SRC/install-macos.sh"; fi
fi

step "Python-окружение в $HOME_DIR/venv"
[[ -x "$HOME_DIR/venv/bin/python" ]] || python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install --upgrade pip 2>/dev/null | tail -1 || echo "  pip не обновлён (нет сети?) — продолжаю"
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

# Переписывание идёт через облачный API (ключ в настройках) — локальные LLM не качаем.

step "Прогрев Python-пакетов (первый импорт компилирует их, иначе первый запуск ждёт полминуты)"
"$HOME_DIR/venv/bin/python" -c "import mlx_whisper, numpy" >/dev/null 2>&1 || true

step "Проверка ядра"
(cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest | tail -1) || fail "самопроверка ядра не прошла"

step "Сборка приложения → $APP"
mkdir -p "$APP_DIR"
F5VOICE_HOME="$HOME_DIR" F5VOICE_APP="$APP" zsh "$SRC/macos/build.sh"
F5VOICE_HOME="$HOME_DIR" "$APP/Contents/MacOS/F5Voice" --check | sed 's/^/    /'

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
rm -rf "$HOME_DIR/F5Voice.app"   # старое место приложения (версии до 1.3)
sed -e "s|@HOME_DIR@|$HOME_DIR|g" -e "s|@APP@|$APP|g" "$SRC/macos/launchagent.plist.template" > "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
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
echo "Приложение: $APP — открой его (Launchpad, Spotlight или значок в строке меню → «Настройки F5Voice…»):"
echo "там сочетание клавиш, стиль плашки, языки, модель, автозапуск и разрешения. Лог: $HOME_DIR/f5voice.log"
