"""A Claude Code session carried on from outside Claude Code, over its stream-json
host protocol (what its SDKs speak): messages in on stdin, events out on stdout.

The permission mode is said outright, so the person's own default (even one
that skips every prompt) never decides what a run from the phone may do. What
Claude Code would ask about comes to `ask` (--permission-prompt-tool stdio,
answered with can_use_tool control responses). A message sent while it works
goes in as the next user message, which Claude Code takes when its turn ends."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

Emit = Callable[..., None]


def args_for(exe: List[str], sid: str, mode: str, model: str = "", effort: str = "") -> List[str]:
    from .claude_brain import claude_settings
    from .shared_memory import NAME, launch
    # Inglen's tools for agents ride along (memory, and call_me so "call me when you're done" works).
    tools = json.dumps({"mcpServers": {NAME: {"type": "stdio", **launch()}}})
    return list(exe) + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                        "--include-partial-messages", "--permission-prompt-tool", "stdio",
                        "--permission-mode", mode, "--settings", json.dumps(claude_settings()),
                        "--mcp-config", tools] + (["--model", model] if model else []) + (["--effort", effort] if effort else []) + (["--resume", sid] if sid else [])


async def run(exe: List[str], sid: str, cwd: str, text: str, emit: Emit, ask: Callable[[str], bool], *,
              mode: str = "default", drain: Optional[Callable[[], List[str]]] = None, model: str = "",
              effort: str = "") -> int:
    """Resume session `sid` (or start one in `cwd` when sid is empty) and run `text` as a turn."""
    from .agent_sessions import _result_text, _steps_of
    from .channel_core import _what, out_of
    from .claude_brain import _QUIET_TOOLS, hearth_view, agent_env

    proc = await asyncio.create_subprocess_exec(
        *args_for(exe, sid, mode, model, effort), cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024, env=agent_env(),
        creationflags=0x08000000 if sys.platform == "win32" else 0)
    errs = asyncio.ensure_future(proc.stderr.read())
    sent, answered = 0, 0                    # user messages given, turns finished
    parts: List[str] = []
    failed, replied = "", False

    def send(obj: Dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))

    def user(words: str) -> None:
        nonlocal sent
        send({"type": "user", "message": {"role": "user", "content": words}})
        sent += 1

    def reply(final: str) -> None:
        nonlocal parts, replied
        said = "".join(parts).strip() or final.strip()
        parts = []
        if said:
            replied = True
            emit("text", text=said)

    line = None
    try:
        send({"type": "control_request", "request_id": "inglen_init", "request": {"subtype": "initialize"}})
        user(text)
        await proc.stdin.drain()
        line = asyncio.ensure_future(proc.stdout.readline())
        while True:
            # Once it has been told there's nothing more, what's sent waits for the caller's next run.
            more = [s for s in (drain() if drain and not proc.stdin.is_closing() else []) if s]
            for s in more:
                user(s)
                emit("note", text="Claude Code takes this next.")
            if more:
                await proc.stdin.drain()
            done, _ = await asyncio.wait([line], timeout=0.3)
            if not done:
                continue
            raw = line.result()
            if not raw:
                break
            line = asyncio.ensure_future(proc.stdout.readline())
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "control_request":
                req = ev.get("request") or {}
                if req.get("subtype") == "can_use_tool":
                    name, args = str(req.get("tool_name") or ""), req.get("input") if isinstance(req.get("input"), dict) else {}
                    from .tools import mode_allows
                    from .channel_core import question_for
                    tname, targs = hearth_view(name, args)
                    if tname == "run_command":
                        targs = dict(targs, cwd=cwd)
                    ok = mode_allows(tname, targs) or await asyncio.to_thread(
                        ask, question_for("Claude Code", tname, targs))
                    answer = ({"behavior": "allow", "updatedInput": args} if ok
                              else {"behavior": "deny", "message": "Not allowed from the phone."})
                    send({"type": "control_response", "response": {"subtype": "success", "request_id": ev.get("request_id"),
                                                                    "response": answer}})
                else:
                    send({"type": "control_response", "response": {"subtype": "error", "request_id": ev.get("request_id"),
                                                                    "error": "Inglen doesn't answer this"}})
                await proc.stdin.drain()
            elif t == "control_response":
                continue
            elif t == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
                emit("session", agent="claude-code", id=str(ev["session_id"]))
            elif ev.get("parent_tool_use_id"):
                continue                     # a helper's own steps
            elif t == "stream_event":
                d = ((ev.get("event") or {}).get("delta") or {})
                if d.get("type") == "text_delta" and d.get("text"):
                    parts.append(d["text"])
                    emit("live", text="".join(parts))
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    emit("think", text=d["thinking"])
            elif t == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") not in _QUIET_TOOLS:
                        _in = b.get("input") if isinstance(b.get("input"), dict) else {}
                        for st in _steps_of(*hearth_view(str(b.get("name") or ""), _in), _in.get("description")):
                            emit("step", words=st["w"], tool=st["tool"], cmd=st.get("cmd", ""))
                        if parts:
                            parts.append("\n\n")
            elif t == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        emit("step_out", out=out_of(_result_text(b.get("content"))))
            elif t == "result":
                answered += 1
                final = str(ev.get("result") or "")
                if ev.get("is_error"):
                    failed = final or str(ev.get("subtype") or "error")
                    parts = []
                else:
                    reply(final)
                more = [s for s in (drain() if drain and not proc.stdin.is_closing() else []) if s]
                for s in more:
                    user(s)
                if more:
                    await proc.stdin.drain()
                if answered >= sent and not proc.stdin.is_closing():
                    proc.stdin.close()       # nothing more to say: it ends once it has written all
        await asyncio.wait_for(proc.wait(), 30)
        if proc.returncode and not failed and not replied:
            failed = (await errs).decode("utf-8", "replace")[-400:] or f"it stopped (exit {proc.returncode})"
    except Exception as e:
        failed = failed or f"{type(e).__name__}: {e}"
    finally:
        if line is not None:
            line.cancel()
            await asyncio.gather(line, return_exceptions=True)
        from .aproc import reap
        await reap(proc)
        errs.cancel()
        await asyncio.gather(errs, return_exceptions=True)
    if failed:
        emit("error", message=f"Claude Code stopped: {failed}"[:600])
    return 0 if replied and not failed else 1


# ---------------------------------------------------------------- a session open on the PC

def deliver(inbox: Dict[str, Any], words: str) -> bool:
    """One message into a live session's messaging inbox: the token line, then the message line."""
    from .agent_sessions import PHONE_FROM, phone_words
    try:
        with open(inbox["pipe"], "r+b", buffering=0) as f:
            f.write((json.dumps({"type": "auth", "token": inbox["token"]}) + "\n").encode("utf-8"))
            f.write((json.dumps({"type": "user", "from": PHONE_FROM,
                                 "message": {"role": "user", "content": phone_words(words)}}) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


SEEN_S = 20            # an idle session that hasn't taken the message by then won't (held, or refused)
BUSY_S = 600           # a busy one takes it when its turn allows
TURN_S = 1800          # how long a reply is followed


async def live(inbox: Dict[str, Any], path: str, text: str, emit: Emit, *,
               drain: Optional[Callable[[], List[str]]] = None) -> int:
    """Send `text` into a Claude Code session running on the PC (VS Code, a terminal, its desktop app)
    through its messaging inbox, so it shows there as it runs, and follow its transcript for the reply:
    steps and words as Claude Code writes them, until its turn ends. Messages sent meanwhile go in the
    same way; the session takes them between its steps."""
    from .agent_sessions import _result_text, _steps_of, from_phone
    from .channel_core import out_of
    from .claude_brain import _QUIET_TOOLS, hearth_view

    pos = os.path.getsize(path) if path and os.path.exists(path) else 0
    if not await asyncio.to_thread(deliver, inbox, text):
        emit("error", message="Claude Code on your PC didn't take the message.")
        return 2
    sent, seen, t0, told = 1, 0, time.time(), False
    parts: List[str] = []
    partial = b""
    replied = False
    while time.time() - t0 < TURN_S:
        for more in (drain() if drain else []):
            if more and await asyncio.to_thread(deliver, inbox, more):
                sent += 1
                emit("note", text="Claude Code has it, in the session on your PC.")
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            chunk = b""
        if chunk:
            pos += len(chunk)
            lines = (partial + chunk).split(b"\n")
            partial = lines.pop()
        else:
            lines = []
        for raw in lines:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if from_phone(o):
                seen += 1
                continue
            if o.get("isSidechain") or o.get("type") not in ("user", "assistant") or seen == 0:
                continue
            m = o.get("message") or {}
            content = m.get("content") if isinstance(m.get("content"), list) else []
            if o["type"] == "user":
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        emit("step_out", out=out_of(_result_text(b.get("content"))))
                continue
            if o.get("isApiErrorMessage") or m.get("model") == "<synthetic>":
                # The session stopped on its own error (a usage limit, an outage): that is the turn's end.
                why = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict)).strip()
                emit("error", message=f"Claude Code stopped: {why or 'it hit an error'}"[:600])
                return 1
            used = wrote = False
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    wrote = True
                    parts.append(str(b["text"]))
                    emit("live", text="\n\n".join(parts))
                elif b.get("type") == "thinking" and b.get("thinking"):
                    emit("think", text=str(b["thinking"]))
                elif b.get("type") == "tool_use" and b.get("name") not in _QUIET_TOOLS:
                    used = True
                    _in = b.get("input") if isinstance(b.get("input"), dict) else {}
                    for st in _steps_of(*hearth_view(str(b.get("name") or ""), _in), _in.get("description")):
                        emit("step", words=st["w"], tool=st["tool"], cmd=st.get("cmd", ""))
            # Claude Code writes each block of a reply as a line of its own, all marked end_turn:
            # the turn is over at the one with its words.
            if m.get("stop_reason") == "end_turn" and wrote and not used and seen >= sent:
                said = "\n\n".join(parts).strip()
                if said:
                    emit("text", text=said)
                    replied = True
                return 0 if replied else 1
        if seen == 0:
            from .agent_sessions import claude_inbox
            now = claude_inbox(str(inbox.get("sid") or "")) if inbox.get("sid") else inbox
            if not now:
                emit("error", message="Claude Code on your PC closed before it took the message.")
                return 2
            if now.get("busy") and not told and time.time() - t0 > 4:
                told = True
                emit("note", text="Claude Code is busy on your PC; it takes this when its turn allows.")
            if time.time() - t0 > (BUSY_S if now.get("busy") else SEEN_S):
                emit("error", message="Claude Code on your PC hasn't taken it. It may be asking there whether to "
                                      "accept messages from outside the session.")
                return 3
        await asyncio.sleep(0.25)
    emit("error", message="Claude Code is still working on your PC. Its reply shows here when you open the session again.")
    return 1
