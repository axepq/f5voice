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


if __name__ == "__main__":
    unittest.main()
