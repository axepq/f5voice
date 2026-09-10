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

    def test_free_style_on_the_fly(self):
        for tail in ("Сделай в юмористичном стиле.", "напиши в дружеском стиле",
                     "перепиши поэтичным стилем", "а сделай это в саркастичном стиле"):
            body, cmd = rewrite.split_command("Привет, как дела, давно не виделись. " + tail)
            self.assertEqual(body, "Привет, как дела, давно не виделись.", tail)
            self.assertEqual(cmd["key"], "free", tail)
            self.assertIn("стиле", cmd["instruction"].lower())

    def test_free_style_ignores_pronoun_and_plain_speech(self):
        for text in ("Мне нравится, когда пишут в этом стиле", "Мы поговорили в деловом ключе",
                     "Он одет в классном стиле"):
            got = rewrite.split_command(text)
            # либо не команда, либо (для «в этом стиле») точно не free-выдумка
            if got[1] is not None:
                self.assertNotEqual(got[1].get("title", ""), "этом")

    def test_essence_style(self):
        body, cmd = rewrite.split_command("Слушай ну там короче надо это сделать по сути.")
        self.assertEqual(cmd["key"], "essence")
        self.assertIn("суть", cmd["instruction"].lower())

    def test_builtin_styles_listed_for_settings(self):
        for phrase, _ in rewrite.BUILTIN_STYLES:  # каждая фраза из списка настроек реально распознаётся
            self.assertIsNotNone(rewrite.split_command("Текст письма. " + phrase + ".")[1], phrase)

    def test_all_builtin_triggers(self):
        for key, (triggers, _) in rewrite.COMMANDS.items():
            for trig in triggers.split("|"):
                body, cmd = rewrite.split_command(f"Текст письма. {trig}.")
                self.assertEqual(body, "Текст письма.", trig)
                self.assertEqual(cmd["key"], key, trig)

    def test_verb_wrappers(self):
        for tail in ("Сделай в официальном стиле.", "Напиши официально", "а теперь сделай это покороче.",
                     "Переведи на английский.", "давай технический стиль"):
            body, cmd = rewrite.split_command("Скиньте договор до пятницы. " + tail)
            self.assertEqual(body, "Скиньте договор до пятницы.", tail)
            self.assertIsNotNone(cmd, tail)

    def test_single_word_trigger_without_punctuation_is_plain_speech(self):
        for text in ("Я хочу сказать это короче", "Объясни мне это понятнее.", "Мы обсудили это чисто технически.",
                     "Он говорит по-английски", "Пиши грамотно", "Всё сделано официально.",
                     "Скажи, что нового на английском"):
            self.assertEqual(rewrite.split_command(text), (text, None), text)

    def test_multi_word_trigger_without_punctuation_is_command(self):
        body, cmd = rewrite.split_command("Скиньте договор до пятницы официальный стиль")
        self.assertEqual((body, cmd["key"]), ("Скиньте договор до пятницы", "official"))

    def test_keyword_after_comma_needs_colon(self):
        for text in ("Привет, команда, как дела?", "Всем привет, команда собирается в пять.",
                     "Наша компания, команда разработчиков сделает всё"):
            self.assertEqual(rewrite.split_command(text), (text, None), text)
        body, cmd = rewrite.split_command("Скиньте до пятницы, команда: сделай списком.")
        self.assertEqual((body, cmd["key"]), ("Скиньте до пятницы", "free"))

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
        body, cmd = rewrite.split_command("Текст. Инструкция: сделай списком.", keyword="инструкция")
        self.assertEqual((body, cmd["key"]), ("Текст.", "free"))

    def test_keyword_with_builtin_trigger_is_builtin(self):
        body, cmd = rewrite.split_command("Текст. Команда: короче.")
        self.assertEqual((body, cmd["key"]), ("Текст.", "shorter"))

    def test_user_commands_extend_and_override(self):
        cmds = rewrite.merge_commands({"вежливо|повежливее": "Перепиши вежливо.",
                                       "короче": "Сократи втрое."})
        body, cmd = rewrite.split_command("Текст. Повежливее.", cmds)
        self.assertEqual(cmd["instruction"], "Перепиши вежливо.")
        body, cmd = rewrite.split_command("Текст. Короче.", cmds)
        self.assertEqual(cmd["instruction"], "Сократи втрое.")
        self.assertEqual(rewrite.split_command("Текст. Сократи.", cmds)[1]["key"], "shorter")


class BuildMessages(unittest.TestCase):
    def test_system_has_rules_and_task(self):
        _, cmd = rewrite.split_command("Текст. Официально.")
        msgs = rewrite.build_messages("Текст.", cmd)
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("только переписанный", msgs[0]["content"])
        self.assertIn("длинного тире", msgs[0]["content"])
        self.assertIn("Пример.", msgs[0]["content"])
        self.assertIn(cmd["instruction"], msgs[1]["content"])
        self.assertIn("Текст: Текст.", msgs[1]["content"])

    def test_free_instruction_goes_as_task(self):
        _, cmd = rewrite.split_command("Текст. Команда: сделай списком.")
        msgs = rewrite.build_messages("Текст.", cmd)
        self.assertIn("сделай списком", msgs[1]["content"])


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
        self.assertEqual(rewrite.humanize("«Альфа» и «Бета»"), "«Альфа» и «Бета»")

    def test_dash_keeps_newlines_and_dialogue(self):
        self.assertEqual(rewrite.humanize("Первое.\n— Второе"), "Первое.\n- Второе")
        self.assertEqual(rewrite.humanize("— Привет, — сказал он."), "- Привет, - сказал он.")

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

    def test_commentary_instead_of_rewrite_rejected(self):
        for bad in ("В тексте присутствуют разговорные выражения, которые не соответствуют требованиям стиля.",
                    "Извините, я не могу переписать этот текст.",
                    "Текст должен быть переписан в нейтральном стиле без разговорных слов и восклицаний."):
            self.assertFalse(rewrite.accept(self.original, bad, self.official), bad)
            self.assertFalse(rewrite.accept(self.original, bad, self.free), bad)

    def test_polite_openings_accepted(self):
        for ok in ("Извините, я опоздаю на полчаса из-за пробок. Начинайте без меня.",
                   "К сожалению, я задержусь на полчаса. Прошу начать без меня.",
                   "В тексте договора указан неверный срок, прошу исправить."):
            self.assertTrue(rewrite.accept(self.original, ok, self.official), ok)

    def test_normal_accepted(self):
        self.assertTrue(rewrite.accept(self.original, "Добрый день. Прошу исправить сумму в договоре.",
                                       self.official))


class SplitAnswer(unittest.TestCase):
    """«Ответь» в начале фразы — вопрос модели; в середине или без вопроса — обычный текст."""

    def test_question_after_keyword(self):
        self.assertEqual(rewrite.split_answer("Ответь, сколько километров от Москвы до Питера?"),
                         "сколько километров от Москвы до Питера?")
        self.assertEqual(rewrite.split_answer("ответь мне пожалуйста, что такое DNS"), "что такое DNS")
        self.assertEqual(rewrite.split_answer("Ответь что такое DNS."), "что такое DNS.")

    def test_whisper_drops_soft_sign(self):
        # Whisper часто теряет мягкий знак: «Ответь» → «Ответ»
        for start in ("Ответ,", "Ответ"):
            q = rewrite.split_answer(start + " как находить человека по IP")
            self.assertEqual(q, "как находить человека по IP", start)
        # но «Ответьте на письмо» — диктовка человеку, не вопрос
        self.assertIsNone(rewrite.split_answer("Ответьте на письмо до пятницы"))

    def test_keyword_alone_or_inside_is_plain(self):
        for text in ("Ответь.", "Ответь", "Я жду, ответь мне.", "Ответьте на письмо до пятницы", "отвечу завтра"):
            self.assertIsNone(rewrite.split_answer(text), text)

    def test_custom_keyword(self):
        self.assertEqual(rewrite.split_answer("Вопрос: что такое DNS", keyword="вопрос"), "что такое DNS")
        self.assertIsNone(rewrite.split_answer("Ответь, что такое DNS", keyword="вопрос"))

    def test_answer_messages(self):
        msgs = rewrite.build_answer_messages("что такое DNS")
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("длинного тире", msgs[0]["content"])
        self.assertEqual(msgs[1]["content"], "что такое DNS")


if __name__ == "__main__":
    unittest.main()
