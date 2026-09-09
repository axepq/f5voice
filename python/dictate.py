#!/usr/bin/env python3
"""F5Voice для Windows и Linux: горячая клавиша → запись → faster-whisper → печать текста.

Та же логика, что в маковской версии, на общем ядре (common/): выбор языка с
приоритетом русского, детектор речи, голосовые команды, чистка галлюцинаций.
Модель — faster-whisper (CTranslate2), на CPU или CUDA.

Запуск:            python python/dictate.py            (в фоне: --log, вывод в ~/.f5voice/f5voice.log)
Переключить извне: python python/dictate.py --toggle   (для сочетаний клавиш рабочего стола, Wayland)
Проверка на файле: python python/dictate.py --file запись.wav
Устройства ввода:  python python/dictate.py --list-devices
Настройки:         ~/.f5voice/config.json (создаётся при первом запуске)

Собрано на macOS, на настоящих Windows/Linux автором не проверялось. Если
что-то падает — пришлите ~/.f5voice/f5voice.log.
"""
import argparse
import json
import os
import platform
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np

def _utf8_console():
    """Консоль Windows по умолчанию cp1252/cp866: без этого print кириллицы и стрелок падает."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_console()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.audio_io import load_audio, resample  # noqa: E402
from common.segments import assemble  # noqa: E402
from common.textproc import DEFAULT_PROMPT, RU_HINT, finalize  # noqa: E402
from common.vad import is_silence  # noqa: E402

RATE = 16000
HOME = Path(os.environ.get("F5VOICE_HOME") or Path.home() / ".f5voice")
CONFIG_PATH = HOME / "config.json"
LAST_WAV = HOME / "last.wav"
LOG_PATH = HOME / "f5voice.log"
PID_PATH = HOME / "dictate.pid"
IS_WINDOWS = platform.system() == "Windows"

DEFAULTS = {
    "hotkey": "<ctrl>+<alt>+space",   # формат pynput: <ctrl>, <alt>, <shift>, <cmd>, <f5>, буквы
    "languages": "ru,en",
    "alt_language_min_prob": 0.95,
    "model": "large-v3-turbo",         # small / medium быстрее на слабом CPU
    "device": "auto",                  # auto | cpu | cuda
    "compute_type": "auto",            # auto | int8 | float16 | int8_float16
    "prompt": "",                      # пусто — стандартная подсказка из common/textproc.py
    "trailing_space": True,
    "max_record_seconds": 180,
    "typing": "type",                  # type — печатать посимвольно; paste — через буфер обмена
    "newline": "shift+enter",          # shift+enter | enter | ctrl+enter
    "input_device": None,              # номер или имя из --list-devices, None — по умолчанию
    "tray": True,                      # значок в области уведомлений (нужны pystray и Pillow)
}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg), flush=True)


def load_config():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            log(f"! config.json не разобран ({e}), работаю по умолчанию")
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"создал настройки: {CONFIG_PATH}")
    return cfg


def beep(kind="start"):
    if IS_WINDOWS:
        try:
            import winsound
            winsound.Beep({"start": 880, "stop": 660}.get(kind, 330), 120)
            return
        except Exception:  # noqa: BLE001
            pass
    print("\a", end="", flush=True)


# ---------------------------------------------------------------- распознавание

class Recognizer:
    def __init__(self, cfg):
        from faster_whisper import WhisperModel

        self.langs = tuple(x.strip() for x in cfg["languages"].split(",") if x.strip()) or ("ru",)
        self.alt_min = float(cfg["alt_language_min_prob"])
        self.prompt = cfg["prompt"] or DEFAULT_PROMPT
        t = time.time()
        log(f"загружаю модель {cfg['model']} ({cfg['device']}, {cfg['compute_type']})…")
        self.model = WhisperModel(cfg["model"], device=cfg["device"], compute_type=cfg["compute_type"])
        warm = np.random.default_rng(0).normal(0, 1e-4, RATE).astype(np.float32)  # нули дают предупреждения numpy
        self.run(warm, self.langs[0])
        log(f"модель готова за {time.time() - t:.1f} с")

    def detect(self, audio):
        _, _, all_probs = self.model.detect_language(audio=audio)
        probs = dict(all_probs)
        return {lang: round(float(probs.get(lang, 0.0)), 3) for lang in self.langs}

    def pick_language(self, audio):
        if len(self.langs) == 1:
            return self.langs[0], {}
        scores = self.detect(audio)
        alt = max(self.langs[1:], key=lambda l: scores[l])
        return (alt if scores[alt] >= self.alt_min else self.langs[0]), scores

    def run(self, audio, lang, prompt=None, temperature=(0.0, 0.2, 0.4)):
        segments, _info = self.model.transcribe(
            audio,
            language=lang,
            task="transcribe",
            initial_prompt=prompt or self.prompt,
            condition_on_previous_text=True,
            temperature=list(temperature) if isinstance(temperature, tuple) else temperature,
            beam_size=5,
            vad_filter=False,
        )
        return [(s.start, s.end, s.text) for s in segments]

    def recognize(self, audio):
        lang, scores = self.pick_language(audio)
        segs = self.run(audio, lang)
        duration = audio.size / RATE

        def redecode(start, end, context):
            chunk = audio[int(start * RATE):int(end * RATE)]
            if chunk.size < RATE // 2:
                return None
            p = self.detect(chunk)
            if p.get(self.langs[0], 0.0) < p.get("en", 0.0):
                return None
            again = self.run(chunk, self.langs[0], prompt=(context or RU_HINT), temperature=0.0)
            return " ".join(t.strip() for _, _, t in again).strip()

        may_fix = lang == self.langs[0] and scores.get("en", 0.0) < 0.6
        text, fixed = assemble(segs, duration, redecode if may_fix else None)
        return finalize(text, self.prompt), lang, scores, fixed


# ---------------------------------------------------------------- запись

class Recorder:
    def __init__(self, device=None):
        import sounddevice as sd

        self.sd = sd
        self.device = device
        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.name = info["name"]
        self.frames = []
        self.stream = None

    def start(self):
        self.frames = []

        def cb(indata, _frames, _time, status):
            self.frames.append(indata[:, 0].copy())

        self.stream = self.sd.InputStream(device=self.device, samplerate=self.rate, channels=1,
                                          dtype="float32", callback=cb)
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(self.frames), self.rate, RATE)


def save_wav(path, audio):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- печать

class Typist:
    def __init__(self, cfg):
        from pynput.keyboard import Controller, Key

        self.kb = Controller()
        self.Key = Key
        self.mode = cfg["typing"]
        self.newline = cfg["newline"]

    def _newline(self):
        mods = {"shift": self.Key.shift, "ctrl": self.Key.ctrl, "alt": self.Key.alt}
        held = [mods[p] for p in self.newline.lower().split("+") if p in mods]
        for m in held:
            self.kb.press(m)
        self.kb.press(self.Key.enter)
        self.kb.release(self.Key.enter)
        for m in reversed(held):
            self.kb.release(m)

    def type(self, text):
        if self.mode == "paste":
            import pyperclip

            old = None
            try:
                old = pyperclip.paste()
            except Exception:  # noqa: BLE001
                pass
            pyperclip.copy(text)
            mod = self.Key.cmd if platform.system() == "Darwin" else self.Key.ctrl
            with self.kb.pressed(mod):
                self.kb.press("v")
                self.kb.release("v")
            if old is not None:
                threading.Timer(0.5, lambda: pyperclip.copy(old)).start()
            return
        lines = text.split("\n")
        for chunk in lines[:-1]:
            self.kb.type(chunk)
            self._newline()
        self.kb.type(lines[-1])


# ---------------------------------------------------------------- значок в трее

class Tray:
    """pystray + Pillow, если установлены; иначе молча без значка."""

    COLORS = {"idle": (120, 120, 130), "recording": (230, 60, 60), "transcribing": (240, 160, 40)}

    def __init__(self, app):
        import pystray
        from PIL import Image, ImageDraw

        self.pystray, self.Image, self.ImageDraw = pystray, Image, ImageDraw
        self.app = app
        menu = pystray.Menu(
            pystray.MenuItem("Запись / стоп", lambda: app.toggle()),
            pystray.MenuItem("Настройки (config.json)", lambda: open_path(CONFIG_PATH)),
            pystray.MenuItem("Лог", lambda: open_path(LOG_PATH)),
            pystray.MenuItem("Выход", lambda: app.quit()),
        )
        self.icon = pystray.Icon("F5Voice", self._image("idle"), "F5Voice", menu)

    def _image(self, state):
        img = self.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = self.ImageDraw.Draw(img)
        d.ellipse((6, 6, 58, 58), fill=self.COLORS[state] + (255,))
        d.rounded_rectangle((25, 14, 39, 38), radius=7, fill=(255, 255, 255, 255))  # капсула микрофона
        d.arc((19, 24, 45, 46), 0, 180, fill=(255, 255, 255, 255), width=3)
        d.line((32, 46, 32, 52), fill=(255, 255, 255, 255), width=3)
        return img

    def set_state(self, state):
        self.icon.icon = self._image(state)
        self.icon.title = {"idle": "F5Voice", "recording": "F5Voice: запись…", "transcribing": "F5Voice: распознаю…"}[state]

    def run(self, setup):
        self.icon.run(setup=lambda icon: setup())

    def stop(self):
        self.icon.stop()


def open_path(path):
    import subprocess

    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------- приложение

class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = "idle"
        self.lock = threading.Lock()
        self.tray = None
        self.hotkeys = None
        log(f"Python {platform.python_version()}, {platform.platform()}, {os.cpu_count()} ядер")
        try:
            self.recorder = Recorder(cfg["input_device"])
        except Exception as e:  # noqa: BLE001
            log(f"[FATAL] микрофон недоступен: {type(e).__name__}: {e}")
            log("  Linux: sudo apt install libportaudio2; список устройств: dictate.py --list-devices; "
                "выбрать: \"input_device\" в config.json")
            sys.exit(1)
        self.recognizer = Recognizer(cfg)
        self.typist = Typist(cfg)
        self.stop_timer = None
        log(f"микрофон: {self.recorder.name} ({self.recorder.rate} Гц)")

    def _set_state(self, state):
        self.state = state
        if self.tray:
            try:
                self.tray.set_state(state)
            except Exception:  # noqa: BLE001
                pass

    def toggle(self):
        with self.lock:
            if self.state == "idle":
                self._start()
            elif self.state == "recording":
                self._stop()
            else:
                log("… ещё распознаю предыдущее")

    def cancel(self):
        with self.lock:
            if self.state == "recording":
                if self.stop_timer:
                    self.stop_timer.cancel()
                self.recorder.stop()
                self._set_state("idle")
                beep("stop")
                log("отменено")

    def _start(self):
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log(f"! запись не началась: {e}")
            return
        self._set_state("recording")
        beep("start")
        log(f"● говорите… ({self.cfg['hotkey']} — готово, Esc — отмена)")
        self.stop_timer = threading.Timer(float(self.cfg["max_record_seconds"]), self.toggle)
        self.stop_timer.daemon = True
        self.stop_timer.start()

    def _stop(self):
        if self.stop_timer:
            self.stop_timer.cancel()
        audio = self.recorder.stop()
        self._set_state("transcribing")
        beep("stop")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio):
        try:
            save_wav(LAST_WAV, audio)
            quiet, dur, speech = is_silence(audio)
            if quiet:
                log(f"ничего не услышал (запись {dur} с, речи {speech} с)")
                beep("error")
                return
            t = time.time()
            text, lang, scores, fixed = self.recognizer.recognize(audio)
            if not text:
                log("ничего не разобрал")
                beep("error")
                return
            log(f"готово: {len(text)} символов, язык {lang} {scores}"
                f"{', исправлено ' + str(fixed) if fixed else ''}, {time.time() - t:.1f} с")
            if self.cfg["trailing_space"] and not text.endswith("\n"):
                text += " "
            self.typist.type(text)
        except Exception as e:  # noqa: BLE001
            log(f"! ошибка распознавания: {type(e).__name__}: {e}")
            beep("error")
        finally:
            with self.lock:
                self._set_state("idle")

    def quit(self):
        log("выход")
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        PID_PATH.unlink(missing_ok=True)
        os._exit(0)

    def _setup(self):
        from pynput import keyboard

        try:
            self.hotkeys = keyboard.GlobalHotKeys({self.cfg["hotkey"]: self.toggle, "<esc>": self.cancel})
            self.hotkeys.start()
            log(f"[READY] готов: {self.cfg['hotkey']} — диктовка, Esc — отмена, Ctrl+C — выход.")
        except Exception as e:  # noqa: BLE001
            log(f"[READY] глобальная клавиша не заработала ({e}). Переключай командой: python python/dictate.py --toggle")
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            log("Wayland: глобальные клавиши через pynput не работают. Назначь в настройках рабочего стола "
                "сочетание на команду «python python/dictate.py --toggle».")
        if self.tray:
            try:  # Windows прячет новые значки за стрелкой — скажем, что мы работаем
                self.tray.icon.notify(f"Готов. {self.cfg['hotkey']} — диктовка, Esc — отмена.", "F5Voice")
            except Exception:  # noqa: BLE001
                pass

    def run(self):
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
        if hasattr(signal, "SIGUSR1"):  # только из главного потока
            signal.signal(signal.SIGUSR1, lambda *_: self.toggle())
            signal.signal(signal.SIGUSR2, lambda *_: self.cancel())
        if self.cfg.get("tray", True):
            try:
                self.tray = Tray(self)
            except Exception as e:  # noqa: BLE001
                log(f"без значка в трее ({type(e).__name__}: {e})")
                self.tray = None
        try:
            if self.tray:
                self.tray.run(self._setup)  # pystray занимает главный поток
            else:
                self._setup()
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.quit()


# ---------------------------------------------------------------- команды запуска

def send_signal(name):
    """--toggle / --cancel: сообщить работающему экземпляру."""
    if IS_WINDOWS:
        print("на Windows используй горячую клавишу или значок в трее")
        return 1
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8"))
        os.kill(pid, getattr(signal, name))
        return 0
    except (FileNotFoundError, ValueError, ProcessLookupError):
        print("F5Voice не запущен: python python/dictate.py --log &")
        return 1


def check(cfg):
    """Диагностика в консоли: окружение, микрофон, загрузка модели, пробное распознавание."""
    import faster_whisper
    import ctranslate2

    print(f"Python {platform.python_version()}, {platform.platform()}, {os.cpu_count()} ядер")
    print(f"faster-whisper {faster_whisper.__version__}, ctranslate2 {ctranslate2.__version__}, "
          f"CUDA-устройств: {ctranslate2.get_cuda_device_count()}, "
          f"типы вычислений CPU: {', '.join(sorted(ctranslate2.get_supported_compute_types('cpu')))}")
    try:
        rec = Recorder(cfg["input_device"])
        print(f"микрофон: {rec.name} ({rec.rate} Гц)")
    except Exception as e:  # noqa: BLE001
        print(f"! микрофон недоступен: {type(e).__name__}: {e}")
    t = time.time()
    r = Recognizer(cfg)
    print(f"модель загружена за {time.time() - t:.1f} с")
    audio = np.random.default_rng(0).normal(0, 0.02, RATE * 2).astype(np.float32)
    t = time.time()
    text, lang, scores, _fixed = r.recognize(audio)
    print(f"пробное распознавание 2 с шума: {time.time() - t:.1f} с, язык {lang} {scores}, текст {text!r}")
    print("[READY] проверка пройдена")


def main():
    ap = argparse.ArgumentParser(description="F5Voice: локальная диктовка по горячей клавише")
    ap.add_argument("--file", help="распознать готовый аудиофайл и выйти")
    ap.add_argument("--list-devices", action="store_true", help="показать устройства ввода")
    ap.add_argument("--model", help="переопределить модель из настроек")
    ap.add_argument("--device", help="cpu | cuda | auto")
    ap.add_argument("--log", action="store_true", help="писать вывод в ~/.f5voice/f5voice.log (для автозапуска)")
    ap.add_argument("--toggle", action="store_true", help="начать/закончить запись в работающем экземпляре")
    ap.add_argument("--cancel", action="store_true", help="отменить запись в работающем экземпляре")
    ap.add_argument("--no-tray", action="store_true", help="без значка в области уведомлений")
    ap.add_argument("--check", action="store_true", help="проверить окружение, микрофон и модель в консоли")
    args = ap.parse_args()

    if args.toggle:
        sys.exit(send_signal("SIGUSR1"))
    if args.cancel:
        sys.exit(send_signal("SIGUSR2"))

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    if args.log or sys.stdout is None or sys.stderr is None:  # pythonw без консоли
        HOME.mkdir(parents=True, exist_ok=True)
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = f

    cfg = load_config()
    if args.model:
        cfg["model"] = args.model
    if args.device:
        cfg["device"] = args.device
    if args.no_tray:
        cfg["tray"] = False

    if args.check:
        check(cfg)
        return

    if args.file:
        rec = Recognizer(cfg)
        audio = load_audio(args.file)
        quiet, dur, speech = is_silence(audio)
        if quiet:
            print(f"тишина (запись {dur} с, речи {speech} с)")
            return
        t = time.time()
        text, lang, scores, fixed = rec.recognize(audio)
        print(f"[{lang} {scores}, исправлено {fixed}, {time.time() - t:.1f} с]\n{text}")
        return

    App(cfg).run()


if __name__ == "__main__":
    main()
