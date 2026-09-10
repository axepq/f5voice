# -*- coding: utf-8 -*-
"""Плашка Windows/Linux: рендер кадра Pillow и подготовка пикселей для слоистого окна.
Запуск: python python/test_hud.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hud  # noqa: E402


class Frames(unittest.TestCase):
    def test_width_follows_text_and_nothing_overflows(self):
        r = hud.Renderer("dark", scale=1.0, shadow=False)
        short = r.frame("info", "Готово", None, 0.0)
        long = r.frame("info", "Очень длинный текст подсказки, который раньше вылезал за пределы плашки на Windows", None, 0.0)
        self.assertEqual(short.height, hud.Renderer.H)
        self.assertGreater(long.width, short.width)
        self.assertLessEqual(long.width, hud.Renderer.MAX_WIDTH)
        self.assertEqual(long.mode, "RGBA")

    def test_every_state_renders_with_bars_and_shadow(self):
        r = hud.Renderer("light", scale=1.5, shadow=True)
        bars = [(46 + i * 6, 3 + i * 2, 1.0) for i in range(11)]
        for state in ("recording", "transcribing", "ok", "error", "info"):
            img = r.frame(state, "Говорите…   Ctrl+Alt+Space — готово", bars, 1.3)
            self.assertEqual(img.mode, "RGBA")
            self.assertEqual(img.height, round((hud.Renderer.H + 2 * hud.Renderer.MARGIN) * 1.5))
        self.assertGreater(max(img.getchannel("A").getdata()), 200)   # плашка непрозрачна внутри
        self.assertEqual(img.getpixel((0, 0))[3], 0)   # угол с тенью прозрачен

    def test_old_style_names_map_to_basic_ones(self):
        self.assertEqual(hud.norm_style("glass"), "dark")
        self.assertEqual(hud.norm_style("metal"), "graphite")
        self.assertEqual(hud.norm_style("light"), "light")
        self.assertEqual(hud.norm_style("nonsense"), "dark")


class Pixels(unittest.TestCase):
    def test_premultiplied_bgra(self):
        from PIL import Image

        img = Image.new("RGBA", (2, 1))
        img.putpixel((0, 0), (255, 0, 0, 255))      # красный, непрозрачный
        img.putpixel((1, 0), (200, 100, 0, 128))    # полупрозрачный оранжевый
        data = hud.premultiplied_bgra(img)
        self.assertEqual(data[:4], bytes([0, 0, 255, 255]))
        self.assertEqual(data[4:8], bytes([0, 50, 100, 128]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
