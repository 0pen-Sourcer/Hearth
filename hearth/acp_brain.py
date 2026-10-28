"""Any agent that speaks the Agent Client Protocol as the assistant's brain.

ACP is JSON-RPC over the agent's stdin and stdout: Hearth starts a session in
the workspace with its own MCP server attached, sends the message, and reads
the agent's updates (its thinking, its reply, each tool call) as they come.
When the agent wants to act it asks the client first, so every command and
file change goes through Hearth's approval switch like Hearth's own tools.

One module serves every such agent; what differs is how each is started,
which lives in claude_brain.ACP.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import claude_brain as _base
from . import env as _env
from .codex_brain import _Rpc

SERVER = "hearth_brain"
_MODELS = os.path.join(_base._HOME, "acp_models.json")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# What kind of thing an agent's tool call is, as the Hearth tool it amounts to.
_KIND = {"execute": "run_command", "edit": "edit_file", "delete": "delete_path", "move": "move_path",
         "fetch": "web_fetch", "read": "read_file", "search": "grep_search"}


def _env_for(provider: str) -> Dict[str, str]:
    """Settings an agent reads from its environment, so nothing is written into the person's files."""
    extra: Dict[str, str] = {"HEARTH_HELPER": "1"}
    if provider == "opencode":
        # Its shell, edits and fetches ask first, so they come to Hearth; MCP calls
        # wait as long as a person takes to answer. Turning a tool off instead would
        # make OpenCode's free models refuse the request.
        extra["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "permission": {"edit": "ask", "bash": "ask", "webfetch": "ask", "external_directory": "ask"},
            "experimental": {"mcp_timeout": 900000}})
    return extra


def _mcp_servers() -> List[Dict[str, Any]]:
    launch = _base._mcp_launch(gate=True)
    return [{"name": SERVER, "command": launch["command"], "args": launch["args"],
             "env": [{"name": k, "value": v} for k, v in launch["env"].items()]}]


async def _start(provider: str, cwd: str, extra: Optional[List[str]] = None):
    exe = _base.exe_for(provider)
    if not exe:
        return None
    return await asyncio.create_subprocess_exec(
        *exe, *_base.ACP[provider].get("args", []), *(extra or []), cwd=cwd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
        env=dict(os.environ, **_env_for(provider)), creationflags=_NO_WINDOW)


async def _hello(rpc: _Rpc) -> Dict[str, Any]:
    return await rpc.call("initialize", {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        "clientInfo": {"name": "hearth", "title": "Hearth", "version": "0.7.6"}}, 60)


def _choices(opt: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A select option's values as {value, name, description}, groups opened up."""
    out: List[Dict[str, Any]] = []
    for o in (opt or {}).get("options") or []:
        if isinstance(o, str):
            out.append({"value": o, "name": o})
        elif isinstance(o, dict) and isinstance(o.get("options"), list):
            out += _choices(o)
        elif isinstance(o, dict) and o.get("value") not in (None, ""):
            out.append(o)
    return out


def _model_option(opts: List[Dict[str, Any]], offered: Any = None) -> Optional[Dict[str, Any]]:
    """The session's model setting: a config option, or the separate model list
    some agents send instead (set with session/set_model)."""
    hit = next((o for o in opts or [] if o.get("category") == "model" or o.get("id") == "model"), None)
    if hit or not isinstance(offered, dict) or not offered.get("availableModels"):
        return hit
    return {"id": "model", "via": "session/set_model", "currentValue": offered.get("currentModelId") or "",
            "options": [{"value": m.get("modelId"), "name": m.get("name") or m.get("modelId"),
                         "description": m.get("description") or ""}
                        for m in offered["availableModels"] if isinstance(m, dict) and m.get("modelId")]}


def _level_option(opts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The session's reasoning effort setting, when the agent offers one for its current model."""
    return next((o for o in opts or [] if o.get("category") == "thought_level"), None)


def _levels(opt: Optional[Dict[str, Any]]) -> List[str]:
    """The levels a thought_level option offers, without its own "default"."""
    return [str(c["value"]) for c in _choices(opt) if str(c["value"]).lower() != "default"]


_STORED_READ: Dict[str, Any] = {"mtime": None, "data": {}}


def _stored(provider: str) -> Dict[str, Any]:
    """This agent's entry in the models file, read again only when the file changes
    (a model list asks once per model)."""
    try:
        mtime = os.path.getmtime(_MODELS)
        if _STORED_READ["mtime"] != mtime:
            with open(_MODELS, encoding="utf-8") as f:
                _STORED_READ.update(mtime=mtime, data=json.load(f) or {})
        return _STORED_READ["data"].get(provider) or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _store(provider: str, **changes: Any) -> None:
    """Update this agent's entry in the models file, keeping what else it holds."""
    try:
        with open(_MODELS, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    stamp = (_base.exe_for(provider) or [""])[-1]
    entry = data.get(provider) or {}
    if entry.get("stamp") != stamp:
        entry = {"stamp": stamp}
    entry.update(changes)
    data[provider] = entry
    try:
        os.makedirs(_base._HOME, exist_ok=True)
        with open(_MODELS, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _remember_models(provider: str, opt: Optional[Dict[str, Any]]) -> None:
    if not opt:
        return
    listed = [{"id": str(o["value"]), "label": str(o.get("name") or o["value"]),
               "about": str(o.get("description") or ""), "model": str(o["value"]),
               "ctx": None} for o in _choices(opt)]
    _store(provider, models=listed, default=str(opt.get("currentValue") or ""))


def _remember_levels(provider: str, opts: List[Dict[str, Any]], model: str = "") -> None:
    """The effort levels the agent offered for the model the session is on:
    an empty list when it offered none for that model."""
    model = model or str((_model_option(opts) or {}).get("currentValue") or "")
    if not model:
        return
    seen = dict(_stored(provider).get("levels") or {})
    seen[model] = _levels(_level_option(opts))
    _store(provider, levels=seen)


def _run_quiet(argv: List[str], timeout: float = 60) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW)
    except Exception:
        return ""
    return (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace")


def parse_variants(text: str) -> Dict[str, List[str]]:
    """model -> its reasoning levels, from a listing that prints each model's
    id on its own line followed by its JSON details with a "variants" object."""
    import re
    dec = json.JSONDecoder()
    out: Dict[str, List[str]] = {}
    pos = 0
    head = re.compile(r"^([^\s{}\[\]\"]+)\s*$", re.M)
    while True:
        m = head.search(text, pos)
        if not m:
            break
        rest = m.end()
        while rest < len(text) and text[rest].isspace():
            rest += 1
        if rest < len(text) and text[rest] == "{":
            try:
                obj, end = dec.raw_decode(text, rest)
            except ValueError:
                pos = m.end()
                continue
            v = obj.get("variants") if isinstance(obj, dict) else None
            out[m.group(1)] = [str(k) for k in v] if isinstance(v, dict) else []
            pos = end
        else:
            pos = m.end()
    return out


def _variants(provider: str) -> Dict[str, List[str]]:
    """Every model's levels from the agent's own verbose model list, where its
    spec names the command for one. Read once per installed version."""
    how = (_base.ACP.get(provider) or {}).get("levels_args")
    exe = _base.exe_for(provider)
    if not how or not exe:
        return {}
    got = _stored(provider)
    if isinstance(got.get("variants"), dict) and got.get("stamp") == exe[-1]:
        return got["variants"]
    found = parse_variants(_run_quiet(exe + list(how), 90))
    if found:
        _store(provider, variants=found)
    return found


def flag_levels(help_text: str, flag: str) -> List[str]:
    """The values a CLI's help lists for a flag ("[possible values: a, b]" or "(choices: "a", "b")")."""
    import re
    i = help_text.find(flag)
    if i < 0:
        return []
    near = help_text[i:i + 400]
    m = re.search(r"\[possible values:\s*([^\]]+)\]|\(choices:\s*([^)]+)\)", near)
    if not m:
        return []
    return [v.strip().strip('"\'') for v in (m.group(1) or m.group(2)).split(",") if v.strip().strip('"\'')]


def _flag_levels(provider: str) -> List[str]:
    """The levels an agent takes as a start flag, read from its --help once per installed version."""
    flag = (_base.ACP.get(provider) or {}).get("effort_flag")
    exe = _base.exe_for(provider)
    if not flag or not exe:
        return []
    got = _stored(provider)
    if isinstance(got.get("flag_levels"), list) and got.get("stamp") == exe[-1]:
        return got["flag_levels"]
    found = flag_levels(_run_quiet(exe + ["--help"], 30), flag)
    _store(provider, flag_levels=found)
    return found


def efforts_for(provider: str, model: str) -> List[str]:
    """The reasoning levels this agent takes with this model: what its session
    offered for it, else its own model list, else its start flag."""
    got = _stored(provider)
    seen = (got.get("levels") or {}).get(model)
    if seen:
        return list(seen)
    return list(_variants(provider).get(model) or _flag_levels(provider))


def models(provider: str) -> List[Dict[str, Any]]:
    """The models the agent offered when a session last started, with its own
    default first, each with the reasoning levels it takes."""
    got = _stored(provider)
    if not got.get("models") and not got.get("asked"):
        try:
            asyncio.run(_discover(provider))
        except Exception:
            pass
        _store(provider, asked=True)
        got = _stored(provider)
    listed = [dict(m, efforts=efforts_for(provider, m.get("model") or m.get("id") or ""))
              for m in got.get("models") or []]
    default = str(got.get("default") or "")
    if not listed and not _flag_levels(provider):
        return []
    return [{"id": "", "label": "Default", "about": default or "The agent's own choice",
             "model": default, "ctx": None, "efforts": efforts_for(provider, default)}] + listed


async def _discover(provider: str) -> None:
    """Start a session in an empty folder only to read which models it offers."""
    import tempfile
    cwd = tempfile.mkdtemp(prefix="hearth-acp-")
    proc = await _start(provider, cwd)
    if proc is None:
        return
    rpc = _Rpc(proc, jsonrpc=True)
    reader = asyncio.ensure_future(rpc.read())
    try:
        await _hello(rpc)
        new = await rpc.call("session/new", {"cwd": cwd, "mcpServers": []}, 60)
        opt = _model_option(new.get("configOptions"), new.get("models"))
        _remember_models(provider, opt)
        _remember_levels(provider, new.get("configOptions") or [], str((opt or {}).get("currentValue") or ""))
    finally:
        reader.cancel()
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        # Let its pipes close inside this loop, not after it is gone.
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except Exception:
            pass


def hearth_call(tool: Dict[str, Any]) -> tuple:
    """(Hearth tool name, args) that a tool call amounts to, for the approval and its card."""
    kind = str(tool.get("kind") or "other")
    raw = tool.get("rawInput") or {}
    where = next((l.get("path") for l in tool.get("locations") or [] if l.get("path")), "")
    title = str(tool.get("title") or "")
    diffs = [c for c in tool.get("content") or [] if c.get("type") == "diff"]
    # One of Hearth's own tools, reached through its MCP server: shown as itself.
    for pre in (f"{SERVER}_", f"mcp__{SERVER}__", f"{SERVER}/", f"{SERVER}."):
        if title.startswith(pre):
            return title[len(pre):], dict(raw)
    if kind == "execute":
        return "run_command", {"command": str(raw.get("command") or title)}
    if kind == "edit" or diffs:
        path = (diffs[0].get("path") if diffs else "") or raw.get("filePath") or raw.get("path") or where
        if diffs and not diffs[0].get("oldText"):
            return "write_file", {"path": path, "content": diffs[0].get("newText") or ""}
        return "edit_file", {"path": path, "edits": [{"old_text": d.get("oldText") or "", "new_text": d.get("newText") or ""}
                                                     for d in diffs]}
    if kind in ("delete", "move"):
        return _KIND[kind], {"path": raw.get("filePath") or raw.get("path") or where, **(
            {"source": raw.get("source") or where, "destination": raw.get("destination") or ""} if kind == "move" else {})}
    if kind == "fetch":
        return "web_fetch", {"url": str(raw.get("url") or title)}
    if kind == "read":
        return "read_file", {"path": raw.get("filePath") or raw.get("path") or where}
    if kind == "search":
        return "grep_search", {"pattern": str(raw.get("pattern") or raw.get("query") or title)}
    # Anything else that asks is treated like a command: shown, and asked about.
    return "run_command", {"command": title or kind}


def _text(content: List[Dict[str, Any]]) -> str:
    out = []
    for c in content or []:
        inner = c.get("content") if c.get("type") == "content" else c
        if isinstance(inner, dict) and inner.get("type") == "text":
            out.append(str(inner.get("text") or ""))
        elif c.get("type") == "diff":
            out.append(f"changed {c.get('path', '')}")
    return "\n".join(out)


async def run_turn(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                   permission_check=None, should_cancel=None, voice: bool = False) -> int:
    provider = _base.current() or ""
    name = _base.BRAINS.get(provider, provider)
    spec = _base.ACP.get(provider) or {}
    if not _base.exe_for(provider):
        emit("error", message=(f"{name} isn't installed on this PC. Get it from {spec.get('site', 'its site')}, "
                               "sign in, or pick another brain."))
        return 2
    _base.ensure_bridge()
    history = history or []
    key = f"{provider}:" + _base._chat_key(history, prompt)
    session = _base.session_for(key, history)
    cwd = _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")
    os.makedirs(cwd, exist_ok=True)
    effort = _base.chosen_effort()
    # An agent that takes the level only when it starts gets it as a flag.
    flag = spec.get("effort_flag")
    extra = [flag, effort] if flag and effort and effort in _flag_levels(provider) else []
    try:
        proc = await _start(provider, cwd, extra)
    except Exception as e:
        emit("error", message=f"{name} didn't start: {type(e).__name__}: {e}")
        return 1
    rpc = _Rpc(proc, jsonrpc=True, who=name)
    reader = asyncio.ensure_future(rpc.read())
    err_task = asyncio.ensure_future(proc.stderr.read())
    t0 = time.time()
    _base._checks[_base._TOKEN] = permission_check
    text_parts: List[str] = []
    shown: Dict[str, tuple] = {}                  # call id -> (when its card appeared, its name)
    usage: Dict[str, Any] = {}
    failure, sid, model_used, replaying = "", None, "", False
    declined: List[str] = []
    try:
        try:
            hello = await _hello(rpc)
        except Exception as e:
            failure = f"{name} didn't answer: {e}"
            raise
        caps = hello.get("agentCapabilities") or {}
        servers = _mcp_servers()
        opened: Dict[str, Any] = {}
        if session:
            how = ("session/resume" if "resume" in (caps.get("sessionCapabilities") or {})
                   else "session/load" if caps.get("loadSession") else "")
            if how:
                # Loading replays the old messages as updates; those were shown already.
                replaying = how == "session/load"
                try:
                    opened = await rpc.call(how, {"sessionId": session, "cwd": cwd, "mcpServers": servers}, 120)
                    sid = session
                except Exception:
                    sid = None
                replaying = False
                while not rpc.inbox.empty():
                    m = rpc.inbox.get_nowait()
                    if m and "id" in m and "method" in m:
                        rpc.send({"id": m["id"], "error": {"code": -32601, "message": "not now"}})
        text_in = prompt
        if not sid:
            opened = await rpc.call("session/new", {"cwd": cwd, "mcpServers": servers}, 120)
            sid = opened.get("sessionId")
            persona = _base.persona_text(
                voice, host=name, tools=f"the {SERVER} MCP tools",
                own_tools=("Use your own shell and file tools as usual: Hearth sees each command and change "
                           "before it happens and asks the user when that is needed."))
            earlier = _base._transcript(history) if history else ""
            text_in = (persona + "\n\n---\n\n"
                       + (f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n" if earlier else "")
                       + prompt)
        opts = opened.get("configOptions") or []
        opt = _model_option(opts, opened.get("models"))
        _remember_models(provider, opt)
        model_used = str((opt or {}).get("currentValue") or "")
        want = _base.chosen_model(provider)
        switched = False
        if want and opt and want != model_used:
            try:
                if opt.get("via"):
                    await rpc.call(opt["via"], {"sessionId": sid, "modelId": want}, 30)
                else:
                    # The answer carries every option again: the effort one
                    # comes and goes with the model.
                    got = await rpc.call("session/set_config_option",
                                         {"sessionId": sid, "configId": opt["id"], "value": want}, 30)
                    opts = (got or {}).get("configOptions") or opts
                model_used, switched = want, True
            except Exception:
                pass
        if opt and not opt.get("via"):
            _remember_levels(provider, opts, model_used)
        lvl = _level_option(opts)
        if lvl:
            offered = [str(c["value"]) for c in _choices(lvl)]
            target = effort if effort in offered else ("default" if "default" in offered else "")
            # After a model switch the level it reports isn't one that was
            # picked, so the level is always set then.
            if target and (switched or target != str(lvl.get("currentValue") or "")):
                try:
                    await rpc.call("session/set_config_option",
                                   {"sessionId": sid, "configId": lvl["id"], "value": target}, 30)
                except Exception:
                    pass

        async def ask(tool_name: str, args: Dict[str, Any]) -> bool:
            return await _base.ask_hearth(permission_check, tool_name, args)

        turn = asyncio.ensure_future(rpc.call("session/prompt", {"sessionId": sid,
                                                                 "prompt": [{"type": "text", "text": text_in}]}, 86400))
        cancelled = False
        # The answer to the prompt can arrive ahead of updates still queued; those are read first.
        while not (turn.done() and rpc.inbox.empty()):
            if should_cancel and should_cancel() and not cancelled:
                cancelled = True
                rpc.send({"method": "session/cancel", "params": {"sessionId": sid}})
                emit("cancelled", message="stopped by user")
            try:
                msg = await asyncio.wait_for(rpc.inbox.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                break
            meth, prm = msg.get("method"), msg.get("params") or {}
            if "id" in msg:                                  # the agent asks something
                if meth == "session/request_permission":
                    tool = prm.get("toolCall") or {}
                    tname, targs = hearth_call(tool)
                    ok = not cancelled and await ask(tname, targs)
                    if not ok:
                        declined.append(str(targs.get("command") or targs.get("path") or targs.get("url") or tname))
                    want_kind = ("allow_once", "allow_always") if ok else ("reject_once", "reject_always")
                    pick = next((o for k in want_kind for o in prm.get("options") or [] if o.get("kind") == k), None)
                    rpc.send({"id": msg["id"], "result": {"outcome": {"outcome": "selected", "optionId": pick["optionId"]}}
                              if pick else {"outcome": {"outcome": "cancelled"}}})
                else:
                    rpc.send({"id": msg["id"], "error": {"code": -32601, "message": "Hearth doesn't offer this"}})
                await proc.stdin.drain()
                continue
            if meth != "session/update":
                continue
            up = prm.get("update") or {}
            kind = up.get("sessionUpdate")
            if kind == "agent_message_chunk":
                piece = str((up.get("content") or {}).get("text") or "")
                if piece:
                    text_parts.append(piece)
                    emit("assistant_chunk", content=piece)
            elif kind == "agent_thought_chunk":
                piece = str((up.get("content") or {}).get("text") or "")
                if piece:
                    emit("thinking_chunk", content=piece)
            elif kind in ("tool_call", "tool_call_update"):
                cid = str(up.get("toolCallId") or "")
                status = up.get("status") or ""
                # The first notice can come before the call has its arguments; the
                # card is shown once it has them, or when it ends.
                tname, targs = hearth_call(up)
                raw = up.get("rawInput") or {}
                has_args = bool(raw.get("command")) if up.get("kind") == "execute" else any(v for v in targs.values())
                if cid not in shown and (has_args or status in ("completed", "failed")):
                    shown[cid] = (time.time(), tname)
                    emit("tool_call", name=tname, args=targs, id=cid)
                if status in ("completed", "failed") and cid in shown:
                    out = _text(up.get("content")) or str((up.get("rawOutput") or {}).get("output") or "")
                    began, first = shown.pop(cid)
                    emit("tool_result", name=first, content=(out if status == "completed" else f"Failed: {out}")[:20000],
                         ms=int((time.time() - began) * 1000), id=cid)
            elif kind == "config_option_update" and opt and not opt.get("via"):
                _remember_levels(provider, up.get("configOptions") or [], model_used)
            elif kind == "usage_update":
                usage["ctx"] = up.get("size")
                usage["used"] = up.get("used")
        result = turn.result() if turn.done() and not turn.exception() else {}
        if turn.done() and turn.exception():
            failure = str(turn.exception())
        u = result.get("usage") or {}
        usage.update(tokens_in=u.get("inputTokens"), tokens_out=u.get("outputTokens"))
        if result.get("stopReason") == "refusal":
            failure = f"{name} declined this request."
    except Exception as e:
        failure = failure or f"{name} stopped: {type(e).__name__}: {e}"
    finally:
        _base._checks.pop(_base._TOKEN, None)
        reader.cancel()
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except Exception:
            pass
    err = ""
    try:
        err = (await asyncio.wait_for(err_task, 5)).decode("utf-8", "replace").strip()
    except Exception:
        pass
    if sid:
        _base._save_session(key, sid, last=_base.asked(prompt))
    reply = "".join(text_parts).strip()
    if failure:
        emit("error", message=(failure + (f" ({err.splitlines()[-1][:300]})" if err else ""))[:700])
    elif not reply and declined:
        # Some agents end the turn when told no, without a word.
        emit("assistant", content=f"Stopped there, since you said no to: {declined[-1][:200]}.")
    elif not reply and not (should_cancel and should_cancel()):
        emit("error", message=f"{name} gave no answer. " + (err[-400:] if err else "Check it's signed in."))
    if reply:
        emit("assistant", content=reply)
    emit("done", duration_ms=int((time.time() - t0) * 1000), iterations=1,
         stats={"brain": name, "model": model_used, "tokens_in": usage.get("tokens_in"),
                "tokens_out": usage.get("tokens_out"), "ctx": usage.get("ctx")},
         reason="finished")
    return 0
