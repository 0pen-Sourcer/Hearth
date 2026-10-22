"""Chat apps of the person's own: a Python file in <workspace>/connectors/.

A file there defines a class that inherits hearth.channel_core.Adapter, gives it
a `name` and `label`, sends (`send`, and `send_file` / `edit` / `typing` where the
app can), and receives in a `listen(hub)` method that runs for as long as the
connector does, handing each message to `hub.receive(chat, sender, text, ...)`.
Who may talk, the conversation, questions before risky steps, voice notes and
files all come from Hearth, the same as for Telegram. docs/CONNECTORS.md has a
worked example.

    python -m hearth.custom_connector <file name without .py>
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import List, Optional


def folder() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "connectors")


def available() -> List[str]:
    try:
        return sorted(f[:-3] for f in os.listdir(folder()) if f.endswith(".py") and not f.startswith("_"))
    except OSError:
        return []


def load(name: str):
    """The connector class in <workspace>/connectors/<name>.py, or None."""
    from . import channel_core as cc
    path = os.path.join(folder(), f"{name}.py")
    if not os.path.isfile(path) or os.path.sep in name or "/" in name:
        return None
    spec = importlib.util.spec_from_file_location(f"hearth_connector_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for value in vars(mod).values():
        if isinstance(value, type) and issubclass(value, cc.Adapter) and value is not cc.Adapter \
                and callable(getattr(value, "listen", None)):
            return value
    return None


def run(name: str) -> Optional[str]:
    """Start a connector; returns why it couldn't, else runs until it ends."""
    from . import channel_core as cc
    try:
        cls = load(name)
    except Exception as e:
        return f"{name}.py failed to load: {type(e).__name__}: {e}"
    if cls is None:
        return f"no connector in {os.path.join(folder(), name + '.py')} (a class inheriting Adapter, with listen(hub))"
    cc.use_saved_brain(lambda s: print(f"[{name}] {s}"))
    adapter = cls()
    hub = cc.Hub(adapter, configured_senders=getattr(adapter, "allowed", ()) or ())
    print(f"[{name}] up as {adapter.label}. Ctrl-C to stop.")
    adapter.listen(hub)
    return None


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    why = run(sys.argv[1] if len(sys.argv) > 1 else "")
    if why:
        print(f"[connector] {why}")
        sys.exit(1)
