"""Детектор речи по энергии кадров.

Whisper на пустой записи галлюцинирует и думает десятки секунд, поэтому
записи без речи отсекаются до модели. Щелчок клавиши даёт высокий пик, но
длится 2–3 кадра, а речь держит энергию сотни миллисекунд.
"""
import numpy as np

RATE = 16000
FRAME_SEC = 0.03
MIN_SPEECH_SEC = 0.22   # два щелчка клавиши дают около 0,1 с
MIN_DUR_SEC = 0.35      # короче — случайное нажатие


def speech_seconds(audio, rate=RATE):
    """Сколько секунд в записи звучит что-то похожее на речь."""
    frame = int(rate * FRAME_SEC)
    n = audio.size // frame
    if n == 0:
        return 0.0
    frames = np.asarray(audio[: n * frame], dtype=np.float64).reshape(n, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 95))  # не max: один щелчок клавиши не должен глушить тихую речь
    thr = max(0.006, 0.1 * loud, min(3.0 * floor, 0.015))
    return float((rms > thr).sum() * FRAME_SEC)


def is_silence(audio, rate=RATE):
    """(тишина?, длительность, секунд речи)."""
    audio = np.asarray(audio)
    if audio.ndim > 1:  # стерео → моно
        audio = audio.mean(axis=1)
    dur = audio.size / rate
    speech = speech_seconds(audio, rate)
    return (dur < MIN_DUR_SEC or speech < MIN_SPEECH_SEC), round(dur, 2), round(speech, 2)
