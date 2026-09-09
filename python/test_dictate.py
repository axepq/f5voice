# -*- coding: utf-8 -*-
"""Что делает программа при запуске в зависимости от того, работает ли уже другой экземпляр.
Запуск: python python/test_dictate.py
"""
import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dictate  # noqa: E402


class StartupAction(unittest.TestCase):
    def action(self, running, **flags):
        args = argparse.Namespace(toggle=False, cancel=False, settings=False, service=False)
        for k, v in flags.items():
            setattr(args, k, v)
        return dictate.startup_action(args, running)

    def test_shortcut_while_running_opens_window(self):
        self.assertEqual(self.action(True), ("send", "settings"))

    def test_autostart_while_running_exits_quietly(self):
        self.assertEqual(self.action(True, service=True), ("exit", None))

    def test_fresh_start_shows_window_unless_service(self):
        self.assertEqual(self.action(False), ("start", True))
        self.assertEqual(self.action(False, service=True), ("start", False))

    def test_toggle_and_cancel_are_sent_to_running_instance(self):
        self.assertEqual(self.action(True, toggle=True), ("send", "toggle"))
        self.assertEqual(self.action(True, cancel=True), ("send", "cancel"))
        self.assertEqual(self.action(False, toggle=True), ("send", "toggle"))  # ответа не будет → «не запущен»


if __name__ == "__main__":
    unittest.main(verbosity=2)
