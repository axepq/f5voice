#!/usr/bin/env bash
# F5Voice: установка одной командой на macOS и Linux.
#   curl -fsSL https://raw.githubusercontent.com/axepq/f5voice/main/get.sh | bash
# Ставит недостающее (на маке Command Line Tools, на Linux git), скачивает
# исходники в ~/.f5voice/src и запускает установщик для своей системы.
set -euo pipefail

REPO_URL="https://github.com/axepq/f5voice.git"
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
SRC="$HOME_DIR/src"

step() { printf '\033[36m▸\033[0m %s\n' "$1"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
Darwin)
    [[ "$(uname -m)" == arm64 ]] || fail "Нужен Mac на Apple Silicon (M1 и новее): whisper здесь работает на его GPU."
    if ! xcode-select -p >/dev/null 2>&1; then
        step "Нужны Command Line Tools от Apple (git, python, компилятор). Сейчас появится окно — нажми «Установить» и дождись конца."
        xcode-select --install >/dev/null 2>&1 || true
        waited=0
        until xcode-select -p >/dev/null 2>&1; do
            sleep 10
            waited=$((waited + 10))
            [[ $waited -ge 3600 ]] && fail "Command Line Tools так и не появились. Поставь их и запусти команду снова."
        done
        step "Command Line Tools установлены, продолжаю"
        sleep 5
    fi
    ;;
Linux)
    if ! command -v git >/dev/null 2>&1; then
        step "Ставлю git (спросит пароль администратора)"
        SUDO=""
        [[ ${EUID:-$(id -u)} -ne 0 ]] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
        if command -v apt-get >/dev/null 2>&1; then $SUDO apt-get install -y -qq git >/dev/null
        elif command -v dnf >/dev/null 2>&1; then $SUDO dnf install -y -q git >/dev/null
        elif command -v pacman >/dev/null 2>&1; then $SUDO pacman -S --needed --noconfirm git >/dev/null
        else fail "Не нашёл apt, dnf или pacman. Поставь git сам и запусти команду снова."
        fi
    fi
    ;;
*)
    fail "Для Windows открой PowerShell и выполни: irm https://raw.githubusercontent.com/axepq/f5voice/main/get.ps1 | iex"
    ;;
esac

mkdir -p "$HOME_DIR"
if [[ -d "$SRC/.git" ]] || git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
    step "Обновляю исходники F5Voice"
    git -C "$SRC" pull --ff-only --quiet || fail "не удалось обновить $SRC"
else
    [[ -e "$SRC" ]] && fail "$SRC уже существует, но это не репозиторий. Убери его и запусти команду снова."
    step "Скачиваю F5Voice"
    git clone --depth 1 --quiet "$REPO_URL" "$SRC"
fi

case "$OS" in
Darwin) exec zsh "$SRC/install.sh" ;;
Linux) exec bash "$SRC/other/install-linux.sh" ;;
esac
