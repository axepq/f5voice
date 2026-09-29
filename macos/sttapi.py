"""Облачное распознавание речи (Speech-to-Text). Пока ElevenLabs Scribe. Только urllib.

Отправляем WAV-файл, получаем текст. Ключ и модель — из настроек. Речь уходит в облако
только при включённом облачном STT.
"""
import json
import mimetypes
import os
import socket
import sys
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


def _attempt_timeout(audio_sec):
    """Сколько ждать один ответ. Обычно Scribe отвечает за 1–3 с (87 с речи — за 7 с),
    но иногда запрос висит десятки секунд; повтор в такой момент проходит быстро."""
    return 10.0 + 0.1 * audio_sec


def transcribe(path, key, model="scribe_v1", language="", provider="elevenlabs", deadline=None):
    """Распознать WAV через облачный STT. Возвращает текст; бросает исключение при ошибке."""
    url = PROVIDERS.get(provider, PROVIDERS["elevenlabs"])[0]
    fields = {"model_id": model or "scribe_v1"}
    lang = (language or "").strip()
    if lang and lang.lower() not in ("auto", "ru,en", ""):
        fields["language_code"] = lang.split(",")[0].strip()
    ctype, body = _multipart(fields, path)
    audio_sec = _audio_seconds(path)
    per_try = _attempt_timeout(audio_sec)
    if deadline is None:
        deadline = budget_for(audio_sec)
    end = time.monotonic() + deadline
    # 429 (сервер занят), 5xx, обрыв сети и зависший ответ у ElevenLabs бывают разово —
    # не роняем диктовку, а переигрываем, пока укладываемся в бюджет.
    last = None
    attempt = 0
    while True:
        left = end - time.monotonic()
        if left < 2.0:
            break
        attempt += 1
        timeout = min(per_try, left)
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "xi-api-key": key,
            "Content-Type": ctype,
        })
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return (data.get("text") or "").strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            last = RuntimeError(f"STT {e.code}: {detail}")
            if not (e.code == 429 or 500 <= e.code < 600):
                raise last from None          # 4xx (ключ/квота) — повтор не поможет
            _log(f"STT {e.code}, попытка {attempt} — повторяю")
            time.sleep(min(1.0, max(0.0, end - time.monotonic() - 2.0)))
        except (TimeoutError, socket.timeout) as e:
            last = RuntimeError(f"STT не ответил за {timeout:.0f} с")
            _log(f"STT молчит {time.monotonic() - started:.1f} с, попытка {attempt} — повторяю")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                last = RuntimeError(f"STT не ответил за {timeout:.0f} с")
            else:
                last = RuntimeError(f"нет связи со STT: {e.reason}")
            _log(f"{last}, попытка {attempt} — повторяю")
            time.sleep(min(0.5, max(0.0, end - time.monotonic() - 2.0)))
    raise (last or RuntimeError("STT не ответил")) from None
