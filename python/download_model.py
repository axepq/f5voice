#!/usr/bin/env python3
"""Скачивает модель faster-whisper с видимым прогрессом.

faster_whisper.utils.download_model нарочно глушит tqdm, и загрузка 1,6 ГБ
выглядит как зависание. Здесь тот же список файлов и то же имя репозитория,
но прогресс по каждому файлу виден. Модель в кэше Hugging Face потом находит
сам WhisperModel.

    python download_model.py large-v3-turbo
"""
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # классическая загрузка: hf_xet на Windows давал битые файлы
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

def _utf8_console():
    """Консоль Windows по умолчанию cp1252/cp866: без этого print кириллицы и стрелок падает."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_console()  # Windows без режима разработчика: копии вместо ссылок, это нормально

from faster_whisper.utils import _MODELS  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402

FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "large-v3-turbo"
    repo = _MODELS.get(name, name)
    print(f"{name} -> {repo}", flush=True)
    path = snapshot_download(repo, allow_patterns=FILES)
    print(f"ok: {path}", flush=True)


if __name__ == "__main__":
    main()
