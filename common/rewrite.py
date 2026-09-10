"""Переписывание надиктованного текста по голосовой команде.

Чистая логика без MLX: найти команду в хвосте фразы, собрать инструкцию модели,
привести ответ к человеческому виду, проверить, что он годится. Сама модель —
в macos/llm.py, эта часть работает и тестируется на любой ОС.

«…скиньте до пятницы. Официальный стиль» → («…скиньте до пятницы.», команда official)
«…до пятницы. Команда: сделай списком»    → («…до пятницы.», свободная инструкция)
"""
import re

DEFAULT_MODEL = "mlx-community/Qwen3-4B-4bit"
DEFAULT_KEYWORD = "команда"
DEFAULT_IDLE_MINUTES = 1

# ключ → (триггеры через «|», инструкция модели). Триггер — то, что говорят в конце фразы.
COMMANDS = {
    "official": ("официальный стиль|официально|в деловом стиле|деловой стиль",
                 "Перепиши в официально-деловом стиле: обращение на Вы, нейтральный тон, "
                 "без разговорных слов и приветствий вроде «привет»."),
    "shorter": ("короче|сократи",
                "Сократи текст примерно вдвое, оставив главное."),
    "fix": ("исправь ошибки|грамотно",
            "Исправь только орфографию, пунктуацию и согласование слов. "
            "Стиль, порядок слов и формулировки не меняй."),
    "english": ("по-английски|по английски|переведи на английский",
                "Переведи текст на английский язык. Верни только перевод."),
    "clearer": ("улучши подачу|улучшить подачу|понятнее",
                "Улучши подачу: сделай мысль яснее, убери воду и повторы, выстрой логичный "
                "порядок. Смысл и факты не меняй."),
    "technical": ("технический стиль|технически",
                  "Перепиши в техническом стиле для IT-специалистов: точные формулировки, "
                  "общепринятые IT-термины, без разговорных слов."),
}

_END = r"\s*[.!?…]*\s*$"                  # хвостовые знаки после команды
_BEFORE = r"(?:^|(?<=[.!?,;:…])\s*|\s+)"  # команда — отдельное предложение или после знака


def merge_commands(user=None):
    """Встроенные команды плюс пользовательские из config.json: {"триггер|триггер": "инструкция"}.
    Совпадающий триггер пользователя переопределяет встроенный."""
    result = dict(COMMANDS)
    for n, (triggers, instruction) in enumerate((user or {}).items(), 1):
        if not isinstance(triggers, str) or not isinstance(instruction, str):
            continue
        mine = {t.strip().lower() for t in triggers.split("|") if t.strip()}
        for key, (their, _) in list(result.items()):
            rest = [t for t in their.split("|") if t.strip().lower() not in mine]
            if len(rest) != len(their.split("|")):
                if rest:
                    result[key] = ("|".join(rest), result[key][1])
                else:
                    del result[key]
        if mine:
            result[f"user{n}"] = ("|".join(sorted(mine)), instruction.strip())
    return result


def _fixed(text, commands):
    """Фиксированная команда в хвосте: (тело, команда) или None."""
    for key, (triggers, instruction) in commands.items():
        alts = "|".join(re.escape(t.strip()) for t in triggers.split("|") if t.strip())
        m = re.search(_BEFORE + r"(?P<t>" + alts + r")" + _END, text, flags=re.IGNORECASE)
        if m:
            body = text[:m.start()].rstrip().rstrip(",;:")
            if body:
                return body, {"key": key, "title": m.group("t").lower(), "instruction": instruction}
    return None


def split_command(text, commands=None, keyword=DEFAULT_KEYWORD):
    """(текст без команды, команда) или (text, None). Команда — только в самом конце
    и только если перед ней есть текст. Ключевое слово проверяется первым: в «команда: короче»
    тело — то, что до ключевого слова, а не до «короче»."""
    text = (text or "").strip()
    commands = commands if commands is not None else COMMANDS
    if keyword:
        m = re.search(r"(?<=[.!?,;:…])\s*" + re.escape(keyword) + r"\s*[:,]?\s+(?P<i>.{3,})$",
                      text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            body = text[:m.start()].rstrip().rstrip(",;:")
            instruction = m.group("i").strip()
            if body and instruction:
                known = _fixed("x. " + instruction, commands)  # «команда: официально» — это official
                if known:
                    return body, known[1]
                return body, {"key": "free", "title": instruction.rstrip(".!?…").strip()[:60],
                              "instruction": instruction}
    found = _fixed(text, commands)
    return found if found else (text, None)
