"""Which build is this — Full or Lite?

Lite ships WITHOUT the bundled llama.cpp server (the user brings their own model
server — LM Studio / Ollama — or a cloud key); Full includes the built-in
server. The edition is stamped into the PyInstaller bundle at build time
(`_hearth_edition.txt` beside the frozen app). Running from source reports
'full' — a dev checkout has every feature.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def name() -> str:
    """'full' or 'lite'."""
    try:
        base = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle dir
        if base:
            marker = os.path.join(base, "_hearth_edition.txt")
            if os.path.isfile(marker):
                with open(marker, encoding="utf-8") as f:
                    val = f.read().strip().lower()
                if val in ("full", "lite"):
                    return val
    except Exception:
        pass
    return "full"


def is_lite() -> bool:
    return name() == "lite"


def label() -> str:
    """'Full' or 'Lite', for display."""
    return "Lite" if is_lite() else "Full"
