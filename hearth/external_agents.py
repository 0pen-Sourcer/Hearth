"""Other coding agents as helpers: Claude Code, Codex, Gemini CLI, OpenCode and
the rest, run headless on the user's own install and subscription.

Each one runs in the background in a working folder and writes the same
transcript as Hearth's own helpers (start, route, tool calls, final text,
end), so it shows up in the Team view and its result reaches the main
assistant the same way. Claude Code's stream is parsed event by event; the
others run in their one-shot mode and their output is the result.

Claude Code note: its headless mode (`claude -p`) is billed to the Agent SDK
credit that comes with a Claude plan, not to the plan's interactive limits.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import env as _env

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _claude_cmd(allow: str) -> List[str]:
    mode = {"auto": "auto", "edits": "acceptEdits"}.get(allow, "plan")
    return ["-p", "--output-format", "stream-json", "--verbose", "--permission-mode", mode]


# id -> how to run it. `args` builds the arguments (the prompt goes on stdin
# unless `prompt_arg` is set). `stream` names the output parser. `allow` is
# how much the user lets agents do on their own: "auto" or "edits". `maker`
# is the company behind it, shown next to its agents in the Team view.
AGENTS: Dict[str, Dict[str, Any]] = {
    "claude-code": {"label": "Claude Code", "maker": "Anthropic", "exe": ["claude"], "args": _claude_cmd,
                    "stream": "claude", "billing": "Agent SDK credit on your Claude plan"},
    "codex": {"label": "Codex", "maker": "OpenAI", "exe": ["codex"],
              "args": lambda allow: ["exec", "--skip-git-repo-check"]
              + (["--full-auto"] if allow in ("auto", "edits") else []) + ["-"], "stream": "text"},
    "antigravity": {"label": "Antigravity", "maker": "Google", "exe": ["agy"],
                    "args": lambda allow: (["--dangerously-skip-permissions"] if allow == "auto" else []) + ["-p"],
                    "stream": "text", "prompt_arg": True},
    "gemini-cli": {"label": "Gemini CLI", "maker": "Google", "exe": ["gemini"],
                   "args": lambda allow: (["--yolo"] if allow == "auto" else []) + ["-p"],
                   "stream": "text", "prompt_arg": True},
    "grok": {"label": "Grok", "maker": "xAI", "exe": ["grok"],
             "args": lambda allow: ["--no-auto-update"] + (["--always-approve"] if allow == "auto" else []) + ["-p"],
             "stream": "text", "prompt_arg": True},
    "kimi": {"label": "Kimi Code", "maker": "Moonshot AI", "exe": ["kimi"],
             "args": lambda allow: ["--print", "-p"], "stream": "text", "prompt_arg": True},
    "opencode": {"label": "OpenCode", "maker": "SST", "exe": ["opencode"], "args": lambda allow: ["run"],
                 "stream": "text", "prompt_arg": True},
    "qwen-code": {"label": "Qwen Code", "maker": "Alibaba", "exe": ["qwen"],
                  "args": lambda allow: (["--yolo"] if allow == "auto" else []) + ["-p"],
                  "stream": "text", "prompt_arg": True},
    "crush": {"label": "Crush", "maker": "Charm", "exe": ["crush"], "args": lambda allow: ["run"],
              "stream": "text", "prompt_arg": True},
    "cursor-agent": {"label": "Cursor Agent", "maker": "Cursor", "exe": ["cursor-agent"],
                     "args": lambda allow: ["-p"] + (["--force"] if allow == "auto" else []),
                     "stream": "text", "prompt_arg": True},
    "copilot": {"label": "Copilot CLI", "maker": "GitHub", "exe": ["copilot"],
                "args": lambda allow: (["--allow-all-tools"] if allow == "auto" else []) + ["-p"],
                "stream": "text", "prompt_arg": True},
    "goose": {"label": "Goose", "maker": "Block", "exe": ["goose"],
              "args": lambda allow: ["run", "--no-session", "-t"], "stream": "text", "prompt_arg": True},
    "aider": {"label": "Aider", "maker": "Aider", "exe": ["aider"],
              "args": lambda allow: (["--yes-always"] if allow in ("auto", "edits") else []) + ["--message"],
              "stream": "text", "prompt_arg": True},
    "droid": {"label": "Droid", "maker": "Factory", "exe": ["droid"],
              "args": lambda allow: ["exec", "--auto", "medium" if allow == "auto" else "low"],
              "stream": "text", "prompt_arg": True},
    "amp": {"label": "Amp", "maker": "Sourcegraph", "exe": ["amp"],
            "args": lambda allow: (["--dangerously-allow-all"] if allow == "auto" else []) + ["-x"],
            "stream": "text", "prompt_arg": True},
    "pi": {"label": "Pi", "maker": "pi.dev", "exe": ["pi"], "args": lambda allow: ["-p"],
           "stream": "text", "prompt_arg": True},
}
# Only these have been run end to end from Hearth; the rest follow their docs.
VERIFIED = {"claude-code"}


def _which(names: List[str]) -> Optional[str]:
    for n in names:
        for cand in (n + ".cmd", n + ".exe", n) if sys.platform == "win32" else (n,):
            p = shutil.which(cand)
            if p:
                return p
    return None


def available() -> List[Dict[str, Any]]:
    """The agent CLIs installed on this machine."""
    out = []
    for aid, spec in AGENTS.items():
        path = _which(spec["exe"])
        if path:
            out.append({"id": aid, "label": spec["label"], "maker": spec.get("maker", ""), "path": path,
                        "tested": aid in VERIFIED, "billing": spec.get("billing", "")})
    return out


def describe() -> str:
    """One line for tool descriptions: what can be delegated to here."""
    found = available()
    if not found:
        return "No other agent CLIs are installed here."
    return "Installed here: " + ", ".join(f"{a['label']} (agent='{a['id']}')" for a in found) + "."


def _direct_argv(exe: str) -> List[str]:
    """How to start an installed CLI without cmd.exe in between.

    npm installs a CLI on Windows as a .cmd file, and running a .cmd means
    cmd.exe parses the arguments: a prompt carrying & or | could run a second
    command. The shim only starts node on a script, so start that directly.
    Anything that can't be resolved that way is refused for argument prompts
    by the caller."""
    if sys.platform != "win32" or not exe.lower().endswith((".cmd", ".bat")):
        return [exe]
    try:
        text = open(exe, encoding="utf-8", errors="replace").read()
    except OSError:
        return [exe]
    import re as _re
    found = [t for t in _re.findall(r'"%(?:~?dp0%?)\\?([^"]+?\.(?:(?:c|m)?js|exe))"', text, _re.I)
             if os.path.basename(t).lower() != "node.exe"]
    if not found:
        return [exe]
    script = os.path.join(os.path.dirname(exe), found[0].lstrip("\\"))
    if script.lower().endswith(".exe"):           # newer shims wrap a native binary
        return [script] if os.path.isfile(script) else [exe]
    node = os.path.join(os.path.dirname(exe), "node.exe")
    node = node if os.path.isfile(node) else shutil.which("node")
    if node and os.path.isfile(script):
        return [node, script]
    return [exe]


def _long_path(p: str) -> str:
    """Expand Windows 8.3 short names (NAME~1 folders): agents treat them as
    suspicious and refuse to read inside them."""
    if sys.platform != "win32":
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, len(buf))
        return buf.value if 0 < n < len(buf) else p
    except Exception:
        return p


def _allow_level() -> str:
    """How much an external agent may do without asking, from the user's own
    standing choices: auto-approve lets it act freely, otherwise it may edit
    files in its folder, and never more than the user allowed Hearth."""
    if _env.get("AUTO_APPROVE") in ("1", "true", "yes"):
        return "auto"
    try:
        from .tools import approval_mode
        return "auto" if approval_mode() == "auto" else "edits"
    except Exception:
        return "edits"


def _parse_claude(line: str, emit: Callable[[Dict[str, Any]], None], state: Dict[str, Any]) -> None:
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        state["model"] = ev.get("model", "")
    elif t == "assistant":
        blocks = (ev.get("message") or {}).get("content") or []
        calls = [{"name": b.get("name", ""), "args": json.dumps(b.get("input") or {})[:400]}
                 for b in blocks if b.get("type") == "tool_use"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if calls:
            emit({"kind": "assistant_tool_call", "content": text, "calls": calls})
            state["pending"] = [c["name"] for c in calls]
        elif text:
            state["last_text"] = text
    elif t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                name = state["pending"].pop(0) if state.get("pending") else "tool"
                body = b.get("content")
                if isinstance(body, list):
                    body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                body = str(body or "")
                emit({"kind": "tool_result", "name": name, "chars": len(body), "preview": body[:400]})
                state["tools"] = state.get("tools", 0) + 1
    elif t == "result":
        state["result"] = ev.get("result") or state.get("last_text", "")
        state["ok"] = not ev.get("is_error") and ev.get("subtype") == "success"
        if not state["ok"]:
            state["error"] = ev.get("result") or ev.get("subtype") or "the agent stopped with an error"


def spawn(agent: str, prompt: str, *, name: str = "", cwd: str = "",
          on_done: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Start an external agent in the background. Returns {ok, agent_id} or {ok: False, error}."""
    from . import subagents as _sa
    spec = AGENTS.get(agent)
    if not spec:
        return {"ok": False, "error": f"unknown agent '{agent}'. Known: {', '.join(AGENTS)}"}
    exe = _which(spec["exe"])
    if not exe:
        return {"ok": False, "error": f"{spec['label']} is not installed on this machine"}
    work = _long_path(os.path.abspath(cwd or _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")))
    if not os.path.isdir(work):
        return {"ok": False, "error": f"folder not found: {work}"}
    allow = _allow_level()
    args = spec["args"](allow)
    head = _direct_argv(exe)
    if spec.get("prompt_arg") and head[0].lower().endswith((".cmd", ".bat")):
        return {"ok": False, "error": f"{spec['label']} is installed as a script Hearth can't start safely "
                                      "with a prompt; reinstall it from npm or its own installer"}
    argv = head + args + ([prompt] if spec.get("prompt_arg") else [])
    agent_id = f"sub_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    tpath = _sa._transcript_path(agent_id)
    _sa._append_transcript(tpath, {"kind": "start", "persona": spec["label"], "name": name or spec["label"],
                                   "prompt": prompt, "runtime": agent, "cwd": work})
    _sa._append_transcript(tpath, {"kind": "route", "runtime": agent, "exe": exe, "allow": allow,
                                   "billing": spec.get("billing", "")})
    with _sa._BG_RESULTS_LOCK:
        _sa._BG_RUNNING.add(agent_id)

    def run() -> None:
        state: Dict[str, Any] = {"pending": [], "tools": 0}
        out_lines: List[str] = []
        emit = lambda rec: _sa._append_transcript(tpath, rec)
        try:
            proc = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    creationflags=_NO_WINDOW)
            try:
                if not spec.get("prompt_arg"):
                    proc.stdin.write(prompt)
                proc.stdin.close()
            except OSError:
                pass
            for line in proc.stdout:
                line = line.rstrip("\n")
                if spec["stream"] == "claude":
                    _parse_claude(line, emit, state)
                elif line.strip():
                    out_lines.append(line)
            code = proc.wait()
            if spec["stream"] == "claude":
                ok = bool(state.get("ok")) and code == 0
                text = state.get("result") or state.get("last_text", "")
                err = state.get("error") or (f"exited with code {code}" if not ok else "")
            else:
                text = "\n".join(out_lines[-200:])
                ok = code == 0
                err = "" if ok else f"exited with code {code}: " + "\n".join(out_lines[-5:])
        except Exception as e:
            ok, text, err = False, "", f"{type(e).__name__}: {e}"
        if text:
            emit({"kind": "assistant_final", "text": text[:4000]})
        result = {"ok": ok, "text": text, "error": err, "persona": spec["label"], "name": name or spec["label"],
                  "turns": state.get("tools", 0), "used_tools": [], "transcript_path": str(tpath)}
        emit({"kind": "end", **{k: v for k, v in result.items() if k != "text"}})
        with _sa._BG_RESULTS_LOCK:
            _sa._BG_RUNNING.discard(agent_id)
            _sa._BG_RESULTS[agent_id] = result
        cb = on_done or _sa._BG_COMPLETION_CB
        if cb:
            try:
                cb(agent_id, result)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name=f"external-{agent}").start()
    return {"ok": True, "agent_id": agent_id, "agent": agent, "label": spec["label"], "maker": spec.get("maker", ""),
            "tested": agent in VERIFIED, "billing": spec.get("billing", "")}
