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

    def _load(self, name, on_download=None):
        import mlx.core as mx
        from mlx_lm import load

        self.unload()
        t0 = time.time()
        if not _cached(name) and on_download:
            on_download(name)  # приложение покажет «скачиваю модель» и не сочтёт воркер зависшим
        # Воркер выставляет HF_HUB_OFFLINE=1 ради Whisper, а huggingface_hub читает его один раз при импорте:
        # новую модель переписывания иначе никогда не скачать. Переключаем флаг только на время загрузки.
        from huggingface_hub import constants as hf
        was = hf.HF_HUB_OFFLINE
        hf.HF_HUB_OFFLINE = _cached(name)
        try:
            self.model, self.tok = load(name)
        finally:
            hf.HF_HUB_OFFLINE = was
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

    def rewrite(self, model, messages, idle_sec, max_tokens=None, thinking=False, on_download=None):
        """Ответ модели на messages. thinking — режим размышлений Qwen3 (медленнее, точнее на задачах
        с расчётом); блок <think> из ответа снимает rewrite.humanize."""
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_logits_processors

        self.idle_sec = float(idle_sec)
        if self.model_name != model:
            self._load(model, on_download)
        self.last_use = time.time()
        prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, enable_thinking=thinking)
        if max_tokens is None:  # ответ не длиннее утроенного исходника; потолок — чтобы зацикливание не длилось минуты
            max_tokens = min(3 * len(self.tok.encode(messages[-1]["content"])) + 128, 2500)
            if thinking:
                max_tokens += 3000  # размышления съедают токены до ответа
        chunks, finish = [], None
        for r in stream_generate(self.model, self.tok, prompt=prompt, max_tokens=max_tokens,
                                 logits_processors=make_logits_processors(repetition_penalty=1.1)):
            chunks.append(r.text)
            finish = r.finish_reason
        self.last_use = time.time()
        if finish == "length":  # упёрлись в потолок: ответ оборван на полуслове или модель зациклилась
            raise ValueError(f"ответ модели оборван на {max_tokens} токенах")
        return "".join(chunks)


def _cached(name):
    pattern = os.path.expanduser("~/.cache/huggingface/hub/models--" + name.replace("/", "--")
                                 + "/snapshots/*/config.json")
    return bool(glob.glob(pattern))
