"""Codex as the assistant's brain.

Each turn talks to `codex app-server` on the user's own install and ChatGPT
plan (the Codex desktop app carries the CLI, so the app alone is enough).
Hearth's tools reach Codex through Hearth's MCP server, the persona rides
along as developer instructions, and each Hearth conversation keeps one Codex
thread. Text streams as it is written, something said part-way reaches the
running turn, and every command and file change Codex wants to make comes to
Hearth first, which answers it the way the Ask / Auto-edit / Auto switch says
(asking the person when that is what the switch means). File changes come
with their patch, so they show as diffs.

`codex exec --json` is the fallback when the app server can't be started
(HEARTH_CODEX_EXEC=1 picks it): there its shell is off whenever a command
needs a yes, since exec has no way to ask.
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
        levels = [x for x in m.get("supported_reasoning_levels") or [] if x.get("effort")]
        out.append({"id": m["slug"], "label": m.get("display_name") or m["slug"],
                    "about": m.get("description") or "", "model": m["slug"],
                    "ctx": m.get("context_window"),
                    "efforts": [x["effort"] for x in levels],
                    # What Codex itself says each level is for.
                    "effort_about": {x["effort"]: x.get("description") or "" for x in levels},
                    # Its Fast tier, in Codex's own words ("2x speed, increased usage").
                    "fast": next((str(t.get("description") or "Faster") for t in m.get("service_tiers") or []
                                  if str(t.get("name") or "").lower() == "fast"), "")})
    # "Default" means whatever the config names, with that model's numbers.
    for m in out[1:]:
        if m["id"] == default:
            out[0]["ctx"], out[0]["efforts"], out[0]["effort_about"] = m["ctx"], m["efforts"], m["effort_about"]
            out[0]["fast"] = m["fast"]
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
               effort: str = "", tier: str = "") -> List[str]:
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
    if tier:
        args += ["-c", f"service_tier={_toml(tier)}"]
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
                   permission_check=None, should_cancel=None, voice: bool = False, drain_steer=None) -> int:
    if os.environ.get("HEARTH_CODEX_EXEC") != "1" and _base.exe_for(PROVIDER):
        rc = await _run_app_server(prompt, emit=emit, history=history, permission_check=permission_check,
                                   should_cancel=should_cancel, voice=voice, drain_steer=drain_steer)
        if rc != _NO_APP_SERVER:
            return rc
    return await _run_exec(prompt, emit=emit, history=history, permission_check=permission_check,
                           should_cancel=should_cancel, voice=voice)


async def _run_exec(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                    permission_check=None, should_cancel=None, voice: bool = False) -> int:
    exe = _base.exe_for(PROVIDER)
    if not exe:
        emit("error", message=("Codex isn't installed on this PC. Install the Codex app or CLI and "
                               "sign in, or pick another brain in Settings."))
        return 2
    _base.ensure_bridge()
    history = history or []
    key = "codex:" + _base._chat_key(history, prompt)
    session = _base.session_for(key, history)
    text_in = prompt
    if history and not session:
        earlier = _base._transcript(history)
        if earlier:
            text_in = f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n{prompt}"
    persona = _base.persona_text(
        voice, host="Codex", tools=f"the {SERVER} MCP tools",
        own_tools=("Your own shell is fine for reading and running things; to change files outside "
                   "the workspace or do anything the sandbox blocks, use Inglen's tools, which ask the user."
                   if _mode() == "auto" else
                   "You have no shell of your own here: run every command with Inglen's run_command, which "
                   "asks the user first. Read files with Inglen's read_file."
                   + (" Your own file edits stay in the workspace." if _mode() == "edits" else
                      " Change files with Inglen's write_file or edit_file, which ask the user.")))
    if len(persona) > _MAX_INLINE:
        text_in = persona + "\n\n---\n\n" + text_in
        persona = ""
    model = _base.chosen_model(PROVIDER)
    effort = _base.chosen_effort()
    offered = next((m["efforts"] for m in models() if m["id"] == model), [])
    args = build_args(exe, session=session, persona=persona, model=model,
                      effort=effort if effort in offered else "",
                      tier=fast_tier(model) if _base.fast_on(PROVIDER) else "")
    cwd = _env.get("WORKSPACE") or __import__("hearth").default_workspace()
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
                    name, args = _search_call(item)
                    emit("tool_call", name=name, args=args, id=iid)
                    emit("tool_result", name=name, content="", ms=ms, id=iid)
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
        _base._save_session(key, sid, last=_base.asked(prompt))
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


# ---------------------------------------------------------------- app server
_NO_APP_SERVER = -99          # the app server didn't come up: use exec instead


def fast_tier(model: str) -> str:
    """The service tier Codex calls Fast for this model, if it has one."""
    try:
        with open(os.path.join(_codex_home(), "models_cache.json"), encoding="utf-8") as f:
            cache = json.load(f) or {}
    except (OSError, ValueError):
        return ""
    want = model or str(_user_config().get("model") or "")
    for m in cache.get("models") or []:
        if m.get("slug") == want:
            for t in m.get("service_tiers") or []:
                if str(t.get("name") or "").lower() == "fast":
                    return str(t.get("id") or "")
    return ""


def _server_args(exe: List[str]) -> List[str]:
    launch = _base._mcp_launch(gate=True)
    args = list(exe) + ["app-server",
                        "-c", f"mcp_servers.{SERVER}.command={_toml(launch['command'])}",
                        "-c", f"mcp_servers.{SERVER}.args={_toml(launch['args'])}",
                        "-c", f"mcp_servers.{SERVER}.env={_toml(launch['env'])}",
                        "-c", f'mcp_servers.{SERVER}.default_tools_approval_mode="approve"',
                        "-c", f"mcp_servers.{SERVER}.tool_timeout_sec=600"]
    for name in _user_hearth_servers():
        args += ["-c", f"mcp_servers.{name}.enabled=false"]
    if _mode() == "auto":
        args += ["-c", "sandbox_workspace_write.network_access=true"]
    return args


def _shown_command(params: Dict[str, Any]) -> str:
    """The command as the person would type it: Codex wraps each one in a
    shell call, and says what is inside separately."""
    acts = [a.get("command") for a in params.get("commandActions") or [] if isinstance(a, dict) and a.get("command")]
    return " && ".join(acts) if acts else str(params.get("command") or "")


def _search_call(item: Dict[str, Any]):
    """(tool, args) a Codex web search step amounts to: opening or searching a
    page is reading that page, the rest is a search for its query."""
    act = item.get("action") if isinstance(item.get("action"), dict) else {}
    kind = str(act.get("type") or "")
    if kind in ("openPage", "open_page", "findInPage", "find_in_page") and act.get("url"):
        return "web_fetch", {"url": str(act.get("url"))}
    query = act.get("query") or next(iter(act.get("queries") or []), "") or item.get("query") or ""
    return "web_search", {"query": str(query)}


def _change_call(c: Dict[str, Any]):
    """A Codex file change as the Hearth tool it amounts to, with what the
    diff card shows."""
    kind = ((c.get("kind") or {}).get("type") if isinstance(c.get("kind"), dict) else c.get("kind")) or "update"
    path = str(c.get("path") or "")
    if kind == "add":
        return "write_file", {"path": path, "content": str(c.get("diff") or "")}
    if kind == "delete":
        return "delete_path", {"path": path}
    return "edit_file", {"path": path, "diff": str(c.get("diff") or "")}


class _Rpc:
    """Newline-delimited JSON requests and replies with the app server."""

    def __init__(self, proc, jsonrpc: bool = False, who: str = "the Codex app server"):
        self.proc, self.n, self.waiting = proc, 0, {}
        self.inbox: asyncio.Queue = asyncio.Queue()
        # Agent Client Protocol peers want every message marked as JSON-RPC 2.0.
        self.mark = {"jsonrpc": "2.0"} if jsonrpc else {}
        self.who = who

    def send(self, obj: Dict[str, Any]) -> None:
        self.proc.stdin.write((json.dumps({**self.mark, **obj}) + "\n").encode("utf-8"))

    async def call(self, method: str, params: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
        self.n += 1
        fut = asyncio.get_event_loop().create_future()
        self.waiting[self.n] = fut
        self.send({"id": self.n, "method": method, "params": params})
        await self.proc.stdin.drain()
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(str((msg.get("error") or {}).get("message") or msg.get("error")))
        return msg.get("result") or {}

    async def read(self) -> None:
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                # It ended: nothing it was asked will be answered.
                for fut in self.waiting.values():
                    if not fut.done():
                        fut.set_exception(EOFError(f"{self.who} closed"))
                await self.inbox.put(None)
                return
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            trace = os.environ.get("HEARTH_CODEX_TRACE")
            if trace:
                try:
                    with open(trace, "a", encoding="utf-8") as f:
                        f.write(line.decode("utf-8", "replace"))
                except OSError:
                    pass
            if "method" not in msg and msg.get("id") in self.waiting:
                fut = self.waiting.pop(msg["id"])
                if not fut.done():
                    fut.set_result(msg)
            else:
                await self.inbox.put(msg)


async def _run_app_server(prompt: str, *, emit, history, permission_check, should_cancel, voice,
                          drain_steer) -> int:
    exe = _base.exe_for(PROVIDER)
    _base.ensure_bridge()
    history = history or []
    key = "codex:" + _base._chat_key(history, prompt)
    session = _base.session_for(key, history)
    text_in = prompt
    persona = _base.persona_text(
        voice, host="Codex", tools=f"the {SERVER} MCP tools",
        own_tools=("Use your own shell and file edits as usual: Inglen sees each command and change "
                   "before it happens and asks the user when that is needed."))
    model = _base.chosen_model(PROVIDER)
    effort = _base.chosen_effort()
    offered = next((m["efforts"] for m in models() if m["id"] == model), [])
    tier = fast_tier(model) if _base.fast_on(PROVIDER) else ""
    cwd = _env.get("WORKSPACE") or __import__("hearth").default_workspace()
    os.makedirs(cwd, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            *_server_args(exe), cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        return _NO_APP_SERVER
    rpc = _Rpc(proc)
    reader = asyncio.ensure_future(rpc.read())
    t0 = time.time()
    _base._checks[_base._TOKEN] = permission_check
    text_parts: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    failure, tid, turn_id = "", None, None
    try:
        try:
            await rpc.call("initialize", {"clientInfo": {"name": "hearth", "title": "Inglen", "version": "0.7.6"}}, 30)
        except Exception:
            return _NO_APP_SERVER
        rpc.send({"method": "initialized"})
        thread_opts = {"cwd": cwd, "approvalPolicy": "untrusted", "sandbox": "workspace-write",
                       "developerInstructions": persona, **({"model": model} if model else {}),
                       **({"serviceTier": tier} if tier else {})}
        if session:
            # Hearth's threads are kept archived between turns, so they don't
            # fill the person's own Codex list; brought back to go on.
            try:
                await rpc.call("thread/unarchive", {"threadId": session}, 15)
            except Exception:
                pass
            try:
                tid = ((await rpc.call("thread/resume", {"threadId": session, **thread_opts})).get("thread") or {}).get("id")
            except Exception:
                tid = None
        if not tid:
            if history:
                earlier = _base._transcript(history)
                if earlier:
                    text_in = f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n{prompt}"
            tid = ((await rpc.call("thread/start", thread_opts)).get("thread") or {}).get("id")
        turn = await rpc.call("turn/start", {"threadId": tid, "input": [{"type": "text", "text": text_in}],
                                             **({"effort": effort} if effort in offered else {}),
                                             **({"serviceTier": tier} if tier else {})})
        turn_id = (turn.get("turn") or {}).get("id")

        items: Dict[str, Dict[str, Any]] = {}       # item id -> the item as it started
        started: Dict[str, float] = {}
        streamed: set = set()                         # messages already sent as they were written
        approved: set = set()                         # commands said yes to in this turn
        shown: set = set()                            # commands with a card
        interrupted = False

        async def ask(name: str, args: Dict[str, Any]) -> bool:
            return await _base.ask_hearth(permission_check, name, args)

        while True:
            if should_cancel and should_cancel() and not interrupted:
                interrupted = True
                try:
                    await rpc.call("turn/interrupt", {"threadId": tid, "turnId": turn_id}, 10)
                except Exception:
                    break
                emit("cancelled", message="stopped by user")
            for said in _base._said(drain_steer):
                try:
                    await rpc.call("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                                  "input": [{"type": "text", "text": said}]}, 15)
                    emit("steered", text=said)
                except Exception:
                    # Too late for this turn: the dispatcher gives it as the next step.
                    pass
            try:
                msg = await asyncio.wait_for(rpc.inbox.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                failure = failure or "Codex closed before the turn ended"
                break
            meth, prm = msg.get("method"), msg.get("params") or {}
            if "id" in msg:                           # Codex asks before doing something
                if meth == "item/commandExecution/requestApproval":
                    cmd = _shown_command(prm)
                    ok = cmd in approved or await ask("run_command", {"command": cmd})
                    if ok:
                        approved.add(cmd)
                    rpc.send({"id": msg["id"], "result": {"decision": "accept" if ok else "decline"}})
                elif meth == "item/fileChange/requestApproval":
                    changes = (items.get(str(prm.get("itemId"))) or {}).get("changes") or []
                    ok = bool(changes)
                    for c in changes:
                        name, args = _change_call(c)
                        if not await ask(name, args):
                            ok = False
                            break
                    rpc.send({"id": msg["id"], "result": {"decision": "accept" if ok else "decline"}})
                else:
                    rpc.send({"id": msg["id"], "error": {"code": -32601, "message": "Inglen doesn't answer this"}})
                await proc.stdin.drain()
                continue
            item = prm.get("item") or {}
            kind, iid = item.get("type"), str(item.get("id") or "")
            if meth == "item/agentMessage/delta":
                delta = str(prm.get("delta") or "")
                if delta:
                    if prm.get("itemId") not in streamed:
                        streamed.add(prm.get("itemId"))
                        if text_parts:
                            text_parts.append("\n\n")
                            emit("assistant_chunk", content="\n\n")
                    text_parts.append(delta)
                    emit("assistant_chunk", content=delta)
            elif meth in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
                if prm.get("delta"):
                    emit("thinking_chunk", content=str(prm["delta"]))
            elif meth == "item/commandExecution/outputDelta":
                # A command shows once it is really running: Codex sometimes
                # starts one, drops it, and does the same thing another way.
                cid = str(prm.get("itemId") or "")
                if cid in items and cid not in shown:
                    shown.add(cid)
                    emit("tool_call", name="run_command", args={"command": _shown_command(items[cid])}, id=cid)
            elif meth == "item/started":
                items[iid] = item
                started[iid] = time.time()
                if kind == "commandExecution":
                    pass
                elif kind == "mcpToolCall":
                    emit("tool_call", name=item.get("tool", ""), args=item.get("arguments") or {}, id=iid)
                elif kind == "webSearch":
                    pass                      # what it searched or opened is known when it's done
                elif kind == "fileChange":
                    for n, c in enumerate(item.get("changes") or []):
                        name, args = _change_call(c)
                        emit("tool_call", name=name, args=args, id=f"{iid}:{n}")
            elif meth == "item/completed":
                ms = int((time.time() - started.pop(iid, time.time())) * 1000)
                if kind == "agentMessage" and iid not in streamed and item.get("text"):
                    chunk = ("\n\n" if text_parts else "") + item["text"]
                    text_parts.append(chunk)
                    emit("assistant_chunk", content=chunk)
                elif kind == "commandExecution":
                    if iid not in shown:
                        shown.add(iid)
                        emit("tool_call", name="run_command", args={"command": _shown_command(item)}, id=iid)
                    out = str(item.get("aggregatedOutput") or "")
                    code = item.get("exitCode")
                    if item.get("status") == "declined":
                        out = "(not run: the user declined)"
                    elif code not in (None, 0):
                        out = (out + f"\n(exit code {code})").strip()
                    emit("tool_result", name="run_command", content=out[:20000], ms=ms, id=iid)
                elif kind == "fileChange":
                    done = item.get("status") == "completed"
                    for n, c in enumerate(item.get("changes") or []):
                        name, _ = _change_call(c)
                        emit("tool_result", name=name, ms=ms, id=f"{iid}:{n}",
                             content=("Done: " if done else "(not changed: " + str(item.get("status")) + ") ")
                             + str(c.get("path") or ""))
                elif kind == "mcpToolCall":
                    body = _mcp_text(item.get("result"))
                    if item.get("error"):
                        body = "Error: " + str((item.get("error") or {}).get("message") or item.get("error"))
                    emit("tool_result", name=item.get("tool", ""), content=body[:20000], ms=ms, id=iid)
                elif kind == "webSearch":
                    name, args = _search_call(item)
                    emit("tool_call", name=name, args=args, id=iid)
                    emit("tool_result", name=name, content="", ms=ms, id=iid)
            elif meth == "thread/tokenUsage/updated":
                usage = ((prm.get("tokenUsage") or {}).get("last") or {})
            elif meth == "error":
                failure = str((prm.get("error") or {}).get("message") or prm.get("message") or failure)
            elif meth == "turn/completed":
                t = prm.get("turn") or {}
                if t.get("status") == "failed":
                    failure = str((t.get("error") or {}).get("message") or failure or "the turn failed")
                break
        # Something Codex started and dropped (it tried another way) still
        # gets an end, or its card would spin for good.
        for iid in list(started):
            it = items.get(iid) or {}
            if it.get("type") == "commandExecution":
                if iid in shown:
                    emit("tool_result", name="run_command", content="(not finished)", id=iid)
            elif it.get("type") == "mcpToolCall":
                emit("tool_result", name=it.get("tool", ""), content="(not finished)", id=iid)
            elif it.get("type") == "fileChange":
                for n, c in enumerate(it.get("changes") or []):
                    emit("tool_result", name=_change_call(c)[0], content="(not finished)", id=f"{iid}:{n}")
    except Exception as e:
        failure = failure or f"{type(e).__name__}: {e}"
    finally:
        _base._checks.pop(_base._TOKEN, None)
        if tid:
            try:
                await rpc.call("thread/archive", {"threadId": tid}, 10)
            except Exception:
                pass
        reader.cancel()
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.kill()
            # Waited for here, so its pipes close while the loop still runs.
            await asyncio.wait_for(proc.wait(), 5)
        except Exception:
            pass

    if tid:
        _base._save_session(key, tid, last=_base.asked(prompt))
    import re as _re
    reply = _re.sub(r"\n{3,}", "\n\n", "".join(text_parts)).strip()
    if failure and not reply:
        emit("error", message=("Codex stopped: " + failure)[:600])
    if reply:
        emit("assistant", content=reply)
    emit("done", duration_ms=int((time.time() - t0) * 1000), iterations=1,
         stats={"brain": "Codex", "model": model or str(_user_config().get("model") or ""),
                "tokens_in": (usage or {}).get("inputTokens"), "tokens_out": (usage or {}).get("outputTokens")}
         if usage else {"brain": "Codex"}, reason="finished")
    return 0
