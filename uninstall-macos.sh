#!/usr/bin/env bash
# Полное удаление F5Voice с macOS. Кэш модели в ~/.cache/huggingface не трогает.
set -uo pipefail
HOME_DIR="${F5VOICE_HOME:-$HOME/.f5voice}"
LABEL="com.alex.f5voice"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
hidutil property --set '{"UserKeyMapping":[]}' >/dev/null 2>&1
tccutil reset Accessibility "$LABEL" >/dev/null 2>&1
tccutil reset Microphone "$LABEL" >/dev/null 2>&1
[[ -L "$HOME_DIR/src" ]] && rm -f "$HOME_DIR/src"
rm -rf "$HOME_DIR"
echo "F5Voice удалён. Модель в ~/.cache/huggingface/hub/models--mlx-community--* можно удалить руками."
