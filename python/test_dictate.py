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




class HoldGate(unittest.TestCase):
    """Нажал-отпустил — переключение; держишь дольше порога — запись до отпускания."""

    def test_short_tap_toggles(self):
        g = dictate.HoldGate(hold_after=0.35)
        self.assertEqual(g.press(recording=False, now=0.0), "start")
        self.assertEqual(g.release(recording=True, now=0.1), "none")     # короткое нажатие: запись идёт
        self.assertEqual(g.press(recording=True, now=2.0), "stop")       # второе нажатие — стоп
        self.assertEqual(g.release(recording=False, now=2.1), "none")

    def test_hold_records_until_release(self):
        g = dictate.HoldGate(hold_after=0.35)
        self.assertEqual(g.press(recording=False, now=0.0), "start")
        self.assertEqual(g.release(recording=True, now=0.8), "stop")     # держал — отпустил — распознаём

    def test_autorepeat_while_held_is_ignored(self):
        g = dictate.HoldGate(hold_after=0.35)
        self.assertEqual(g.press(recording=False, now=0.0), "start")
        self.assertEqual(g.press(recording=True, now=0.5), "none")       # автоповтор клавиши
        self.assertEqual(g.release(recording=True, now=0.9), "stop")

    def test_release_after_stop_by_second_tap_does_nothing(self):
        g = dictate.HoldGate(hold_after=0.35)
        g.press(recording=False, now=0.0)
        g.release(recording=True, now=0.1)
        self.assertEqual(g.press(recording=True, now=5.0), "stop")
        self.assertEqual(g.release(recording=False, now=5.9), "none")    # долго держал второе нажатие — ничего


class RecordModes(unittest.TestCase):
    def test_toggle_mode_ignores_long_hold(self):
        g = dictate.HoldGate(mode="toggle", hold_after=0.35)
        self.assertEqual(g.press(recording=False, now=0.0), "start")
        self.assertEqual(g.release(recording=True, now=3.0), "none")     # держал долго — запись продолжается
        self.assertEqual(g.press(recording=True, now=4.0), "stop")

    def test_hold_mode_stops_on_any_release(self):
        g = dictate.HoldGate(mode="hold", hold_after=0.35)
        self.assertEqual(g.press(recording=False, now=0.0), "start")
        self.assertEqual(g.release(recording=True, now=0.1), "stop")     # даже короткое нажатие — отпустил, стоп

    def test_auto_is_default(self):
        self.assertEqual(dictate.HoldGate().mode, "auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
