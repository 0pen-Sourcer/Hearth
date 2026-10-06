"""Codex as the assistant's brain.

Each turn runs `codex exec --json` on the user's own install and ChatGPT plan.
The Codex desktop app carries the CLI, so the app alone is enough. Hearth's
tools reach Codex through Hearth's MCP server, the persona rides along as
developer instructions, and each Hearth conversation keeps one Codex thread.

Codex has no hook that asks someone before a tool runs, so Hearth's server
asks Hearth itself before anything risky (the same strip, spoken question and
Ask / Auto-edit / Auto switch as everywhere else). Its own shell runs
commands without asking anyone, so on Ask and Auto-edit, where a command
needs a yes, it has none: commands go through Hearth's run_command. Its file
edits are held to the workspace on Auto-edit and refused on Ask.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from . import claude_brain as _base
from . import env as _env

PROVIDER = "codex"
SERVER = "hearth_brain"
# A Windows command line holds 32,767 characters; past this the persona goes
# in the first message instead of on the command line.
_MAX_INLINE = 22000


def _codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _user_config() -> Dict[str, Any]:
    try:
        import tomllib
        with open(os.path.join(_codex_home(), "config.toml"), "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def models() -> List[Dict[str, Any]]:
    """The models this account can pick, as Codex itself lists them."""
    try:
        with open(os.path.join(_codex_home(), "models_cache.json"), encoding="utf-8") as f:
            cache = json.load(f) or {}
    except (OSError, ValueError):
        cache = {}
    default = str(_user_config().get("model") or "")
    out = [{"id": "", "label": "Default", "about": f"Codex's own choice ({default})" if default else "Codex's own choice",
            "model": default, "ctx": None, "efforts": []}]
    for m in cache.get("models") or []:
        if m.get("visibility") != "list" or not m.get("slug"):
            continue
        out.append({"id": m["slug"], "label": m.get("display_name") or m["slug"],
                    "about": m.get("description") or "", "model": m["slug"],
                    "ctx": m.get("context_window"),
                    "efforts": [x.get("effort") for x in m.get("supported_reasoning_levels") or [] if x.get("effort")]})
    # "Default" means whatever the config names, with that model's numbers.
    for m in out[1:]:
        if m["id"] == default:
            out[0]["ctx"], out[0]["efforts"] = m["ctx"], m["efforts"]
    return out


def _toml(v: Any) -> str:
    """A value for `-c key=value`. JSON strings and arrays are valid TOML."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)


def _user_hearth_servers() -> List[str]:
    """MCP servers in the user's Codex config that start Hearth. They are
    switched off for these runs: Hearth's own entry asks before risky tools,
    and a hand-made one may be set to approve everything."""
    path = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "config.toml")
    try:
        import tomllib
        with open(path, "rb") as f:
            servers = tomllib.load(f).get("mcp_servers") or {}
    except Exception:
        return []
    out = []
    for name, spec in servers.items():
        blob = json.dumps(spec).lower() if isinstance(spec, dict) else ""
        if name.lower() == "hearth" or "hearth.mcp_server" in blob or "mcp_server.py" in blob:
            out.append(name)
    return out


def _mode() -> str:
    try:
        from .tools import approval_mode
        return approval_mode()
    except Exception:
        return "ask"


# Codex's own command tools. Both run without asking, so they are switched off
# whenever Hearth's switch says a command needs a yes.
_NO_SHELL = ["-c", "features.shell_tool=false", "-c", "features.unified_exec=false"]


def _sandbox() -> List[str]:
    mode = _mode()
    if mode == "ask":
        return ["-c", 'sandbox_mode="read-only"'] + _NO_SHELL
    out = ["-c", 'sandbox_mode="workspace-write"']
    if mode == "auto":
        out += ["-c", "sandbox_workspace_write.network_access=true"]
    else:
        out += _NO_SHELL
    return out


def build_args(exe: List[str], *, session: Optional[str], persona: str, model: str = "",
               effort: str = "") -> List[str]:
    launch = _base._mcp_launch(gate=True)
    args = list(exe) + ["exec"]
    if session:
        args += ["resume", session]
    args += ["--json", "--skip-git-repo-check",
             "-c", 'approval_policy="never"',
             "-c", f"mcp_servers.{SERVER}.command={_toml(launch['command'])}",
             "-c", f"mcp_servers.{SERVER}.args={_toml(launch['args'])}",
             "-c", f"mcp_servers.{SERVER}.env={_toml(launch['env'])}",
             # Hearth asks before risky tools itself, so Codex shouldn't stop to.
             "-c", f'mcp_servers.{SERVER}.default_tools_approval_mode="approve"',
             "-c", f"mcp_servers.{SERVER}.tool_timeout_sec=600"]
    for name in _user_hearth_servers():
        args += ["-c", f"mcp_servers.{name}.enabled=false"]
    args += _sandbox()
    if persona:
        args += ["-c", f"developer_instructions={_toml(persona)}"]
    if model:
        args += ["-m", model]
    if effort:
        args += ["-c", f"model_reasoning_effort={_toml(effort)}"]
    return args + ["-"]


def _mcp_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    parts = []
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
    return "\n".join(parts)


async def run_turn(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                   permission_check=None, should_cancel=None, voice: bool = False) -> int:
    exe = _base.exe_for(PROVIDER)
    if not exe:
        emit("error", message=("Codex isn't installed on this PC. Install the Codex app or CLI and "
                               "sign in, or pick another brain in Settings."))
        return 2
    _base.ensure_bridge()
    history = history or []
    key = "codex:" + _base._chat_key(history, prompt)
    session = (_base._load_sessions().get(key) or {}).get("session") if history else None
    text_in = prompt
    if history and not session:
        earlier = _base._transcript(history)
        if earlier:
            text_in = f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n{prompt}"
    persona = _base.persona_text(
        voice, host="Codex", tools=f"the {SERVER} MCP tools",
        own_tools=("Your own shell is fine for reading and running things; to change files outside "
                   "the workspace or do anything the sandbox blocks, use Hearth's tools, which ask the user."
                   if _mode() == "auto" else
                   "You have no shell of your own here: run every command with Hearth's run_command, which "
                   "asks the user first. Read files with Hearth's read_file."
                   + (" Your own file edits stay in the workspace." if _mode() == "edits" else
                      " Change files with Hearth's write_file or edit_file, which ask the user.")))
    if len(persona) > _MAX_INLINE:
        text_in = persona + "\n\n---\n\n" + text_in
        persona = ""
    model = _base.chosen_model(PROVIDER)
    effort = _base.chosen_effort()
    offered = next((m["efforts"] for m in models() if m["id"] == model), [])
    args = build_args(exe, session=session, persona=persona, model=model,
                      effort=effort if effort in offered else "")
    cwd = _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")
    os.makedirs(cwd, exist_ok=True)

    t0 = time.time()
    _base._checks[_base._TOKEN] = permission_check
    started: Dict[str, float] = {}
    text_parts: List[str] = []
    sid, usage, failure = None, None, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        proc.stdin.write(text_in.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        # Codex's stderr carries its own log lines; drain it so a chatty run
        # can't fill the pipe and stall.
        err_task = asyncio.ensure_future(proc.stderr.read())
        while True:
            if should_cancel and should_cancel():
                proc.kill()
                emit("cancelled", message="stopped by user")
                break
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
            item = ev.get("item") or {}
            kind = item.get("type")
            iid = str(item.get("id") or "")
            if t == "thread.started":
                sid = ev.get("thread_id") or sid
            elif t == "item.started":
                if kind == "command_execution":
                    started[iid] = time.time()
                    emit("tool_call", name="run_command", args={"command": item.get("command", "")}, id=iid)
                elif kind == "mcp_tool_call":
                    started[iid] = time.time()
                    emit("tool_call", name=item.get("tool", ""), args=item.get("arguments") or {}, id=iid)
                elif kind == "web_search":
                    started[iid] = time.time()
                    emit("tool_call", name="web_search", args={"query": item.get("query", "")}, id=iid)
            elif t == "item.completed":
                t_start = started.pop(iid, None)
                ms = int((time.time() - t_start) * 1000) if t_start else 0
                if kind == "agent_message" and item.get("text"):
                    chunk = ("\n\n" if text_parts else "") + item["text"]
                    text_parts.append(chunk)
                    emit("assistant_chunk", content=chunk)
                elif kind == "reasoning" and item.get("text"):
                    emit("thinking_chunk", content=item["text"])
                elif kind == "command_execution":
                    if t_start is None:
                        emit("tool_call", name="run_command", args={"command": item.get("command", "")}, id=iid)
                    out = str(item.get("aggregated_output") or "")
                    code = item.get("exit_code")
                    if code not in (None, 0):
                        out = (out + f"\n(exit code {code})").strip()
                    emit("tool_result", name="run_command", content=out[:20000], ms=ms, id=iid)
                elif kind == "file_change":
                    changes = item.get("changes") or []
                    paths = [str(c.get("path") or "") for c in changes if isinstance(c, dict)]
                    emit("tool_call", name="edit_file", args={"path": paths[0] if paths else ""}, id=iid)
                    emit("tool_result", name="edit_file", ms=ms, id=iid,
                         content="\n".join(f"{c.get('kind', 'update')}: {c.get('path', '')}"
                                           for c in changes if isinstance(c, dict)))
                elif kind == "mcp_tool_call":
                    if t_start is None:
                        emit("tool_call", name=item.get("tool", ""), args=item.get("arguments") or {}, id=iid)
                    body = _mcp_text(item.get("result"))
                    if item.get("error"):
                        body = "Error: " + str((item.get("error") or {}).get("message") or item.get("error"))
                    emit("tool_result", name=item.get("tool", ""), content=body[:20000], ms=ms, id=iid)
                elif kind == "web_search":
                    emit("tool_result", name="web_search", content=item.get("query", ""), ms=ms, id=iid)
            elif t == "turn.completed":
                usage = ev.get("usage") or {}
            elif t == "turn.failed":
                failure = str((ev.get("error") or {}).get("message") or "the turn failed")
            elif t == "error":
                failure = str(ev.get("message") or failure)
        await proc.wait()
        err = (await err_task).decode("utf-8", "replace").strip()
    except Exception as e:
        emit("error", message=f"Codex didn't run: {type(e).__name__}: {e}")
        return 1
    finally:
        _base._checks.pop(_base._TOKEN, None)

    if sid:
        _base._save_session(key, sid)
    reply = "".join(text_parts).strip()
    if failure:
        emit("error", message=("Codex stopped: " + failure)[:600])
    elif not reply and not (should_cancel and should_cancel()):
        tail = [l for l in err.splitlines() if "ERROR" in l or "error" in l.lower()][-3:]
        emit("error", message=("Codex gave no answer. " + ("\n".join(tail)[-400:] if tail else
                               "Check it's signed in: open the Codex app, or run `codex login`.")))
    if reply:
        emit("assistant", content=reply)
    emit("done", duration_ms=int((time.time() - t0) * 1000), iterations=1,
         stats={"brain": "Codex", "model": model or str(_user_config().get("model") or ""), "tokens_in": (usage or {}).get("input_tokens"),
                "tokens_out": (usage or {}).get("output_tokens")} if usage is not None else {"brain": "Codex"},
         reason="finished")
    return 0
