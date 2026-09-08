# F5Voice для Windows и Linux

Та же диктовка, что на маке, но на Python и faster-whisper: работает на CPU
(медленнее) или на видеокарте NVIDIA (CUDA). Логика общая с маковской
версией: русский по умолчанию, английские названия латиницей, голосовые
команды, отсечение тишины. Значок микрофона в области уведомлений: серый —
ждёт, красный — запись, оранжевый — распознаёт.

**Честное предупреждение:** эта часть собрана на маке и на настоящих
Windows/Linux не проверялась. Если что-то падает, присылайте `~/.f5voice/f5voice.log`.

## Установка одной командой

Linux, в терминале:

```
curl -fsSL https://raw.githubusercontent.com/axepq/f5voice/main/install-linux.sh | bash
```

Windows, в PowerShell:

```
irm https://raw.githubusercontent.com/axepq/f5voice/main/install-windows.ps1 | iex
```

Установщик сам ставит недостающее (git, на Windows и Python — через winget),
скачивает исходники в `~/.f5voice/src`, создаёт своё окружение и запускает
F5Voice. Из клона репозитория: `./install-linux.sh` или
`powershell -ExecutionPolicy Bypass -File .\install-windows.ps1`.

Что делает установщик: ставит недостающее (Windows — Python 3.12 и Git через
winget; Linux — python3-venv, libportaudio2, xclip через apt/dnf/pacman),
создаёт `~/.f5voice/venv`, ставит зависимости, скачивает модель large-v3-turbo
(около 1,6 ГБ), прогоняет самопроверку ядра, добавляет автозапуск (Windows —
ярлык в автозагрузке, Linux — `~/.config/autostart/f5voice.desktop`) и
запускает F5Voice в фоне с логом в `~/.f5voice/f5voice.log`.

Обновление: `git -C ~/.f5voice/src pull` и снова установщик.
Удаление: `uninstall-linux.sh` или `uninstall-windows.ps1` в корне репозитория.

## Как пользоваться

Ctrl+Alt+Space — запись (короткий сигнал), ещё раз — распознать и напечатать
в активное поле, Esc — отмена. Первая фраза после запуска ждёт загрузку модели.
Голосовые команды и правила те же, что в [основном README](../README.md).

## Настройки — `~/.f5voice/config.json`

После правки перезапустить: Linux — `pkill -f python/dictate.py` и снова
запустить из автозапуска или командой ниже; Windows — «Выход» в меню значка и
ярлык F5Voice в автозагрузке (или перезайти в систему).

| ключ | что |
|---|---|
| `hotkey` | формат pynput: `<ctrl>+<alt>+space`, `<f5>`, `<cmd>+<shift>+d` |
| `languages` | языки через запятую, первый — основной |
| `model` | `large-v3-turbo`, `medium`, `small` (быстрее на слабом CPU), или репозиторий Hugging Face |
| `device`, `compute_type` | `auto`; для NVIDIA `cuda` + `float16` (нужны CUDA 12 и cuDNN 9); для CPU `cpu` + `int8` |
| `typing` | `type` — печатать посимвольно; `paste` — через буфер обмена, если символы теряются |
| `newline` | что нажимать для «новая строка»: `shift+enter`, `enter`, `ctrl+enter` |
| `input_device` | номер или имя микрофона из `--list-devices` |
| `tray` | `false` — без значка в области уведомлений |

## Команды

```
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --log          запустить в фоне (Linux)
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --toggle       начать/закончить запись извне
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --file a.wav   проверить распознавание на файле
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --list-devices микрофоны
```

На Windows вместо `venv/bin/python` — `venv\Scripts\python.exe`; `--toggle`
там не работает, есть горячая клавиша и значок.

## Linux: Wayland

pynput не видит глобальные клавиши под Wayland и не печатает в
Wayland-приложения. Обход: в настройках рабочего стола (GNOME, KDE) назначить
сочетание клавиш на команду `dictate.py --toggle` (полный путь выше), а в
config.json поставить `"typing": "paste"` и установить `wl-clipboard`.
Под X11 всё работает напрямую.
