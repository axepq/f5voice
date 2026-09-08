"""Быстрая самопроверка ядра без модели: python -m common.selftest"""
import sys

import numpy as np

from .segments import assemble
from .textproc import apply_commands, clean, latin_share
from .vad import is_silence

CASES = [
    ("Два плюс два равно четыре, новая строка. Дальше текст абзац. Новый абзац начинается здесь.",
     "Два + два = четыре,\nДальше текст\n\nНачинается здесь."),
    ("Открой src слэш components слэш button точка tsx", "Открой src/components/button.tsx"),
    ("Пиши мне на alex собака gmail точка com, там пять знак процента скидка",
     "Пиши мне на alex@gmail.com, там пять% скидка"),
    ("Версия v1 точка 2, решётка 42, знак доллара 100", "Версия v1.2, #42, $100"),
    ("Это плюс, а это минус. Плюс-минус нормально.", "Это +, а это -. Плюс-минус нормально."),
    ("Открыть кавычки привет закрыть кавычки, открыть скобку пояснение закрыть скобку",
     "«привет», (пояснение)"),
    ("Привет запятая как дела вопросительный знак Отлично восклицательный знак",
     "Привет, как дела? Отлично!"),
    ("Пять минус три равно два точка", "Пять - три = два."),
    ("Просто обычная фраза без команд.", "Просто обычная фраза без команд."),
]


def main():
    failed = 0
    for src, want in CASES:
        got = apply_commands(clean(src))
        ok = got == want
        failed += not ok
        print(("ok  " if ok else "FAIL") + f" {src!r}\n     → {got!r}" + ("" if ok else f"\n     ожидалось {want!r}"))

    for src in ["Спасибо за просмотр!", "Субтитры сделал DimaTorzok", "Продолжение следует..."]:
        got = clean(src)
        ok = got == ""
        failed += not ok
        print(("ok  " if ok else "FAIL") + f" галлюцинация {src!r} → {got!r}")

    rng = np.random.default_rng(1)
    silence = rng.normal(0, 0.0025, 3 * 16000).astype(np.float32)
    for at in (0.05, 2.85):  # щелчки клавиши
        n = int(0.06 * 16000)
        i = int(at * 16000)
        silence[i:i + n] += (rng.normal(0, 1, n) * np.exp(-np.arange(n) / (0.012 * 16000)) * 0.35).astype(np.float32)
    quiet, dur, speech = is_silence(silence)
    ok = quiet
    failed += not ok
    print(("ok  " if ok else "FAIL") + f" тишина со щелчками: speech={speech}s → {'тишина' if quiet else 'речь'}")
    speechlike = np.sin(np.linspace(0, 2000, 16000)) * 0.1 * (1 + np.sin(np.linspace(0, 30, 16000)))
    quiet, dur, speech = is_silence(speechlike.astype(np.float32))
    ok = not quiet
    failed += not ok
    print(("ok  " if ok else "FAIL") + f" сигнал 1 с: speech={speech}s → {'тишина' if quiet else 'речь'}")

    text, fixed = assemble([(0, 5, "Привет"), (5, 10, "Воспоминания."), (10, 12, "Воспоминания."), (12, 14, "Воспоминания.")], duration=11.0)
    ok = text == "Привет Воспоминания." and fixed == 0
    failed += not ok
    print(("ok  " if ok else "FAIL") + f" сегменты: {text!r}")
    ok = latin_share("Please open the settings page") == 1.0 and latin_share("Я задеплоил на Vercel") < 0.5
    failed += not ok
    print(("ok  " if ok else "FAIL") + " latin_share")

    print("\nвсё в порядке" if not failed else f"\nпровалов: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
