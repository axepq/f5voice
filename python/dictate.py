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
import collections
import ctypes
import glob
import json
import math
import os
import queue
import platform
import signal
import subprocess
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.audio_io import load_audio, resample  # noqa: E402
from common.segments import assemble  # noqa: E402
from common.textproc import DEFAULT_PROMPT, RU_HINT, finalize  # noqa: E402
from common.vad import is_silence  # noqa: E402

RATE = 16000
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")              # классическая загрузка вместо hf_xet
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
HOME = Path(os.environ.get("F5VOICE_HOME") or Path.home() / ".f5voice")
CONFIG_PATH = HOME / "config.json"
LAST_WAV = HOME / "last.wav"
LOG_PATH = HOME / "f5voice.log"
PID_PATH = HOME / "dictate.pid"
IS_WINDOWS = platform.system() == "Windows"

DEFAULTS = {
    "hotkey": "<ctrl>+<alt>+<space>",   # можно и «ctrl+alt+space» или «F5»: имена приводятся к формату pynput
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
    "hud": True,                       # плашка внизу экрана: запись, уровень, распознавание, результат
    "style": "glass",                  # glass | metal | light | dark — вид плашки (стекло через DWM на Windows 11)
    "cpu_threads": 0,                  # потоков для CTranslate2 на процессоре, 0 — по числу ядер
    "beam_size": 0,                    # ширина поиска, 0 — авто: 1 на процессоре (быстро), 5 на видеокарте
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


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log(f"! не смог записать {CONFIG_PATH}: {e}")


def _enable_pip_cuda_libs():
    """Подхватывает cuBLAS/cuDNN из pip-пакетов nvidia-cublas-cu12 и nvidia-cudnn-cu12, если они стоят.

    Windows: каталоги с DLL добавляются в поиск. Linux: библиотеки нужны в LD_LIBRARY_PATH
    до старта процесса, поэтому процесс перезапускает себя с нужной переменной.
    """
    import importlib.util

    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return
    if not spec or not spec.submodule_search_locations:
        return
    dirs = []
    for base in spec.submodule_search_locations:
        for sub in ("cublas", "cudnn"):
            for d in ("bin", "lib"):
                path = Path(base) / sub / d
                if path.is_dir():
                    dirs.append(str(path))
    if not dirs:
        return
    if IS_WINDOWS:
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except (AttributeError, OSError):
                pass
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
    elif platform.system() == "Linux" and not os.environ.get("F5VOICE_LD_SET"):
        current = os.environ.get("LD_LIBRARY_PATH", "")
        if any(d not in current for d in dirs):
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(dirs + ([current] if current else []))
            os.environ["F5VOICE_LD_SET"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)


def probe_backend(model, device, compute_type):
    """Дочерний процесс: загрузить модель и прогнать секунду тишины. Падение — только здесь."""
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetErrorMode(0x8003)  # без окна «программа перестала работать»
        except Exception:  # noqa: BLE001
            pass
    if device == "cuda":
        _enable_pip_cuda_libs()
    from faster_whisper import WhisperModel

    m = WhisperModel(model, device=device, compute_type=compute_type)
    segments, _ = m.transcribe(np.zeros(RATE, dtype=np.float32), language="ru")
    list(segments)
    print(f"{device} {compute_type} ok")


def _probe(model, device, compute_type, timeout=900):
    """(получилось?, последняя строка вывода или код)."""
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--probe", model,
                            "--probe-device", device, "--compute-type", compute_type],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "не уложилась в 15 минут"
    if r.returncode == 0:
        return True, ""
    lines = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip()]
    code = r.returncode & 0xFFFFFFFF if r.returncode < 0 else r.returncode
    detail = lines[-1] if lines else ""
    if code == 0xC0000005:
        detail = (detail + " " if detail else "") + "крах 0xC0000005 (access violation)"
    elif code == 0xC000001D:
        detail = (detail + " " if detail else "") + "крах 0xC000001D: процессору не хватает инструкций (нужен AVX2)"
    return False, detail or f"код {r.returncode}"


def model_files(model):
    """(репозиторий, каталог snapshot в кэше или None)."""
    from download_model import resolve_repo

    repo = resolve_repo(model)
    root = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface") / "hub")
    if os.environ.get("HF_HUB_CACHE") is None and os.environ.get("HF_HOME"):
        root = Path(os.environ["HF_HOME"]) / "hub"
    snaps = root / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not snaps.is_dir():
        return repo, None
    dirs = sorted(snaps.iterdir(), key=lambda d: d.stat().st_mtime)
    return repo, (dirs[-1] if dirs else None)


def verify_model_files(model):
    """Сверяет размеры файлов модели с сервером. (всё ли на месте, описание)."""
    repo, snap = model_files(model)
    if snap is None:
        return False, "файлы модели не скачаны"
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
        remote = {s.rfilename: s.size for s in info.siblings if s.size}
    except Exception as e:  # noqa: BLE001
        sizes = {f.name: f.stat().st_size for f in snap.iterdir() if f.is_file()}
        return "model.bin" in sizes and sizes["model.bin"] > 1_000_000, f"сервер недоступен ({type(e).__name__}), локально: {sizes}"
    bad = []
    for name, size in remote.items():
        if name not in ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json") and not name.startswith("vocabulary"):
            continue
        local = snap / name
        if not local.exists():
            bad.append(f"{name}: нет")
        elif local.stat().st_size != size:
            bad.append(f"{name}: {local.stat().st_size} байт вместо {size}")
    return (not bad), (", ".join(bad) if bad else f"все файлы совпадают с сервером ({snap})")


def redownload_model(model):
    """Удаляет кэш модели и качает заново классическим способом."""
    import shutil

    from download_model import FILES, resolve_repo
    from huggingface_hub import snapshot_download

    repo = resolve_repo(model)
    _, snap = model_files(model)
    if snap is not None:
        shutil.rmtree(snap.parent.parent, ignore_errors=True)
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    snapshot_download(repo, allow_patterns=FILES)


def resolve_backend(cfg):
    """Возвращает (device, compute_type), на которых модель точно загружается.

    Всё рискованное — в дочерних процессах: CTranslate2 на Windows при проблемах не
    бросает исключение, а роняет процесс (0xC0000005). Результат записывается в
    config.json, чтобы не проверять при каждом запуске; вернуть проверку —
    "device": "auto" или удалить "backend_checked".
    """
    model = cfg["model"]
    want_dev = str(cfg.get("device") or "auto").lower()
    want_ct = str(cfg.get("compute_type") or "auto").lower()
    checked = cfg.get("backend_checked") or ""
    if checked == f"{model}|{want_dev}|{want_ct}" and want_dev != "auto" and want_ct != "auto":
        return want_dev, want_ct

    def remember(dev, ct):
        cfg["device"], cfg["compute_type"], cfg["backend_checked"] = dev, ct, f"{model}|{dev}|{ct}"
        save_config(cfg)
        return dev, ct

    if want_dev in ("auto", "cuda"):
        log("проверяю CUDA в отдельном процессе…")
        ok, detail = _probe(model, "cuda", want_ct)
        if ok:
            log("CUDA работает — распознаю на видеокарте")
            return remember("cuda", want_ct)
        log(f"CUDA не заработала ({detail}) — работаю на процессоре. Для видеокарты NVIDIA см. README: "
            "нужны cuBLAS и cuDNN для CUDA 12.")
        cfg["device_note"] = "auto → cpu: CUDA не прошла проверку. Поставь \"auto\", чтобы проверить снова."

    candidates = [want_ct] if want_ct != "auto" else ["int8", "float32"]
    fallback_versions = ["4.5.0", "4.4.0"]  # старые сборки CTranslate2 работают на процессорах без AVX2
    for attempt in range(1 + len(fallback_versions)):
        for ct in candidates:
            log(f"проверяю загрузку модели на процессоре ({ct})…")
            ok, detail = _probe(model, "cpu", ct)
            if ok:
                return remember("cpu", ct)
            log(f"не загрузилась ({ct}): {detail}")
        if attempt == len(fallback_versions):
            break
        # Модель не грузится ни так, ни так: либо файлы, либо сама библиотека.
        ok_files, what = verify_model_files(model)
        log(f"файлы модели: {what}")
        ok_tiny, detail = _probe("tiny", "cpu", "float32")
        if not ok_tiny:
            version = fallback_versions[attempt]
            if "ctranslate2" in sys.modules:
                log("! ctranslate2 уже загружен в этот процесс — после замены версии перезапусти F5Voice")
            log(f"даже крошечная модель не грузится ({detail}) — ставлю CTranslate2 {version} "
                f"(и setuptools<80: старым версиям нужен pkg_resources)")
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", f"ctranslate2=={version}", "setuptools<80"],
                           check=False)  # в setuptools 80+ pkg_resources убран
        elif not ok_files:
            log("крошечная модель работает — большая, похоже, повреждена, скачиваю заново")
            redownload_model(model)
        else:
            log("крошечная модель работает и файлы целы — возможно, не хватает памяти; пробую ещё раз")
    raise RuntimeError(f"не удалось загрузить модель {model} ни на CUDA, ни на процессоре — см. лог выше")


_KEY_ALIASES = {"control": "ctrl", "option": "alt", "opt": "alt", "win": "cmd", "windows": "cmd",
                "super": "cmd", "command": "cmd", "escape": "esc", "return": "enter", "spacebar": "space"}


def normalize_hotkey(spec):
    """«ctrl+alt+space», «<ctrl>+<alt>+space», «F5» → формат pynput: <ctrl>+<alt>+<space>, <f5>.

    pynput пишет специальные клавиши в угловых скобках, а буквы — как есть; голое
    «space» он понимает как пять букв и падает.
    """
    parts = []
    for raw in str(spec).split("+"):
        name = raw.strip().strip("<>").lower()
        if not name:
            continue
        name = _KEY_ALIASES.get(name, name)
        parts.append(name if len(name) == 1 else f"<{name}>")
    return "+".join(parts)


_VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "insert": 0x2D,
       "delete": 0x2E, "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22, "pause": 0x13,
       "scroll_lock": 0x91, "print_screen": 0x2C, "caps_lock": 0x14, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def win_hotkey_spec(normalized):
    """«<ctrl>+<alt>+<space>» → (модификаторы, виртуальный код) для RegisterHotKey, иначе None."""
    mods, vk = 0x4000, None  # MOD_NOREPEAT
    for part in normalized.split("+"):
        name = part.strip("<>")
        if name == "ctrl":
            mods |= 0x0002
        elif name == "alt":
            mods |= 0x0001
        elif name == "shift":
            mods |= 0x0004
        elif name == "cmd":
            mods |= 0x0008
        elif name in _VK:
            vk = _VK[name]
        elif name[:1] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            vk = 0x70 + int(name[1:]) - 1
        elif len(name) == 1 and name.isalnum():
            vk = ord(name.upper())
        else:
            return None
    return (mods, vk) if vk is not None else None


class WinHotkey(threading.Thread):
    """Глобальное сочетание через RegisterHotKey — родной способ Windows, надёжнее хуков pynput."""

    def __init__(self, mods, vk, on_press):
        super().__init__(daemon=True)
        self.mods, self.vk, self.on_press = mods, vk, on_press
        self.ready = threading.Event()
        self.ok = False
        self.error = ""

    def run(self):
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, self.mods, self.vk):
            self.error = f"RegisterHotKey: код {ctypes.GetLastError()} (сочетание занято другой программой?)"
            self.ready.set()
            return
        self.ok = True
        self.ready.set()
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                try:
                    self.on_press()
                except Exception as e:  # noqa: BLE001
                    log(f"! обработчик клавиши: {e}")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


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
        self.langs = tuple(x.strip() for x in cfg["languages"].split(",") if x.strip()) or ("ru",)
        self.alt_min = float(cfg["alt_language_min_prob"])
        self.prompt = cfg["prompt"] or DEFAULT_PROMPT
        # Сначала выбираем рабочий бэкенд (может переустановить ctranslate2), и только потом
        # импортируем библиотеку: загруженную DLL pip заменить не может, старая версия упала бы.
        device, compute_type = resolve_backend(cfg)
        if device == "cuda":
            _enable_pip_cuda_libs()
        from faster_whisper import WhisperModel

        threads = int(cfg.get("cpu_threads") or 0) or max(4, min(16, (os.cpu_count() or 8) // 2))
        self.beam = int(cfg.get("beam_size") or 0) or (5 if device == "cuda" else 1)
        t = time.time()
        log(f"загружаю модель {cfg['model']} ({device}, {compute_type}, потоков {threads}, лучей {self.beam})…")
        self.model = WhisperModel(cfg["model"], device=device, compute_type=compute_type, cpu_threads=threads)
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

    def run(self, audio, lang, prompt=None, temperature=(0.0, 0.2, 0.4, 0.6), with_info=False):
        segments, info = self.model.transcribe(
            audio,
            language=lang,
            task="transcribe",
            initial_prompt=prompt or self.prompt,
            condition_on_previous_text=True,
            temperature=list(temperature) if isinstance(temperature, tuple) else temperature,
            beam_size=self.beam,
            vad_filter=False,
        )
        segs = [(s.start, s.end, s.text, {"compression_ratio": s.compression_ratio,
                                          "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob})
                for s in segments]
        return (segs, info) if with_info else segs

    def recognize(self, audio):
        # Один проход: язык модель определяет по тому же прогону энкодера, что и распознаёт
        # (отдельный detect стоил ещё один проход — на процессоре это лишние секунды).
        segs, info = self.run(audio, None, with_info=True)
        primary = self.langs[0]
        lang = info.language
        probs = dict(info.all_language_probs or [])
        scores = {l: round(float(probs.get(l, 0.0)), 3) for l in self.langs}
        if lang != primary and (lang not in self.langs or scores.get(lang, 0.0) < self.alt_min):
            lang = primary  # чужой или неуверенный язык — распознаём заново на основном
            segs = self.run(audio, primary)
        duration = audio.size / RATE

        def redecode(start, end, context):
            chunk = audio[int(start * RATE):int(end * RATE)]
            if chunk.size < RATE // 2:
                return None
            p = self.detect(chunk)
            if p.get(self.langs[0], 0.0) < p.get("en", 0.0):
                return None
            again = self.run(chunk, self.langs[0], prompt=(context or RU_HINT), temperature=0.0)
            return " ".join(seg[2].strip() for seg in again).strip()

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
            rms = float(np.sqrt((indata[:, 0] ** 2).mean()) + 1e-9)
            self.level = max(0.0, min(1.0, (20 * math.log10(rms) + 55) / 55))

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


# ---------------------------------------------------------------- плашка на экране

class HUD(threading.Thread):
    """Плашка внизу экрана, как на маке: точка, полоски уровня, текст. tkinter в своём потоке."""

    COLORS = {"recording": "#ff4d4d", "transcribing": "#ffb347", "ok": "#5ad36b", "error": "#ff8a5c", "info": "#dddddd"}
    STYLES = {  # фон плашки, цвет текста, тёмная ли тема для стекла DWM
        "glass": ("#16161c", "#ffffff", True),
        "metal": ("#2a2c33", "#f2f2f2", True),
        "light": ("#f2f2f5", "#111111", False),
        "dark": ("#0d0d10", "#ffffff", True),
    }

    def __init__(self, level_fn, style="glass", on_settings_saved=None, cfg=None):
        super().__init__(daemon=True)
        self.level_fn = level_fn
        self.style = style if style in self.STYLES else "glass"
        self.on_settings_saved = on_settings_saved
        self.cfg = cfg or {}
        self.q = queue.Queue()
        self.state = None
        self.text = ""
        self.hide_at = None
        self.phase = 0.0
        self.hist = collections.deque([0.0] * 10, maxlen=10)
        self.start()

    def show(self, state, text, ttl=None):
        self.q.put(("show", state, text, ttl))

    def hide(self):
        self.q.put(("hide", None, None, None))

    def open_settings(self):
        self.q.put(("settings", None, None, None))

    def _apply_windows_glass(self, root):
        """Windows 11: акриловое стекло и скруглённые углы через DWM; иначе просто тёмная панель."""
        if not IS_WINDOWS:
            return
        try:
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            dwm = ctypes.windll.dwmapi
            corner = ctypes.c_int(2)  # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
            if self.style != "dark":
                dark = ctypes.c_int(1 if self.STYLES[self.style][2] else 0)
                dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
                backdrop = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW — акрил
                if dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4) == 0:
                    root.attributes("-transparentcolor", "#010203")
                    self.canvas.configure(bg="#010203")
                    self.glass = True
        except Exception as e:  # noqa: BLE001
            log(f"стекло DWM недоступно ({e}) — обычная панель")

    def run(self):
        try:
            import tkinter as tk
        except ImportError:
            log("плашка недоступна: нет tkinter (Linux: sudo apt install python3-tk)")
            return
        try:
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-alpha", 0.93)
            except tk.TclError:
                pass
            self.W, self.H = 460, 56
            bg, self.fg, _ = self.STYLES[self.style]
            self.glass = False
            self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg=bg, highlightthickness=0)
            self.canvas.pack()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{sh - self.H - 80}")
            self.root = root
            self.tk = tk
            self._apply_windows_glass(root)
            root.after(40, self._tick)
            root.mainloop()
        except Exception as e:  # noqa: BLE001
            log(f"плашка отключена: {type(e).__name__}: {e}")

    def _tick(self):
        try:
            while True:
                cmd, state, text, ttl = self.q.get_nowait()
                if cmd == "show":
                    self.state, self.text = state, text
                    self.hide_at = time.time() + ttl if ttl else None
                    if state == "recording":
                        self.hist.extend([0.0] * 10)
                    self.root.deiconify()
                    self.root.lift()
                elif cmd == "settings":
                    self._settings_window()
                else:
                    self.state = None
                    self.root.withdraw()
        except queue.Empty:
            pass
        if self.hide_at and time.time() > self.hide_at:
            self.state, self.hide_at = None, None
            self.root.withdraw()
        if self.state:
            self._draw()
        self.root.after(40, self._tick)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        self.phase += 0.18
        color = self.COLORS.get(self.state, "#ffffff")
        cy = self.H / 2
        if self.style == "metal":  # металлический блик поверх стекла
            for i in range(0, self.W, 8):
                shade = int(52 + 30 * math.sin(i / 90 + self.phase * 0.05))
                c.create_rectangle(i, 0, i + 8, self.H, fill=f"#{shade:02x}{shade + 2:02x}{shade + 8:02x}", outline="")
            c.create_rectangle(0, 0, self.W, 2, fill="#8a8d99", outline="")
        r = 7 + (2.5 * abs(math.sin(self.phase * 0.6)) if self.state == "recording" else 0)
        c.create_oval(22 - r, cy - r, 22 + r, cy + r, fill=color, outline="")
        x = 46
        if self.state in ("recording", "transcribing"):
            if self.state == "recording":
                self.hist.append(float(self.level_fn() or 0.0))
            for i in range(10):
                v = self.hist[i] if self.state == "recording" else 0.5 + 0.5 * math.sin(self.phase - i * 0.65)
                h = 3 + 20 * max(0.0, min(1.0, v))
                c.create_rectangle(x + i * 6, cy - h / 2, x + i * 6 + 3, cy + h / 2, fill=self.fg, outline="")
            x += 70
        c.create_text(x, cy, text=self.text, anchor="w", fill=self.fg, font=("Segoe UI", 12))

    def _settings_window(self):
        """Окно настроек: сочетание (можно записать нажатием), стиль, языки, модель, пробел, перенос."""
        tk = self.tk if hasattr(self, "tk") else __import__("tkinter")
        from tkinter import ttk

        win = tk.Toplevel(self.root)
        win.title("F5Voice — настройки")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        cfg = self.cfg
        rows = [("Сочетание клавиш", "hotkey", None), ("Стиль плашки", "style", list(self.STYLES)),
                ("Языки (первый — основной)", "languages", None),
                ("Модель", "model", ["large-v3-turbo", "medium", "small"]),
                ("Перенос строки клавишей", "newline", ["shift+enter", "enter", "ctrl+enter"])]
        vars_ = {}
        for i, (label, key, options) in enumerate(rows):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=10, pady=4)
            v = tk.StringVar(value=str(cfg.get(key, "")))
            vars_[key] = v
            if options:
                ttk.Combobox(win, textvariable=v, values=options, width=28).grid(row=i, column=1, padx=10, pady=4)
            else:
                ttk.Entry(win, textvariable=v, width=31).grid(row=i, column=1, padx=10, pady=4)
        trailing = tk.BooleanVar(value=bool(cfg.get("trailing_space", True)))
        ttk.Checkbutton(win, text="Пробел после надиктованного", variable=trailing).grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=10)
        hint = ttk.Label(win, text="Нажмите «Записать» и сочетание на клавиатуре, например Ctrl+Alt+D", foreground="#666")
        hint.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", padx=10)

        def record():
            from pynput import keyboard

            hint.configure(text="Нажмите сочетание…")
            held = set()

            def name(k):
                if isinstance(k, keyboard.Key):
                    n = k.name.replace("_l", "").replace("_r", "").replace("cmd", "win")
                    return n
                return (getattr(k, "char", None) or "").lower() or None

            def on_press(k):
                n = name(k)
                if n in ("ctrl", "alt", "shift", "win", "alt_gr"):
                    held.add("alt" if n == "alt_gr" else n)
                    return True
                if n:
                    combo = "+".join(sorted(held, key=["ctrl", "alt", "shift", "win"].index) + [n])
                    self.root.after(0, lambda: (vars_["hotkey"].set(combo), hint.configure(text=f"Записано: {combo}")))
                    return False
                return True

            keyboard.Listener(on_press=on_press).start()

        ttk.Button(win, text="Записать", command=record).grid(row=0, column=2, padx=6)

        def save():
            for key, v in vars_.items():
                cfg[key] = v.get().strip()
            cfg["trailing_space"] = bool(trailing.get())
            if cfg.get("model") != self.cfg.get("model"):
                cfg.pop("backend_checked", None)
            save_config(cfg)
            win.destroy()
            if self.on_settings_saved:
                self.on_settings_saved()

        ttk.Button(win, text="Сохранить и перезапустить", command=save).grid(row=len(rows) + 2, column=0, columnspan=3, pady=10)


def pretty_hotkey(normalized):
    return "+".join(part.strip("<>").capitalize() for part in normalized.split("+"))


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
            pystray.MenuItem("Настройки…", lambda: app.hud.open_settings() if app.hud else open_path(CONFIG_PATH)),
            pystray.MenuItem("Файл настроек (config.json)", lambda: open_path(CONFIG_PATH)),
            pystray.MenuItem("Лог", lambda: open_path(LOG_PATH)),
            pystray.MenuItem("Выход", lambda: app.quit()),
        )
        self.icon = pystray.Icon("F5Voice", self._image("idle"), "F5Voice", menu)

    def _image(self, state):
        icon_png = Path(__file__).resolve().parent / "F5Voice.png"
        try:
            img = self.Image.open(icon_png).convert("RGBA").resize((64, 64), self.Image.LANCZOS)
        except Exception:  # noqa: BLE001
            img = self.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            self.ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=self.COLORS[state] + (255,))
        if state != "idle":  # точка состояния в углу
            d = self.ImageDraw.Draw(img)
            d.ellipse((40, 40, 62, 62), fill=self.COLORS[state] + (255,), outline=(255, 255, 255, 255), width=2)
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
        self.esc_listener = None
        self.hud = None
        log(f"Python {platform.python_version()}, {platform.platform()}, {os.cpu_count()} ядер, {platform.processor() or '?'}")
        try:
            self.recorder = Recorder(cfg["input_device"])
        except Exception as e:  # noqa: BLE001
            log(f"[FATAL] микрофон недоступен: {type(e).__name__}: {e}")
            log("  Linux: sudo apt install libportaudio2; список устройств: dictate.py --list-devices; "
                "выбрать: \"input_device\" в config.json")
            sys.exit(1)
        self.hud = HUD(lambda: self.recorder.level, cfg.get("style", "glass"), self.restart, cfg) if cfg.get("hud", True) else None
        self.hotkey_title = pretty_hotkey(normalize_hotkey(cfg["hotkey"]))
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

    def _show(self, state, text, ttl=None):
        if self.hud:
            self.hud.show(state, text, ttl)

    def toggle(self):
        with self.lock:
            if self.state == "recording":
                self._stop()
            else:  # idle или ещё распознаём предыдущее — новую запись можно начинать сразу
                self._start()

    def cancel(self):
        with self.lock:
            if self.state == "recording":
                if self.stop_timer:
                    self.stop_timer.cancel()
                self.recorder.stop()
                self._set_state("idle")
                beep("stop")
                self._show("info", "Отменено", 1.2)
                log("отменено")

    def _start(self):
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log(f"! запись не началась: {e}")
            return
        self._set_state("recording")
        beep("start")
        self._show("recording", f"Говорите…   {self.hotkey_title} — готово · Esc — отмена")
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
        self._show("transcribing", "Распознаю…")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio):
        try:
            save_wav(LAST_WAV, audio)
            quiet, dur, speech = is_silence(audio)
            if quiet:
                log(f"ничего не услышал (запись {dur} с, речи {speech} с)")
                beep("error")
                self._show("error", f"Ничего не услышал: запись {dur} с, речи {speech} с", 3)
                return
            t = time.time()
            text, lang, scores, fixed = self.recognizer.recognize(audio)
            if not text:
                log("ничего не разобрал")
                beep("error")
                self._show("error", "Модель ничего не разобрала", 3)
                return
            log(f"готово: {len(text)} символов, язык {lang} {scores}"
                f"{', исправлено ' + str(fixed) if fixed else ''}, {time.time() - t:.1f} с")
            if self.cfg["trailing_space"] and not text.endswith("\n"):
                text += " "
            self._show("ok", f"Напечатано {len(text.strip())} символов", 1.5)
            self.typist.type(text)
        except Exception as e:  # noqa: BLE001
            log(f"! ошибка распознавания: {type(e).__name__}: {e}")
            beep("error")
            self._show("error", f"Ошибка: {type(e).__name__}: {str(e)[:60]}", 6)
        finally:
            with self.lock:
                if self.state == "transcribing":  # если уже пишем новую — не сбивать
                    self._set_state("idle")

    def restart(self):
        """После смены настроек: новый экземпляр с --log, этот выходит."""
        log("перезапуск с новыми настройками")
        exe = sys.executable
        if IS_WINDOWS and exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        subprocess.Popen([exe, os.path.abspath(__file__), "--log"], cwd=str(HOME))
        self.quit()

    def quit(self):
        log("выход")
        if self.hotkeys and hasattr(self.hotkeys, "stop"):
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        PID_PATH.unlink(missing_ok=True)
        os._exit(0)

    def _setup(self):
        from pynput import keyboard

        hotkey = normalize_hotkey(self.cfg["hotkey"])
        registered = ""
        if IS_WINDOWS:
            spec = win_hotkey_spec(hotkey)
            if spec:
                wh = WinHotkey(spec[0], spec[1], self.toggle)
                wh.start()
                wh.ready.wait(3)
                if wh.ok:
                    registered = "RegisterHotKey"
                    self.hotkeys = wh
                else:
                    log(f"RegisterHotKey не удался ({wh.error}) — пробую pynput")
            try:  # Esc — через слушатель pynput, RegisterHotKey отобрал бы Esc у всех программ
                self.esc_listener = keyboard.Listener(
                    on_press=lambda k: self.cancel() if k == keyboard.Key.esc else None)
                self.esc_listener.daemon = True
                self.esc_listener.start()
            except Exception as e:  # noqa: BLE001
                log(f"Esc для отмены недоступен ({e})")
        if not registered:
            try:
                keys = {hotkey: self.toggle}
                if "<alt>" in hotkey:  # правый Alt на Windows/Linux приходит как alt_gr
                    keys[hotkey.replace("<alt>", "<alt_gr>")] = self.toggle
                if not IS_WINDOWS:
                    keys["<esc>"] = self.cancel
                self.hotkeys = keyboard.GlobalHotKeys(keys)
                self.hotkeys.start()
                registered = "pynput"
            except Exception as e:  # noqa: BLE001
                log(f"[READY] глобальная клавиша не заработала ({e}). Переключай командой: python python/dictate.py --toggle")
                return
        log(f"[READY] готов: {hotkey} — диктовка ({registered}), Esc — отмена, Ctrl+C — выход.")
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


def total_ram_gb():
    try:
        if IS_WINDOWS:
            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            s = MemStatus()
            s.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return f"{s.ullTotalPhys / 2**30:.1f} ГБ, свободно {s.ullAvailPhys / 2**30:.1f} ГБ"
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return f"{total / 2**30:.1f} ГБ"
    except Exception:  # noqa: BLE001
        return "?"


def check(cfg):
    """Диагностика в консоли: окружение, микрофон, загрузка модели, пробное распознавание."""
    from importlib.metadata import version

    print(f"Python {platform.python_version()}, {platform.platform()}, {os.cpu_count()} ядер, ОЗУ {total_ram_gb()}")
    print(f"процессор: {platform.processor() or '?'}")
    info = subprocess.run([sys.executable, "-c", "import ctranslate2 as c; print(c.get_cuda_device_count(), "
                           "','.join(sorted(c.get_supported_compute_types('cpu'))))"], capture_output=True, text=True)
    cuda, types = (info.stdout.split() + ["?", "?"])[:2] if info.returncode == 0 else ("?", "импорт ctranslate2 упал")
    print(f"faster-whisper {version('faster_whisper')}, ctranslate2 {version('ctranslate2')}, "
          f"CUDA-устройств: {cuda}, типы вычислений CPU: {types}")
    try:
        rec = Recorder(cfg["input_device"])
        print(f"микрофон: {rec.name} ({rec.rate} Гц)")
    except Exception as e:  # noqa: BLE001
        print(f"! микрофон недоступен: {type(e).__name__}: {e}")
    ok_files, what = verify_model_files(cfg["model"])
    print(f"файлы модели: {what}")
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
    ap.add_argument("--probe", metavar="MODEL", help=argparse.SUPPRESS)
    ap.add_argument("--probe-device", help=argparse.SUPPRESS)
    ap.add_argument("--compute-type", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.probe:
        probe_backend(args.probe, args.probe_device or "cpu", args.compute_type or "auto")
        return

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
