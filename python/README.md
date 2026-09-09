# F5Voice для Windows и Linux

Та же диктовка, что на маке, но на Python и faster-whisper: работает на CPU
(медленнее) или на видеокарте NVIDIA (CUDA). Логика общая с маковской
версией: русский по умолчанию, английские названия латиницей, голосовые
команды, отсечение тишины. Значок микрофона в области уведомлений: серый —
ждёт, красный — запись, оранжевый — распознаёт. Windows прячет новые значки
за стрелкой `^` справа внизу; при старте F5Voice показывает уведомление.
Ярлык «F5Voice» (Windows: рабочий стол и Пуск; Linux: меню приложений)
открывает окно программы, а если её закрыли через «Выход» — запускает заново
и открывает окно. Работает всегда один экземпляр: автозапуск при уже
запущенной программе тихо выходит.

**Честно:** проверено на Windows 11 (Intel i7, RTX 3060 Ti) и на маке; на
настоящем Linux пока нет. Если что-то падает, присылайте `~/.f5voice/f5voice.log`.

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

## Настройки

Окно программы: ярлык F5Voice, значок в трее → «Настройки…» или команда
`dictate.py --settings`. В нём состояние (готов или запись, модель, процессор
или видеокарта), сочетание клавиш (кнопка «Записать» — нажать нужные клавиши),
стиль плашки (glass, metal, light, dark; на Windows 11 стекло и скруглённые
углы через DWM, на Windows 10 плоская панель), языки, модель, перенос строки,
пробел после текста, автозапуск при входе в систему, кнопка пробной записи
и лог. «Сохранить и перезапустить» применяет всё разом.
Файл `~/.f5voice/config.json`:

После правки файла перезапустить: «Выход» в меню значка и снова ярлык
F5Voice (или в окне — «Сохранить и перезапустить»).

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
| `hud` | `false` — без плашки внизу экрана |
| `cpu_threads` | потоков CTranslate2 на процессоре, `0` — половина логических ядер |
| `beam_size` | `0` — авто: 1 на процессоре, 5 на видеокарте; больше — точнее и медленнее |

## Команды

```
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --log          запустить (второй запуск откроет окно первого)
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --service --log автозапуск: без окна, при работающей — выход
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --settings     открыть окно работающей программы
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --toggle       начать/закончить запись извне
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --file a.wav   проверить распознавание на файле
~/.f5voice/venv/bin/python ~/.f5voice/src/python/dictate.py --list-devices микрофоны
```

На Windows вместо `venv/bin/python` — `venv\Scripts\python.exe` (или
`pythonw.exe`, чтобы без окна консоли).

## Linux: Wayland

pynput не видит глобальные клавиши под Wayland и не печатает в
Wayland-приложения. Обход: в настройках рабочего стола (GNOME, KDE) назначить
сочетание клавиш на команду `dictate.py --toggle` (полный путь выше), а в
config.json поставить `"typing": "paste"` и установить `wl-clipboard`.
Под X11 всё работает напрямую.

## Видеокарта NVIDIA

Установщик сам находит видеокарту NVIDIA и ставит библиотеки CUDA (cuBLAS и
cuDNN для CUDA 12, около 1 ГБ), после чего проверка включает её: в логе будет
«CUDA работает». Нужен драйвер NVIDIA не старее 525. Без библиотек CTranslate2
на Windows не выдаёт ошибку, а падает, поэтому CUDA проверяется в отдельном
процессе, а при неудаче F5Voice работает на процессоре. Поставить библиотеки
вручную:

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
