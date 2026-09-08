#!/usr/bin/env bash
# Полное удаление F5Voice с Linux. Кэш модели в ~/.cache/huggingface не трогает.
set -uo pipefail
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
pkill -f "python/dictate.py" 2>/dev/null
rm -f "$HOME/.config/autostart/f5voice.desktop"
[[ -L "$HOME_DIR/src" ]] && rm -f "$HOME_DIR/src"
rm -rf "$HOME_DIR"
echo "F5Voice удалён. Модель в ~/.cache/huggingface/hub можно удалить руками."
