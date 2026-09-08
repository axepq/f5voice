# F5Voice для Windows и Linux

Та же диктовка, что на маке, но на Python и faster-whisper: работает на CPU
(медленнее) или на видеокарте NVIDIA (CUDA). Логика общая с маковской
версией: русский по умолчанию, английские названия латиницей, голосовые
команды, отсечение тишины.

**Честное предупреждение:** эта часть собрана на маке и на настоящих
Windows/Linux не проверялась. Ошибки вероятны, присылайте вывод консоли.

## Установка

Нужен Python 3.10+ ([python.org](https://www.python.org/downloads/), на Windows
отметить «Add to PATH»).

```
git clone https://github.com/axepq/f5voice.git
cd f5voice
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux
pip install -r other/requirements.txt
python other/dictate.py
```

Первый запуск скачает модель (large-v3-turbo, около 1,6 ГБ) и создаст
`~/.f5voice/config.json`. Горячая клавиша по умолчанию Ctrl+Alt+Space:
нажать — запись, ещё раз — распознать и напечатать, Esc — отмена.

Linux дополнительно: `sudo apt install libportaudio2 xclip`. Глобальные
горячие клавиши работают под X11; под Wayland pynput их не видит.

Видеокарта NVIDIA: поставить CUDA 12 и cuDNN 9, в настройках `"device": "cuda"`,
`"compute_type": "float16"`.

Слабый CPU: `"model": "small"` или `"medium"` — заметно быстрее, хуже точность.

## Настройки — `~/.f5voice/config.json`

| ключ | что |
|---|---|
| `hotkey` | формат pynput: `<ctrl>+<alt>+space`, `<f5>`, `<cmd>+<shift>+d` |
| `languages` | языки через запятую, первый — основной |
| `model` | `large-v3-turbo`, `medium`, `small`, или репозиторий Hugging Face |
| `device`, `compute_type` | `auto`, либо `cuda`+`float16`, либо `cpu`+`int8` |
| `typing` | `type` — печатать посимвольно, `paste` — через буфер обмена (если символы теряются) |
| `newline` | что нажимать для «новая строка»: `shift+enter`, `enter`, `ctrl+enter` |
| `input_device` | номер или имя микрофона из `--list-devices` |

## Автозапуск

Windows: ярлык на `venv\Scripts\pythonw.exe other\dictate.py` (с рабочей
папкой репозитория) в `shell:startup`. Linux: `~/.config/autostart/f5voice.desktop`
или пользовательская служба systemd.

## Проверка без микрофона

```
python other/dictate.py --file запись.wav --model tiny
```
