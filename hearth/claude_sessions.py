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
import sys
from typing import Any, Callable, Dict, List, Optional

Emit = Callable[..., None]


def args_for(exe: List[str], sid: str, mode: str, model: str = "") -> List[str]:
    from .claude_brain import claude_settings
    return list(exe) + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                        "--include-partial-messages", "--permission-prompt-tool", "stdio",
                        "--permission-mode", mode, "--settings", json.dumps(claude_settings())] + (
        ["--model", model] if model else []) + (["--resume", sid] if sid else [])


async def run(exe: List[str], sid: str, cwd: str, text: str, emit: Emit, ask: Callable[[str], bool], *,
              mode: str = "default", drain: Optional[Callable[[], List[str]]] = None, model: str = "") -> int:
    """Resume session `sid` (or start one in `cwd` when sid is empty) and run `text` as a turn."""
    from .agent_sessions import _result_text, _steps_of
    from .channel_core import _what, out_of
    from .claude_brain import _QUIET_TOOLS, hearth_view, agent_env

    proc = await asyncio.create_subprocess_exec(
        *args_for(exe, sid, mode, model), cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
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
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        errs.cancel()
        await asyncio.gather(errs, return_exceptions=True)
    if failed:
        emit("error", message=f"Claude Code stopped: {failed}"[:600])
    return 0 if replied and not failed else 1
