# F5Voice для Windows и Linux

Та же диктовка, что на маке, но на Python и faster-whisper: работает на CPU
(медленнее) или на видеокарте NVIDIA (CUDA). Логика общая с маковской
версией: русский по умолчанию, английские названия латиницей, голосовые
команды, отсечение тишины. Значок микрофона в области уведомлений: серый —
ждёт, красный — запись, оранжевый — распознаёт. Windows прячет новые значки
за стрелкой `^` справа внизу; при старте F5Voice показывает уведомление.
На Windows установщик также кладёт ярлыки «F5Voice» на рабочий стол и в
Пуск — они запускают службу заново, если её закрыли через «Выход».

**Честное предупреждение:** эта часть собрана на маке и на настоящих
Windows/Linux не проверялась. Если что-то падает, присылайте `~/.f5voice/f5voice.log`.

## Установка одной командой

Linux, в терминале:

```
bash <(curl -fsSL --connect-timeout 20 https://raw.githubusercontent.com/axepq/f5voice/main/install-linux.sh)
```

Windows, в PowerShell:

```
irm -TimeoutSec 60 https://raw.githubusercontent.com/axepq/f5voice/main/install-windows.ps1 | iex
```

Установщик сам ставит недостающее (на Linux пакеты через apt/dnf/pacman, на
Windows Python через winget), скачивает исходники архивом в `~/.f5voice/src`
(git не нужен), создаёт своё окружение и запускает F5Voice. Из клона репозитория: `./install-linux.sh` или
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
| `hotkey` | `ctrl+alt+space`, `F5`, `cmd+shift+d` — имена приводятся к формату pynput сами |
| `languages` | языки через запятую, первый — основной |
| `model` | `large-v3-turbo`, `medium`, `small` (быстрее на слабом CPU), или репозиторий Hugging Face |
| `device`, `compute_type` | `auto`: при первом запуске CUDA проверяется в отдельном процессе; без библиотек программа переходит на `cpu` и записывает это в настройки. `compute_type` лучше оставить `auto` |
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

## Видеокарта NVIDIA

Без библиотек cuBLAS и cuDNN для CUDA 12 CTranslate2 на Windows не выдаёт
ошибку, а падает, поэтому F5Voice проверяет CUDA в отдельном процессе и при
неудаче работает на процессоре, записав `"device": "cpu"` в настройки.
Чтобы включить видеокарту (драйвер NVIDIA не старее 525):

Windows, PowerShell:
```
& "$HOME\.f5voice\venv\Scripts\python.exe" -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Linux:
```
~/.f5voice/venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Это около 1 ГБ. Потом в `~/.f5voice/config.json` поставить `"device": "auto"`
и перезапустить F5Voice: библиотеки из этих пакетов программа находит сама,
проверка пройдёт, и в логе появится «CUDA работает».

## Известные особенности Windows

- CTranslate2 4.8 падает при загрузке любой модели на некоторых процессорах Intel
  12-го поколения и новее (Alder Lake, «Family 6 Model 151»). F5Voice замечает это
  при проверке и сам ставит CTranslate2 4.5.0 вместе с `setuptools<80`.
- Новые значки Windows 11 прячет за стрелкой `^`; при старте показывается уведомление.
