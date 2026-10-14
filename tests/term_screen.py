"""Drive a terminal program in a real Windows console and read its screen.

    with Screen([python, "-m", "hearth_cli"]) as s:
        s.wait_for("❯")
        s.type("hi\\r")
        print(s.text())

Test-only (pywinpty + pyte); nothing here ships.
"""
from __future__ import annotations

import threading
import time
from typing import List

import pyte
from winpty import PtyProcess


class Screen:
    def __init__(self, argv: List[str], cols: int = 110, rows: int = 32, env=None, cwd=None):
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.proc = PtyProcess.spawn(argv, dimensions=(rows, cols), env=env, cwd=cwd)
        self._lock = threading.Lock()
        self._stop = False
        self.raw = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while not self._stop:
            try:
                data = self.proc.read(4096)
            except EOFError:
                return
            except Exception:
                time.sleep(0.02)
                continue
            if data:
                with self._lock:
                    self.raw.append(data)
                    self.stream.feed(data)

    def type(self, keys: str, delay: float = 0.02):
        for ch in keys:
            self.proc.write(ch)
            time.sleep(delay)

    def text(self) -> str:
        with self._lock:
            return "\n".join(line.rstrip() for line in self.screen.display).rstrip()

    def wait_for(self, needle: str, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.text():
                return True
            time.sleep(0.1)
        return False

    def close(self):
        self._stop = True
        try:
            self.proc.terminate(force=True)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
