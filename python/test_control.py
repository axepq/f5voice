# -*- coding: utf-8 -*-
"""Тесты канала управления: второй запуск программы должен достучаться до первого.
Запуск: python python/test_control.py
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control  # noqa: E402


class ControlChannel(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.got = []
        self.seen = threading.Event()

    def handler(self, cmd):
        self.got.append(cmd)
        self.seen.set()
        return "ok"

    def test_command_reaches_running_instance(self):
        server = control.ControlServer(self.handler, self.home).start()
        try:
            self.assertTrue(control.send("settings", self.home))
            self.assertTrue(self.seen.wait(2))
            self.assertEqual(self.got, ["settings"])
        finally:
            server.stop()

    def test_send_when_nothing_is_running_is_false(self):
        self.assertFalse(control.send("settings", self.home))
        server = control.ControlServer(self.handler, self.home).start()
        server.stop()
        self.assertFalse(control.send("settings", self.home))   # файл убран, порт закрыт
        self.assertEqual(self.got, [])

    def test_wrong_token_is_ignored(self):
        server = control.ControlServer(self.handler, self.home).start()
        try:
            info = json.loads((self.home / "control.json").read_text(encoding="utf-8"))
            info["token"] = "чужой"
            (self.home / "control.json").write_text(json.dumps(info), encoding="utf-8")
            self.assertFalse(control.send("quit", self.home))
            self.assertEqual(self.got, [])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
