"""Облачное распознавание речи (Speech-to-Text). Пока ElevenLabs Scribe. Только urllib.

Отправляем WAV-файл, получаем текст. Ключ и модель — из настроек. Речь уходит в облако
только при включённом облачном STT.
"""
import json
import mimetypes
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


# провайдер -> (url, модель по умолчанию)
PROVIDERS = {
    "elevenlabs": ("https://api.elevenlabs.io/v1/speech-to-text", "scribe_v1"),
}


def _multipart(fields, filepath):
    """Тело multipart/form-data: поля + файл. Возвращает (content_type, body_bytes)."""
    boundary = "----f5voice" + uuid.uuid4().hex
    nl = b"\r\n"
    buf = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        buf += b"--" + boundary.encode() + nl
        buf += f'Content-Disposition: form-data; name="{name}"'.encode() + nl + nl
        buf += str(value).encode("utf-8") + nl
    fname = os.path.basename(filepath)
    ctype = mimetypes.guess_type(fname)[0] or "audio/wav"
    with open(filepath, "rb") as f:
        data = f.read()
    buf += b"--" + boundary.encode() + nl
    buf += f'Content-Disposition: form-data; name="file"; filename="{fname}"'.encode() + nl
    buf += f"Content-Type: {ctype}".encode() + nl + nl
    buf += data + nl
    buf += b"--" + boundary.encode() + b"--" + nl
    return "multipart/form-data; boundary=" + boundary, bytes(buf)


def budget_for(audio_sec):
    """Сколько всего можно потратить на попытки. Приложение считает воркер зависшим на 4 с позже
    (transcribeTimeoutSeconds в main.swift: 30 + 0,2 с на секунду записи) и убивает его вместе
    с запросом — поэтому бюджет строго меньше. Формулы меняются вместе."""
    return 26.0 + 0.2 * max(0.0, audio_sec)


def _audio_seconds(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, OSError, EOFError):
        return 0.0


def _hedge_after(audio_sec):
    """Через сколько без ответа слать тот же файл ещё раз, не обрывая первый запрос.
    Замер 29.09: один и тот же 7-секундный файл Scribe отдаёт то за 2 с, то за 18–24 с,
    сеть при этом чистая — задержка на стороне сервера и случайна от запроса к запросу.
    Быстрые ответы приходят за 1,5–4 с (87 с речи — за 7 с), поэтому ждать дольше незачем."""
    return 3.5 + 0.05 * audio_sec


MAX_PARALLEL = 3   # каждый запрос ElevenLabs тарифицирует отдельно: дубли идут только при задержке


def transcribe(path, key, model="scribe_v1", language="", provider="elevenlabs", deadline=None):
    """Распознать WAV через облачный STT. Возвращает текст; бросает исключение при ошибке.

    Запросы идут внахлёст: если первый не ответил за _hedge_after(), вдогонку уходит второй,
    потом третий; побеждает первый ответ. 4xx (ключ, квота) — сразу ошибка. 429/5xx/обрыв —
    этот запрос выбывает, остальные продолжают, при нужде уходит замена."""
    url = PROVIDERS.get(provider, PROVIDERS["elevenlabs"])[0]
    fields = {"model_id": model or "scribe_v1"}
    lang = (language or "").strip()
    if lang and lang.lower() not in ("auto", "ru,en", ""):
        fields["language_code"] = lang.split(",")[0].strip()
    ctype, body = _multipart(fields, path)
    audio_sec = _audio_seconds(path)
    hedge = _hedge_after(audio_sec)
    if deadline is None:
        deadline = budget_for(audio_sec)
    t0 = time.monotonic()
    end = t0 + deadline
    results = queue.Queue()

    def attempt(n):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "xi-api-key": key,
            "Content-Type": ctype,
        })
        try:
            with urllib.request.urlopen(req, timeout=max(1.0, end - time.monotonic())) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results.put((n, "ok", (data.get("text") or "").strip()))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            fatal = not (e.code == 429 or 500 <= e.code < 600)
            results.put((n, "fatal" if fatal else "retry", RuntimeError(f"STT {e.code}: {detail}")))
        except (TimeoutError, socket.timeout):
            results.put((n, "retry", RuntimeError(f"STT не ответил за {deadline:.0f} с")))
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                results.put((n, "retry", RuntimeError(f"STT не ответил за {deadline:.0f} с")))
            else:
                results.put((n, "retry", RuntimeError(f"нет связи со STT: {e.reason}")))
        except Exception as e:  # noqa: BLE001 — поток не должен умирать молча
            results.put((n, "retry", RuntimeError(f"STT: {type(e).__name__}: {e}")))

    started = 0
    in_flight = 0
    last = None
    next_at = t0

    while True:
        now = time.monotonic()
        if now >= end - 0.2 and in_flight == 0:
            break
        if in_flight < MAX_PARALLEL and started < MAX_PARALLEL + 2 and now >= next_at and end - now > 2.0:
            started += 1
            in_flight += 1
            if started > 1:
                _log(f"STT молчит {now - t0:.1f} с — шлю запрос {started} вдогонку")
            threading.Thread(target=attempt, args=(started,), daemon=True).start()
            next_at = now + hedge
        wait = end - time.monotonic()
        if in_flight < MAX_PARALLEL and started < MAX_PARALLEL + 2:
            wait = min(wait, next_at - time.monotonic())
        try:
            n, kind, val = results.get(timeout=max(0.05, wait))
        except queue.Empty:
            if time.monotonic() >= end:
                break
            continue
        in_flight -= 1
        if kind == "ok":
            if started > 1:
                _log(f"STT ответил на запрос {n} из {started} за {time.monotonic() - t0:.1f} с")
            return val
        last = val
        if kind == "fatal":
            raise last from None
        _log(f"{last} (запрос {n})")
        next_at = min(next_at, time.monotonic() + 0.5)   # выбывший заменяем почти сразу
    raise (last or RuntimeError(f"STT не ответил за {deadline:.0f} с")) from None
