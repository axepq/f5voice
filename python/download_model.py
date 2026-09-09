#!/usr/bin/env python3
"""Скачивает модель faster-whisper с видимым прогрессом.

faster_whisper.utils.download_model нарочно глушит tqdm, и загрузка 1,6 ГБ
выглядит как зависание. Здесь тот же список файлов и те же репозитории, но
прогресс по каждому файлу виден, и ничего не зависит от ctranslate2 —
загрузка работает, даже если сама библиотека на этой машине не импортируется.

    python download_model.py large-v3-turbo
"""
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # классическая загрузка: hf_xet на Windows давал странный вывод
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows без режима разработчика: копии вместо ссылок

FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

# Как в faster_whisper.utils._MODELS, без импорта самой библиотеки.
REPOS = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def resolve_repo(name):
    """Короткое имя → репозиторий Hugging Face; незнакомое имя считаем репозиторием."""
    if name in REPOS:
        return REPOS[name]
    try:
        from faster_whisper.utils import _MODELS

        return _MODELS.get(name, name)
    except Exception:  # noqa: BLE001 — ctranslate2 может не импортироваться
        return name


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    _utf8_console()
    from huggingface_hub import snapshot_download

    name = sys.argv[1] if len(sys.argv) > 1 else "large-v3-turbo"
    repo = resolve_repo(name)
    print(f"{name} -> {repo}", flush=True)
    path = snapshot_download(repo, allow_patterns=FILES)
    print(f"ok: {path}", flush=True)


if __name__ == "__main__":
    main()
