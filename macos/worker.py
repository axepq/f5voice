#!/usr/bin/env python3
"""Воркер распознавания F5Voice для macOS (облачный STT + локальное переписывание).

Держит модель в памяти и по строке из stdin (путь к WAV) отвечает одной
JSON-строкой в stdout. Сам выходит, если его не трогали F5_IDLE_SEC секунд —
приложение поднимет заново, модель грузится параллельно с записью.

Настройки приходят переменными окружения (их выставляет приложение из
~/.f5voice/config.json):
  F5_MODEL          репозиторий модели, по умолчанию mlx-community/whisper-large-v3-turbo
  F5_LANGS          языки через запятую, первый — основной (ru,en)
  F5_ALT_MIN_PROB   уверенность, с которой берём не основной язык (0.95)
  F5_PROMPT         подсказка модели (стиль, названия латиницей)
  F5_IDLE_SEC       простой до выгрузки модели (900); 0 — не выгружать

Протокол:
  <- {"ready": true, "load_sec": 2.5}
  -> /Users/alex/.f5voice/last.wav
  -> (пустая строка)                                          пинг: не выгружать модель, идёт запись
  -> {"rewrite": {"text": "…", "trigger": "официальный стиль"}} переписать готовый текст (кнопка «Проверить»)
  <- {"status": "download", "command": "mlx-community/…"}     качаю модель переписывания/ответов, один раз
  <- {"status": "rewrite", "command": "официальный стиль"}   только если в хвосте команда
  <- {"status": "answer", "command": "что такое DNS"}         фраза начиналась с «ответь»
  <- {"text": "", "question": "что такое DNS", "answer": "…", "answer_sec": 6.1}
  <- {"text": "", "question": "…", "answer_error": "…"}
  <- {"status": "variants", "command": "официально"}            просили «предложи варианты»
  <- {"text": "", "variants": ["…","…","…"], "style": "…", "body": "исходник"}
  <- {"text": "", "variants_error": "…"}
  <- {"text": "Привет", "lang": "ru", "scores": {"ru": 0.99, "en": 0.01}, "fixed": 0, "dur": 2.1, "sec": 0.8,
      "rewrite": "official", "rewrite_sec": 3.2}            rewrite_* — только при команде
  <- {"text": "…исходник без команды…", "rewrite": "official", "rewrite_error": "…"}
  <- {"text": "", "reason": "silence"}       тишина или слишком коротко
  <- {"text": "", "error": "..."}            не смог прочитать файл и т.п.

Настройки переписывания (rewrite_model, rewrite_idle_minutes, rewrite_keyword, rewrite_commands)
и ответов (answer_model, answer_keyword, answer_thinking) воркер читает из ~/.f5voice/config.json
сам, при каждом запросе — правки без перезапуска. "rewrite_model": "" выключает переписывание,
"answer_model": "" — ответы.
"""
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
from common.textproc import DEFAULT_PROMPT, finalize, fix_command_endings  # noqa: E402
from common.vad import is_silence  # noqa: E402
from common import history  # noqa: E402
from common import rewrite  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Rewriter  # noqa: E402
import apillm  # noqa: E402
import sttapi  # noqa: E402

HOME_DIR = Path(os.environ.get("F5VOICE_HOME") or Path.home() / ".f5voice")

MODEL = os.environ.get("F5_MODEL") or "mlx-community/whisper-large-v3-turbo"
LANGS = tuple(x.strip() for x in (os.environ.get("F5_LANGS") or "ru,en").split(",") if x.strip()) or ("ru",)
ALT_MIN_PROB = float(os.environ.get("F5_ALT_MIN_PROB") or "0.95")
PROMPT = os.environ.get("F5_PROMPT") or DEFAULT_PROMPT
IDLE_SEC = float(os.environ.get("F5_IDLE_SEC") or "900")
if IDLE_SEC <= 0:
    IDLE_SEC = float("inf")  # 0 в настройках — «не выгружать», а не «выгрузить сразу»
RATE = 16000


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    sys.stderr.flush()


def _personas(user):
    """Встроенные манеры ответа плюс свои из config.json (некорректные записи пропускаем)."""
    result = dict(rewrite.ANSWER_PERSONAS)
    if isinstance(user, dict):
        for k, v in user.items():
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                result[k.strip().lower()] = v.strip()
    return result


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
    sel_keyword = cfg.get("selection_keyword", rewrite.DEFAULT_SELECTION_KEYWORD)
    answer_model = cfg.get("answer_model", "")  # по умолчанию ответы выключены; код цел, включается в настройках
    answer_keyword = cfg.get("answer_keyword", rewrite.ANSWER_KEYWORD)
    return {"model": model if isinstance(model, str) else "", "idle_sec": idle_sec,
            "keyword": keyword.strip() if isinstance(keyword, str) else "",
            "selection_keyword": sel_keyword.strip() if isinstance(sel_keyword, str) else rewrite.DEFAULT_SELECTION_KEYWORD,
            "commands": rewrite.merge_commands(cfg.get("rewrite_commands")),
            "answer_model": answer_model if isinstance(answer_model, str) else "",
            "answer_keyword": answer_keyword.strip() if isinstance(answer_keyword, str) else "",
            "answer_thinking": bool(cfg.get("answer_thinking", False)),
            "personas": _personas(cfg.get("answer_personas")),
            # облачный движок переписывания: если задан ключ, переписываем через API, а не локально
            "api_url": str(cfg.get("rewrite_api_url") or ""),
            "api_key": str(cfg.get("rewrite_api_key") or ""),
            "api_model": str(cfg.get("rewrite_api_model") or ""),
            "enabled": bool(cfg.get("rewrite_enabled", True))}


def fix_cmd_enabled():
    """Флаг «править окончания команд» из config.json (по умолчанию выкл)."""
    try:
        with open(HOME_DIR / "config.json", encoding="utf-8") as f:
            return bool(json.load(f).get("fix_command_endings", False))
    except (OSError, ValueError):
        return False


def stt_settings():
    """Настройки облачного распознавания речи из config.json (по умолчанию выключено)."""
    try:
        with open(HOME_DIR / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    key = str(cfg.get("stt_api_key") or "")
    return {"enabled": bool(cfg.get("stt_enabled", False)) and bool(key),
            "provider": str(cfg.get("stt_provider") or "elevenlabs"),
            "key": key, "model": str(cfg.get("stt_model") or ""),
            "language": str(cfg.get("languages") or "")}


def use_api(rw):
    return bool(rw["api_url"] and rw["api_key"] and rw["api_model"])


def on_download(name):
    out({"status": "download", "command": name})
    log(f"скачиваю модель {name} — это один раз")


def do_rewrite(llm, rw, body, cmd):
    """(текст для вставки, поля ответа). При любой ошибке — исходник без команды и rewrite_error."""
    out({"status": "rewrite", "command": cmd["title"]})
    where = "API" if use_api(rw) else "локально"
    log(f"переписываю ({cmd['key']}: {cmd['title']}, {where}) ← {body[:300]}")
    t2 = time.time()
    try:
        messages = rewrite.build_messages(body, cmd)
        if use_api(rw):
            raw = apillm.chat(rw["api_url"], rw["api_key"], rw["api_model"], messages, max_tokens=800)
        elif rw["model"]:
            raw = llm.rewrite(rw["model"], messages, rw["idle_sec"], on_download=on_download)
        else:
            raise ValueError("переписывание не настроено — добавьте ключ API в настройках")
        result = rewrite.humanize(raw)
        log(f"переписано за {time.time() - t2:.1f} с → {result[:300]}")
        if not rewrite.accept(body, result, cmd):
            if cmd.get("rude") and not result.strip():
                raise ValueError("модель отказалась от грубого стиля (у DeepSeek цензура) — попробуйте Grok")
            raise ValueError("ответ модели пустой, слишком короткий или это рассуждение вместо текста")
        return result, {"rewrite": cmd["key"], "rewrite_sec": round(time.time() - t2, 2)}
    except Exception as e:  # noqa: BLE001 — текст терять нельзя, вставляем исходник
        log(f"! переписать не вышло: {type(e).__name__}: {e}")
        return body, {"rewrite": cmd["key"], "rewrite_error": f"{type(e).__name__}: {e}"}


def main():
    def cloud_recognize(path):
        """Распознать WAV облачным STT; вернуть (text, lang, scores, fixed), как прежний recognize().
        Локального Whisper больше нет — при выключенном/недоступном облаке поднимаем ошибку."""
        stt = stt_settings()
        if not stt["enabled"]:
            raise RuntimeError("облачное распознавание выключено — задай stt_* в config.json")
        raw = sttapi.transcribe(path, stt["key"], stt["model"], stt["language"], stt["provider"])
        text = finalize(raw, PROMPT)
        if fix_cmd_enabled():
            text = fix_command_endings(text)
        return text, "scribe", {}, 0

    # Локальная модель не грузится — воркер готов сразу.
    out({"ready": True, "load_sec": 0.0, "langs": list(LANGS), "provider": "cloud"})

    llm = Rewriter(log)
    last_use = time.time()
    while True:
        timeout = IDLE_SEC - (time.time() - last_use)
        if llm.loaded:
            timeout = min(timeout, llm.idle_left())
        readable, _, _ = select.select([sys.stdin], [], [], None if timeout == float("inf") else max(0.0, timeout))
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
        last_use = t1 = time.time()
        if not path:  # пинг: приложение начало запись, модель нужна живой
            if llm.loaded:
                llm.last_use = last_use
            continue
        if path.startswith("{"):  # команда без звука
            try:
                req = json.loads(path)
                rw = rewrite_settings()
                if "rewrite" in req:
                    body = str(req["rewrite"].get("text") or "").strip()
                    trigger = str(req["rewrite"].get("trigger") or "официальный стиль")
                    _, cmd = rewrite.split_command("x. " + trigger, rw["commands"], rw["keyword"])
                    if not body or not cmd:
                        out({"text": "", "error": "нечего переписывать или неизвестный стиль"})
                        continue
                    text, extra = do_rewrite(llm, rw, body, cmd)
                    out({"text": text, "sec": round(time.time() - t1, 2), **extra})
                elif "rewrite_selection" in req:
                    sel_text = str(req["rewrite_selection"].get("text") or "").strip()
                    sel_instr = str(req["rewrite_selection"].get("instruction") or "").strip()
                    if not sel_text or not sel_instr or not use_api(rw):
                        out({"text": "", "error": "нет текста, команды или облачной модели"})
                        continue
                    cmd = rewrite.selection_command(sel_instr)
                    text, extra = do_rewrite(llm, rw, sel_text, cmd)
                    out({"text": text, "sec": round(time.time() - t1, 2), **extra})
                elif "refine" in req and use_api(rw):
                    r = req["refine"]
                    variants = [str(v) for v in (r.get("variants") or [])]
                    instruction, _lang, _sc, _fx = cloud_recognize(r["path"])   # голосовую правку распознаём облаком
                    out({"status": "variants", "command": instruction[:40]})
                    log(f"правка вариантов ← {instruction[:200]}")
                    raw = apillm.chat(rw["api_url"], rw["api_key"], rw["api_model"],
                                      rewrite.build_refine_messages(variants, instruction, r.get("style", "")),
                                      max_tokens=1400)
                    new = rewrite.parse_variants(raw, count=6)
                    if not new:
                        raise ValueError("модель не вернула варианты после правки")
                    out({"text": "", "variants": new, "style": r.get("style", ""),
                         "body": r.get("body", ""), "sec": round(time.time() - t1, 2)})
                else:
                    out({"text": "", "error": "неизвестная команда"})
            except Exception as e:  # noqa: BLE001
                out({"text": "", "error": f"{type(e).__name__}: {e}"})
            continue
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
            try:
                text, lang, scores, fixed = cloud_recognize(path)
            except Exception as e:  # noqa: BLE001
                out({"text": "", "error": f"облачный STT: {e}"})
                continue
            log(f"облачный STT: {text[:200]}")
            extra = {}
            rw = rewrite_settings()
            asked = rewrite.split_answer(text, rw["answer_keyword"], rw["personas"]) if rw["answer_model"] or use_api(rw) else None
            if asked:  # «ответь, …» — вопрос модели, в текст ничего не вставляется
                question, persona = asked
                out({"status": "answer", "command": (persona + ": " if persona else "") + question[:60]})
                log(f"отвечаю{' (' + persona + ')' if persona else ''} ← {question[:300]}")
                t2 = time.time()
                try:
                    ans_msgs = rewrite.build_answer_messages(question, persona)
                    if use_api(rw):
                        raw = apillm.chat(rw["api_url"], rw["api_key"], rw["api_model"], ans_msgs, max_tokens=1500)
                    else:
                        raw = llm.rewrite(rw["answer_model"], ans_msgs, rw["idle_sec"],
                                          max_tokens=1500, thinking=rw["answer_thinking"], on_download=on_download)
                    answer = rewrite.humanize(raw)
                    if not answer:
                        raise ValueError("модель ничего не ответила")
                    log(f"ответ за {time.time() - t2:.1f} с → {answer[:300]}")
                    history.add(HOME_DIR / "history.json", f"Вопрос: {question}\nОтвет: {answer}", lang, time.time() - t1)
                    out({"text": "", "question": question, "answer": answer, "answer_sec": round(time.time() - t2, 2),
                         "sec": round(time.time() - t1, 2)})
                except Exception as e:  # noqa: BLE001
                    out({"text": "", "question": question, "answer_error": f"{type(e).__name__}: {e}"})
                last_use = time.time()
                continue
            active = rw["enabled"] and (use_api(rw) or rw["model"])
            # Сказана только команда над выделенным текстом («исправь этот текст…») — просим приложение
            # скопировать выделение и прислать его на переписывание, вставленный текст не печатаем.
            sel = rewrite.selection_instruction(text, rw["selection_keyword"]) if (active and use_api(rw)) else None
            if sel:
                out({"text": "", "selection_rewrite": sel})
                last_use = time.time()
                continue
            want_variants = False
            if active:
                text2, want_variants, over_text = rewrite.strip_variants(text)
                body, cmd = rewrite.split_command(text2, rw["commands"], rw["keyword"], auto=False)
            else:
                body, cmd, over_text = text, None, False
            if want_variants and use_api(rw):  # показать несколько вариантов вместо вставки
                vcmd = cmd or (rewrite.REWRITE_VARIANT_COMMAND if over_text else rewrite.DEFAULT_VARIANT_COMMAND)
                out({"status": "variants", "command": vcmd["title"]})
                log(f"варианты ({vcmd['key']}: {vcmd['title']}) ← {body[:200]}")
                t2 = time.time()
                try:
                    raw = apillm.chat(rw["api_url"], rw["api_key"], rw["api_model"],
                                      rewrite.build_variants_messages(body, vcmd), max_tokens=1200)
                    variants = rewrite.parse_variants(raw)
                    if not variants:
                        raise ValueError("модель не вернула варианты")
                    log(f"вариантов {len(variants)} за {time.time() - t2:.1f} с")
                    out({"text": "", "variants": variants, "style": vcmd["title"], "body": body,
                         "sec": round(time.time() - t1, 2)})
                except Exception as e:  # noqa: BLE001
                    out({"text": "", "variants_error": f"{type(e).__name__}: {e}"})
                last_use = time.time()
                continue
            if cmd:
                text, extra = do_rewrite(llm, rw, body, cmd)
                last_use = time.time()
            history.add(HOME_DIR / "history.json", text, lang, time.time() - t1)
            out({"text": text, "lang": lang, "scores": scores, "fixed": fixed,
                 "dur": dur, "speech": speech, "sec": round(time.time() - t1, 2), **extra})
        except Exception as e:  # noqa: BLE001
            out({"text": "", "error": f"{type(e).__name__}: {e}"})

if __name__ == "__main__":
    main()
