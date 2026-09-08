#!/usr/bin/env python3
"""F5Voice для Windows и Linux: горячая клавиша → запись → faster-whisper → печать текста.

Та же логика, что в маковской версии, на общем ядре (common/): выбор языка с
приоритетом русского, детектор речи, голосовые команды, чистка галлюцинаций.
Модель — faster-whisper (CTranslate2), на CPU или CUDA.

Запуск:            python other/dictate.py
Проверка на файле: python other/dictate.py --file запись.wav
Устройства ввода:  python other/dictate.py --list-devices
Настройки:         ~/.f5voice/config.json (создаётся при первом запуске)

Не проверялось на реальных Windows/Linux — автор собирал на macOS. Если что-то
падает, пришлите вывод консоли.
"""
import argparse
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np

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
}


def load_config():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            print(f"! config.json не разобран ({e}), работаю по умолчанию")
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"создал настройки: {CONFIG_PATH}")
    return cfg


def beep(kind="start"):
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.Beep(880 if kind == "start" else 660 if kind == "stop" else 330, 120)
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
        print(f"загружаю модель {cfg['model']} ({cfg['device']}, {cfg['compute_type']})…", flush=True)
        self.model = WhisperModel(cfg["model"], device=cfg["device"], compute_type=cfg["compute_type"])
        self.run(np.random.default_rng(0).normal(0, 1e-4, RATE).astype(np.float32), self.langs[0])  # прогрев тихим шумом, нули дают предупреждения numpy
        print(f"модель готова за {time.time() - t:.1f} с", flush=True)

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
        self.level = 0.0

    def start(self):
        self.frames = []

        def cb(indata, _frames, _time, status):
            self.frames.append(indata[:, 0].copy())
            self.level = float(np.sqrt((indata[:, 0] ** 2).mean()))

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
        audio = np.concatenate(self.frames)
        return resample(audio, self.rate, RATE)


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
        parts = self.newline.lower().split("+")
        held = [mods[p] for p in parts if p in mods]
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
        for chunk in text.split("\n")[:-1]:
            self.kb.type(chunk)
            self._newline()
        self.kb.type(text.split("\n")[-1])


# ---------------------------------------------------------------- приложение

class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = "idle"
        self.lock = threading.Lock()
        self.recognizer = Recognizer(cfg)
        self.recorder = Recorder(cfg["input_device"])
        self.typist = Typist(cfg)
        self.stop_timer = None
        print(f"микрофон: {self.recorder.name} ({self.recorder.rate} Гц)")

    def toggle(self):
        with self.lock:
            if self.state == "idle":
                self._start()
            elif self.state == "recording":
                self._stop()
            else:
                print("… ещё распознаю предыдущее")

    def cancel(self):
        with self.lock:
            if self.state == "recording":
                if self.stop_timer:
                    self.stop_timer.cancel()
                self.recorder.stop()
                self.state = "idle"
                beep("stop")
                print("отменено")

    def _start(self):
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            print(f"! запись не началась: {e}")
            return
        self.state = "recording"
        beep("start")
        print(f"● говорите… ({self.cfg['hotkey']} — готово, Esc — отмена)", flush=True)
        self.stop_timer = threading.Timer(float(self.cfg["max_record_seconds"]), self.toggle)
        self.stop_timer.daemon = True
        self.stop_timer.start()

    def _stop(self):
        if self.stop_timer:
            self.stop_timer.cancel()
        audio = self.recorder.stop()
        self.state = "transcribing"
        beep("stop")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio):
        try:
            save_wav(LAST_WAV, audio)
            quiet, dur, speech = is_silence(audio)
            if quiet:
                print(f"ничего не услышал (запись {dur} с, речи {speech} с)")
                beep("error")
                return
            t = time.time()
            text, lang, scores, fixed = self.recognizer.recognize(audio)
            if not text:
                print("ничего не разобрал")
                beep("error")
                return
            print(f"[{lang} {scores}{', исправлено ' + str(fixed) if fixed else ''}, {time.time() - t:.1f} с] {text}")
            if self.cfg["trailing_space"] and not text.endswith("\n"):
                text += " "
            self.typist.type(text)
        except Exception as e:  # noqa: BLE001
            print(f"! ошибка распознавания: {type(e).__name__}: {e}")
            beep("error")
        finally:
            with self.lock:
                self.state = "idle"

    def run(self):
        from pynput import keyboard

        hotkeys = keyboard.GlobalHotKeys({self.cfg["hotkey"]: self.toggle, "<esc>": self.cancel})
        hotkeys.start()
        print(f"готов: {self.cfg['hotkey']} — диктовка. Ctrl+C — выход.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nвыход")
        finally:
            hotkeys.stop()


def main():
    ap = argparse.ArgumentParser(description="F5Voice: локальная диктовка по горячей клавише")
    ap.add_argument("--file", help="распознать готовый аудиофайл и выйти")
    ap.add_argument("--list-devices", action="store_true", help="показать устройства ввода")
    ap.add_argument("--model", help="переопределить модель из настроек")
    ap.add_argument("--device", help="cpu | cuda | auto")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    cfg = load_config()
    if args.model:
        cfg["model"] = args.model
    if args.device:
        cfg["device"] = args.device

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
