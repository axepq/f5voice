# -*- coding: utf-8 -*-
"""Плашка внизу экрана для Windows и Linux.

Кадр рисует Pillow (RGBA, двукратный суперсэмплинг, объёмная капсула с градиентом, бликом по
кромке и мягкой тенью, иконки, столбики уровня, текст), поэтому края гладкие независимо от
tkinter. На Windows кадр показывается собственным слоистым окном с попиксельной прозрачностью
(UpdateLayeredWindow): тень и скругления настоящие, окно не перехватывает мышь и фокус.
На Linux тот же кадр выводится в окно tkinter поверх непрозрачного фона.
"""
import collections
import ctypes
import math
import platform
import queue
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

IS_WINDOWS = platform.system() == "Windows"
TICK = 1 / 30

# Стили: заливка сверху и снизу, текст, кайма, блик по верхней кромке, плотность тени.
STYLES = {
    "dark": {"top": (52, 52, 58, 236), "bottom": (24, 24, 28, 242), "text": (255, 255, 255),
             "edge": (255, 255, 255, 60), "gloss": (255, 255, 255, 110), "shadow": 120},
    "light": {"top": (255, 255, 255, 244), "bottom": (230, 231, 236, 248), "text": (22, 22, 26),
              "edge": (0, 0, 0, 40), "gloss": (255, 255, 255, 255), "shadow": 80},
    "graphite": {"top": (106, 108, 116, 240), "bottom": (54, 56, 62, 246), "text": (246, 246, 248),
                 "edge": (255, 255, 255, 90), "gloss": (255, 255, 255, 150), "shadow": 120},
}
STYLE_TITLES = {"dark": "тёмный", "light": "светлый", "graphite": "графит"}
LEGACY = {"glass": "dark", "metal": "graphite", "clear": "light"}
COLORS = {"recording": (255, 77, 77), "transcribing": (255, 179, 71), "ok": (90, 211, 107),
          "error": (255, 138, 92), "info": (205, 205, 210)}


def norm_style(name):
    name = LEGACY.get(name, name)
    return name if name in STYLES else "dark"


def _font(px):
    names = ["segoeui.ttf", "C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


class Renderer:
    """Кадр плашки → PIL.Image RGBA. Размеры в логических пикселях, scale — множитель DPI."""

    H = 56
    PAD = 18
    ICON = 22
    GAP = 10
    BARS_W = 62
    MARGIN = 18          # поле под тень
    MAX_WIDTH = 760

    def __init__(self, style, scale=1.0, shadow=True):
        self.style = STYLES[norm_style(style)]
        self.scale = float(scale)
        self.shadow = shadow
        self.k = 2 * self.scale   # суперсэмплинг ×2 поверх DPI
        self.font = _font(max(8, int(round(15 * self.k))))
        self._grad = {}
        self._base = {}

    def _text_width(self, text):
        left, _, right, _ = self.font.getbbox(text)
        return (right - left) / self.k

    def _fit(self, text, max_w):
        if self._text_width(text) <= max_w:
            return text
        while text and self._text_width(text + "…") > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def _gradient(self, w, h):
        key = (w, h)
        if key not in self._grad:
            top, bottom = self.style["top"], self.style["bottom"]
            col = Image.new("RGBA", (1, h))
            px = col.load()
            for y in range(h):
                px[0, y] = _lerp(top, bottom, y / max(1, h - 1))
            self._grad[key] = col.resize((w, h))
        return self._grad[key]

    def _background(self, W, H, M):
        """Тень, капсула с градиентом, кайма и блик — зависят только от ширины, поэтому кэшируются."""
        key = (W, H, M)
        if key in self._base:
            return self._base[key]
        k = self.k
        img = Image.new("RGBA", (W + 2 * M, H + 2 * M), (0, 0, 0, 0))
        if self.shadow:
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle((M, M + 7 * k, M + W, M + H + 7 * k), radius=H / 2,
                                                 fill=(0, 0, 0, self.style["shadow"]))
            img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9 * k)))
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=H / 2, fill=255)
        body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        body.paste(self._gradient(W, H), (0, 0), mask)
        img.alpha_composite(body, (M, M))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((M + 0.5 * k, M + 0.5 * k, M + W - 0.5 * k, M + H - 0.5 * k), radius=H / 2,
                            outline=self.style["edge"], width=max(1, int(round(k))))
        d.line((M + H / 2, M + 1.6 * k, M + W - H / 2, M + 1.6 * k), fill=self.style["gloss"], width=max(1, int(round(k))))
        if len(self._base) > 64:
            self._base.clear()
        self._base[key] = img
        return img

    def frame(self, state, text, bars, phase):
        """state: recording | transcribing | ok | error | info; bars: [(x, h, fade)] или None."""
        k = self.k
        text = text or ""
        has_bars = bars is not None
        lead = self.PAD + self.ICON + self.GAP + (self.BARS_W + self.GAP if has_bars else 0)
        text = self._fit(text, self.MAX_WIDTH - lead - self.PAD)
        w = lead + self._text_width(text) + self.PAD
        W, H = int(round(w * k)), int(round(self.H * k))
        M = int(round(self.MARGIN * k)) if self.shadow else 0
        img = self._background(W, H, M).copy()
        d = ImageDraw.Draw(img)
        cy = M + H / 2
        self._icon(d, state, M + (self.PAD + self.ICON / 2) * k, cy, phase)
        if has_bars:
            x0 = M + (self.PAD + self.ICON + self.GAP) * k
            for bx, bh, fade in bars:
                bh = max(2.0, bh) * k
                fill = _lerp(self.style["bottom"][:3], self.style["text"], max(0.0, min(1.0, fade)))
                left = x0 + (bx - 46) * k
                d.rounded_rectangle((left, cy - bh / 2, left + 3 * k, cy + bh / 2), radius=1.5 * k, fill=fill)
        d.text((M + lead * k, cy), text, font=self.font, fill=self.style["text"], anchor="lm")
        return img.resize((round(img.width / 2), round(img.height / 2)), Image.LANCZOS)

    def _icon(self, d, state, cx, cy, phase):
        k = self.k
        s = self.ICON * k
        color = COLORS.get(state, COLORS["info"])
        white = (255, 255, 255)
        if state == "recording":  # микрофон
            cw, ch = 0.36 * s, 0.56 * s
            top = cy - 0.5 * s
            d.rounded_rectangle((cx - cw / 2, top, cx + cw / 2, top + ch), radius=cw / 2, fill=color)
            aw = 0.68 * s
            d.arc((cx - aw / 2, top + ch * 0.28, cx + aw / 2, top + ch + 0.08 * s), start=0, end=180,
                  fill=color, width=max(1, int(round(2.2 * k))))
            d.line((cx, top + ch + 0.08 * s, cx, cy + 0.5 * s - 0.05 * s), fill=color, width=max(1, int(round(2.2 * k))))
            d.line((cx - 0.22 * s, cy + 0.5 * s, cx + 0.22 * s, cy + 0.5 * s), fill=color, width=max(1, int(round(2.2 * k))))
        elif state == "transcribing":  # волна
            for i in range(5):
                v = 0.35 + 0.65 * abs(math.sin(phase * 1.1 + i * 0.9))
                bh = s * (0.25 + 0.7 * v)
                x = cx - 0.4 * s + i * 0.2 * s
                d.rounded_rectangle((x - 0.06 * s, cy - bh / 2, x + 0.06 * s, cy + bh / 2), radius=0.06 * s, fill=color)
        else:  # кружок с галочкой, крестиком или без ничего
            r = 0.44 * s
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
            lw = max(1, int(round(2.4 * k)))
            if state == "ok":
                d.line((cx - 0.22 * s, cy + 0.02 * s, cx - 0.06 * s, cy + 0.18 * s, cx + 0.24 * s, cy - 0.16 * s),
                       fill=white, width=lw, joint="curve")
            elif state == "error":
                d.line((cx - 0.17 * s, cy - 0.17 * s, cx + 0.17 * s, cy + 0.17 * s), fill=white, width=lw)
                d.line((cx - 0.17 * s, cy + 0.17 * s, cx + 0.17 * s, cy - 0.17 * s), fill=white, width=lw)


def premultiplied_bgra(img):
    """RGBA → байты BGRA с предумноженной альфой, как ждёт UpdateLayeredWindow."""
    arr = np.asarray(img.convert("RGBA"), dtype=np.uint16)
    a = arr[:, :, 3:4]
    rgb = (arr[:, :, :3] * a + 127) // 255
    return np.concatenate([rgb[:, :, ::-1], a], axis=2).astype(np.uint8).tobytes()


class HudState:
    """Что показываем и как анимируем: появление/уход, столбики уровня, таймер скрытия."""

    ALPHA_UP = 0.25
    ALPHA_DOWN = 0.16

    def __init__(self, level_fn):
        self.level_fn = level_fn
        self.state = None
        self.text = ""
        self.hide_at = None
        self.alpha = 0.0
        self.alpha_target = 0.0
        self.closing = False
        self.phase = 0.0
        self.hist = collections.deque([0.0] * 11, maxlen=11)
        self.scroll = 0.0
        self.level = 0.0

    @property
    def active(self):
        return self.state is not None or self.alpha > 0

    def show(self, state, text, ttl=None):
        if self.state != "recording" and state == "recording":
            self.hist.extend([0.0] * len(self.hist))
            self.level = 0.0
        self.state, self.text = state, text
        self.hide_at = time.time() + ttl if ttl else None
        self.closing = False
        self.alpha_target = 1.0

    def hide(self):
        self.closing = True
        self.alpha_target = 0.0

    def step(self):
        """Один кадр. Возвращает True, если после него плашку надо убрать с экрана."""
        self.phase += 0.18
        if self.hide_at and time.time() > self.hide_at:
            self.hide_at = None
            self.hide()
        if self.alpha != self.alpha_target:
            step = self.ALPHA_UP if self.alpha_target > self.alpha else -self.ALPHA_DOWN
            a = self.alpha + step
            if (step > 0 and a >= self.alpha_target) or (step < 0 and a <= self.alpha_target):
                a = self.alpha_target
            self.alpha = a
        if self.closing and self.alpha <= 0.0:
            self.closing = False
            self.state = None
            return True
        return False

    def bars(self):
        x = 46
        if self.state == "recording":
            self.level += (float(self.level_fn() or 0.0) - self.level) * 0.5
            self.scroll += 2.0
            if self.scroll >= 6.0:
                self.scroll -= 6.0
                self.hist.append(max(0.0, min(1.0, self.level)))
            f = self.scroll / 6.0
            return [(x + i * 6 - self.scroll, 3 + 20 * self.hist[i], 1.0 - f if i == 0 else (f if i == 10 else 1.0))
                    for i in range(11)]
        if self.state == "transcribing":
            return [(x + i * 6, 3 + 20 * (0.5 + 0.5 * math.sin(self.phase - i * 0.65)), 1.0) for i in range(10)]
        return None


# ---------------------------------------------------------------- Windows: слоистое окно

if IS_WINDOWS:
    from ctypes import wintypes

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class LayeredWindow:
        """Окно без рамки поверх всех, с попиксельной прозрачностью, не берёт фокус и мышь."""

        CLASS = "F5VoiceHud"

        def __init__(self):
            u, g, k = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
            self.u, self.g = u, g
            u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            u.DefWindowProcW.restype = ctypes.c_ssize_t
            k.GetModuleHandleW.restype = wintypes.HMODULE
            u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            u.CreateWindowExW.restype = wintypes.HWND
            u.GetDC.argtypes = [wintypes.HWND]
            u.GetDC.restype = wintypes.HDC
            u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            g.CreateCompatibleDC.argtypes = [wintypes.HDC]
            g.CreateCompatibleDC.restype = wintypes.HDC
            g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
            g.CreateDIBSection.restype = wintypes.HBITMAP
            g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            g.SelectObject.restype = wintypes.HGDIOBJ
            g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            g.DeleteDC.argtypes = [wintypes.HDC]
            u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                              ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                              wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
            u.UpdateLayeredWindow.restype = wintypes.BOOL
            u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            u.DispatchMessageW.restype = ctypes.c_ssize_t
            u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]

            self._proc = WNDPROC(lambda hwnd, msg, wp, lp: u.DefWindowProcW(hwnd, msg, wp, lp))
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW(0, self._proc, 0, 0, hinst, None, None, None, None, self.CLASS)
            u.RegisterClassW(ctypes.byref(wc))  # повторная регистрация того же класса — не ошибка
            ex = 0x00080000 | 0x8 | 0x80 | 0x20 | 0x08000000  # LAYERED | TOPMOST | TOOLWINDOW | TRANSPARENT | NOACTIVATE
            self.hwnd = u.CreateWindowExW(ex, self.CLASS, "F5Voice", 0x80000000, 0, 0, 1, 1, None, None, hinst, None)
            if not self.hwnd:
                raise OSError(f"CreateWindowExW: ошибка {ctypes.get_last_error()}")
            self.shown = False

        def pump(self):
            msg = wintypes.MSG()
            while self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self.u.TranslateMessage(ctypes.byref(msg))
                self.u.DispatchMessageW(ctypes.byref(msg))

        def work_area(self):
            rect = wintypes.RECT()
            self.u.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom

        def dpi_scale(self):
            try:
                return self.u.GetDpiForSystem() / 96.0
            except AttributeError:
                return 1.0

        def update(self, img, x, y, alpha):
            u, g = self.u, self.g
            w, h = img.size
            data = premultiplied_bgra(img)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biSizeImage = w * h * 4
            screen = u.GetDC(None)
            mem = g.CreateCompatibleDC(screen)
            bits = ctypes.c_void_p()
            bmp = g.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not bmp:
                g.DeleteDC(mem)
                u.ReleaseDC(None, screen)
                raise OSError("CreateDIBSection не удался")
            ctypes.memmove(bits, data, len(data))
            old = g.SelectObject(mem, bmp)
            blend = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
            dst, size, src = wintypes.POINT(int(x), int(y)), wintypes.SIZE(w, h), wintypes.POINT(0, 0)
            u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(dst), ctypes.byref(size), mem,
                                  ctypes.byref(src), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
            g.SelectObject(mem, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mem)
            u.ReleaseDC(None, screen)
            if not self.shown:
                u.ShowWindow(self.hwnd, 8)  # SW_SHOWNA
                self.shown = True

        def hide(self):
            if self.shown:
                self.u.ShowWindow(self.hwnd, 0)
                self.shown = False


class LayeredHud(threading.Thread):
    """Поток плашки на Windows: очередь команд, анимация, кадр → слоистое окно."""

    def __init__(self, style, level_fn, log=print):
        super().__init__(daemon=True, name="hud")
        self.style = norm_style(style)
        self.level_fn = level_fn
        self.log = log
        self.q = queue.Queue()
        self.ok = threading.Event()
        self.failed = False

    def show(self, state, text, ttl=None):
        self.q.put(("show", state, text, ttl))

    def hide(self):
        self.q.put(("hide", None, None, None))

    def run(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            self.failed = True
            self.log(f"плашка отключена: {type(e).__name__}: {e}")
            self.ok.set()

    def _run(self):
        win = LayeredWindow()
        scale = win.dpi_scale()
        renderer = Renderer(self.style, scale=scale, shadow=True)
        state = HudState(self.level_fn)
        self.ok.set()
        while True:
            try:
                while True:
                    cmd, st, text, ttl = self.q.get_nowait()
                    if cmd == "show":
                        state.show(st, text, ttl)
                    else:
                        state.hide()
            except queue.Empty:
                pass
            win.pump()
            if not state.active:
                time.sleep(0.05)
                continue
            t0 = time.perf_counter()
            gone = state.step()
            if gone:
                win.hide()
                continue
            img = renderer.frame(state.state, state.text, state.bars(), state.phase)
            left, top, right, bottom = win.work_area()
            x = (left + right - img.width) // 2
            y = bottom - img.height - int(28 * scale) + int((1.0 - state.alpha) * 10 * scale)
            win.update(img, x, y, int(state.alpha * 255))
            time.sleep(max(0.0, TICK - (time.perf_counter() - t0)))
