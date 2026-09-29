"""Потоковое распознавание ElevenLabs Scribe Realtime: звук уходит на сервер, пока человек говорит.

Зачем. Обычный запрос (sttapi.transcribe) отправляет файл целиком после записи, и 29.09 такие
запросы стали отвечать от 2 до 77+ с. Потоковый сервер в те же минуты отдавал текст через
0,25 с после отпускания клавиши — на том же файле, слово в слово. Поэтому основной путь теперь
поток, а обычный запрос — запасной, если поток не сложился.

Как. Приложение пишет запись в last.wav (AVAudioRecorder, PCM 16 кГц моно 16 бит) и в начале
записи шлёт воркеру пинг. Воркер открывает WebSocket и дочитывает растущий файл, отправляя новые
байты. Когда запись окончена и приложение прислало путь, finish() досылает остаток, делает commit
и ждёт текст. Любой сбой — None, и распознаёт обычный запрос.
"""
import base64
import json
import os
import queue
import threading
import time

try:
    from websockets.sync.client import connect
except ImportError:  # зависимости нет — остаётся обычный запрос
    connect = None

URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
MODEL = "scribe_v2_realtime"
CHUNK = 16000 * 2 // 4   # 250 мс звука; сервер принимает куски до секунды
TICK = 0.1


def _data_offset(f):
    """Смещение звуковых данных в WAV. AVAudioRecorder вставляет перед data служебный
    чанк FLLR, поэтому 44 байта предполагать нельзя. None — заголовок ещё не записан."""
    f.seek(0)
    head = f.read(4096)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(head):
        cid, size = head[pos:pos + 4], int.from_bytes(head[pos + 4:pos + 8], "little")
        if cid == b"data":
            return pos + 8
        pos += 8 + size + (size & 1)
    return None


class Session:
    """Одна запись — одна сессия. Живёт в своём потоке, пока не придёт finish() или close()."""

    def __init__(self, path, key, log, not_before_ino=None):
        self.path, self.key, self.log = path, key, log
        self.not_before_ino = not_before_ino   # inode прошлой записи: её не отправлять
        self.ino = None
        self.sent = 0                  # сколько байт звука уже ушло
        self.error = None
        self.ready = threading.Event()  # сессия открыта сервером
        self.done = threading.Event()   # приложение прислало путь — дослать и закоммитить
        self.closed = threading.Event()
        self.commits = queue.Queue()    # тексты committed_transcript
        self.ws = None
        self.lock = threading.Lock()
        self.pump_lock = threading.Lock()   # поток отправки и finish() не дописывают хвост одновременно
        threading.Thread(target=self._run, daemon=True).start()

    # --- поток отправки -------------------------------------------------------------------
    def _run(self):
        try:
            self.ws = connect(f"{URL}?model_id={MODEL}&audio_format=pcm_16000&commit_strategy=manual",
                              additional_headers={"xi-api-key": self.key},
                              open_timeout=5, close_timeout=1, max_size=2 ** 22)
            threading.Thread(target=self._recv, daemon=True).start()
            if not self.ready.wait(5):
                raise RuntimeError("сервер не начал сессию за 5 с")
            f, off = self._open_file()
            idle_since = time.monotonic()
            while not self.closed.is_set():
                if self.done.is_set():
                    return
                with self.pump_lock:
                    grew = self._pump(f, off, final=False)
                idle_since = time.monotonic() if grew else idle_since
                if time.monotonic() - idle_since > 120:   # запись отменили, а путь так и не пришёл
                    raise RuntimeError("запись не растёт 2 мин — сессия закрыта")
                time.sleep(TICK)
        except Exception as e:  # noqa: BLE001 — любой сбой уводит на обычный запрос
            self.error = f"{type(e).__name__}: {e}"
            self.close()

    def _open_file(self):
        """Дождаться файла новой записи: старый last.wav приложение удаляет чуть позже пинга."""
        end = time.monotonic() + 5
        while time.monotonic() < end and not self.closed.is_set():
            try:
                st = os.stat(self.path)
                if st.st_ino != self.not_before_ino:
                    f = open(self.path, "rb")
                    if os.fstat(f.fileno()).st_ino == st.st_ino:
                        off = _data_offset(f)
                        if off is not None:
                            self.ino = st.st_ino
                            return f, off
                    f.close()
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError("файл записи не появился")

    def _pump(self, f, off, final):
        """Отправить то, что дописалось с прошлого раза. Только целые сэмплы."""
        f.seek(0, os.SEEK_END)
        avail = f.tell() - off
        if final:  # после stop() заголовок знает точный размер данных
            f.seek(off - 4)
            size = int.from_bytes(f.read(4), "little")
            if 0 < size <= avail:
                avail = size
        avail -= avail & 1
        grew = False
        while avail - self.sent >= (1 if final else CHUNK):
            n = min(CHUNK, avail - self.sent)
            f.seek(off + self.sent)
            data = f.read(n)
            if not data:
                break
            self._send({"message_type": "input_audio_chunk", "sample_rate": 16000,
                        "audio_base_64": base64.b64encode(data).decode()})
            self.sent += len(data)
            grew = True
        return grew

    def _send(self, obj):
        with self.lock:
            self.ws.send(json.dumps(obj))

    # --- поток приёма ---------------------------------------------------------------------
    def _recv(self):
        try:
            for raw in self.ws:
                m = json.loads(raw)
                kind = m.get("message_type", "")
                if kind == "session_started":
                    self.ready.set()
                elif kind == "committed_transcript":
                    self.commits.put(m.get("text") or "")
                elif "error" in kind:
                    self.error = f"{kind}: {m.get('error') or m.get('message') or m}"
                    self.commits.put(None)
                    return
        except Exception as e:  # noqa: BLE001
            if not self.closed.is_set():
                self.error = self.error or f"{type(e).__name__}: {e}"
        finally:
            self.commits.put(None)

    # --- из основного потока воркера ------------------------------------------------------
    def finish(self, path, timeout=4.0):
        """Запись закончена: дослать остаток, закоммитить, вернуть текст. None — не вышло."""
        self.done.set()
        try:
            if self.error or not self.ready.wait(1.0):
                return None
            for _ in range(int(1 / TICK) + 5):   # файл мог ещё не открыться
                if self.ino is not None or self.error:
                    break
                time.sleep(TICK)
            if self.ino is None or os.stat(path).st_ino != self.ino:   # в сессии чужой файл — не рискуем текстом
                self.error = self.error or "в потоке другой файл"
                return None
            with self.pump_lock, open(path, "rb") as f:
                off = _data_offset(f)
                if off is None:
                    return None
                self._pump(f, off, final=True)
            if self.sent == 0:
                return None
            texts = []
            while not self.commits.empty():   # сервер мог закоммитить куски сам, по длине
                t = self.commits.get_nowait()
                if t is None:
                    return None
                texts.append(t)
            self._send({"message_type": "input_audio_chunk", "sample_rate": 16000,
                        "audio_base_64": "", "commit": True})
            t = self.commits.get(timeout=timeout)
            if t is None:
                return None
            texts.append(t)
            return " ".join(x.strip() for x in texts if x.strip())
        except queue.Empty:
            self.error = f"текст не пришёл за {timeout:.0f} с после записи"
            return None
        except Exception as e:  # noqa: BLE001
            self.error = self.error or f"{type(e).__name__}: {e}"
            return None
        finally:
            self.close()

    def close(self):
        self.closed.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
