"""Чтение аудиофайлов в моно float32 16 кГц без ffmpeg.

Порядок попыток:
  1. свой разбор WAV — включая WAVE_FORMAT_EXTENSIBLE (тег 0xFFFE), который
     модуль wave не читает;
  2. soundfile, если установлен (ogg/opus, flac, aiff);
  3. на macOS — системный afconvert (m4a, aac, mp3, caf).
"""
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

TARGET_RATE = 16000
PCM_TAGS = (0x0001, 0xFFFE)  # обычный PCM и расширенный


def _parse_wav(path):
    """(моно float32, частота) или None, если это не 16-битный PCM WAV."""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None

    fmt = pcm = None
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = raw[pos : pos + 4]
        size = struct.unpack("<I", raw[pos + 4 : pos + 8])[0]
        body = raw[pos + 8 : pos + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + size + (size & 1)  # чанки выравниваются по чётной границе

    if fmt is None or pcm is None:
        return None
    tag, channels, rate, _byte_rate, _align, bits = fmt
    if tag not in PCM_TAGS or bits != 16 or channels < 1:
        return None

    audio = np.frombuffer(pcm[: len(pcm) - (len(pcm) % (2 * channels))], np.int16)
    audio = audio.astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def _read_soundfile(path):
    try:
        import soundfile as sf
    except ImportError:
        return None
    try:
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception:  # noqa: BLE001
        return None
    return audio.mean(axis=1), rate


def _via_afconvert(path):
    if sys.platform != "darwin":
        return None
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", path, tmp],
            check=True,
            capture_output=True,
        )
        return _parse_wav(tmp)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resample(audio, rate, target=TARGET_RATE):
    """Линейная передискретизация — для речи достаточно."""
    audio = np.asarray(audio, dtype=np.float32)
    if rate == target or audio.size == 0:
        return audio
    duration = audio.size / rate
    old_t = np.linspace(0.0, duration, audio.size, endpoint=False)
    new_t = np.linspace(0.0, duration, int(duration * target), endpoint=False)
    return np.interp(new_t, old_t, audio).astype(np.float32)


def load_audio(path):
    """Моно float32 16 кГц. ValueError, если формат не поддержан или запись короче 1/8 с."""
    for reader in (_parse_wav, _read_soundfile, _via_afconvert):
        parsed = reader(path)
        if parsed is not None:
            audio, rate = parsed
            if audio.ndim > 1:  # стерео → моно
                audio = audio.mean(axis=1).astype(np.float32)
            if audio.size >= rate // 8:  # 1/8 с в исходной частоте, а не в 16 кГц
                return resample(audio, rate)
    raise ValueError("не смог прочитать аудио: формат не поддержан или запись пустая")
