"""Облачное распознавание речи (Speech-to-Text). Пока ElevenLabs Scribe. Только urllib.

Отправляем WAV-файл, получаем текст. Ключ и модель — из настроек. Речь уходит в облако
только при включённом облачном STT; иначе распознаёт локальный Whisper.
"""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

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


def transcribe(path, key, model="scribe_v1", language="", provider="elevenlabs", timeout=60):
    """Распознать WAV через облачный STT. Возвращает текст; бросает исключение при ошибке."""
    url = PROVIDERS.get(provider, PROVIDERS["elevenlabs"])[0]
    fields = {"model_id": model or "scribe_v1"}
    lang = (language or "").strip()
    if lang and lang.lower() not in ("auto", "ru,en", ""):
        fields["language_code"] = lang.split(",")[0].strip()
    ctype, body = _multipart(fields, path)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": key,
        "Content-Type": ctype,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"STT {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"нет связи со STT: {e.reason}") from None
    return (data.get("text") or "").strip()
