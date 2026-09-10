"""Локальная языковая модель для переписывания текста (mlx-lm, Apple Silicon).

Грузится при первой команде, выгружается после idle_sec простоя — память занята
только пока модель реально нужна. Один экземпляр на воркер.
"""
import glob
import os
import time

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # «Fetching 9 files» в логе ни к чему


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
    pattern = os.path.expanduser("~/.cache/huggingface/hub/models--" + name.replace("/", "--")
                                 + "/snapshots/*/config.json")
    return bool(glob.glob(pattern))
