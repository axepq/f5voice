"""Сборка текста из сегментов whisper с отсечением мусора.

Сегмент — кортеж (start, end, text). Модельная часть (проверка языка по
звуку, повторное декодирование) остаётся в бэкенде: он передаёт сюда функцию
`redecode(start, end, context) -> text | None`, которая вызывается только для
подозрительных сегментов — записанных латиницей при русском языке.
"""
from .textproc import latin_share


def assemble(segments, duration, redecode=None, min_latin=0.6, min_words=3):
    """Возвращает (текст, сколько сегментов переделано).

    1. Сегменты, начавшиеся за концом записи, — галлюцинации на тишине, которой
       whisper дополняет последнее окно до 30 секунд.
    2. Подряд повторяющийся одинаковый сегмент — тоже галлюцинация.
    3. Сегмент латиницей при русском токене отдаётся в redecode(): если по звуку
       он русский, бэкенд декодирует его заново с русским контекстом.
    """
    pieces, fixed, context, last = [], 0, "", None
    for start, end, text in segments:
        text = (text or "").strip()
        if not text or start >= duration - 0.25:
            continue
        if text == last:
            continue
        last = text
        if redecode and latin_share(text) > min_latin and len(text.split()) >= min_words:
            better = redecode(start, min(end, duration), context[-300:])
            if better and latin_share(better) < latin_share(text):
                text, fixed = better, fixed + 1
        pieces.append(text)
        if latin_share(text) < 0.3:
            context = (context + " " + text).strip()
    return " ".join(pieces), fixed
