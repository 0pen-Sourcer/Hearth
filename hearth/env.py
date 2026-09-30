"""Environment settings.

Hearth's variables are named HEARTH_*. The JARVIS_* names from earlier versions
are still read, so existing setups and scripts keep working, and a write sets
both so anything still reading the old name sees the same value.
"""
from __future__ import annotations

import os
from typing import Optional

_NEW, _OLD = "HEARTH_", "JARVIS_"


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(_NEW + name)
    if v is None:
        v = os.environ.get(_OLD + name)
    return default if v is None else v


def set(name: str, value) -> None:  # noqa: A001 - mirrors os.environ's verbs
    os.environ[_NEW + name] = str(value)
    os.environ[_OLD + name] = str(value)


def setdefault(name: str, value) -> str:
    if get(name) is None:
        set(name, value)
    return get(name)


def pop(name: str, default: Optional[str] = None) -> Optional[str]:
    v = get(name, default)
    os.environ.pop(_NEW + name, None)
    os.environ.pop(_OLD + name, None)
    return v
