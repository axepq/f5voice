# -*- coding: utf-8 -*-
"""История диктовок: последние тексты в ~/.f5voice/history.json.
Запуск: python python/test_history.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import history  # noqa: E402


class History(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "history.json"

    def test_newest_first_with_time_and_language(self):
        history.add(self.path, "первая", lang="ru", seconds=1.5)
        history.add(self.path, "second", lang="en", seconds=0.7)
        items = history.load(self.path)
        self.assertEqual([i["text"] for i in items], ["second", "первая"])
        self.assertEqual(items[0]["lang"], "en")
        self.assertRegex(items[0]["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_keeps_only_last_entries(self):
        for i in range(40):
            history.add(self.path, f"текст {i}", limit=30)
        items = history.load(self.path)
        self.assertEqual(len(items), 30)
        self.assertEqual(items[0]["text"], "текст 39")
        self.assertEqual(items[-1]["text"], "текст 10")

    def test_blank_text_is_not_stored_and_broken_file_is_ignored(self):
        history.add(self.path, "   ")
        self.assertEqual(history.load(self.path), [])
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(history.load(self.path), [])
        history.add(self.path, "после поломки")
        self.assertEqual([i["text"] for i in history.load(self.path)], ["после поломки"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
