#!/usr/bin/env python3
"""Воркер распознавания F5Voice для macOS (mlx-whisper, Apple Silicon).

Держит модель в памяти и по строке из stdin (путь к WAV) отвечает одной
JSON-строкой в stdout. Сам выходит, если его не трогали F5_IDLE_SEC секунд —
приложение поднимет заново, модель грузится параллельно с записью.

Настройки приходят переменными окружения (их выставляет приложение из
~/.f5voice/config.json):
  F5_MODEL          репозиторий модели, по умолчанию mlx-community/whisper-large-v3-turbo
  F5_LANGS          языки через запятую, первый — основной (ru,en)
  F5_ALT_MIN_PROB   уверенность, с которой берём не основной язык (0.95)
  F5_PROMPT         подсказка модели (стиль, названия латиницей)
  F5_IDLE_SEC       простой до выгрузки модели (900)

Протокол:
  <- {"ready": true, "load_sec": 2.5}
  -> /Users/alex/.f5voice/last.wav
  <- {"text": "Привет", "lang": "ru", "scores": {"ru": 0.99, "en": 0.01}, "fixed": 0, "dur": 2.1, "sec": 0.8}
  <- {"text": "", "reason": "silence"}       тишина или слишком коротко
  <- {"text": "", "error": "..."}            не смог прочитать файл и т.п.
"""
import glob
import json
import os
import select
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from common.audio_io import load_audio  # noqa: E402
from common.segments import assemble  # noqa: E402
from common.textproc import DEFAULT_PROMPT, RU_HINT, finalize  # noqa: E402
from common.vad import is_silence  # noqa: E402

MODEL = os.environ.get("F5_MODEL") or "mlx-community/whisper-large-v3-turbo"
LANGS = tuple(x.strip() for x in (os.environ.get("F5_LANGS") or "ru,en").split(",") if x.strip()) or ("ru",)
ALT_MIN_PROB = float(os.environ.get("F5_ALT_MIN_PROB") or "0.95")
PROMPT = os.environ.get("F5_PROMPT") or DEFAULT_PROMPT
IDLE_SEC = float(os.environ.get("F5_IDLE_SEC") or "900")
RATE = 16000


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cache_ready(model):
    pattern = os.path.expanduser(
        "~/.cache/huggingface/hub/models--" + model.replace("/", "--") + "/snapshots/*/config.json"
    )
    return bool(glob.glob(pattern))


def main():
    if cache_ready(MODEL):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    import mlx.core as mx
    import mlx_whisper
    import numpy as np
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    dtype = mx.float16

    def detect(audio):
        """Вероятности языков из LANGS для куска звука (как в mlx_whisper.transcribe)."""
        model = ModelHolder.get_model(MODEL, dtype)
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)
        _, probs = model.detect_language(segment)
        return {lang: round(float(probs.get(lang, 0.0)), 3) for lang in LANGS}

    def pick_language(audio):
        """Основной язык — LANGS[0]; другой берём только при высокой уверенности,
        иначе whisper с чужим токеном не транскрибирует речь, а переводит её."""
        if len(LANGS) == 1:
            return LANGS[0], {}
        scores = detect(audio)
        alt = max(LANGS[1:], key=lambda l: scores[l])
        return (alt if scores[alt] >= ALT_MIN_PROB else LANGS[0]), scores

    def run(audio, lang, prompt=PROMPT, temperature=(0.0, 0.2, 0.4)):
        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=MODEL,
            language=lang,
            task="transcribe",
            initial_prompt=prompt,
            # Контекст переносится между 30-секундными окнами: без него второе окно
            # с английскими словами модель теряла целиком, а названия писала транслитом.
            condition_on_previous_text=True,
            temperature=temperature,
            fp16=True,
            verbose=None,
        )

    def recognize(audio):
        lang, scores = pick_language(audio)
        result = run(audio, lang)
        segs = [(s["start"], s["end"], s.get("text", "")) for s in result.get("segments") or []]
        duration = audio.size / RATE

        def redecode(start, end, context):
            """Сегмент латиницей при русском токене: если по звуку русский — декодируем заново."""
            chunk = audio[int(start * RATE):int(end * RATE)]
            if chunk.size < RATE // 2:
                return None
            p = detect(chunk)
            if p.get(LANGS[0], 0.0) < p.get("en", 0.0):
                return None  # действительно английский, не трогаем
            again = run(chunk, LANGS[0], prompt=(context or RU_HINT), temperature=0.0)
            return (again.get("text") or "").strip()

        may_fix = lang == LANGS[0] and scores.get("en", 0.0) < 0.6
        text, fixed = assemble(segs, duration, redecode if may_fix else None)
        return finalize(text, PROMPT), lang, scores, fixed

    t0 = time.time()
    warm = np.zeros(RATE, dtype=np.float32)
    pick_language(warm)
    run(warm, LANGS[0])  # прогрев: грузим веса и компилируем ядра
    out({"ready": True, "load_sec": round(time.time() - t0, 1), "langs": list(LANGS), "model": MODEL})

    while True:
        readable, _, _ = select.select([sys.stdin], [], [], IDLE_SEC)
        if not readable:
            out({"bye": "idle"})
            return
        line = sys.stdin.readline()
        if not line:  # EOF — приложение закрылось
            return
        path = line.strip()
        if not path:
            continue
        t1 = time.time()
        try:
            try:
                audio = load_audio(path)
            except ValueError:  # короче 1/8 секунды — случайное нажатие
                out({"text": "", "reason": "silence", "dur": 0})
                continue
            quiet, dur, speech = is_silence(audio)
            if quiet:
                out({"text": "", "reason": "silence", "dur": dur, "speech": speech})
                continue
            text, lang, scores, fixed = recognize(audio)
            out({"text": text, "lang": lang, "scores": scores, "fixed": fixed,
                 "dur": dur, "speech": speech, "sec": round(time.time() - t1, 2)})
        except Exception as e:  # noqa: BLE001
            out({"text": "", "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
