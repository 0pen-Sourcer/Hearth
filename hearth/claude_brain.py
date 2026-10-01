"""Claude Code as the assistant's brain.

With the brain set to Claude Code, each turn runs `claude -p` on the user's own
install and plan instead of calling a model endpoint. Hearth's tools reach it
through Hearth's MCP server (desktop, apps, memory, voice and the rest), the
assistant's persona and memory ride along as an appended system prompt, and
each Hearth conversation keeps one Claude session so it remembers the chat.

Permission questions come back the same way Hearth's own do. Claude Code calls
the `approve` tool on Hearth's MCP server, which asks this process over the
local API, which runs the turn's normal permission check: the approval strip
in the window, a spoken question in voice mode, and the Ask / Auto-edit / Auto
switch deciding what goes through on its own.

Helpers keep running on whatever local model is loaded; only the main
assistant is Claude. `claude -p` is billed to the Agent SDK credit that comes
with a Claude plan.

Codex is the other brain (codex_brain.py). It shares the permission bridge,
the sessions file and the persona here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import env as _env

PROVIDER = "claude-code"
# Agent programs that can be the brain: settings value -> name shown.
BRAINS = {"claude-code": "Claude Code", "codex": "Codex", "cursor-agent": "Cursor"}
# Whose plan each one runs on, for the lines that say so.
PLANS = {"claude-code": "Claude", "codex": "ChatGPT", "cursor-agent": "Cursor"}
_HOME = os.path.join(os.path.expanduser("~"), ".hearth")
_SESSIONS = os.path.join(_HOME, "claude_sessions.json")
_SESSIONS_LOCK = threading.Lock()

# The local API this process serves, and the secret an MCP server it starts
# must present to ask for a permission. Set by web.serve.
_bridge_url = ""
_TOKEN = secrets.token_hex(16)
_checks: Dict[str, Callable] = {}          # token -> the running turn's permission check


def set_bridge_url(url: str) -> None:
    global _bridge_url
    _bridge_url = url.rstrip("/")


def ensure_bridge() -> None:
    """Where there is no Hearth web server (the terminal, a phone bridge),
    serve just the permission question on a local port."""
    if _bridge_url:
        return
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Ask(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path not in ("/api/brain/permission", "/api/brain/tool"):
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            answer = decide if self.path.endswith("permission") else run_tool
            out = json.dumps(answer(str(body.get("token") or ""), str(body.get("tool") or ""),
                                    body.get("input") or {})).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ask)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    set_bridge_url(f"http://127.0.0.1:{srv.server_address[1]}")


def current() -> Optional[str]:
    """The agent program picked as the brain in Settings, if one is."""
    try:
        from .web import _load_settings
        p = str(_load_settings().get("llm_provider") or "").strip().lower()
    except Exception:
        return None
    return p if p in BRAINS else None


def active() -> bool:
    return current() is not None


def label() -> str:
    return BRAINS.get(current() or "", "")


def exe_for(provider: str) -> Optional[List[str]]:
    from . import external_agents as _xa
    exe = _xa.find(provider)
    return _xa._direct_argv(exe) if exe else None


def claude_exe() -> Optional[List[str]]:
    return exe_for(PROVIDER)


# Where to get each one when it isn't installed.
SITES = {"claude-code": "https://claude.com/claude-code", "codex": "https://chatgpt.com/codex",
         "cursor-agent": "https://cursor.com/cli"}


def installed() -> List[Dict[str, str]]:
    """The brains that can be picked on this PC."""
    return [{"id": k, "label": v} for k, v in BRAINS.items() if exe_for(k)]


def choices() -> List[Dict[str, Any]]:
    """Every agent brain, installed or not, for the model menu."""
    return [{"id": k, "label": v, "installed": bool(exe_for(k)), "site": SITES.get(k, ""),
             "plan": PLANS.get(k, "")}
            for k, v in BRAINS.items()]


def set_brain(provider: str, model: Optional[str] = None) -> Optional[str]:
    """Make an agent program the brain, or with "" go back to the endpoint in
    Settings. The local address is left alone either way: helpers and
    background memory keep using it. A model is saved only when one is
    given, so switching from the model sticker keeps the one set in Settings.
    Returns an error to show, or None."""
    from .web import _load_settings, _save_settings
    s = _load_settings()
    if provider:
        if provider not in BRAINS:
            return f"Unknown brain '{provider}'."
        if not exe_for(provider):
            return (f"{BRAINS[provider]} isn't installed on this PC. Get it from "
                    f"{SITES[provider].split('//')[1]} and sign in first.")
        s["llm_provider"] = provider
        if model is not None:
            s["claude_model" if provider == PROVIDER else provider + "_model"] = model
    elif current():
        url = str(s.get("llm_url") or os.environ.get("LOCAL_API_BASE") or "").lower()
        s["llm_provider"] = "local" if (not url or "127.0.0.1" in url or "localhost" in url) else ""
    _save_settings(s)
    return None


# ---------------------------------------------------------------- models
# Claude Code lists the models this install and account can use when a
# headless session starts (its `initialize` answer), with what each is for and
# the effort levels it takes. Hearth asks once per installed version. The
# context window isn't in that list; it comes back with every reply and is
# remembered per model.
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_SEEN = os.path.join(_HOME, "agent_models.json")
_LIST_CACHE = os.path.join(_HOME, "claude_models.json")


def _claude_list() -> List[Dict[str, Any]]:
    exe = claude_exe()
    if not exe:
        return []
    try:
        stamp = f"{exe[-1]}|{os.path.getmtime(exe[-1])}"
    except OSError:
        stamp = exe[-1]
    try:
        with open(_LIST_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("stamp") == stamp and cached.get("models"):
            return cached["models"]
    except (OSError, ValueError):
        pass
    import subprocess
    ask = json.dumps({"type": "control_request", "request_id": "models",
                      "request": {"subtype": "initialize"}}) + "\n"
    try:
        r = subprocess.run(exe + ["-p", "--input-format", "stream-json", "--output-format", "stream-json",
                                  "--verbose"], input=ask.encode("utf-8"), capture_output=True, timeout=60,
                           cwd=os.path.expanduser("~"), creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        return []
    models: List[Dict[str, Any]] = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "control_response":
            models = ((ev.get("response") or {}).get("response") or {}).get("models") or []
            break
    if models:
        try:
            os.makedirs(_HOME, exist_ok=True)
            with open(_LIST_CACHE, "w", encoding="utf-8") as f:
                json.dump({"stamp": stamp, "models": models}, f)
        except OSError:
            pass
    return models


def _seen() -> Dict[str, Any]:
    try:
        with open(_SEEN, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _remember(provider: str, model: str, ctx: Optional[int]) -> None:
    """The context window a model reported, by its full name."""
    if not model or not ctx:
        return
    data = _seen()
    data.setdefault(provider, {})[model] = ctx
    try:
        os.makedirs(_HOME, exist_ok=True)
        with open(_SEEN, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _model_key(provider: str) -> str:
    return "claude_model" if provider == PROVIDER else provider + "_model"


def chosen_model(provider: str) -> str:
    try:
        from .web import _load_settings
        return str(_load_settings().get(_model_key(provider)) or "").strip()
    except Exception:
        return ""


def chosen_effort() -> str:
    """The think button's level, when it's on. Empty: the agent's own default."""
    try:
        from .web import _load_settings
        s = _load_settings()
    except Exception:
        return ""
    if not s.get("think_default"):
        return ""
    return str(s.get("think_level") or "").strip().lower()


def agent_models(provider: str) -> Dict[str, Any]:
    """The models an agent offers, for the model menu and the think button."""
    seen = _seen().get(provider, {})
    if provider == PROVIDER:
        models = []
        for m in _claude_list():
            value = str(m.get("value") or "")
            full = str(m.get("resolvedModel") or "")
            # "Opus 5.5 · Best for everyday, complex tasks": the version is the name.
            name, _, rest = str(m.get("description") or "").partition(" · ")
            default = value == "default"
            models.append({"id": "" if default else value,
                           "label": "Default" if default else (name or m.get("displayName") or value),
                           "about": str(m.get("description") or "") if default else rest,
                           "model": full, "ctx": seen.get(full),
                           "efforts": list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort") else []})
    elif provider == "codex":
        from . import codex_brain
        models = codex_brain.models()
    elif provider == "cursor-agent":
        from . import cursor_brain
        models = cursor_brain.models()
    else:
        models = []
    return {"provider": provider, "label": BRAINS.get(provider, provider), "models": models,
            "model": chosen_model(provider), "effort": chosen_effort()}


def context_window(provider: Optional[str] = None) -> Optional[int]:
    """The context window of the model the agent brain will use, when known."""
    provider = provider or current()
    if not provider:
        return None
    want = chosen_model(provider)
    for m in agent_models(provider)["models"]:
        if m["id"] == want:
            return m.get("ctx")
    return None


# ---------------------------------------------------------------- tool names
# Claude Code's own tools shown as the Hearth tools they amount to, so a shell
# command gets the command card, an edit gets the diff, a fetch the site icon.
def hearth_view(tool: str, inp: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    inp = inp if isinstance(inp, dict) else {}
    if tool.startswith("mcp__hearth__"):
        return tool[len("mcp__hearth__"):], inp
    if tool in ("Bash", "PowerShell"):
        return "run_command", {"command": inp.get("command", ""), "shell": tool.lower()}
    if tool == "Read":
        return "read_file", {"path": inp.get("file_path", "")}
    if tool == "Write":
        return "write_file", {"path": inp.get("file_path", ""), "content": inp.get("content", "")}
    if tool == "Edit":
        return "edit_file", {"path": inp.get("file_path", ""),
                             "edits": [{"old_text": inp.get("old_string", ""), "new_text": inp.get("new_string", "")}]}
    if tool == "MultiEdit":
        return "edit_file", {"path": inp.get("file_path", ""),
                             "edits": [{"old_text": e.get("old_string", ""), "new_text": e.get("new_string", "")}
                                       for e in inp.get("edits") or []]}
    if tool == "Glob":
        return "find_file", {"name": inp.get("pattern", ""), "path": inp.get("path", "")}
    if tool == "Grep":
        return "grep_search", {"pattern": inp.get("pattern", ""), "path": inp.get("path", "")}
    if tool == "WebSearch":
        return "web_search", {"query": inp.get("query", "")}
    if tool == "WebFetch":
        return "web_fetch", {"url": inp.get("url", "")}
    return tool, inp


# Claude Code's bookkeeping, not work the user needs to see.
_QUIET_TOOLS = {"ToolSearch", "TodoWrite", "mcp__hearth__approve"}


# ---------------------------------------------------------------- permissions
def decide(token: str, tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    """Answer Claude Code's permission question with the turn's own check."""
    if token != _TOKEN:
        return {"behavior": "deny", "message": "not a Hearth session"}
    name, args = hearth_view(tool, inp)
    check = _checks.get(token)
    if check is None:
        # No one is watching this turn: only what the approval switch already
        # lets through goes ahead.
        try:
            from .tools import mode_allows
            ok = mode_allows(name, args)
        except Exception:
            ok = False
        return ({"behavior": "allow", "updatedInput": inp} if ok
                else {"behavior": "deny", "message": "Nobody is at the computer to approve this."})
    try:
        verdict = check(name, args)
    except Exception as e:
        verdict = {"decision": "deny", "reason": f"{type(e).__name__}: {e}"}
    reason = ""
    if isinstance(verdict, dict):
        reason = verdict.get("reason") or ""
        verdict = verdict.get("decision") or "deny"
    if verdict == "allow":
        return {"behavior": "allow", "updatedInput": inp}
    if verdict == "timeout":
        return {"behavior": "deny", "message": "The user didn't answer in time. Ask them again or do something else."}
    return {"behavior": "deny",
            "message": ("The user declined: " + reason) if reason else "The user declined this."}


# ---------------------------------------------------------------- helpers
# Tools whose state lives in this process, not in the MCP server the brain
# talks to. The server passes these calls here, so a helper the brain sends
# is one of Hearth's own: it shows in the Team view and can be told things.
HEARTH_SIDE = ("spawn_subagent", "tell_subagent", "get_subagent_result")
_sent: Dict[str, int] = {}     # helpers this turn sent -> checks since one was last seen running


def run_tool(token: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if token != _TOKEN or name not in HEARTH_SIDE:
        return {"result": "Error: not a Hearth session"}
    from .tools import execute_tool
    args = dict(args or {})
    if name == "spawn_subagent":
        args["mode"] = "background"               # the turn waits for it and hands over its report
    out = str(execute_tool(name, args))
    if name == "spawn_subagent":
        import re
        m = re.search(r"sub_\d+_[0-9a-f]{6}", out)
        if m and '"launched"' in out:
            _sent[m.group(0)] = 0
    return {"result": out}


def helper_news(emit=None) -> List[str]:
    """Reports from helpers that have finished, worded for the brain."""
    from . import subagents as _sa
    out = []
    for n in _sa.drain_pending_notifications():
        _sent.pop(n.get("agent_id"), None)
        if emit:
            emit("subagent_done", **_sa.card_fields(n))
        out.append(_sa.format_notification_as_user_message(n))
    return out


def helpers_out() -> bool:
    """Whether a helper this turn sent has yet to report. One that stopped
    running without a report (its report is queued a moment after it stops)
    is given a few checks, then let go."""
    from . import subagents as _sa
    live = _sa.still_running(_sent)
    for aid in list(_sent):
        _sent[aid] = 0 if aid in live else _sent[aid] + 1
        if _sent[aid] > 6:
            _sent.pop(aid, None)
    return bool(_sent)


# ---------------------------------------------------------------- sessions
def _load_sessions() -> Dict[str, Any]:
    try:
        with open(_SESSIONS, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save_session(key: str, sid: str) -> None:
    with _SESSIONS_LOCK:
        data = _load_sessions()
        data[key] = {"session": sid, "at": int(time.time())}
        if len(data) > 400:                       # keep the newest 400 chats
            data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-400:])
        os.makedirs(_HOME, exist_ok=True)
        tmp = _SESSIONS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _SESSIONS)


def _chat_key(history: List[Dict[str, Any]], prompt: str) -> str:
    """A chat is known by how it began: its first user message."""
    first = next((m.get("content") for m in history or [] if m.get("role") == "user"), None) or prompt
    return hashlib.sha1(str(first).strip()[:2000].encode("utf-8", "replace")).hexdigest()


def _transcript(history: List[Dict[str, Any]], limit: int = 14, each: int = 1200) -> str:
    rows = []
    for m in (history or [])[-limit:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = str(m.get("content") or "").strip()
        if text:
            rows.append(f"{'User' if role == 'user' else 'You'}: {text[:each]}")
    return "\n".join(rows)


# ---------------------------------------------------------------- setup files
def _mcp_launch(gate: bool = False) -> Dict[str, Any]:
    """How to start Hearth's MCP server, with what it needs to ask this
    process for permissions. With `gate` the server asks before every risky
    tool itself, for a brain that has no permission hook."""
    if getattr(sys, "frozen", False):
        launch = {"command": sys.executable, "args": ["--hearth-run-python", "-m", "hearth.mcp_server"]}
    else:
        launch = {"command": sys.executable,
                  "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")]}
    env = {"HEARTH_BRAIN_URL": _bridge_url, "HEARTH_BRAIN_TOKEN": _TOKEN, "PYTHONIOENCODING": "utf-8"}
    ws = _env.get("WORKSPACE")
    if ws:
        env["HEARTH_WORKSPACE"] = ws
    if gate:
        env["HEARTH_BRAIN_GATE"] = "1"
    launch["env"] = env
    return launch


def _mcp_config() -> str:
    launch = _mcp_launch()
    path = os.path.join(_HOME, "claude_mcp.json")
    os.makedirs(_HOME, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"hearth": launch}}, f, indent=2)
    return path


def persona_text(voice: bool, host: str = "Claude Code", tools: str = "mcp__hearth__<name>",
                 own_tools: str = "Your own file and shell tools are fine for files and commands.") -> str:
    from . import system_prompt
    preface = (
        f"You are running inside {host} as the brain of Hearth, the user's local "
        f"assistant, under the name and persona below. Hearth's own tools are available "
        f"as {tools}: use them for the desktop, apps, windows, screenshots, "
        f"memory, reminders, the phone and anything outside a code folder. {own_tools} "
        f"To hand work to helpers, use Hearth's spawn_subagent, not {host}'s own sub-agents: "
        f"Hearth's helpers appear in the user's Team view, and each one's report is given to you "
        f"when it finishes, so after sending them you can simply end your message. "
        f"Answer as the persona, never as {host}.\n\n")
    return preface + system_prompt(voice=voice)


def _persona_file(voice: bool) -> str:
    path = os.path.join(_HOME, "claude_persona.md")
    os.makedirs(_HOME, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(persona_text(voice))
    return path


def _safe_hearth_tools() -> List[str]:
    """Hearth's tools that never need a yes (reads, searches, memory lookups),
    allowed up front so Claude Code doesn't stop to ask about them."""
    try:
        from .tools import TOOL_DEFINITIONS, _RISKY_TOOLS
        return [f"mcp__hearth__{t['name']}" for t in TOOL_DEFINITIONS if t["name"] not in _RISKY_TOOLS]
    except Exception:
        return []


def _permission_mode() -> str:
    """Hearth's switch as Claude Code's own mode. Everything Claude Code would
    ask about still comes back through Hearth, which applies the switch, so
    this only spares a round trip for edits in Auto-edit."""
    try:
        from .tools import approval_mode
        return "acceptEdits" if approval_mode() == "edits" else "default"
    except Exception:
        return "default"


def quick(prompt: str, timeout: float = 45) -> str:
    """A short answer from the brain with no tools, persona or session: for a
    chat's title. Empty when the agent isn't there or doesn't answer in time."""
    provider = current()
    exe = exe_for(provider) if provider else None
    if not exe:
        return ""
    if provider == "claude-code":
        args = exe + ["-p", "--model", "haiku", "--no-session-persistence", "--strict-mcp-config", "--tools", ""]
    elif provider == "codex":
        args = exe + ["exec", "--skip-git-repo-check", "--sandbox", "read-only",
                      "-c", 'model_reasoning_effort="low"', "-"]
    else:
        return ""              # Cursor counts every request against the plan, a title included
    import tempfile
    try:
        r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout,
                           cwd=tempfile.gettempdir(),
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        return ""
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------- one turn
def _said(drain_steer) -> List[str]:
    """What was typed while the turn ran, if anything."""
    if drain_steer is None:
        return []
    try:
        return [s.strip() for s in (drain_steer() or []) if (s or "").strip()]
    except Exception:
        return []


def user_line(text: str) -> bytes:
    """One user message in the JSON-lines form Claude Code reads on stdin."""
    return (json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
            + "\n").encode("utf-8")


async def run_turn(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                   permission_check=None, should_cancel=None, voice: bool = False,
                   model: Optional[str] = None, drain_steer=None) -> int:
    """One turn on whichever agent program is the brain. Something said while
    it works reaches it: Claude Code reads it part-way; Codex and Cursor run a
    step to its end, so they are given it as soon as that step finishes.
    A helper's report reaches it the same way, and the turn waits for helpers
    it sent."""
    _sent.clear()
    news = helper_news(emit)
    from .headless import NOTIFY_FLUSH
    if prompt.strip() == NOTIFY_FLUSH:
        if not news:
            emit("done", reply="", iterations=0)
            return 0
        prompt = "\n\n".join(news)
    elif news:
        prompt = "\n\n".join(news) + "\n\n" + prompt
    other = {"codex": "codex_brain", "cursor-agent": "cursor_brain"}.get(current() or "")
    if not other:
        return await _run_claude(prompt, emit=emit, history=history, permission_check=permission_check,
                                 should_cancel=should_cancel, voice=voice, drain_steer=drain_steer)
    import importlib
    mod = importlib.import_module("." + other, __package__)
    # Several steps are still one reply: one "done", one full text.
    replies: List[str] = []
    last_done: Dict[str, Any] = {}

    def relay(kind: str, **fields) -> None:
        if kind == "assistant":
            replies.append(str(fields.get("content") or ""))
        elif kind == "done":
            last_done.clear()
            last_done.update(fields)
        else:
            emit(kind, **fields)

    hist = list(history or [])
    while True:
        rc = await mod.run_turn(prompt, emit=relay, history=hist, permission_check=permission_check,
                                should_cancel=should_cancel, voice=voice)
        said, news = [], []
        while rc == 0 and not (should_cancel and should_cancel()):
            said, news = _said(drain_steer), helper_news(emit)
            if said or news or not helpers_out():
                break
            await asyncio.sleep(0.5)
        if not said and not news:
            break
        for s in said:
            emit("steered", text=s)
        hist = hist + [{"role": "user", "content": prompt}, {"role": "assistant", "content": replies[-1] if replies else ""}]
        prompt = "\n".join(said + news)
        emit("assistant_chunk", content="\n\n")
    if replies:
        emit("assistant", content="\n\n".join(r for r in replies if r))
    emit("done", **(last_done or {"reason": "finished"}))
    return rc


async def _run_claude(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                      permission_check=None, should_cancel=None, voice: bool = False,
                      drain_steer=None) -> int:
    exe = claude_exe()
    if not exe:
        emit("error", message=("Claude Code isn't installed on this PC. Install it from "
                               "claude.com/claude-code and sign in, or pick another brain in Settings."))
        return 2
    ensure_bridge()
    history = history or []
    key = _chat_key(history, prompt)
    session = (_load_sessions().get(key) or {}).get("session") if history else None
    text_in = prompt
    if history and not session:
        # A chat that started on another brain: Claude hasn't seen it, so it
        # gets the recent part as context.
        earlier = _transcript(history)
        if earlier:
            text_in = f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n{prompt}"
    args = exe + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                  "--include-partial-messages",
                  "--mcp-config", _mcp_config(),
                  "--permission-prompt-tool", "mcp__hearth__approve",
                  "--permission-mode", _permission_mode(),
                  "--append-system-prompt-file", _persona_file(voice)]
    safe = _safe_hearth_tools()
    if safe:
        args += ["--allowedTools", ",".join(safe)]
    if session:
        args += ["--resume", session]
    alias = chosen_model(PROVIDER)
    if alias:
        args += ["--model", alias]
    effort = chosen_effort()
    if effort and effort in next((m["efforts"] for m in agent_models(PROVIDER)["models"] if m["id"] == alias),
                                 CLAUDE_EFFORTS):
        args += ["--effort", effort]
    cwd = _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")
    os.makedirs(cwd, exist_ok=True)

    t0 = time.time()
    _checks[_TOKEN] = permission_check
    tool_names: Dict[str, Tuple[str, float]] = {}
    text_parts: List[str] = []
    final_text, result, sid = "", None, None
    streamed = False
    gap = False                                   # a tool call came between two stretches of text
    writing: Optional[Dict[str, Any]] = None      # the tool call being generated right now
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        proc.stdin.write(user_line(text_in))
        await proc.stdin.drain()
        open_in = True                            # stdin stays open so it can be told things

        async def pass_on() -> bool:
            """Give Claude Code what was said while it works. It reads it at
            its next step, the same as typing to it in a terminal."""
            said = _said(drain_steer) if open_in else []
            for s in said:
                proc.stdin.write(user_line(s))
                emit("steered", text=s)
            news = helper_news(emit) if open_in else []
            for n in news:
                proc.stdin.write(user_line(n))
            if said or news:
                await proc.stdin.drain()
            return bool(said or news)

        answered = False                          # it has finished a reply and waits on stdin
        while True:
            if should_cancel and should_cancel():
                proc.kill()
                emit("cancelled", message="stopped by user")
                break
            if await pass_on():
                # Something said, or a helper's report: that is its next step.
                gap, answered = bool(text_parts), False
            elif answered and open_in and not helpers_out():
                open_in = False
                proc.stdin.close()                # closing stdin is what lets it exit
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            try:
                ev = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            t = ev.get("type")
            if ev.get("parent_tool_use_id"):
                continue                          # Claude Code's own sub-agents: their work, not the reply
            if t == "system" and ev.get("subtype") == "init":
                sid = ev.get("session_id") or sid
            elif t == "stream_event":
                inner = ev.get("event") or {}
                d = inner.get("delta") or {}
                # A tool call being written: its input arrives in pieces, and
                # is passed on so a file shows while it is being written.
                if inner.get("type") == "content_block_start":
                    block = inner.get("content_block") or {}
                    writing = None
                    if block.get("type") == "tool_use" and block.get("name") not in _QUIET_TOOLS:
                        writing = {"name": hearth_view(block.get("name", ""), {})[0], "text": "", "sent": 0}
                elif d.get("type") == "input_json_delta" and writing is not None:
                    writing["text"] += d.get("partial_json") or ""
                    if len(writing["text"]) - writing["sent"] >= 60:
                        writing["sent"] = len(writing["text"])
                        emit("tool_call_partial", name=writing["name"], args_text=writing["text"])
                if d.get("type") == "text_delta" and d.get("text"):
                    streamed = True
                    chunk = ("\n\n" + d["text"]) if (gap and text_parts) else d["text"]
                    gap = False
                    text_parts.append(chunk)
                    emit("assistant_chunk", content=chunk)
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    emit("thinking_chunk", content=d["thinking"])
            elif t == "assistant":
                blocks = (ev.get("message") or {}).get("content") or []
                for b in blocks:
                    if b.get("type") == "tool_use" and b.get("name") not in _QUIET_TOOLS:
                        name, shown = hearth_view(b.get("name", ""), b.get("input") or {})
                        tool_names[b.get("id", "")] = (name, time.time())
                        gap = True
                        emit("tool_call", name=name, args=shown, id=b.get("id", ""))
                    elif b.get("type") == "text" and not streamed and b.get("text"):
                        text_parts.append(b["text"])
                        emit("assistant_chunk", content=b["text"])
                if any(b.get("type") == "tool_use" for b in blocks):
                    streamed = False              # the next reply segment streams afresh
            elif t == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    hit = tool_names.pop(b.get("tool_use_id", ""), None)
                    if not hit:
                        continue
                    body = b.get("content")
                    if isinstance(body, list):
                        body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
                    emit("tool_result", name=hit[0], content=str(body or "")[:20000],
                         ms=int((time.time() - hit[1]) * 1000), id=b.get("tool_use_id", ""))
            elif t == "result":
                result = ev
                sid = ev.get("session_id") or sid
                final_text = ev.get("result") or ""
                answered = True
        await proc.wait()
        err = (await proc.stderr.read()).decode("utf-8", "replace").strip()
    except Exception as e:
        emit("error", message=f"Claude Code didn't run: {type(e).__name__}: {e}")
        return 1
    finally:
        _checks.pop(_TOKEN, None)

    if sid:
        _save_session(key, sid)
    # The model that did the work: Claude Code also runs a small one on the
    # side, so it's the one with the most output.
    usage = (result or {}).get("modelUsage") or {}
    if usage:
        name, u = max(usage.items(), key=lambda kv: (kv[1] or {}).get("outputTokens") or 0)
        _remember(PROVIDER, name, (u or {}).get("contextWindow"))
    reply = "".join(text_parts).strip() or str(final_text).strip()
    if result and result.get("is_error"):
        emit("error", message=("Claude Code stopped: " + str(result.get("result") or result.get("subtype") or "error"))[:600])
    elif not reply and not (should_cancel and should_cancel()):
        emit("error", message=("Claude Code gave no answer. " + (err[-400:] if err else
                               "Check it's signed in: run `claude` once in a terminal.")))
    if reply:
        emit("assistant", content=reply)
    emit("done", duration_ms=int((time.time() - t0) * 1000),
         iterations=int((result or {}).get("num_turns") or 1),
         stats={"brain": "Claude Code", "model": (max(usage, key=lambda k: (usage[k] or {}).get("outputTokens") or 0)
                                                 if usage else ""),
                "cost_usd": (result or {}).get("total_cost_usd")} if result else None,
         reason="finished")
    return 0
