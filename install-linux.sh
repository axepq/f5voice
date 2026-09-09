#!/usr/bin/env bash
# F5Voice для Linux: установка одной командой.
#   bash <(curl -fsSL --connect-timeout 20 https://raw.githubusercontent.com/axepq/f5voice/main/install-linux.sh)
# Можно и из клона репозитория: ./install-linux.sh
# Ставит системные пакеты (python3-venv, portaudio, xclip), скачивает исходники архивом
# (git не нужен), создаёт своё Python-окружение с faster-whisper, скачивает модель,
# включает автозапуск в графической сессии и запускает. Повторный запуск обновляет.
set -euo pipefail

REPO="axepq/f5voice"
TARBALL="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
SRC="$HOME_DIR/src"

step() { printf '\n\033[36m▸ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

echo "F5Voice: установка для Linux начинается, это займёт несколько минут."
[[ "$(uname -s)" == Linux ]] || fail "Это установщик для Linux. macOS — install-macos.sh, Windows — install-windows.ps1"

# Запущены через curl | bash: ввод занят скриптом, а sudo и apt хотят терминал.
# Поэтому сначала скачиваем исходники и перезапускаемся из файла с вводом с терминала.
SELF="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SELF" && -f "$SELF" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd -P)"
fi
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/python/dictate.py" ]]; then
    if [[ "$SCRIPT_DIR" != "$(cd "$SRC" 2>/dev/null && pwd -P || true)" ]]; then
        if [[ -e "$SRC" && ! -L "$SRC" ]]; then
            step "Убираю старую копию исходников в $SRC"
            rm -rf "$SRC"
        fi
        mkdir -p "$HOME_DIR"
        ln -sfn "$SCRIPT_DIR" "$SRC"
        echo "Исходники: $SCRIPT_DIR (ссылка $SRC)"
    fi
else
    mkdir -p "$HOME_DIR"
    step "Скачиваю исходники F5Voice ($TARBALL)"
    command -v curl >/dev/null 2>&1 || fail "нет curl — поставь его (sudo apt install curl) и запусти снова"
    TMP="$(mktemp -d)"
    curl -fL --connect-timeout 20 --progress-bar "$TARBALL" | tar xz -C "$TMP" \
        || fail "не удалось скачать исходники. Проверь доступ к github.com; если он закрыт, нужен VPN."
    [[ -f "$TMP/f5voice-main/python/dictate.py" ]] || fail "архив распакован, но исходников в нём нет"
    if [[ -L "$SRC" ]]; then rm -f "$SRC"; else rm -rf "$SRC"; fi
    mv "$TMP/f5voice-main" "$SRC"
    rmdir "$TMP" 2>/dev/null || true
    echo "Исходники: $SRC"
    # Ввод с терминала — для sudo и вопросов; если терминала нет (автоматизация), без него.
    if { : </dev/tty; } 2>/dev/null; then exec bash "$SRC/install-linux.sh" </dev/tty; else exec bash "$SRC/install-linux.sh"; fi
fi

SUDO=""
if [[ ${EUID:-$(id -u)} -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
if command -v apt-get >/dev/null 2>&1; then
    step "Системные пакеты через apt: python3, venv, portaudio, xclip (может спросить пароль)"
    $SUDO apt-get install -y python3 python3-venv python3-pip python3-tk libportaudio2 xclip || fail "apt-get не смог поставить пакеты"
elif command -v dnf >/dev/null 2>&1; then
    step "Системные пакеты через dnf: python3, portaudio, xclip (может спросить пароль)"
    $SUDO dnf install -y python3 python3-pip python3-tkinter portaudio xclip || fail "dnf не смог поставить пакеты"
elif command -v pacman >/dev/null 2>&1; then
    step "Системные пакеты через pacman: python, portaudio, xclip (может спросить пароль)"
    $SUDO pacman -S --needed --noconfirm python python-pip tk portaudio xclip || fail "pacman не смог поставить пакеты"
else
    step "Пакетный менеджер не распознан. Нужны: python3 (3.9+) с модулем venv, libportaudio2, xclip"
fi
command -v python3 >/dev/null 2>&1 || fail "нет python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || fail "нужен Python 3.9 или новее, сейчас $(python3 --version)"

step "Python-окружение в $HOME_DIR/venv"
if [[ ! -x "$HOME_DIR/venv/bin/python" ]]; then
    python3 -m venv "$HOME_DIR/venv" || fail "не создался venv (Debian/Ubuntu: sudo apt install python3-venv)"
fi
"$HOME_DIR/venv/bin/pip" install --upgrade pip
step "Зависимости (faster-whisper и остальное, около 200 МБ)"
"$HOME_DIR/venv/bin/pip" install -r "$SRC/python/requirements.txt" || fail "pip не смог поставить зависимости"

[[ -f "$HOME_DIR/config.json" ]] || cp "$SRC/python/config.example.json" "$HOME_DIR/config.json"
MODEL="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('model') or 'large-v3-turbo')")"
HOTKEY="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('hotkey') or '<ctrl>+<alt>+<space>')")"

step "Модель $MODEL (первый раз около 1,6 ГБ, ниже будет прогресс)"
PYTHONWARNINGS=ignore "$HOME_DIR/venv/bin/python" "$SRC/python/download_model.py" "$MODEL" \
    || fail "не удалось скачать модель $MODEL — проверь интернет и имя модели в $HOME_DIR/config.json"

step "Проверка ядра"
(cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest | tail -1) || fail "самопроверка ядра не прошла"

step "Проверка микрофона и загрузки модели (на процессоре может занять минуту)"
"$HOME_DIR/venv/bin/python" "$SRC/python/dictate.py" --check || fail "проверка модели или микрофона не прошла — причина выше"

if [[ -z "${F5VOICE_NO_SERVICE:-}" ]]; then
    step "Автозапуск в графической сессии"
    mkdir -p "$HOME/.config/autostart"
    cat > "$HOME/.config/autostart/f5voice.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=F5Voice
Comment=Локальная диктовка по горячей клавише
Exec=$HOME_DIR/venv/bin/python $SRC/python/dictate.py --log
X-GNOME-Autostart-enabled=true
DESKTOP

    step "Запуск"
    pkill -f "python/dictate.py" 2>/dev/null || true
    sleep 1
    MARK=$(wc -l < "$HOME_DIR/f5voice.log" 2>/dev/null || echo 0)
    nohup "$HOME_DIR/venv/bin/python" "$SRC/python/dictate.py" --log >/dev/null 2>&1 &
    echo "Жду, пока F5Voice загрузит модель (до 90 с)…"
    READY=""
    for _ in $(seq 1 30); do
        sleep 3
        if tail -n +"$((MARK + 1))" "$HOME_DIR/f5voice.log" 2>/dev/null | grep -q '\[READY\]'; then READY=1; break; fi
        if tail -n +"$((MARK + 1))" "$HOME_DIR/f5voice.log" 2>/dev/null | grep -q '\[FATAL\]'; then break; fi
        pgrep -f "python/dictate.py" >/dev/null || break
    done
    echo "Хвост лога ($HOME_DIR/f5voice.log):"
    tail -8 "$HOME_DIR/f5voice.log" 2>/dev/null | sed 's/^/    /' || true
    [[ -n "$READY" ]] || fail "F5Voice не сообщил о готовности — причина в логе выше"
fi

printf '\n\033[32mГотово.\033[0m F5Voice будет стартовать вместе с рабочим столом.\n'
echo "  $HOTKEY — запись, ещё раз — текст в активном поле, Esc — отмена. Значок микрофона в области уведомлений."
echo "  Настройки: $HOME_DIR/config.json (клавиша, модель, языки), лог: $HOME_DIR/f5voice.log"
echo "  Проверка на файле: $HOME_DIR/venv/bin/python $SRC/python/dictate.py --file запись.wav"
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    printf '\n\033[33mWayland:\033[0m глобальные клавиши через pynput не работают. В настройках рабочего стола назначь сочетание на команду:\n'
    echo "  $HOME_DIR/venv/bin/python $SRC/python/dictate.py --toggle"
    echo "Печать в приложения Wayland тоже ограничена: поставь в config.json \"typing\": \"paste\" (нужен wl-clipboard)."
fi
