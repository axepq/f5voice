"""Чистка распознанного текста и голосовые команды.

clean()          — убирает то, что whisper выдумывает на тишине и шуме
apply_commands() — «два плюс два новая строка дальше» → «два + два\\nДальше»
latin_share()    — доля слов латиницей (нужна для ловли дрейфа в английский)
"""
import difflib
import re

# Подсказка задаёт стиль: русская речь, английские названия латиницей, пунктуация.
# Заканчивается по-русски намеренно: английский хвост тянул модель в английский.
DEFAULT_PROMPT = (
    "Это диктовка на русском языке, в ней встречаются английские названия: Claude Code, "
    "GitHub, Supabase, Vercel, Telegram, Next.js, API, deploy, commit. Итак, продолжаем."
)
# Контекст для повторного декодирования сегмента, уехавшего в английский.
RU_HINT = "Продолжение диктовки на русском языке."

# ---------------------------------------------------------------- чистка

STRIP_ANYWHERE = [
    r"субтитры\s+\S+\s+dimatorzok",
    r"dimatorzok",
    r"редактор субтитров[^.]*",
    r"корректор\s+[а-яё]\.\s*[а-яё]+\s*$",   # титр в конце; «звонок в корректор А. Иванов» в речи оставляем
    r"продолжение следует\.*",
    r"\[[^\]\d]{1,30}\]",    # [музыка], [смех], [music]; arr[0] и [1] — не титры
    r"\([^)]*музык[^)]*\)",  # (музыка)
    r"[♪♫]+",
]
STRIP_AT_END = [
    r"спасибо за просмотр[!.]*",
    r"продолжение следует[.…]*",
]
ONLY_IF_WHOLE = [
    r"спасибо за просмотр",
    r"спасибо за внимание",
    r"с вами был\S*.*",
    r"подписывайтесь на канал.*",
    r"до новых встреч",
    r"thank you for watching",
    r"thanks for watching",
    r"you",
]


def _norm(s):
    return re.sub(r"[^\wа-яё]+", " ", s.lower()).strip()


def latin_share(text):
    """Доля слов латиницей среди всех слов с буквами."""
    words = re.findall(r"[^\W\d_]+", text)
    if not words:
        return 0.0
    return sum(1 for w in words if all(ord(c) < 128 for c in w)) / len(words)


def looks_like_prompt_echo(text, prompt):
    """На тишине whisper часто просто повторяет подсказку."""
    t = _norm(text)
    if not t:
        return True
    p = _norm(prompt or "")
    if not p:
        return False
    if len(t.split()) >= 4 and t in p and len(t) >= 0.5 * len(p):
        return True  # кусок подсказки короче половины — это может быть и живая речь («GitHub, Vercel, …»)
    return difflib.SequenceMatcher(None, t, p).ratio() > 0.75


def clean(text, prompt=DEFAULT_PROMPT):
    t = text
    for pat in STRIP_ANYWHERE:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    for pat in STRIP_AT_END:
        t = re.sub(r"(?:^|(?<=[.!?…]))\s*" + pat + r"\s*$", "", t, flags=re.IGNORECASE).strip()
    core = t.strip(" .!?,…-—").lower()
    for pat in ONLY_IF_WHOLE:
        if re.fullmatch(pat, core, flags=re.IGNORECASE):
            return ""
    if looks_like_prompt_echo(t, prompt):
        return ""
    return t


# ---------------------------------------------------------------- команды

# (что произнесено, что вставить, как стыковать с соседями)
#   newline — перенос: пробелы вокруг убрать, следующее слово с заглавной
#   punct   — знак препинания: прижать к предыдущему слову, после — пробел
#   none    — оставить пробелы как есть: «2 + 2»
#   both    — прижать с обеих сторон: «src/components», «alex@mail»
#   left    — прижать к предыдущему слову: «5%»
#   right   — прижать к следующему: «#tag», «$100»
# Порядок важен: длинные формы раньше коротких.
COMMANDS = [
    (r"(?:с\s+)?нов(?:ый|ого)\s+абзац(?:а|ем)?|с\s+абзаца|абзац(?:ем)?", "\n\n", "newline"),
    (r"(?:с|на)\s+нов(?:ой|ую)\s+строк[иу]|нов(?:ая|ой|ую)\s+строк[аиу]|перенос\s+строки|перевод\s+строки", "\n", "newline"),
    (r"точка\s+с\s+запятой", ";", "punct"),
    (r"вопросительный\s+знак", "?", "punct"),
    (r"восклицательный\s+знак", "!", "punct"),
    (r"многоточие", "…", "punct"),
    (r"двоеточие", ":", "punct"),
    (r"запятая", ",", "punct"),
    (r"точка", ".", "punct"),
    (r"обратн(?:ый|ая)\s+(?:сл[эе]ш|косая\s+черта)|б[эе]к\s*сл[эе]ш", "\\", "both"),
    (r"(?:прямой\s+)?сл[эе]ш|косая\s+черта|дробь", "/", "both"),
    (r"знак\s+(?:равно|равенства)|(?<!вс[её]\s)равно", "=", "none"),   # «всё равно» — не знак
    (r"плюс", "+", "none"),
    (r"минус", "-", "none"),
    (r"зв[её]здочка|астериск", "*", "none"),
    (r"реш[её]тка|х[эе]штег|диез", "#", "right"),
    (r"(?<=[a-z0-9]\s)собак[аи](?=\s[a-z0-9])|собачка", "@", "both"),   # только между латиницей: alex собака gmail
    (r"амперсанд", "&", "none"),
    (r"знак\s+процента", "%", "left"),
    (r"знак\s+доллара", "$", "right"),
    (r"(?:нижнее\s+)?подч[её]ркивание", "_", "both"),
    (r"тире", "—", "none"),
    (r"дефис", "-", "both"),
    (r"стрелка\s+влево", "←", "none"),
    (r"стрелка(?:\s+вправо)?", "→", "none"),
    (r"откры(?:ть|вается|та|тая)\s+скобк[аиу]|скобка\s+открывается", "(", "right"),
    (r"закры(?:ть|вается|та|тая)\s+скобк[аиу]|скобка\s+закрывается", ")", "left"),
    (r"откры(?:ть|ты|тые)\s+кавычки|кавычки\s+открываются", "«", "right"),
    (r"закры(?:ть|ты|тые)\s+кавычки|кавычки\s+закрываются", "»", "left"),
]
_COMMAND_RX = [
    (re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % pat, re.IGNORECASE), sym, mode)
    for pat, sym, mode in COMMANDS
]
_TOKEN = "\x00"


def _cap(s):
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
        if not ch.isspace():
            return s
    return s


def apply_commands(text):
    """«два плюс два новая строка дальше» → «два + два\\nДальше»."""
    for rx, sym, mode in _COMMAND_RX:
        text = rx.sub(lambda m, sym=sym, mode=mode: f"{_TOKEN}{mode}{_TOKEN}{sym}{_TOKEN}", text)
    if _TOKEN not in text:
        return text
    parts = re.split(r"(\x00[a-z]+\x00[^\x00]*\x00)", text)

    def peek(i):
        """Первый непробельный символ следующего куска текста ('' если дальше команда или конец)."""
        for j in range(i + 1, len(parts)):
            s = parts[j].strip()
            if s:
                return "" if s.startswith(_TOKEN) else s[0]
        return ""

    result = ""
    pending_strip = None   # что убрать в начале следующего куска текста
    cap_next = False
    lower_next = False     # после «точка» внутри домена whisper любит заглавную: gmail точка Com
    for i, part in enumerate(parts):
        m = re.fullmatch(r"\x00([a-z]+)\x00([^\x00]*)\x00", part)
        if not m:
            if pending_strip:
                part = part.lstrip(pending_strip)
                pending_strip = None
            if cap_next and part.strip():
                part = _cap(part)
                cap_next = False
            if lower_next and part.strip():
                part = re.sub(r"^\s*\S", lambda m: m.group(0).lower(), part)
                lower_next = False
            result += part
            continue
        mode, sym = m.groups()
        nxt = peek(i)
        next_is_punct = nxt != "" and nxt in ".,;:!?…"
        next_is_latin_or_digit = bool(re.match(r"[a-z0-9]", nxt, re.IGNORECASE))
        if mode == "newline":
            result = result.rstrip(" ") + sym
            pending_strip, cap_next = " .,;:!?", True
        elif mode == "punct":
            result = result.rstrip(" .,;:!?") + sym
            pending_strip = " .,;:!?"
            if sym == "." and next_is_latin_or_digit:
                lower_next = True  # button.tsx, v1.2, gmail.com — без пробела и заглавной
            else:
                result += " "
                cap_next = sym in ".!?…"
        elif mode == "none":
            result = result.rstrip(" ")
            if result and not result.endswith("\n"):
                result += " "
            result += sym
            if not next_is_punct:
                result += " "
            pending_strip = " "
        elif mode == "both":
            result = result.rstrip(" ") + sym
            pending_strip = " "
        elif mode == "left":
            result = result.rstrip(" ") + sym
            if not next_is_punct:
                result += " "
            pending_strip = " "
        elif mode == "right":
            result = result.rstrip(" ")
            if result and not result.endswith(("\n", "(", "«")):
                result += " "
            result += sym
            pending_strip = " "
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip(" ")


def _norm_word(w):
    return re.sub(r"[^\w]+", "", w.lower())


def collapse_repeats(text, min_words=2, max_words=10):
    """Петля whisper: «что вы можете сделать это, » × 30. Группа из 2–10 слов, идущая подряд
    3+ раз, схлопывается до одного вхождения; если она повторилась 4+ раз и заняла больше
    половины текста — это галлюцинация целиком, группа удаляется."""
    words = text.split()
    n = len(words)
    if n < 2 * min_words:
        return text
    norm = [_norm_word(w) for w in words]
    out, i = [], 0
    while i < n:
        best = None
        for size in range(min_words, max_words + 1):
            if i + 2 * size > n:
                break
            unit = norm[i:i + size]
            if not any(unit):
                continue
            reps = 1
            while i + (reps + 1) * size <= n and norm[i + reps * size:i + (reps + 1) * size] == unit:
                reps += 1
            if reps >= 3 and (best is None or reps * size > best[0] * best[1]):
                best = (reps, size)
        if best:
            reps, size = best
            if not (reps >= 4 and reps * size > n / 2 and n >= 20):  # галлюцинация whisper всегда длинная
                out.extend(words[i:i + size])
            i += reps * size
        else:
            out.append(words[i])
            i += 1
    result = " ".join(out).strip(" ,;:")
    return result if result else ""


def finalize(text, prompt=DEFAULT_PROMPT):
    """Полная обработка сырого текста модели."""
    return apply_commands(collapse_repeats(clean(text, prompt)))


# Частая ошибка Whisper по-русски: команду в повелительном слышит как 1-е лицо будущего
# («сделай»→«сделаю», «покажи»→«покажу»). Осторожно правим обратно, но НЕ после «я/мы/буду…»,
# где 1-е лицо законно. Включается флагом в настройках (в обычных сообщениях мешало бы).
_CMD_ENDINGS = {
    "сделаю": "сделай", "переделаю": "переделай", "покажу": "покажи", "напишу": "напиши",
    "перепишу": "перепиши", "добавлю": "добавь", "исправлю": "исправь", "поправлю": "поправь",
    "уберу": "убери", "удалю": "удали", "поменяю": "поменяй", "изменю": "измени",
    "проверю": "проверь", "ускорю": "ускорь", "замедлю": "замедли", "увеличу": "увеличь",
    "уменьшу": "уменьши", "сохраню": "сохрани", "запущу": "запусти", "соберу": "собери",
    "объясню": "объясни", "открою": "открой", "закрою": "закрой", "начну": "начни",
    "создам": "создай", "поставлю": "поставь", "приступаю": "приступай", "продолжаю": "продолжай",
}
_CMD_GUARD = {"я", "мы", "ты", "вы", "буду", "будем", "будешь", "тебе",
              "вам", "потом", "завтра", "тогда", "сейчас", "сам", "сама"}


def fix_command_endings(text):
    """Чинит окончания команд, спутанные Whisper (сделаю→сделай), кроме случаев после «я/мы/…»."""
    if not text:
        return text
    tokens = re.split(r"(\s+)", text)   # сохраняем пробелы между словами
    prev = None
    for i, tok in enumerate(tokens):
        if not tok.strip():
            continue
        m = re.match(r"^(\W*)(.+?)(\W*)$", tok, re.UNICODE)
        if not m:
            prev = tok.lower()
            continue
        lead, core, tail = m.group(1), m.group(2), m.group(3)
        repl = _CMD_ENDINGS.get(core.lower())
        if repl and prev not in _CMD_GUARD:
            if core[:1].isupper():
                repl = repl[:1].upper() + repl[1:]
            tokens[i] = lead + repl + tail
        prev = re.sub(r"\W", "", core.lower(), flags=re.UNICODE)
    return "".join(tokens)
