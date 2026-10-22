"""Hearth's memory, shared with the coding agents on this PC.

Claude Code, Codex, Cursor and OpenCode each keep their own notes, so the same
thing gets told to each one. Switched on here, each of them gets Hearth's
memory as an MCP server of its own ("hearth-memory"): the saved facts, past
chats and what's going on lately, readable and writable, nothing else. It's
added and removed with each program's own command or config file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

NAME = "hearth-memory"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def launch() -> Dict[str, Any]:
    """How an agent starts Hearth's memory server."""
    if getattr(sys, "frozen", False):
        cmd, args = sys.executable, ["--hearth-run-python", "-m", "hearth.memory_mcp"]
    else:
        cmd = sys.executable
        args = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_mcp.py")]
    env = {"PYTHONIOENCODING": "utf-8"}
    try:
        from .tools import WORKSPACE
        env["HEARTH_WORKSPACE"] = WORKSPACE
    except Exception:
        pass
    return {"command": cmd, "args": args, "env": env}


def _exe(agent: str) -> Optional[List[str]]:
    try:
        from .claude_brain import exe_for
        return exe_for(agent)
    except Exception:
        return None


def _run(argv: List[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


# --- programs with their own `mcp add / get / remove` ------------------------

def _cli_add(agent: str) -> Dict[str, Any]:
    exe = _exe(agent)
    if not exe:
        return {"ok": False, "error": "not installed"}
    l = launch()
    envs: List[str] = []
    for k, v in l["env"].items():
        envs += (["-e", f"{k}={v}"] if agent == "claude-code" else ["--env", f"{k}={v}"])
    scope = ["-s", "user"] if agent == "claude-code" else []
    _cli_remove(agent)                        # a stale entry would make add fail
    # The name goes first: Claude Code's -e takes a list and would swallow it.
    r = _run(exe + ["mcp", "add"] + scope + [NAME] + envs + ["--", l["command"]] + l["args"])
    return {"ok": r.returncode == 0, "error": (r.stderr or r.stdout).strip()[:300] if r.returncode else ""}


def _cli_remove(agent: str) -> Dict[str, Any]:
    exe = _exe(agent)
    if not exe:
        return {"ok": False, "error": "not installed"}
    scope = ["-s", "user"] if agent == "claude-code" else []
    r = _run(exe + ["mcp", "remove"] + scope + [NAME])
    return {"ok": r.returncode == 0}


def _cli_on(agent: str) -> Optional[bool]:
    exe = _exe(agent)
    if not exe:
        return None
    try:
        r = _run(exe + ["mcp", "get", NAME], timeout=30)
    except Exception:
        return False
    return r.returncode == 0


# --- programs that read a JSON config file -----------------------------------

def _json_path(agent: str) -> str:
    home = os.path.expanduser("~")
    if agent == "cursor-agent":
        return os.path.join(home, ".cursor", "mcp.json")
    return os.path.join(home, ".config", "opencode", "opencode.json")


def _json_entry() -> Dict[str, Any]:
    l = launch()
    return {"command": l["command"], "args": l["args"], "env": l["env"]}


def _json_set(agent: str, on: bool) -> Dict[str, Any]:
    path = _json_path(agent)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"couldn't read {path}: {e}"}
    if agent == "cursor-agent":
        servers = data.setdefault("mcpServers", {})
        if on:
            servers[NAME] = _json_entry()
        else:
            servers.pop(NAME, None)
    else:
        servers = data.setdefault("mcp", {})
        if on:
            l = launch()
            servers[NAME] = {"type": "local", "command": [l["command"]] + l["args"],
                             "environment": l["env"], "enabled": True}
        else:
            servers.pop(NAME, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"ok": True}


def _json_on(agent: str) -> Optional[bool]:
    if not _exe(agent):
        return None
    try:
        with open(_json_path(agent), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    box = data.get("mcpServers" if agent == "cursor-agent" else "mcp") or {}
    return NAME in box


AGENTS = {
    "claude-code": {"label": "Claude Code", "how": "cli"},
    "codex": {"label": "Codex", "how": "cli"},
    "cursor-agent": {"label": "Cursor", "how": "json"},
    "opencode": {"label": "OpenCode", "how": "json"},
}


def status() -> List[Dict[str, Any]]:
    """Each agent: installed or not, and whether it has Hearth's memory."""
    out = []
    for agent, a in AGENTS.items():
        on = _cli_on(agent) if a["how"] == "cli" else _json_on(agent)
        out.append({"id": agent, "label": a["label"], "installed": on is not None, "on": bool(on)})
    return out


def set_shared(agent: str, on: bool) -> Dict[str, Any]:
    a = AGENTS.get(agent)
    if not a:
        return {"ok": False, "error": f"unknown agent {agent}"}
    if a["how"] == "cli":
        return _cli_add(agent) if on else _cli_remove(agent)
    if not _exe(agent):
        return {"ok": False, "error": "not installed"}
    return _json_set(agent, on)
