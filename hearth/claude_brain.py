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
assistant is Claude. Signed in with a Claude plan, `claude -p` counts against
that plan's usage limits; an API key is billed separately.

Codex is the other brain (codex_brain.py). It shares the permission bridge,
the sessions file and the persona here.
"""
from __future__ import annotations

import asyncio
import hashlib
import contextvars
import json
import os
import re
import secrets
import shutil
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
# Agents that speak the Agent Client Protocol, run by acp_brain: how each is
# started over ACP, and, where ACP comes as a separate adapter, its program.
# levels_args: the command that lists every model with its reasoning levels.
# effort_flag: the start flag that sets the level, its values read from --help.
# Either way a thought_level option the session offers comes first.
ACP = {
    "opencode": {"label": "OpenCode", "plan": "OpenCode", "site": "https://opencode.ai", "args": ["acp"],
                 "levels_args": ["models", "--verbose"],
                 # Where it writes a failed request (out of free usage, a provider error) that it
                 # only retries and never sends back over the protocol.
                 "log": "~/.local/share/opencode/log/opencode.log"},
    "copilot": {"label": "Copilot CLI", "plan": "GitHub Copilot", "site": "https://github.com/github/copilot-cli",
                "args": ["--acp"], "effort_flag": "--reasoning-effort"},
    "goose": {"label": "Goose", "plan": "own provider's", "site": "https://block.github.io/goose", "args": ["acp"]},
    # Not "grok": that name is xAI's API in the provider list.
    "grok-build": {"label": "Grok Build", "plan": "xAI", "site": "https://x.ai", "agent": "grok",
                   "args": ["agent", "stdio"]},
    "gemini-cli": {"label": "Gemini CLI", "plan": "Google", "site": "https://github.com/google-gemini/gemini-cli",
                   "args": ["--experimental-acp"]},
    "pi": {"label": "Pi", "plan": "own provider's", "site": "https://pi.dev", "adapter": "pi-acp", "args": []},
    "amp": {"label": "Amp", "plan": "Amp", "site": "https://ampcode.com", "adapter": "amp-acp", "args": []},
    "antigravity": {"label": "Antigravity", "plan": "Google", "site": "https://antigravity.google",
                    "adapter": "agy-agent-acp", "args": []},
}
BRAINS.update({k: v["label"] for k, v in ACP.items()})
PLANS.update({k: v["plan"] for k, v in ACP.items()})
# The three with adapters of their own; the rest are listed once installed.
CORE = ("claude-code", "codex", "cursor-agent")
_HOME = os.path.join(os.path.expanduser("~"), ".hearth")
_SESSIONS = os.path.join(_HOME, "claude_sessions.json")
_SESSIONS_LOCK = threading.Lock()

# The local API this process serves, and the secret an MCP server it starts
# must present to ask for a permission. Set by web.serve.
_bridge_url = ""
_TOKEN = secrets.token_hex(16)
_checks: Dict[str, Callable] = {}          # token -> the running turn's permission check
_turns: Dict[str, Any] = {}                # token -> the running turn, for Auto's reviewer


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
            if self.path not in ("/api/brain/permission", "/api/brain/tool", "/api/brain/desktop"):
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            answer = {"/api/brain/permission": decide, "/api/brain/tool": run_tool}.get(self.path, desktop)
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


_EXE_SEEN: Dict[str, Tuple[float, Optional[List[str]]]] = {}
_EXE_KEEP_S = 30.0


def exe_for(provider: str) -> Optional[List[str]]:
    """How to start an agent program. Finding it scans every folder on PATH, so
    the answer is kept for half a minute: a model list asks once per model."""
    hit = _EXE_SEEN.get(provider)
    if hit and time.time() - hit[0] < _EXE_KEEP_S:
        return list(hit[1]) if hit[1] else None
    from . import external_agents as _xa
    spec = ACP.get(provider) or {}
    adapter = spec.get("adapter")
    exe = (shutil.which(adapter + ".cmd") or shutil.which(adapter)) if adapter else _xa.find(spec.get("agent") or provider)
    out = _xa._direct_argv(exe) if exe else None
    _EXE_SEEN[provider] = (time.time(), list(out) if out else None)
    return out


def claude_exe() -> Optional[List[str]]:
    return exe_for(PROVIDER)


# Where to get each one when it isn't installed.
SITES = {"claude-code": "https://claude.com/claude-code", "codex": "https://chatgpt.com/codex",
         "cursor-agent": "https://cursor.com/cli", **{k: v["site"] for k, v in ACP.items()}}


def installed() -> List[Dict[str, str]]:
    """The brains that can be picked on this PC."""
    return [{"id": k, "label": v} for k, v in BRAINS.items() if exe_for(k)]


def choices() -> List[Dict[str, Any]]:
    """The agent brains for the model menu: the three with their own adapters
    always, the protocol ones once they are on this PC."""
    out = []
    for k, v in BRAINS.items():
        here = bool(exe_for(k))
        if k in CORE or here:
            from .external_agents import icon_site
            out.append({"id": k, "label": v, "installed": here, "site": SITES.get(k, ""), "icon_site": icon_site(k),
                        "plan": PLANS.get(k, ""), "core": k in CORE})
    return out


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


# ---------------------------------------------------------------- plan limits
# How much of their plan's usage windows an agent has used, as the agent itself
# reports it: Claude Code sends a rate_limit_event with each run, Codex writes
# its limits into its own session files. Kept in one file for /status.
_LIMITS = os.path.join(_HOME, "plan_limits.json")


def _keep_limits(provider: str, windows: Dict[str, Any]) -> None:
    try:
        with open(_LIMITS, encoding="utf-8") as f:
            all_ = json.load(f) or {}
    except (OSError, ValueError):
        all_ = {}
    all_[provider] = {"at": time.time(), "windows": windows}
    try:
        os.makedirs(_HOME, exist_ok=True)
        with open(_LIMITS, "w", encoding="utf-8") as f:
            json.dump(all_, f)
    except OSError:
        pass


_OUT_OF_USAGE = re.compile(
    r"usage limit|session limit|weekly limit|5-hour limit|limit reached|hit your (?:\w+ )?limit|"
    r"out of (?:usage|credits)|quota (?:exceeded|reached)|usageLimitExceeded", re.I)


def out_of_usage(agent: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """An agent that stopped because its plan's usage ran out says so as that,
    in its own words (which carry when it resets), not as a bare stop."""
    msg = str(fields.get("message") or "")
    if fields.get("category") == "quota" or not _OUT_OF_USAGE.search(msg):
        return fields
    name = BRAINS.get(agent, "The agent")
    said = re.sub(r"^\s*[\w .-]+? stopped:\s*", "", msg).strip()
    return dict(fields, category="quota",
                message=f"{name} is out of usage: {said.rstrip('.')}.")


def _chosen_row(agent: str) -> str:
    """The model row the agent was asked to use (the id the model menu shows)."""
    chosen = chosen_model(agent)
    if agent == "cursor-agent" and chosen:
        from . import cursor_brain
        row = cursor_brain.row_for(chosen)
        chosen = row["id"] if row else chosen
    return chosen


def not_on_plan(agent: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """An agent refusing the picked model because the plan doesn't include it says
    so in a line, with the models it names and a one-tap way back to its default,
    and the refusal is kept so the model menu marks that model."""
    from . import plan_refusals
    got = plan_refusals.parse(str(fields.get("message") or ""))
    if not got:
        return fields
    chosen = _chosen_row(agent)
    if chosen:
        plan_refusals.remember(agent, chosen, got.get("plan") or "")
    if not got.get("model") and chosen:
        rows = {m.get("id"): m for m in (agent_models(agent).get("models") or [])}
        got["model"] = (rows.get(chosen) or {}).get("label") or ""
    default = "Auto" if any(n.lower() == "auto" for n in got.get("included") or []) else "Default"
    return dict(fields, category="plan", message=plan_refusals.say(BRAINS.get(agent, "The agent"), got),
                switch={"provider": agent, "model": "", "label": f"Use {default}"} if chosen else None)


def _plan_answered(agent: str) -> None:
    """A model that answered is on the plan now: its mark goes."""
    try:
        from . import plan_refusals
        chosen = _chosen_row(agent)
        if chosen and chosen in plan_refusals.refused(agent):
            plan_refusals.forget(agent, chosen)
    except Exception:
        pass


def note_claude_limits(ev: Dict[str, Any]) -> None:
    """Keep what a Claude Code rate_limit_event says about the plan's windows."""
    info = ev.get("rate_limit_info") or {}
    wins = info.get("unifiedWindows") or {}
    out = {}
    for name, key in (("five_hour", "5 hours"), ("seven_day", "7 days")):
        w = wins.get(name) or {}
        if w.get("utilization") is not None:
            out[key] = {"used": round(float(w["utilization"]) * 100), "resets_at": w.get("resetsAt")}
    if str(info.get("status") or "").lower() == "rejected" and info.get("resetsAt"):
        key = {"five_hour": "5 hours", "seven_day": "7 days"}.get(str(info.get("rateLimitType") or ""), "5 hours")
        out[key] = {"used": 100, "resets_at": info.get("resetsAt"), "refused": True}
    if out:
        _keep_limits(PROVIDER, out)


def used_up(agent: str) -> Optional[Dict[str, Any]]:
    """A plan window this agent reported as used up and not reset yet: the
    turn is not started, so nothing is spent finding that out again."""
    lim = plan_limits(agent) or {}
    now = time.time()
    for name, w in (lim.get("windows") or {}).items():
        try:
            at = float(w.get("resets_at") or 0)
        except (TypeError, ValueError):
            continue
        # Claude Code says when it refused; past 100% it may still run on extra
        # usage. Codex only reports how much is used.
        full = w.get("refused") or (agent == "codex" and (w.get("used") or 0) >= 100)
        if full and at > now:
            return {"window": name, "resets_at": at}
    return None


def _codex_limits() -> Optional[Dict[str, Any]]:
    """The newest limits Codex wrote in its session files. The windows are the
    account's, so the newest session says the most."""
    import glob
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    files = sorted(glob.glob(os.path.join(home, "sessions", "*", "*", "*", "*.jsonl")), key=os.path.getmtime, reverse=True)
    for path in files[:6]:
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 400_000))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in reversed(tail.splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                rl = ((json.loads(line).get("payload") or {}).get("rate_limits")) or {}
            except ValueError:
                continue
            out = {}
            for k in ("primary", "secondary"):
                w = rl.get(k) or {}
                if w.get("used_percent") is not None:
                    mins = int(w.get("window_minutes") or 0)
                    name = f"{mins // 60} hours" if mins and mins < 1440 else f"{mins // 1440} days" if mins else k
                    out[name] = {"used": round(float(w["used_percent"])), "resets_at": w.get("resets_at")}
            if out:
                return {"at": os.path.getmtime(path), "windows": out}
    return None


def plan_limits(provider: str) -> Optional[Dict[str, Any]]:
    """{"at": when it was read, "windows": {"5 hours": {"used": 54, "resets_at": epoch}}}, or None."""
    if provider == "codex":
        return _codex_limits()
    try:
        with open(_LIMITS, encoding="utf-8") as f:
            return (json.load(f) or {}).get(provider)
    except (OSError, ValueError):
        return None


def chosen_model(provider: str) -> str:
    try:
        from .web import _load_settings
        return str(_load_settings().get(_model_key(provider)) or "").strip()
    except Exception:
        return ""


# Claude Code says per turn whether fast mode was on (fast_mode_state). A model
# it turned out not to be on for, with this plan, isn't offered it again.
FAST_ABOUT = "Faster replies, uses more of the plan"


def _no_fast() -> Dict[str, bool]:
    try:
        with open(os.path.join(_HOME, "claude_no_fast.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _mark_no_fast(model: str) -> None:
    data = _no_fast()
    data[model or "default"] = True
    try:
        os.makedirs(_HOME, exist_ok=True)
        with open(os.path.join(_HOME, "claude_no_fast.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def fast_on(provider: str) -> bool:
    """Whether the agent's Fast tier was switched on for it (quicker answers
    that use more of the plan)."""
    try:
        from .web import _load_settings
        return bool(_load_settings().get("fast_" + provider))
    except Exception:
        return False


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
            # Older versions name a model "Opus" and put "Opus 5.5 · what it's
            # for" in its description; newer ones name it "Opus 5.5" and the
            # description is only what it's for.
            desc, shown = str(m.get("description") or ""), str(m.get("displayName") or value)
            name, sep, rest = desc.partition(" · ")
            default = value == "default"
            older = bool(sep) and name.split(" ")[0].lower() == shown.split(" ")[0].lower()
            models.append({"id": "" if default else value,
                           "label": "Default" if default else (name if older else shown),
                           "about": desc if default else (rest if older else desc),
                           "model": full, "ctx": seen.get(full),
                           "efforts": list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort") else [],
                           "fast": "" if _no_fast().get("default" if default else value) else FAST_ABOUT})
    elif provider == "codex":
        from . import codex_brain
        models = codex_brain.models()
    elif provider == "cursor-agent":
        from . import cursor_brain
        models = cursor_brain.models()
    elif provider in ACP:
        from . import acp_brain
        models = acp_brain.models(provider)
    else:
        models = []
    chosen = chosen_model(provider)
    if provider == "cursor-agent" and chosen:
        # A full Cursor id saved before its levels were grouped is its model's row.
        from . import cursor_brain
        row = cursor_brain.row_for(chosen, models)
        chosen = row["id"] if row else chosen
    from . import plan_refusals
    refused = plan_refusals.refused(provider)
    if refused:
        models = [dict(m, refused=refused[m.get("id")]) if m.get("id") in refused else m for m in models]
    return {"provider": provider, "label": BRAINS.get(provider, provider), "models": models,
            "model": chosen, "effort": chosen_effort(), "fast_on": fast_on(provider)}


def context_window(provider: Optional[str] = None) -> Optional[int]:
    """The context window of the model the agent brain will use, when known."""
    provider = provider or current()
    if not provider:
        return None
    info = agent_models(provider)
    want = info["model"]
    for m in info["models"]:
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


def says_of(tool: str, inp: Dict[str, Any]) -> str:
    """What Claude Code says a step of its own is for (the `description` it writes
    on a command or a task). Inglen's own tools' arguments are theirs, so not those."""
    if tool.startswith("mcp__") or not isinstance(inp, dict):
        return ""
    d = inp.get("description")
    return " ".join(d.split())[:120] if isinstance(d, str) else ""


# Claude Code's bookkeeping, not work the user needs to see.
_QUIET_TOOLS = {"ToolSearch", "TodoWrite", "mcp__hearth__approve"}


# ---------------------------------------------------------------- permissions
def decide(token: str, tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    """Answer Claude Code's permission question with the turn's own check."""
    if token != _TOKEN:
        return {"behavior": "deny", "message": "not an Inglen session"}
    # This arrives on the bridge's own thread: put the turn back so Auto's
    # reviewer checks the step against what the person said in it.
    from . import auto_review
    with auto_review.using(_turns.get(token)):
        return _decide(token, tool, inp)


def _decide(token: str, tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
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
    if verdict in ("ask_aloud", "aside") and reason:
        return {"behavior": "deny", "message": reason}       # a voice turn: not a no (call.ASK_ALOUD)
    return {"behavior": "deny",
            "message": ("The user declined: " + reason) if reason else "The user declined this."}


# ---------------------------------------------------------------- helpers
# Tools whose state lives in this process, not in the MCP server the brain
# talks to. The server passes these calls here, so a helper the brain sends
# is one of Hearth's own: it shows in the Team view and can be told things.
HEARTH_SIDE = ("spawn_subagent", "tell_subagent", "get_subagent_result", "launch_team", "helper_changes", "delegate",
               "race_helpers", "what_can_i_do", "set_up", "load_model", "connectors")
_sent: Dict[str, int] = {}     # helpers this turn sent -> checks since one was last seen running


def desktop(token: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """A desktop action from the brain's tool server: shown here, where it outlives the turn."""
    if token != _TOKEN:
        return {"ok": False}
    from . import desktop_session
    args = args or {}
    if tool == "_raised":
        desktop_session.raised(int(args.get("hwnd") or 0))
    elif tool == "_overlays":
        from . import overlays
        overlays.set_visible(bool(args.get("show")))
    elif tool == "_make_way":
        from . import overlays
        try:
            overlays.make_way(int(args.get("x")), int(args.get("y")), physical=True)
        except (TypeError, ValueError):
            return {"ok": False}
    else:
        desktop_session.begin(tool, args or {})
    return {"ok": True}


def run_tool(token: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # An MCP server is connected here too, so its tools run here and are found here.
    if token != _TOKEN or not (name in HEARTH_SIDE or name.startswith("mcp_") or name == "find_mcp_tools"):
        return {"result": "Error: not an Inglen session"}
    from .tools import execute_tool, unlock_tools
    args = dict(args or {})
    if name == "find_mcp_tools":
        return {"result": [td for td in unlock_tools(str(args.get("query") or ""), touch=False)
                           if td["name"].startswith("mcp_")]}
    if name == "spawn_subagent":
        args["mode"] = "background"               # the turn waits for it and hands over its report
    out = str(execute_tool(name, args))
    if name in ("spawn_subagent", "launch_team", "race_helpers", "delegate"):
        # The turn stays open until every helper it started has reported.
        import re
        for aid in set(re.findall(r"sub_\d+_[0-9a-f]{6}", out)):
            _sent[aid] = 0
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


def _fingerprint(text: Any) -> str:
    return hashlib.sha1(str(text or "").strip()[:2000].encode("utf-8", "replace")).hexdigest()


def session_for(key: str, history: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """The agent's own session for this chat, if it still matches the chat. A
    session remembers the last message it answered; when that message is no
    longer in the chat (an earlier one was edited, or the reply regenerated),
    the agent starts again from the chat as it now reads."""
    if not history:
        return None
    rec = _load_sessions().get(key) or {}
    last = rec.get("last")
    if last and not any(m.get("role") == "user" and _fingerprint(m.get("content")) == last for m in history):
        return None
    return rec.get("session")


_ASKED: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("hearth_asked", default=None)


def asked(prompt: str) -> Optional[str]:
    """The message this turn answers, for the session to remember; None for a
    turn nobody typed (a report coming in), which leaves the last one kept.
    A brain run without the dispatcher answers the prompt it was given."""
    a = _ASKED.get()
    return prompt if a is None else (a or None)


def _save_session(key: str, sid: str, last: Any = None) -> None:
    with _SESSIONS_LOCK:
        data = _load_sessions()
        keep = (data.get(key) or {}).get("last") if last is None else _fingerprint(last)
        data[key] = {"session": sid, "at": int(time.time())}
        if keep:
            data[key]["last"] = keep
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
        f"You are running inside {host} as the brain of Inglen, the user's local "
        f"assistant, under the name and persona below. Inglen's own tools are available "
        f"as {tools}: use them for the desktop, apps, windows, screenshots, "
        f"memory, reminders, the phone and anything outside a code folder. {own_tools} "
        f"Press keys, switch and close windows with Inglen's desktop tools, not from a shell: "
        f"Inglen shows the user what those tools do and gives them the screen back after. "
        f"To hand work to helpers, use Inglen's spawn_subagent, not {host}'s own sub-agents: "
        f"Inglen's helpers appear in the user's Team view, and each one's report is given to you "
        f"when it finishes, so after sending them you can simply end your message. "
        f"A helper can think with the model on this PC (runs_on 'local'), with a provider and model "
        f"you name, or be an agent program; never call a model server's API yourself to get work done. "
        f"More of Inglen's tools exist than are listed: find them with find_tools and run one with use_tool; "
        f"wherever the text below says load_tools, that is find_tools. "
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
        from .outbound import URL_ARGS
        # A fetch is asked about each time: whether it would carry data out
        # depends on what the turn has read, which only Inglen knows.
        return [f"mcp__hearth__{t['name']}" for t in TOOL_DEFINITIONS
                if t["name"] not in _RISKY_TOOLS and t["name"] not in URL_ARGS]
    except Exception:
        return []


def agent_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """An agent program's environment, without the keys Inglen put in this one."""
    from .keystore import agent_env as _clean
    return _clean(extra)


def claude_settings(fast: bool = False) -> Dict[str, Any]:
    """Settings for Claude Code's --settings: a read of a place that holds keys
    asks, so it comes to Inglen's check; Fast when picked."""
    try:
        from .secret_places import claude_rules
        rules = claude_rules()
    except Exception:
        rules = []
    out: Dict[str, Any] = {"permissions": {"ask": rules}}
    if fast:
        out["fastMode"] = True
    return out


def _permission_mode() -> str:
    """Hearth's switch as Claude Code's own mode. Everything Claude Code would
    ask about still comes back through Hearth, which applies the switch, so
    this only spares a round trip for edits in Auto-edit."""
    try:
        from .tools import approval_mode
        return "acceptEdits" if approval_mode() == "edits" else "default"
    except Exception:
        return "default"


def quick_model(provider: str) -> str:
    """Which model quick() answers with for this agent: its small one where quick()
    asks for it, else the one the person picked ('' for the agent's own default)."""
    if provider == "claude-code":
        return "haiku"
    if provider == "codex":
        return ""
    return chosen_model(provider) or ""


def quick(prompt: str, timeout: float = 45, any_agent: bool = False) -> str:
    """A short answer from the brain with no tools, persona or session: for a
    chat's title, or the memory pass. Empty when the agent isn't there or
    doesn't answer in time. Cursor and the ACP agents answer only with
    `any_agent` (the memory pass): each such request counts against their plan,
    which a title isn't worth."""
    provider = current()
    exe = exe_for(provider) if provider else None
    if not exe:
        return ""
    if provider == "claude-code":
        args = exe + ["-p", "--model", quick_model(provider), "--no-session-persistence", "--strict-mcp-config", "--tools", ""]
    elif provider == "codex":
        args = exe + ["exec", "--skip-git-repo-check", "--sandbox", "read-only",
                      "-c", 'model_reasoning_effort="low"', "-"]
    elif not any_agent:
        return ""
    elif provider == "cursor-agent":
        model = quick_model(provider)
        args = exe + ["-p", "--output-format", "text"] + (["--model", model] if model else [])
    elif provider in ACP:
        try:
            from . import acp_brain
            return asyncio.run(asyncio.wait_for(acp_brain.once(provider, prompt), timeout))[0]
        except Exception:
            return ""
    else:
        return ""
    # A folder of its own, so an agent that keeps sessions files this one apart from
    # the person's projects (and the phone's Coding agents list leaves it out).
    import shutil
    import tempfile
    cwd = tempfile.mkdtemp(prefix="hearth-once-")
    try:
        r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout,
                           cwd=cwd, creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        return ""
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------- one turn
async def ask_hearth(permission_check, name: str, args: Dict[str, Any]) -> bool:
    """Whether an agent brain may do what it asks: the turn's own approval
    check, or with none, what the approval switch allows."""
    if permission_check is None:
        try:
            from .tools import mode_allows
            return bool(mode_allows(name, args))
        except Exception:
            return False
    verdict = await asyncio.to_thread(permission_check, name, args)
    if asyncio.iscoroutine(verdict):          # a check that asks on screen
        verdict = await verdict
    if isinstance(verdict, dict):
        verdict = verdict.get("decision")
    return verdict == "allow"


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


def on_plan(agent: str) -> Optional[bool]:
    """Whether an agent bills a subscription rather than a key, from its own
    sign-in; None when Hearth can't tell."""
    if agent == "codex":
        try:
            from .codex_brain import _codex_home
            with open(os.path.join(_codex_home(), "auth.json"), encoding="utf-8") as f:
                return (json.load(f) or {}).get("auth_mode") == "chatgpt"
        except Exception:
            return None
    if agent == "cursor-agent":
        return True                      # Cursor bills its own plans
    return None


def priced(agent: str, stats: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A turn's stats with what it cost: the agent's own figure, else its tokens
    at the catalog's prices, marked when a plan covers it."""
    if not stats:
        return stats
    out = dict(stats)
    if out.get("plan") is None:
        plan = on_plan(agent)
        if plan is not None:
            out["plan"] = plan
    if out.get("cost_usd") is None and (out.get("tokens_in") or out.get("tokens_out")):
        try:
            from . import model_facts
            cost = model_facts.turn_cost(agent, out.get("model") or chosen_model(agent) or "",
                                         {"in": out.get("tokens_in") or 0, "out": out.get("tokens_out") or 0})
            if cost:
                out["cost_usd"] = round(cost, 6)
        except Exception:
            pass
    return out


def reached_tool(kind: str, fields: Dict[str, Any], reached: Dict[str, Optional[str]]) -> Optional[Dict[str, Any]]:
    """An agent brain reaches most of Hearth's tools through find_tools and
    use_tool. Shown as what they amount to: use_tool as the tool it ran, with
    that tool's arguments, and find_tools (a lookup, not work) not at all.
    Returns the fields to show, or None to show nothing."""
    name = str(fields.get("name") or "")
    for pre in ("mcp__hearth__", "mcp__hearth_brain__", "hearth_brain_", "hearth_brain/", "hearth_brain."):
        if name.startswith(pre):
            name = name[len(pre):]
    cid = str(fields.get("id") or "")
    if kind == "tool_result":
        if cid in reached:
            inner = reached[cid]
            return None if inner is None else {**fields, "name": inner}
        if name == "find_tools":
            return None
        return fields
    if name == "find_tools":
        if cid:
            reached[cid] = None
        return None
    if name != "use_tool":
        return fields
    args = fields.get("args") if isinstance(fields.get("args"), dict) else {}
    inner = str(args.get("name") or "")
    if not inner:
        # Still being written: shown once the call names its tool.
        return None if kind == "tool_call_partial" else fields
    inner_args = args.get("arguments")
    if isinstance(inner_args, str):
        try:
            inner_args = json.loads(inner_args)
        except ValueError:
            inner_args = {"input": inner_args}
    if cid:
        reached[cid] = inner
    return {**fields, "name": inner, "args": inner_args if isinstance(inner_args, dict) else {}}


# After this long with nothing from the agent (no text, thought or step, and no
# step of its own running), the person is told it has gone quiet and can stop it.
# Nothing ends on a timer: a model may think for minutes without streaming it.
QUIET_S = float(os.environ.get("HEARTH_AGENT_QUIET_S", "90") or "90")
_LOG_PREFIXES = ("AI_RetryError: Failed after 3 attempts. Last error: ", "AI_APICallError: ", "Error: ")


def last_logged_error(agent: str, since: float, session: str = "") -> Tuple[str, float]:
    """(the newest error the agent wrote to its own log since `since`, on
    `session` when given, in its own words; when), or ('', 0). Only for agents
    whose spec names their log: one that retries a refused request without a
    word (OpenCode out of free usage) would otherwise look busy forever."""
    spec = ACP.get(agent) or {}
    path = os.path.expanduser(str(spec.get("log") or ""))
    if not path or not os.path.isfile(path):
        return "", 0.0
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 400_000))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return "", 0.0
    found, found_at = "", 0.0
    for line in tail.splitlines():
        if "level=ERROR" not in line or (session and session not in line):
            continue
        m = re.search(r"timestamp=(\S+)", line)
        try:
            from datetime import datetime
            when = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() if m else 0
        except ValueError:
            when = 0
        if not when or when < since - 2:
            continue
        e = re.search(r'error\.error="([^"]+)', line) or re.search(r'error="([^"]+)', line)
        if e:
            found, found_at = e.group(1), when
    for pre in _LOG_PREFIXES:
        while found.startswith(pre):
            found = found[len(pre):]
    return found.strip()[:400], (found_at if found else 0.0)


def logged_failure(agent: str, since: float, session: str = "") -> str:
    return last_logged_error(agent, since, session)[0]


async def run_turn(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                   permission_check=None, should_cancel=None, voice: bool = False,
                   model: Optional[str] = None, drain_steer=None, context: str = "") -> int:
    """One turn on whichever agent program is the brain. Something said while
    it works reaches it: Claude Code reads it part-way; Codex and Cursor run a
    step to its end, so they are given it as soon as that step finishes.
    A helper's report reaches it the same way, and the turn waits for helpers
    it sent."""
    _sent.clear()
    # Every agent's turn ends with what it cost, by one rule for all of them.
    from .redact import clean_emit
    _emit, agent = clean_emit(emit), current() or ""      # what the agent shows: no saved key
    reached: Dict[str, Optional[str]] = {}      # call id -> the tool use_tool ran (None: a lookup)
    # When the agent last said anything, and which of its own steps are running.
    heard = {"at": time.time(), "steps": set(), "told": False, "stop": False}
    # What the agent's steps bring in from outside is noted on this turn, whichever
    # process ran them (its own tools, or Inglen's through the tool server).
    from . import outbound
    seen, called = outbound.current(), {}

    def emit(kind: str, **fields) -> None:
        heard["at"], heard["told"] = time.time(), False
        cid = str(fields.get("id") or "")
        if kind == "tool_call" and cid:
            heard["steps"].add(cid)
        elif kind == "tool_result":
            heard["steps"].discard(cid)
        if kind in ("tool_call", "tool_call_partial", "tool_result"):
            fields = reached_tool(kind, fields, reached)
            if fields is None:
                return
            if kind == "tool_call":
                called[cid] = fields.get("args")
            elif kind == "tool_result":
                outbound.note_on(seen, str(fields.get("name") or ""), called.pop(cid, None), fields.get("content"))
        if kind == "done":
            fields["stats"] = priced(agent, fields.get("stats"))
        elif kind == "error":
            fields = not_on_plan(agent, out_of_usage(agent, fields))
            heard["refused"] = heard.get("refused") or fields.get("category") == "plan"
        if kind == "done" and not heard.get("refused") and fields.get("reason") == "finished":
            _plan_answered(agent)
        _emit(kind, **fields)

    full = used_up(agent)
    if full:
        from .clock import hm
        when = hm(full["resets_at"])
        emit("error", category="quota",
             message=f"{BRAINS.get(agent, 'The agent')} is out of usage until {when}.")
        emit("done", reply="", iterations=0)
        return 0
    news = helper_news(emit)
    from .headless import NOTIFY_FLUSH
    # What was asked, as it sits in the chat: the session is matched against it
    # next turn, and reports put in front of it here are not part of it.
    _ASKED.set("" if prompt.strip() == NOTIFY_FLUSH else prompt)
    if prompt.strip() == NOTIFY_FLUSH:
        if not news:
            emit("done", reply="", iterations=0)
            return 0
        prompt = "\n\n".join(news)
    elif news:
        prompt = "\n\n".join(news) + "\n\n" + prompt
    if context and prompt.strip() != NOTIFY_FLUSH:
        # What has been going on in their other chats; not part of what was asked.
        prompt = f"<lately>\n{context}\n</lately>\n\n{prompt}"
    began = time.time()
    asked_stop = should_cancel

    def should_cancel() -> bool:
        return bool(heard["stop"] or (asked_stop and asked_stop()))

    async def watch(turn) -> None:
        """Say when the agent has gone quiet; end the turn only on an error it logged."""
        name = BRAINS.get(agent, "The agent")
        while True:
            await asyncio.sleep(5)
            quiet = time.time() - heard["at"]
            if heard["steps"] or quiet < min(20.0, QUIET_S):
                continue
            said = logged_failure(agent, began)
            if said:
                emit("error", message=said if _OUT_OF_USAGE.search(said) else f"{name} couldn't answer: {said}")
                heard["stop"] = True
                # An agent stuck retrying may never confirm the stop: give it a moment, then end it.
                await asyncio.sleep(10)
                if not turn.done():
                    turn.cancel()
                return
            if quiet >= QUIET_S and not heard["told"]:
                heard["told"] = True
                _emit("quiet", seconds=int(quiet), name=name,
                      message=f"{name} has been quiet for {int(quiet)} s. It may still be thinking; Stop ends it.")

    # Every agent's permission questions arrive on a thread of the bridge: they are
    # answered in this turn (Auto's reviewer, what the turn has read from outside).
    from . import auto_review
    _turns[_TOKEN] = auto_review.current()
    turn = asyncio.ensure_future(_turn_on(prompt, emit, history, permission_check, should_cancel, voice, drain_steer))
    watcher = asyncio.ensure_future(watch(turn))
    try:
        return await turn
    except asyncio.CancelledError:
        if not heard["stop"]:
            raise
        emit("done", reason="stopped", iterations=0)
        return 0
    finally:
        watcher.cancel()
        _turns.pop(_TOKEN, None)


async def _turn_on(prompt, emit, history, permission_check, should_cancel, voice, drain_steer) -> int:
    """The turn on the agent program in use (run_turn wraps it)."""
    other = {"codex": "codex_brain", "cursor-agent": "cursor_brain"}.get(current() or "") or (
        "acp_brain" if current() in ACP else "")
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
        # Codex takes what is said part-way into the running turn; Cursor gets
        # it as the next step, below.
        extra = {"drain_steer": drain_steer} if other == "codex_brain" else {}
        rc = await mod.run_turn(prompt, emit=relay, history=hist, permission_check=permission_check,
                                should_cancel=should_cancel, voice=voice, **extra)
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
    session = session_for(key, history)
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
    # Fast is a setting of Claude Code's own; it says afterwards whether it applied.
    want_fast = fast_on(PROVIDER) and not _no_fast().get(alias or "default")
    args += ["--settings", json.dumps(claude_settings(fast=want_fast))]
    cwd = _env.get("WORKSPACE") or __import__("hearth").default_workspace()
    os.makedirs(cwd, exist_ok=True)

    t0 = time.time()
    _checks[_TOKEN] = permission_check
    try:
        from . import auto_review
        _turns[_TOKEN] = auto_review.current()
    except Exception:
        pass
    tool_names: Dict[str, Tuple[str, float]] = {}
    text_parts: List[str] = []
    final_text, result, sid = "", None, None
    on_plan = False
    streamed = False
    gap = False                                   # a tool call came between two stretches of text
    writing: Optional[Dict[str, Any]] = None      # the tool call being generated right now
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024, env=agent_env(),
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
            if t == "rate_limit_event":
                note_claude_limits(ev)
            elif t == "system" and ev.get("subtype") == "init":
                sid = ev.get("session_id") or sid
                # "none": signed in with a Claude plan, not an API key.
                on_plan = ev.get("apiKeySource") == "none"
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
                        emit("tool_call", name=name, args=shown, id=b.get("id", ""),
                             says=says_of(b.get("name", ""), b.get("input") or {}))
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
                if want_fast and ev.get("fast_mode_state") == "off":
                    _mark_no_fast(alias)
                    emit("heads_up", title="Fast isn't available here",
                         message="Claude Code ran this at normal speed: fast mode isn't on for this model or plan.")
        await proc.wait()
        err = (await proc.stderr.read()).decode("utf-8", "replace").strip()
    except Exception as e:
        emit("error", message=f"Claude Code didn't run: {type(e).__name__}: {e}")
        return 1
    finally:
        _checks.pop(_TOKEN, None)
        _turns.pop(_TOKEN, None)
        from .aproc import reap
        await reap(proc)

    if sid:
        _save_session(key, sid, last=asked(prompt))
    # The model that did the work: Claude Code also runs a small one on the
    # side, so it's the one with the most output.
    usage = (result or {}).get("modelUsage") or {}
    if usage:
        name, u = max(usage.items(), key=lambda kv: (kv[1] or {}).get("outputTokens") or 0)
        _remember(PROVIDER, name, (u or {}).get("contextWindow"))
    reply = "".join(text_parts).strip() or str(final_text).strip()
    if result and result.get("is_error"):
        said = str(result.get("result") or result.get("subtype") or "error")
        # Out of usage, Claude Code writes the limit line as text too; it is
        # the stop, not something JARVIS said.
        if reply and (reply == said.strip() or _OUT_OF_USAGE.search(reply) and len(reply) < 300):
            reply = ""
        emit("error", message=("Claude Code stopped: " + said)[:600])
    elif not reply and not (should_cancel and should_cancel()):
        emit("error", message=("Claude Code gave no answer. " + (err[-400:] if err else
                               "Check it's signed in: run `claude` once in a terminal.")))
    if reply:
        emit("assistant", content=reply)
    emit("done", duration_ms=int((time.time() - t0) * 1000),
         iterations=int((result or {}).get("num_turns") or 1),
         stats={"brain": "Claude Code", "model": (max(usage, key=lambda k: (usage[k] or {}).get("outputTokens") or 0)
                                                 if usage else ""),
                "cost_usd": (result or {}).get("total_cost_usd"), "plan": on_plan} if result else None,
         reason="finished")
    return 0
