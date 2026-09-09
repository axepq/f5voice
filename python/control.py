# -*- coding: utf-8 -*-
"""Канал управления работающим экземпляром F5Voice (Windows и Linux).

Программа слушает локальный порт и записывает его вместе со случайным ключом
в ~/.f5voice/control.json. Второй запуск (ярлык, --toggle, --settings) находит
этот файл, шлёт одну команду и выходит; если ответа нет — экземпляр не работает.
"""
import json
import os
import secrets
import socket
import threading
from pathlib import Path

FILE_NAME = "control.json"
COMMANDS = ("ping", "toggle", "cancel", "settings", "quit")


class ControlServer:
    def __init__(self, handler, home):
        self.handler = handler
        self.path = Path(home) / FILE_NAME
        self.token = secrets.token_hex(16)
        self.sock = None
        self.thread = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": os.getpid(), "port": self.sock.getsockname()[1], "token": self.token}),
                             encoding="utf-8")
        self.thread = threading.Thread(target=self._serve, name="control", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        sock, self.sock = self.sock, None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        try:
            self.path.unlink()
        except OSError:
            pass

    def _serve(self):
        while self.sock:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            try:
                conn.settimeout(2)
                line = conn.makefile("r", encoding="utf-8").readline().strip()
                token, _, cmd = line.partition(" ")
                if token != self.token or cmd not in COMMANDS:
                    conn.sendall(b"denied\n")
                    return
                reply = self.handler(cmd) if cmd != "ping" else "ok"
                conn.sendall(((reply or "ok") + "\n").encode("utf-8"))
            except OSError:
                pass


def send(cmd, home, timeout=2.0):
    """True, если работающий экземпляр принял команду."""
    path = Path(home) / FILE_NAME
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as s:
            s.sendall(f"{info.get('token', '')} {cmd}\n".encode("utf-8"))
            return s.makefile("r", encoding="utf-8").readline().strip() not in ("", "denied")
    except (OSError, ValueError, KeyError, TypeError):
        return False
