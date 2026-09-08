#!/usr/bin/env bash
# F5Voice для Linux: установка одной командой.
#   curl -fsSL https://raw.githubusercontent.com/axepq/f5voice/main/install-linux.sh | bash
# Можно и из клона репозитория: ./install-linux.sh
# Ставит системные пакеты (git, python3-venv, portaudio, xclip), своё Python-окружение
# с faster-whisper, скачивает модель, включает автозапуск в графической сессии и запускает.
set -euo pipefail

REPO_URL="https://github.com/axepq/f5voice.git"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
SRC="$HOME_DIR/src"

step() { printf '\033[36m▸\033[0m %s\n' "$1"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

[[ "$(uname -s)" == Linux ]] || fail "Это установщик для Linux. macOS — install-macos.sh, Windows — install-windows.ps1"

SUDO=""
if [[ ${EUID:-$(id -u)} -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
if command -v apt-get >/dev/null 2>&1; then
    step "Системные пакеты (apt): git, python3, venv, portaudio, xclip — может спросить пароль"
    $SUDO apt-get install -y -qq git python3 python3-venv python3-pip libportaudio2 xclip >/dev/null \
        || fail "apt-get не смог поставить пакеты"
elif command -v dnf >/dev/null 2>&1; then
    step "Системные пакеты (dnf): git, python3, portaudio, xclip — может спросить пароль"
    $SUDO dnf install -y -q git python3 python3-pip portaudio xclip >/dev/null || fail "dnf не смог поставить пакеты"
elif command -v pacman >/dev/null 2>&1; then
    step "Системные пакеты (pacman): git, python, portaudio, xclip — может спросить пароль"
    $SUDO pacman -S --needed --noconfirm git python python-pip portaudio xclip >/dev/null || fail "pacman не смог поставить пакеты"
else
    step "Пакетный менеджер не распознан. Нужны: git, python3 (3.9+) с модулем venv, libportaudio2, xclip"
fi
command -v git >/dev/null 2>&1 || fail "нет git"
command -v python3 >/dev/null 2>&1 || fail "нет python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || fail "нужен Python 3.9 или новее, сейчас $(python3 --version)"

mkdir -p "$HOME_DIR"

# Откуда исходники: запущены из файла внутри клона — используем его,
# иначе (curl | bash) скачиваем репозиторий и перезапускаемся из него.
SELF="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SELF" && -f "$SELF" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd -P)"
fi
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/python/dictate.py" ]]; then
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
        git -C "$SRC" pull --ff-only --quiet || fail "не удалось обновить $SRC"
    else
        [[ -e "$SRC" ]] && fail "$SRC уже существует, но это не репозиторий. Убери его и запусти команду снова."
        step "Скачиваю F5Voice"
        git clone --depth 1 --quiet "$REPO_URL" "$SRC"
    fi
    exec bash "$SRC/install-linux.sh"
fi

step "Python-окружение"
if [[ ! -x "$HOME_DIR/venv/bin/python" ]]; then
    python3 -m venv "$HOME_DIR/venv" || fail "не создался venv (Debian/Ubuntu: sudo apt install python3-venv)"
fi
"$HOME_DIR/venv/bin/pip" install --quiet --upgrade pip
"$HOME_DIR/venv/bin/pip" install --quiet -r "$SRC/python/requirements.txt" || fail "pip не смог поставить зависимости"

[[ -f "$HOME_DIR/config.json" ]] || cp "$SRC/python/config.example.json" "$HOME_DIR/config.json"
MODEL="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('model') or 'large-v3-turbo')")"
HOTKEY="$("$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('hotkey') or '<ctrl>+<alt>+space')")"

step "Модель $MODEL (первый раз около 1,6 ГБ)"
"$HOME_DIR/venv/bin/python" -c "from faster_whisper.utils import download_model; download_model('$MODEL')" \
    || fail "не удалось скачать модель $MODEL — проверь интернет и имя модели в $HOME_DIR/config.json"

step "Проверка ядра"
(cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest >/dev/null) \
    || { (cd "$SRC" && "$HOME_DIR/venv/bin/python" -m common.selftest) || fail "самопроверка ядра не прошла"; }

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
nohup "$HOME_DIR/venv/bin/python" "$SRC/python/dictate.py" --log >/dev/null 2>&1 &

printf '\n\033[32mГотово.\033[0m F5Voice запущен и будет стартовать вместе с рабочим столом.\n'
echo "  $HOTKEY — запись, ещё раз — текст в активном поле, Esc — отмена. Значок микрофона в области уведомлений."
echo "  Настройки: $HOME_DIR/config.json (клавиша, модель, языки), лог: $HOME_DIR/f5voice.log"
echo "  Проверка на файле: $HOME_DIR/venv/bin/python $SRC/python/dictate.py --file запись.wav"
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    printf '\n\033[33mWayland:\033[0m глобальные клавиши через pynput не работают. В настройках рабочего стола назначь сочетание на команду:\n'
    echo "  $HOME_DIR/venv/bin/python $SRC/python/dictate.py --toggle"
    echo "Печать в приложения Wayland тоже ограничена: поставь в config.json \"typing\": \"paste\" (нужен wl-clipboard)."
fi
