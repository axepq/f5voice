"""Переписывание через облачный API (OpenAI-совместимый: DeepSeek, xAI Grok, OpenAI и т.п.).

Только стандартный библиотечный urllib, без лишних зависимостей. Один вызов chat/completions,
сообщения — те же, что строит common.rewrite (роли system/user). Ключ и адрес приходят из настроек.
Речь и распознавание остаются локальными: сюда попадает только текст для переписывания.
"""
import json
import urllib.error
import urllib.request

# Готовые провайдеры для окна настроек: имя → (базовый адрес, модель по умолчанию).
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-flash"),
    "grok": ("https://api.x.ai/v1", "grok-4.20-0309-non-reasoning"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


def chat(url, key, model, messages, max_tokens=512, timeout=40):
    """Ответ модели на messages ([{role, content}, …]) через POST {url}/chat/completions.
    Возвращает текст ответа; бросает исключение при сетевой ошибке или отказе API."""
    base = (url or "").rstrip("/")
    if not base.endswith("/chat/completions"):
        base = base + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(base, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"API {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"нет связи с API: {e.reason}") from None
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"неожиданный ответ API: {str(data)[:200]}") from None
