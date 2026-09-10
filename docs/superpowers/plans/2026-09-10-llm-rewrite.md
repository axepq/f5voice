# Переписывание текста голосовой командой — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Команда в конце надиктованной фразы («официальный стиль», «команда: сделай списком») переписывает текст локальной моделью Qwen3 4B на macOS; без команды диктовка не меняется.

**Architecture:** Чистая логика (разбор хвоста, инструкции, фильтр «как человек», проверка ответа) в `common/rewrite.py` с тестами на любой ОС. Загрузка/выгрузка модели и генерация через `mlx-lm` в `macos/llm.py`. Воркер `macos/worker.py` вызывает их после `finalize`, шлёт приложению строку статуса; приложение на Swift показывает «Переписываю…» и переносит таймаут. Установщик докачивает модель.

**Tech Stack:** Python 3.9+ (venv `~/.f5voice/venv`), `mlx-lm>=0.29`, `huggingface_hub`, Swift (AppKit, сборка `macos/build.sh`), bash-установщик, unittest.

**Spec:** `docs/superpowers/specs/2026-09-10-llm-rewrite-design.md`

## Global Constraints

- Только macOS / Apple Silicon. `common/` остаётся без импорта MLX: CI гоняет тесты на Ubuntu и Windows.
- Без команды текст проходит нетронутым, модель не загружается (тест обязателен).
- При любой ошибке модели вставляется исходный текст без фразы-команды.
- Модель по умолчанию `mlx-community/Qwen3-4B-4bit`, выгрузка через `rewrite_idle_minutes` (по умолчанию 1), `"rewrite_model": ""` выключает функцию.
- Ключевое слово по умолчанию «команда», срабатывает только после знака препинания.
- Фильтр `humanize`: «—»/«–» → « - », markdown снимается, «!!» → «!», `<think>…</think>` удаляется.
- Комментарии в коде и сообщения пользователю по-русски, в стиле остального репозитория.
- Коммиты с подписью: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` и `Claude-Session: https://claude.ai/code/session_01TeYzwoNHfsK9fG1rraNjQi`.
- Проверка перед каждым коммитом Python: `python3 -m pyflakes <файлы>` (если pyflakes установлен) и запуск затронутых тестов.

---

### Task 1: Разбор команды в хвосте фразы (`common/rewrite.py`)

**Files:**
- Create: `common/rewrite.py`
- Create: `python/test_rewrite.py`
- Modify: `.github/workflows/ci.yml:27-30` (добавить строку с тестом)

**Interfaces:**
- Produces: `DEFAULT_MODEL = "mlx-community/Qwen3-4B-4bit"`, `DEFAULT_KEYWORD = "команда"`, `DEFAULT_IDLE_MINUTES = 1`
- Produces: `COMMANDS: dict[str, tuple[str, str]]` — ключ → (триггеры через `|`, инструкция)
- Produces: `merge_commands(user: dict | None) -> dict[str, tuple[str, str]]` — встроенные + пользовательские (`{"триггер|триггер": "инструкция"}`, ключ `user1`, `user2`, …; совпадающий триггер пользователя переопределяет встроенный)
- Produces: `split_command(text: str, commands=None, keyword=DEFAULT_KEYWORD) -> tuple[str, dict | None]` — `(текст_без_команды, {"key", "title", "instruction"})` или `(text, None)`

- [ ] **Step 1: Написать падающие тесты**

`python/test_rewrite.py`:

```python
"""Тесты разбора команд переписывания, фильтра «как человек» и проверки ответа модели."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import rewrite  # noqa: E402


class SplitCommand(unittest.TestCase):
    """Команда стоит в конце фразы, после знака препинания; текст перед ней обязателен."""

    def test_plain_text_is_untouched(self):
        text = "Привет, скиньте договор до пятницы."
        self.assertEqual(rewrite.split_command(text), (text, None))

    def test_command_after_sentence(self):
        body, cmd = rewrite.split_command("Скиньте договор до пятницы. Официальный стиль.")
        self.assertEqual(body, "Скиньте договор до пятницы.")
        self.assertEqual(cmd["key"], "official")
        self.assertEqual(cmd["title"], "официальный стиль")

    def test_command_after_comma_and_case(self):
        body, cmd = rewrite.split_command("скиньте договор до пятницы, ОФИЦИАЛЬНО")
        self.assertEqual(body, "скиньте договор до пятницы")
        self.assertEqual(cmd["key"], "official")

    def test_all_builtin_triggers(self):
        for key, (triggers, _) in rewrite.COMMANDS.items():
            for trig in triggers.split("|"):
                body, cmd = rewrite.split_command(f"Текст письма. {trig}.")
                self.assertEqual(body, "Текст письма.", trig)
                self.assertEqual(cmd["key"], key, trig)

    def test_command_alone_is_plain_text(self):
        text = "Официальный стиль."
        self.assertEqual(rewrite.split_command(text), (text, None))

    def test_command_in_the_middle_is_plain_text(self):
        text = "Официальный стиль мне не нравится, пиши проще."
        self.assertEqual(rewrite.split_command(text), (text, None))

    def test_free_instruction_after_keyword(self):
        body, cmd = rewrite.split_command("Скиньте до пятницы. Команда: сделай списком и без грубости.")
        self.assertEqual(body, "Скиньте до пятницы.")
        self.assertEqual(cmd["key"], "free")
        self.assertEqual(cmd["instruction"], "сделай списком и без грубости.")
        self.assertEqual(cmd["title"], "сделай списком и без грубости")

    def test_keyword_needs_punctuation_before_it(self):
        text = "Наша команда сделает всё в срок."
        self.assertEqual(rewrite.split_command(text), (text, None))

    def test_keyword_needs_instruction(self):
        text = "Скиньте до пятницы. Команда."
        self.assertEqual(rewrite.split_command(text), (text, None))

    def test_custom_keyword(self):
        body, cmd = rewrite.split_command("Текст. Инструкция: короче.", keyword="инструкция")
        self.assertEqual((body, cmd["key"]), ("Текст.", "free"))

    def test_user_commands_extend_and_override(self):
        cmds = rewrite.merge_commands({"вежливо|повежливее": "Перепиши вежливо.",
                                       "короче": "Сократи втрое."})
        body, cmd = rewrite.split_command("Текст. Повежливее.", cmds)
        self.assertEqual(cmd["instruction"], "Перепиши вежливо.")
        body, cmd = rewrite.split_command("Текст. Короче.", cmds)
        self.assertEqual(cmd["instruction"], "Сократи втрое.")
        self.assertEqual(rewrite.split_command("Текст. Сократи.", cmds)[1]["key"], "shorter")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd ~/Projects/f5voice && python3 python/test_rewrite.py`
Expected: `ModuleNotFoundError: No module named 'common.rewrite'`

- [ ] **Step 3: Написать `common/rewrite.py` (пока только разбор)**

```python
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


def split_command(text, commands=None, keyword=DEFAULT_KEYWORD):
    """(текст без команды, команда) или (text, None). Команда — только в самом конце
    и только если перед ней есть текст."""
    text = (text or "").strip()
    commands = commands if commands is not None else COMMANDS
    for key, (triggers, instruction) in commands.items():
        alts = "|".join(re.escape(t.strip()) for t in triggers.split("|") if t.strip())
        m = re.search(_BEFORE + r"(?P<t>" + alts + r")" + _END, text, flags=re.IGNORECASE)
        if m:
            body = text[:m.start()].rstrip().rstrip(",;:")
            if body:
                return body, {"key": key, "title": m.group("t").lower(), "instruction": instruction}
    if keyword:
        m = re.search(r"(?<=[.!?,;:…])\s*" + re.escape(keyword) + r"\s*[:,]?\s+(?P<i>.{3,})$",
                      text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            body = text[:m.start()].rstrip().rstrip(",;:")
            instruction = m.group("i").strip()
            if body and instruction:
                return body, {"key": "free", "title": instruction.rstrip(".!?…").strip()[:60],
                              "instruction": instruction}
    return text, None
```

- [ ] **Step 4: Прогнать тесты**

Run: `python3 python/test_rewrite.py`
Expected: `OK`, 11 тестов. Если `test_command_in_the_middle_is_plain_text` падает — проверить, что `_END` требует конец строки (`$`) и в тексте после «стиль» идут слова.

- [ ] **Step 5: Добавить тест в CI**

В `.github/workflows/ci.yml` после строки `- run: python python/test_hud.py` добавить:

```yaml
      - run: python python/test_rewrite.py
```

- [ ] **Step 6: Коммит**

```bash
python3 -m pyflakes common/rewrite.py python/test_rewrite.py
git add common/rewrite.py python/test_rewrite.py .github/workflows/ci.yml
git commit -m "Переписывание: разбор команды в хвосте фразы и свободной инструкции"
```

---

### Task 2: Инструкция модели, фильтр «как человек», проверка ответа

**Files:**
- Modify: `common/rewrite.py` (добавить в конец)
- Modify: `python/test_rewrite.py` (добавить классы)

**Interfaces:**
- Consumes: `split_command` из Task 1 (структура команды `{"key","title","instruction"}`)
- Produces: `RULES: str` — общие правила для системной инструкции
- Produces: `build_messages(text: str, cmd: dict) -> list[dict]` — `[{"role": "system", ...}, {"role": "user", ...}]`
- Produces: `humanize(text: str) -> str`
- Produces: `accept(original: str, result: str, cmd: dict) -> bool`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `python/test_rewrite.py` перед `if __name__`:

```python
class BuildMessages(unittest.TestCase):
    def test_system_has_rules_and_task(self):
        _, cmd = rewrite.split_command("Текст. Официально.")
        msgs = rewrite.build_messages("Текст.", cmd)
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("Верни только", msgs[0]["content"])
        self.assertIn("длинного тире", msgs[0]["content"])
        self.assertIn(cmd["instruction"], msgs[0]["content"])
        self.assertEqual(msgs[1]["content"], "Текст.")

    def test_free_instruction_goes_as_task(self):
        _, cmd = rewrite.split_command("Текст. Команда: сделай списком.")
        msgs = rewrite.build_messages("Текст.", cmd)
        self.assertIn("сделай списком", msgs[0]["content"])


class Humanize(unittest.TestCase):
    """Модель иногда не слушается инструкции — следы ИИ снимаем жёстко."""

    def test_dashes_become_hyphen(self):
        self.assertEqual(rewrite.humanize("Срок — пятница, 5–10 штук"), "Срок - пятница, 5 - 10 штук")

    def test_markdown_removed(self):
        self.assertEqual(rewrite.humanize("## Заголовок\n**жирно** и *курсив*\n- пункт\n* ещё"),
                         "Заголовок\nжирно и курсив\nпункт\nещё")

    def test_exclamations_collapsed(self):
        self.assertEqual(rewrite.humanize("Ура!!! Готово!!"), "Ура! Готово!")

    def test_think_block_and_wrapping_quotes_removed(self):
        self.assertEqual(rewrite.humanize("<think>\nдумаю\n</think>\n«Готово.»"), "Готово.")
        self.assertEqual(rewrite.humanize('"Готово."'), "Готово.")

    def test_inner_quotes_kept(self):
        self.assertEqual(rewrite.humanize("Проект «Альфа» готов."), "Проект «Альфа» готов.")

    def test_whitespace_normalized(self):
        self.assertEqual(rewrite.humanize("  а   б \n\n\n\n в  "), "а б\n\nв")


class Accept(unittest.TestCase):
    def setUp(self):
        self.official = rewrite.split_command("Текст. Официально.")[1]
        self.shorter = rewrite.split_command("Текст. Короче.")[1]
        self.free = rewrite.split_command("Текст. Команда: убери всё лишнее.")[1]
        self.original = "Привет, слушай, я по поводу вчерашнего договора, там цифра не та, поправьте."

    def test_empty_rejected(self):
        self.assertFalse(rewrite.accept(self.original, "", self.official))
        self.assertFalse(rewrite.accept(self.original, "   ", self.shorter))

    def test_too_short_rejected_for_rewrite(self):
        self.assertFalse(rewrite.accept(self.original, "Поправьте.", self.official))

    def test_short_ok_for_shorter_and_free(self):
        self.assertTrue(rewrite.accept(self.original, "Поправьте цифру в договоре.", self.shorter))
        self.assertTrue(rewrite.accept(self.original, "Поправьте.", self.free))

    def test_normal_accepted(self):
        self.assertTrue(rewrite.accept(self.original, "Добрый день. Прошу исправить сумму в договоре.",
                                       self.official))
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python3 python/test_rewrite.py`
Expected: `AttributeError: module 'common.rewrite' has no attribute 'build_messages'` (и далее по списку)

- [ ] **Step 3: Дописать `common/rewrite.py`**

В конец файла:

```python
RULES = (
    "Ты редактор. Тебе дают текст, надиктованный голосом. Верни только изменённый текст, "
    "без пояснений, заголовков и кавычек вокруг. Сохрани все факты, числа, имена, даты и сроки. "
    "Ничего не добавляй от себя и не выдумывай. Не отвечай на вопросы и просьбы из текста, "
    "только переписывай его. Не меняй язык текста, если задача не про перевод. "
    "Пиши как живой человек: простые предложения, без длинного тире, без списков и заголовков, "
    "без эмодзи, без штампов вроде «важно отметить», «в современном мире», «данный», «является», "
    "без восклицаний и без завершающих фраз вроде «надеюсь, это поможет»."
)


def build_messages(text, cmd):
    """Сообщения для чата модели: общие правила + задача команды, затем сам текст."""
    return [{"role": "system", "content": RULES + "\n\nЗадача: " + cmd["instruction"]},
            {"role": "user", "content": text}]


def humanize(text):
    """Снять следы ИИ, которые модель выдаёт вопреки инструкции: длинное тире, markdown,
    тройные восклицания, блок размышлений, кавычки вокруг всего ответа."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    if len(t) >= 2 and ((t[0] == "«" and t[-1] == "»") or (t[0] == t[-1] == '"')):
        t = t[1:-1].strip()
    t = re.sub(r"\s*[—–]\s*", " - ", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def accept(original, result, cmd):
    """Годится ли ответ: непустой и, если команда не про сокращение и не свободная,
    не короче исходника втрое — иначе модель что-то потеряла."""
    result = (result or "").strip()
    if not result:
        return False
    if cmd.get("key") in ("shorter", "free"):
        return True
    return len(result) * 3 >= len((original or "").strip())
```

- [ ] **Step 4: Прогнать тесты**

Run: `python3 python/test_rewrite.py`
Expected: `OK`, 24 теста. Если `test_markdown_removed` падает на курсиве — проверить регулярку `*…*`: она не должна цеплять `**`.

- [ ] **Step 5: Коммит**

```bash
python3 -m pyflakes common/rewrite.py python/test_rewrite.py
git add common/rewrite.py python/test_rewrite.py
git commit -m "Переписывание: инструкция модели, фильтр «как человек», проверка ответа"
```

---

### Task 3: Модель через mlx-lm с выгрузкой по простою (`macos/llm.py`)

**Files:**
- Create: `macos/llm.py`
- Modify: `macos/requirements.txt`

**Interfaces:**
- Consumes: сообщения из `build_messages` (Task 2)
- Produces: `class Rewriter(log=print)` с методами:
  - `rewrite(model: str, messages: list[dict], idle_sec: float, max_tokens: int | None = None) -> str` — грузит модель при необходимости (другая модель → выгрузить старую), генерирует без размышлений, обновляет время последнего использования
  - `loaded -> bool` (свойство), `idle_left() -> float` (секунды до выгрузки, 0 если не загружена), `unload()`

- [ ] **Step 1: Написать `macos/llm.py`**

```python
"""Локальная языковая модель для переписывания текста (mlx-lm, Apple Silicon).

Грузится при первой команде, выгружается после idle_sec простоя — память занята
только пока модель реально нужна. Один экземпляр на воркер.
"""
import os
import time


class Rewriter:
    def __init__(self, log=print):
        self.log = log
        self.model_name = None
        self.model = None
        self.tok = None
        self.last_use = 0.0
        self.idle_sec = 60.0

    @property
    def loaded(self):
        return self.model is not None

    def idle_left(self):
        if not self.loaded:
            return 0.0
        return max(0.0, self.idle_sec - (time.time() - self.last_use))

    def _load(self, name):
        import mlx.core as mx
        from mlx_lm import load

        self.unload()
        t0 = time.time()
        if _cached(name):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        self.model, self.tok = load(name)
        self.model_name = name
        self.log(f"модель переписывания {name} загружена за {time.time() - t0:.1f} с, "
                 f"память {mx.get_active_memory() / 1e9:.1f} ГБ")

    def unload(self):
        if not self.loaded:
            return
        import gc
        import mlx.core as mx

        self.model = self.tok = None
        gc.collect()
        mx.clear_cache()
        self.log(f"модель переписывания {self.model_name} выгружена")
        self.model_name = None

    def rewrite(self, model, messages, idle_sec, max_tokens=None):
        from mlx_lm import generate

        self.idle_sec = float(idle_sec)
        if self.model_name != model:
            self._load(model)
        self.last_use = time.time()
        prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, enable_thinking=False)
        if max_tokens is None:
            max_tokens = 2 * len(self.tok.encode(messages[-1]["content"])) + 64
        text = generate(self.model, self.tok, prompt=prompt, max_tokens=max_tokens, verbose=False)
        self.last_use = time.time()
        return text


def _cached(name):
    import glob

    pattern = os.path.expanduser("~/.cache/huggingface/hub/models--" + name.replace("/", "--")
                                 + "/snapshots/*/config.json")
    return bool(glob.glob(pattern))
```

- [ ] **Step 2: Добавить зависимость**

`macos/requirements.txt`, в конец:

```
mlx-lm>=0.29
```

Поставить в окружение: `~/.f5voice/venv/bin/pip install -q -r macos/requirements.txt`

- [ ] **Step 3: Ручная проверка на M4**

Run (модель уже скачана в `~/.cache/huggingface` после сравнения):

```bash
cd ~/Projects/f5voice && ~/.f5voice/venv/bin/python - <<'EOF'
import sys, time; sys.path.insert(0, "."); sys.path.insert(0, "macos")
from common import rewrite
from llm import Rewriter
r = Rewriter()
body, cmd = rewrite.split_command("привет слушай там в третьем пункте цифра не та надо 150 тысяч а не 115 поправьте до пятницы. Официальный стиль.")
t0 = time.time(); out = r.rewrite(rewrite.DEFAULT_MODEL, rewrite.build_messages(body, cmd), 60)
print(f"{time.time()-t0:.1f} с:", rewrite.humanize(out)); print("loaded:", r.loaded, "idle_left:", round(r.idle_left()))
r.unload(); print("loaded:", r.loaded)
EOF
```

Expected: строка лога о загрузке (~2 с), переписанный текст без «Привет», без тире, `loaded: True`, `idle_left` около 60, затем `loaded: False`.

- [ ] **Step 4: Коммит**

```bash
python3 -m pyflakes macos/llm.py
git add macos/llm.py macos/requirements.txt
git commit -m "Переписывание: модель через mlx-lm с выгрузкой по простою"
```

---

### Task 4: Воркер: команда → статус → модель → ответ

**Files:**
- Modify: `macos/worker.py` (docstring-протокол, импорты, цикл `main`)

**Interfaces:**
- Consumes: `rewrite.split_command/merge_commands/build_messages/humanize/accept/DEFAULT_*` (Task 1–2), `llm.Rewriter` (Task 3)
- Produces (протокол stdout): промежуточная строка `{"status": "rewrite", "command": "<title>"}`; в итоговом ответе поля `rewrite: "<key>"`, `rewrite_sec: float` при успехе, `rewrite_error: "<текст>"` при ошибке (текст в ответе тогда исходный, без команды)
- Produces: `rewrite_settings() -> dict(model, idle_sec, keyword, commands)` — читает `HOME_DIR/config.json` при каждом запросе

- [ ] **Step 1: Обновить docstring протокола** (`macos/worker.py`, строки 15–22)

Заменить блок «Протокол:» на:

```python
Протокол:
  <- {"ready": true, "load_sec": 2.5}
  -> /Users/alex/.f5voice/last.wav
  <- {"status": "rewrite", "command": "официальный стиль"}   только если в хвосте команда
  <- {"text": "Привет", "lang": "ru", "scores": {"ru": 0.99, "en": 0.01}, "fixed": 0, "dur": 2.1, "sec": 0.8,
      "rewrite": "official", "rewrite_sec": 3.2}            rewrite_* — только при команде
  <- {"text": "…исходник без команды…", "rewrite": "official", "rewrite_error": "…"}
  <- {"text": "", "reason": "silence"}       тишина или слишком коротко
  <- {"text": "", "error": "..."}            не смог прочитать файл и т.п.

Настройки переписывания (rewrite_model, rewrite_idle_minutes, rewrite_keyword, rewrite_commands)
воркер читает из ~/.f5voice/config.json сам, при каждом запросе — правки без перезапуска.
"rewrite_model": "" выключает переписывание.
```

- [ ] **Step 2: Импорты и чтение настроек**

После `from common import history  # noqa: E402` добавить:

```python
from common import rewrite  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Rewriter  # noqa: E402
```

После определения `def cache_ready(model):` (перед `def main():`) добавить:

```python
def log(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    sys.stderr.flush()


def rewrite_settings():
    """Настройки переписывания из config.json; файл маленький, читаем на каждый запрос."""
    try:
        with open(HOME_DIR / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    model = cfg.get("rewrite_model", rewrite.DEFAULT_MODEL)
    minutes = cfg.get("rewrite_idle_minutes", rewrite.DEFAULT_IDLE_MINUTES)
    try:
        idle_sec = max(5.0, float(minutes) * 60)
    except (TypeError, ValueError):
        idle_sec = rewrite.DEFAULT_IDLE_MINUTES * 60
    keyword = cfg.get("rewrite_keyword", rewrite.DEFAULT_KEYWORD)
    return {"model": model if isinstance(model, str) else "", "idle_sec": idle_sec,
            "keyword": keyword.strip() if isinstance(keyword, str) else "",
            "commands": rewrite.merge_commands(cfg.get("rewrite_commands"))}
```

Проверить, что stderr воркера попадает в лог приложения: `grep -n "standardError\|errPipe" macos/main.swift`. Если stderr не читается — вместо `log` в stderr использовать `out({"log": msg})` и в Task 5 обработать `obj["log"]` в Swift (`if let m = obj["log"] as? String { log("воркер: " + m); return }`).

- [ ] **Step 3: Цикл с выгрузкой модели и переписыванием**

В `main()` заменить блок от `while True:` до конца функции на:

```python
    llm = Rewriter(log)
    last_use = time.time()
    while True:
        timeout = IDLE_SEC - (time.time() - last_use)
        if llm.loaded:
            timeout = min(timeout, llm.idle_left())
        readable, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout))
        if not readable:
            if llm.loaded and llm.idle_left() <= 0:
                llm.unload()
            if time.time() - last_use >= IDLE_SEC:
                out({"bye": "idle"})
                return
            continue
        line = sys.stdin.readline()
        if not line:  # EOF — приложение закрылось
            return
        path = line.strip()
        if not path:
            continue
        last_use = t1 = time.time()
        try:
            try:
                audio = load_audio(path)
            except ValueError:  # короче 1/8 секунды — случайное нажатие
                out({"text": "", "reason": "silence", "dur": 0})
                continue
            quiet, dur, speech = is_silence(audio)
            if quiet:
                out({"text": "", "reason": "silence", "dur": dur, "speech": speech})
                continue
            text, lang, scores, fixed = recognize(audio)
            extra = {}
            rw = rewrite_settings()
            body, cmd = rewrite.split_command(text, rw["commands"], rw["keyword"]) if rw["model"] else (text, None)
            if cmd:
                out({"status": "rewrite", "command": cmd["title"]})
                t2 = time.time()
                try:
                    result = rewrite.humanize(llm.rewrite(rw["model"], rewrite.build_messages(body, cmd), rw["idle_sec"]))
                    if not rewrite.accept(body, result, cmd):
                        raise ValueError("ответ модели пустой или слишком короткий")
                    text = result
                    extra = {"rewrite": cmd["key"], "rewrite_sec": round(time.time() - t2, 2)}
                except Exception as e:  # noqa: BLE001 — текст терять нельзя, вставляем исходник
                    text = body
                    extra = {"rewrite": cmd["key"], "rewrite_error": f"{type(e).__name__}: {e}"}
                last_use = time.time()
            history.add(HOME_DIR / "history.json", text, lang, time.time() - t1)
            out({"text": text, "lang": lang, "scores": scores, "fixed": fixed,
                 "dur": dur, "speech": speech, "sec": round(time.time() - t1, 2), **extra})
        except Exception as e:  # noqa: BLE001
            out({"text": "", "error": f"{type(e).__name__}: {e}"})
```

- [ ] **Step 4: Ручная проверка воркера без приложения**

Записать тестовый WAV голосом (или взять `~/.f5voice/last.wav` после любой диктовки, сказав в конце «официальный стиль»). Затем:

```bash
cd ~/Projects/f5voice && printf '%s\n' ~/.f5voice/last.wav | F5_IDLE_SEC=20 ~/.f5voice/venv/bin/python -u macos/worker.py
```

Expected: строка `{"ready": …}`, затем `{"status": "rewrite", "command": "официальный стиль"}`, затем ответ с `"rewrite": "official"` и переписанным текстом; в stderr лог загрузки модели. Через 20 с `{"bye": "idle"}`. Повторить с обычной фразой без команды: ответ без полей `rewrite*`, лога загрузки модели нет.

Проверка отказа: временно `"rewrite_model": "mlx-community/no-such-model"` в config.json → ответ с `rewrite_error` и исходным текстом без «официальный стиль». Вернуть настройку.

- [ ] **Step 5: Коммит**

```bash
python3 -m pyflakes macos/worker.py
git add macos/worker.py
git commit -m "Воркер: переписывание по команде, статус приложению, выгрузка модели по простою"
```

---

### Task 5: Приложение: плашка «Переписываю…», таймаут, ошибка переписывания

**Files:**
- Modify: `macos/main.swift:34` (константа таймаута), `:346-359` (Reply, колбэк), `:463-493` (handle line), `:933-936` (подписка), `:1336-1380` (таймаут и обработка ответа)

**Interfaces:**
- Consumes: строки `status`/`rewrite`/`rewrite_error` от воркера (Task 4)
- Produces: `Worker.onStatus: ((String, String) -> Void)?`, `Reply.rewrite: String?`, `Reply.rewriteError: String?`, `let rewriteTimeoutSeconds: Double = 120`

- [ ] **Step 1: Константа**

После строки 34 (`let transcribeTimeoutSeconds…`) добавить:

```swift
let rewriteTimeoutSeconds: Double = 120  // переписывание моделью: загрузка + генерация длинного текста
```

- [ ] **Step 2: Reply и колбэк статуса**

В `struct Reply` добавить поля:

```swift
        var rewrite: String?        // ключ команды переписывания
        var rewriteError: String?   // переписать не вышло — text тогда исходный без команды
```

Рядом с `var onReady: (() -> Void)?` добавить:

```swift
    var onStatus: ((String, String) -> Void)?   // (status, command) — промежуточная строка воркера
```

- [ ] **Step 3: Разбор строки**

В `private func handle(_ line: Data)` после `if obj["bye"] != nil { return }` добавить:

```swift
        if let st = obj["status"] as? String {
            onStatus?(st, (obj["command"] as? String) ?? "")
            return
        }
        if let m = obj["log"] as? String { log("воркер: " + m); return }
```

После `r.reason = obj["reason"] as? String` добавить:

```swift
        r.rewrite = obj["rewrite"] as? String
        r.rewriteError = obj["rewrite_error"] as? String
```

- [ ] **Step 4: Подписка на статус и перенос таймаута**

После блока `worker.onReady = { … }` (строка ~936) добавить:

```swift
        worker.onStatus = { [weak self] status, command in
            guard let self = self, self.state == .transcribing, status == "rewrite" else { return }
            self.hud.show("Переписываю: \(command)…   Esc — отменить", symbol: "wand.and.stars",
                          tint: NSColor(calibratedRed: 0.86, green: 0.7, blue: 1.0, alpha: 1),
                          bars: .wave, animate: .pulse)
            self.transcribeTimeout?.cancel()
            let timeout = DispatchWorkItem { [weak self] in
                guard let self = self, self.state == .transcribing else { return }
                log("модель переписывания не ответила за \(Int(rewriteTimeoutSeconds)) с — перезапускаю воркер")
                self.worker.stop(reason: "переписывание зависло и было прервано")
                self.worker.start()
            }
            self.transcribeTimeout = timeout
            DispatchQueue.main.asyncAfter(deadline: .now() + rewriteTimeoutSeconds, execute: timeout)
        }
```

- [ ] **Step 5: Показ ошибки переписывания, текст всё равно вставляется**

В `private func handle(_ reply: Worker.Reply)` заменить строку `hud.hide()` (перед `log(String(format: "готово…`) на:

```swift
        if let err = reply.rewriteError {
            log("переписать не вышло (\(reply.rewrite ?? "?")): \(err) — вставляю исходный текст")
            play("Basso")
            hud.show("Не смог переписать, вставляю как сказано", symbol: "exclamationmark.triangle.fill",
                     tint: .systemOrange, hideAfter: 3)
        } else {
            if let key = reply.rewrite { log("переписано (\(key))") }
            hud.hide()
        }
```

- [ ] **Step 6: Собрать и проверить**

```bash
cd ~/Projects/f5voice && zsh macos/build.sh && /Applications/F5Voice.app/Contents/MacOS/F5Voice --check | tail -3
```

Expected: `собрано: /Applications/F5Voice.app`, `--check` без ошибок. Перезапустить приложение (значок в строке меню → выход, затем открыть из Launchpad) и продиктовать фразу с «официальный стиль» в конце: плашка «Переписываю: официальный стиль…», затем вставляется переписанный текст. Фраза без команды вставляется как раньше. Лог `~/.f5voice/f5voice.log` содержит «переписано (official)».

- [ ] **Step 7: Коммит**

```bash
git add macos/main.swift
git commit -m "Приложение: плашка «Переписываю…», таймаут на модель, исходник при ошибке переписывания"
```

---

### Task 6: Установщик, пример настроек, README

**Files:**
- Modify: `install-macos.sh:114-122` (после шага модели Whisper)
- Modify: `macos/config.example.json`
- Modify: `README.md:122-141` (после раздела «Голосовые команды») и `:142-178` (настройки)

- [ ] **Step 1: Шаг загрузки модели в установщике**

После блока `snapshot_download` модели Whisper (после строки `PY`, перед `step "Прогрев Python-пакетов…"`) добавить:

```bash
RW_MODEL="$("$HOME_DIR/venv/bin/python" -c "import json;c=json.load(open('$HOME_DIR/config.json'));print(c.get('rewrite_model','mlx-community/Qwen3-4B-4bit'))")"
if [[ -n "$RW_MODEL" ]]; then
    step "Модель для переписывания $RW_MODEL (первый раз около 2,5 ГБ)"
    "$HOME_DIR/venv/bin/python" - "$RW_MODEL" <<'PY' || fail "не удалось скачать модель $RW_MODEL — проверь интернет; выключить переписывание: \"rewrite_model\": \"\" в $HOME_DIR/config.json"
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1])
PY
fi
```

В строке прогрева заменить `import mlx_whisper, numpy` на `import mlx_whisper, mlx_lm, numpy`.

- [ ] **Step 2: Пример настроек**

В `macos/config.example.json` после `"style": "glass"` (добавить запятую после него):

```json
  "rewrite_model": "mlx-community/Qwen3-4B-4bit",
  "rewrite_idle_minutes": 1,
  "rewrite_keyword": "команда",
  "rewrite_commands": {}
```

- [ ] **Step 3: README**

После абзаца про Shift+Enter в разделе «Голосовые команды» добавить:

```markdown
### Переписать сказанное (только macOS)

Скажите команду в самом конце фразы, после паузы: «…скиньте договор до
пятницы. Официальный стиль». Локальная модель (Qwen3 4B через mlx-lm, около
2,5 ГБ, качается при установке) перепишет текст, и вставится уже результат.
Пока она работает, плашка показывает «Переписываю…», обычно 2–5 секунд.
Без команды модель не участвует и памяти не занимает: она загружается по
команде и выгружается через минуту простоя.

| команда в конце | что делает |
|---|---|
| официальный стиль, официально, в деловом стиле | деловое письмо, на Вы, без разговорных слов |
| короче, сократи | вдвое короче, факты и сроки сохраняются |
| исправь ошибки, грамотно | только орфография и пунктуация |
| по-английски, переведи на английский | перевод |
| улучши подачу, понятнее | яснее донести мысль, без воды |
| технический стиль, технически | IT-терминология, точные формулировки |
| команда: …любая инструкция… | например «команда: сделай списком без грубости» |

Модель пишет как человек: без длинного тире, списков, заголовков и штампов.
Если переписать не вышло, вставляется сказанное без фразы-команды. Свои
команды — в `rewrite_commands` в config.json.
```

В разделе настроек добавить абзац:

```markdown
Переписывание: `rewrite_model` (по умолчанию `mlx-community/Qwen3-4B-4bit`;
`""` выключает, `mlx-community/Qwen3-8B-4bit` — точнее, но 5 ГБ памяти),
`rewrite_idle_minutes` (выгрузка модели после простоя, 1),
`rewrite_keyword` («команда»), `rewrite_commands`
(`{"вежливо|повежливее": "Перепиши вежливо, сохранив смысл."}` — триггеры через
`|`, свои переопределяют встроенные). Правки применяются без перезапуска.
```

- [ ] **Step 4: Проверка установщика**

```bash
bash -n install-macos.sh && cd ~/Projects/f5voice && F5VOICE_NO_SERVICE=1 ./install-macos.sh 2>&1 | tail -15
```

Expected: шаг «Модель для переписывания …» проходит мгновенно (уже в кэше), сборка и проверка без ошибок. Затем запустить приложение обычным способом.

- [ ] **Step 5: Коммит и пуш**

```bash
git add install-macos.sh macos/config.example.json README.md
git commit -m "Установщик и README: модель для переписывания и её настройки"
git push origin main
```

---

### Task 7: Живая проверка на M4

- [ ] Продиктовать четыре фразы и сверить результат с ожиданием:
  1. Без команды → текст как раньше, в логе нет загрузки модели.
  2. «…Официальный стиль» → деловой текст, без «Привет», без тире; лог «переписано (official)», `rewrite_sec` 2–6 с.
  3. «…Команда: сделай короче и вежливее» → переписанный текст; лог «переписано (free)».
  4. Через полторы минуты после пункта 3 в логе строка «модель переписывания … выгружена».
- [ ] Проверить память: `ps -o rss= -p $(pgrep -f macos/worker.py)` до команды, сразу после и через 2 минуты: возвращается к значению до команды (± 300 МБ).
- [ ] Записать результаты в сообщение пользователю, при расхождении — вернуться к соответствующей задаче.
