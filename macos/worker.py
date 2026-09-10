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
  <- {"status": "rewrite", "command": "официальный стиль"}   только если в хвосте команда
  <- {"text": "Привет", "lang": "ru", "scores": {"ru": 0.99, "en": 0.01}, "fixed": 0, "dur": 2.1, "sec": 0.8,
      "rewrite": "official", "rewrite_sec": 3.2}            rewrite_* — только при команде
  <- {"text": "…исходник без команды…", "rewrite": "official", "rewrite_error": "…"}
  <- {"text": "", "reason": "silence"}       тишина или слишком коротко
  <- {"text": "", "error": "..."}            не смог прочитать файл и т.п.

Настройки переписывания (rewrite_model, rewrite_idle_minutes, rewrite_keyword, rewrite_commands)
воркер читает из ~/.f5voice/config.json сам, при каждом запросе — правки без перезапуска.
"rewrite_model": "" выключает переписывание.
"""
import glob
import json
import os
from pathlib import Path
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
from common import history  # noqa: E402
from common import rewrite  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Rewriter  # noqa: E402

HOME_DIR = Path(os.environ.get("F5VOICE_HOME") or Path.home() / ".f5voice")

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


def log(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    sys.stderr.flush()


def rewrite_settings():
    """Настройки переписывания из config.json; файл маленький, читаем на каждый запрос."""
    try:
        with open(HOME_DIR / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    model = cfg.get("rewrite_model", rewrite.DEFAULT_MODEL)
    minutes = cfg.get("rewrite_idle_minutes", rewrite.DEFAULT_IDLE_MINUTES)
    try:
        idle_sec = max(5.0, float(minutes) * 60)
    except (TypeError, ValueError):
        idle_sec = rewrite.DEFAULT_IDLE_MINUTES * 60
    keyword = cfg.get("rewrite_keyword", rewrite.DEFAULT_KEYWORD)
    return {"model": model if isinstance(model, str) else "", "idle_sec": idle_sec,
            "keyword": keyword.strip() if isinstance(keyword, str) else "",
            "commands": rewrite.merge_commands(cfg.get("rewrite_commands"))}


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

    def run(audio, lang, prompt=PROMPT, temperature=(0.0, 0.2, 0.4, 0.6)):  # 0.6 сбрасывает контекст при петле
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
        segs = [(s["start"], s["end"], s.get("text", ""),
                 {k: s.get(k) for k in ("compression_ratio", "no_speech_prob", "avg_logprob")})
                for s in result.get("segments") or []]
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

    llm = Rewriter(log)
    last_use = time.time()
    while True:
        timeout = IDLE_SEC - (time.time() - last_use)
        if llm.loaded:
            timeout = min(timeout, llm.idle_left())
        readable, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout))
        if not readable:
            if llm.loaded and llm.idle_left() <= 0:
                llm.unload()
            if time.time() - last_use >= IDLE_SEC:
                out({"bye": "idle"})
                return
            continue
        line = sys.stdin.readline()
        if not line:  # EOF — приложение закрылось
            return
        path = line.strip()
        if not path:
            continue
        last_use = t1 = time.time()
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
            extra = {}
            rw = rewrite_settings()
            body, cmd = rewrite.split_command(text, rw["commands"], rw["keyword"]) if rw["model"] else (text, None)
            if cmd:
                out({"status": "rewrite", "command": cmd["title"]})
                log(f"переписываю ({cmd['key']}: {cmd['title']}) ← {body[:300]}")
                t2 = time.time()
                try:
                    raw = llm.rewrite(rw["model"], rewrite.build_messages(body, cmd), rw["idle_sec"])
                    result = rewrite.humanize(raw)
                    log(f"модель ответила за {time.time() - t2:.1f} с → {result[:300]}")
                    if not rewrite.accept(body, result, cmd):
                        raise ValueError("ответ модели пустой или слишком короткий")
                    text = result
                    extra = {"rewrite": cmd["key"], "rewrite_sec": round(time.time() - t2, 2)}
                except Exception as e:  # noqa: BLE001 — текст терять нельзя, вставляем исходник
                    text = body
                    extra = {"rewrite": cmd["key"], "rewrite_error": f"{type(e).__name__}: {e}"}
                last_use = time.time()
            history.add(HOME_DIR / "history.json", text, lang, time.time() - t1)
            out({"text": text, "lang": lang, "scores": scores, "fixed": fixed,
                 "dur": dur, "speech": speech, "sec": round(time.time() - t1, 2), **extra})
        except Exception as e:  # noqa: BLE001
            out({"text": "", "error": f"{type(e).__name__}: {e}"})

if __name__ == "__main__":
    main()
