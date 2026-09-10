"""Тесты чистки текста и голосовых команд: ложные срабатывания в обычной речи, краевые случаи
детектора тишины, чтения аудио и истории."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import audio_io, history, textproc, vad  # noqa: E402


class FalseCommands(unittest.TestCase):
    """Слова-команды внутри обычной речи не должны превращаться в символы."""

    def test_vsyo_ravno_is_not_equals(self):
        self.assertEqual(textproc.finalize("Мне всё равно, что ты думаешь"), "Мне всё равно, что ты думаешь")
        self.assertEqual(textproc.finalize("Мне все равно"), "Мне все равно")

    def test_ravno_between_numbers_still_works(self):
        self.assertEqual(textproc.finalize("Пять минус три равно два точка"), "Пять - три = два.")

    def test_sobaka_only_inside_address(self):
        self.assertEqual(textproc.finalize("У меня собака и кошка"), "У меня собака и кошка")
        self.assertEqual(textproc.finalize("Пиши на alex собака gmail точка com"), "Пиши на alex@gmail.com")

    def test_domain_after_dot_is_lowercased(self):
        self.assertEqual(textproc.finalize("alex собака gmail точка Com"), "alex@gmail.com")


class CleanEdgeCases(unittest.TestCase):
    def test_listing_of_prompt_words_is_not_echo(self):
        self.assertNotEqual(textproc.finalize("GitHub, Supabase, Vercel, Telegram"), "")

    def test_brackets_with_digits_kept(self):
        self.assertEqual(textproc.finalize("массив arr[0] и [1]"), "массив arr[0] и [1]")

    def test_music_tag_removed(self):
        self.assertEqual(textproc.finalize("[музыка] привет"), "привет")

    def test_corrector_only_at_end(self):
        self.assertIn("корректор", textproc.finalize("звонок в корректор а. иванов сегодня").lower())
        self.assertEqual(textproc.finalize("Привет всем. Корректор А. Иванов"), "Привет всем.")

    def test_short_repeats_survive(self):
        self.assertNotEqual(textproc.finalize("ладно, ладно, ладно, ладно, ладно, ладно, ладно, ладно"), "")


class Silence(unittest.TestCase):
    def test_click_does_not_hide_quiet_speech(self):
        t = np.arange(16000 * 3) / 16000.0
        speech = (0.012 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        speech[:16000] = 0.0                       # секунда тишины, потом тихая речь
        quiet, _, _ = vad.is_silence(speech)
        self.assertFalse(quiet)
        with_click = speech.copy()
        with_click[100:180] = 0.35                 # щелчок клавиши в начале
        quiet, _, _ = vad.is_silence(with_click)
        self.assertFalse(quiet)

    def test_stereo_is_accepted(self):
        stereo = np.zeros((16000, 2), dtype=np.float32)
        quiet, dur, _ = vad.is_silence(stereo)
        self.assertTrue(quiet)
        self.assertAlmostEqual(dur, 1.0, places=2)


class LoadAudio(unittest.TestCase):
    def _wav(self, rate, seconds, channels=1):
        import struct
        n = int(rate * seconds)
        data = struct.pack("<%dh" % (n * channels), *([1000] * (n * channels)))
        header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack(
            "<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16) + b"data" + struct.pack("<I", len(data))
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        f.write(header + data)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_min_length_is_checked_after_resampling(self):
        with self.assertRaises(ValueError):
            audio_io.load_audio(self._wav(48000, 0.05))   # 2400 сэмплов на 48 кГц — это 0,05 с, мало
        self.assertEqual(audio_io.load_audio(self._wav(8000, 0.25)).size, 4000)

    def test_stereo_becomes_mono(self):
        audio = audio_io.load_audio(self._wav(16000, 1.0, channels=2))
        self.assertEqual(audio.ndim, 1)
        self.assertEqual(audio.size, 16000)


class History(unittest.TestCase):
    def test_garbage_entries_are_dropped_and_write_is_atomic(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([1, "x", {"text": "старое", "time": "t"}], f)
        history.add(path, "новое")
        items = json.load(open(path, encoding="utf-8"))
        self.assertTrue(all(isinstance(i, dict) for i in items))
        self.assertEqual([i["text"] for i in items], ["старое", "новое"])
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_limit_zero_keeps_one(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "history.json")
        history.add(path, "а", limit=0)
        history.add(path, "б", limit=0)
        self.assertEqual([i["text"] for i in history.load(path)], ["б"])


if __name__ == "__main__":
    unittest.main()
