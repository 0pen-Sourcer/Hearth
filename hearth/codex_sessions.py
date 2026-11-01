"""A Codex session carried on from outside the Codex app, over its app-server
(the protocol codex_brain uses for Inglen's own turns).

The thread keeps its own model, approval policy, reviewer and sandbox: it is
resumed without overrides. The one exception is a thread an older Inglen left
read-only with approvals off (`never` + read-only, what `codex exec` used to be
given): that is lifted to the session default, on-request in the project
folder, never lower. What Codex asks about comes to `ask`; messages sent while
a turn runs steer it (turn/steer), or start the next turn when it has ended."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

Emit = Callable[..., None]

_EXEC_DEFAULT = ("never", "readOnly")
_NEXT_MODEL: Dict[str, str] = {}             # thread -> the model /model picked, for its next turn
COMMANDS = ("/compact", "/review", "/model")


def inglen_tools() -> List[str]:
    """`-c` options giving this app-server Inglen's tools for agents (memory, and call_me)."""
    from .codex_brain import _toml
    from .shared_memory import NAME, launch
    out: List[str] = []
    for k, v in launch().items():
        out += ["-c", f"mcp_servers.{NAME}.{k}={_toml(v)}"]
    return out


def _sandbox_kind(policy: Any) -> str:
    return str(policy.get("type") or "") if isinstance(policy, dict) else str(policy or "")


def lifted(resumed: Dict[str, Any], cwd: str) -> Dict[str, Any]:
    """Overrides for turn/start: none, unless the thread came back read-only with approvals off."""
    if (str(resumed.get("approvalPolicy") or ""), _sandbox_kind(resumed.get("sandbox"))) != _EXEC_DEFAULT:
        return {}
    return {"approvalPolicy": "on-request",
            "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": False}}


async def run(exe: List[str], sid: str, cwd: str, text: str, emit: Emit, ask: Callable[[str], bool], *,
              drain: Optional[Callable[[], List[str]]] = None, model: str = "", effort: str = "") -> int:
    """Resume thread `sid` (or start one in `cwd` when sid is empty) and run `text` as a turn."""
    from .agent_sessions import _step
    from .channel_core import _what, out_of, question_for
    from .codex_brain import _Rpc, _change_call, _shown_command
    from .tools import mode_allows
    from .keystore import agent_env

    proc = await asyncio.create_subprocess_exec(
        *exe, "app-server", *inglen_tools(), cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024, env=agent_env(),
        creationflags=0x08000000 if sys.platform == "win32" else 0)
    rpc = _Rpc(proc)
    reader = asyncio.ensure_future(rpc.read())
    replies: Dict[str, str] = {}             # agent message id -> its text, in the order written
    items: Dict[str, Dict[str, Any]] = {}
    waiting: List[str] = []                  # said on the phone, not yet given to Codex
    failure = ""

    def said() -> None:
        waiting.extend(s for s in (drain() if drain else []) if s)

    async def may(question: str) -> bool:
        return bool(await asyncio.to_thread(ask, question))

    try:
        await rpc.call("initialize", {"clientInfo": {"name": "inglen_sessions", "title": "Inglen", "version": "0.7.6"}}, 30)
        rpc.send({"method": "initialized"})
        if sid:
            got = await rpc.call("thread/resume", {"threadId": sid, "cwd": cwd, "excludeTurns": True}, 120)
        else:
            got = await rpc.call("thread/start", {"cwd": cwd}, 120)
        tid = str((got.get("thread") or {}).get("id") or "")
        if not tid:
            raise RuntimeError("Codex gave no session id")
        emit("session", agent="codex", id=tid)
        override = lifted(got, cwd)
        if model:
            _NEXT_MODEL.setdefault(tid, model)

        async def begin(words: str) -> Optional[str]:
            """Start what `words` ask for: Codex's own commands as its app-server takes them,
            anything else as a message. The turn's id ('' while a compaction runs), or None
            when nothing runs."""
            head, _, rest = words.strip().partition(" ")
            low, rest = head.lower(), rest.strip()
            if low == "/compact":
                await rpc.call("thread/compact/start", {"threadId": tid}, 60)
                emit("note", text="Codex is compacting this session.")
                return ""
            if low == "/review":
                target = {"type": "custom", "instructions": rest} if rest else {"type": "uncommittedChanges"}
                r = await rpc.call("review/start", {"threadId": tid, "target": target}, 60)
                return str((r.get("turn") or {}).get("id") or "")
            if low == "/model":
                if not rest:
                    ids = [str(m.get("id") or m.get("model") or "") for m in (await rpc.call("model/list", {}, 30)).get("data") or []]
                    emit("text", text="Models: " + ", ".join(i for i in ids if i) if ids else "Codex listed no models.")
                    return None
                _NEXT_MODEL[tid] = rest
                emit("text", text=f"Codex uses {rest} from your next message.")
                return None
            if low.startswith("/") and len(low) > 1 and " " not in head and low not in COMMANDS:
                emit("text", text=f"{head} works in the Codex app. From here: " + ", ".join(COMMANDS) + ".")
                return None
            model = _NEXT_MODEL.pop(tid, "")
            r = await rpc.call("turn/start", {"threadId": tid, "input": [{"type": "text", "text": words}], **override,
                                              **({"model": model} if model else {}),
                                              **({"effort": effort} if effort else {})}, 60)
            return str((r.get("turn") or {}).get("id") or "")

        turn_id = await begin(text)
        if turn_id is None:
            return 0
        while True:
            said()
            while waiting:
                try:
                    await rpc.call("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                                  "input": [{"type": "text", "text": waiting[0]}]}, 15)
                except Exception:
                    break                    # the turn just ended: it starts the next one below
                waiting.pop(0)
                emit("note", text="Codex has it, in the turn it's on.")
            try:
                msg = await asyncio.wait_for(rpc.inbox.get(), timeout=0.3)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                failure = failure or "Codex closed before the turn ended"
                break
            meth, prm = msg.get("method"), msg.get("params") or {}
            if prm.get("threadId") not in (None, tid):
                continue
            if "id" in msg:                  # Codex asks before doing something
                if meth == "item/commandExecution/requestApproval":
                    run = {"command": _shown_command(prm), "cwd": str(prm.get("cwd") or cwd)}
                    ok = mode_allows("run_command", run) or await may(question_for("Codex", "run_command", run))
                elif meth == "item/fileChange/requestApproval":
                    changes = (items.get(str(prm.get("itemId"))) or {}).get("changes") or []
                    ok = bool(changes)
                    for c in changes:
                        if not (mode_allows(*_change_call(c)) or await may(question_for("Codex", *_change_call(c)))):
                            ok = False
                            break
                else:
                    rpc.send({"id": msg["id"], "error": {"code": -32601, "message": "Inglen doesn't answer this"}})
                    await proc.stdin.drain()
                    continue
                rpc.send({"id": msg["id"], "result": {"decision": "accept" if ok else "decline"}})
                await proc.stdin.drain()
                continue
            item = prm.get("item") or {}
            kind, iid = item.get("type"), str(item.get("id") or prm.get("itemId") or "")
            if meth == "item/agentMessage/delta":
                replies[iid] = replies.get(iid, "") + str(prm.get("delta") or "")
                emit("live", text="\n\n".join(t for t in replies.values() if t))
            elif meth in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta") and prm.get("delta"):
                emit("think", text=str(prm["delta"]))
            elif meth == "item/started":
                items[iid] = item
                steps = ([_step("shell", {"command": _shown_command(item)})] if kind == "commandExecution"
                         else [_step(*_change_call(c)) for c in item.get("changes") or []] if kind == "fileChange" else [])
                for st in filter(None, steps):
                    emit("step", words=st["w"], tool=st["tool"], cmd=st.get("cmd", ""))
            elif meth == "item/completed":
                if kind == "agentMessage" and item.get("text"):
                    replies[iid] = str(item["text"])
                    emit("live", text="\n\n".join(t for t in replies.values() if t))
                elif kind == "commandExecution" and item.get("aggregatedOutput"):
                    emit("step_out", out=out_of(str(item["aggregatedOutput"])))
            elif meth == "error":
                failure = str((prm.get("error") or {}).get("message") or prm.get("message") or "error")
            elif meth == "turn/completed" or (meth == "thread/compacted" and turn_id == ""):
                t = prm.get("turn") or {}
                if t.get("status") not in (None, "completed"):
                    failure = str((t.get("error") or {}).get("message") or t.get("status"))
                if meth == "thread/compacted":
                    emit("note", text="Compacted.")
                said()
                turn_id = None
                while waiting and not failure and turn_id is None:
                    turn_id = await begin(waiting.pop(0))
                if turn_id is None:
                    break
    except Exception as e:
        failure = failure or f"{type(e).__name__}: {e}"
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        from .aproc import reap
        await reap(proc, 5)
    reply = "\n\n".join(t for t in replies.values() if t).strip()
    if failure:
        emit("error", message=f"Codex stopped: {failure}"[:600])
    if reply:
        emit("text", text=reply)
    return 0 if reply and not failure else 1


# ---------------------------------------------------------------- a session open in the Codex app

PICKUP_S = 45          # a queued message not started by then waits for that session to be free
TURN_S = 1800


async def follow(path: str, emit: Emit, *, queue_more: Optional[Callable[[str], "asyncio.Future"]] = None,
                 drain: Optional[Callable[[], List[str]]] = None) -> int:
    """After `codex queue` has handed a message to the Codex app holding the session, follow the session's
    rollout for that turn (Codex marks it turn_trigger "queue"): its steps and words as written, until
    it completes. What the phone sends meanwhile is queued the same way and runs after."""
    from .agent_sessions import _steps_of
    from .channel_core import out_of

    pos = os.path.getsize(path) if os.path.exists(path) else 0
    partial, parts, started, t0 = b"", [], False, time.time()
    while time.time() - t0 < TURN_S:
        for more in (drain() if drain else []):
            if more and queue_more is not None and await queue_more(more):
                emit("note", text="Codex takes this next, in the session on your PC.")
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            chunk = b""
        lines = []
        if chunk:
            pos += len(chunk)
            lines = (partial + chunk).split(b"\n")
            partial = lines.pop()
        for raw in lines:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            kind, p = o.get("type"), o.get("payload") if isinstance(o.get("payload"), dict) else {}
            if kind == "event_msg" and p.get("type") == "task_started":
                started = started or ((p.get("turn_attribution") or {}).get("turn_trigger") == "queue")
                continue
            if not started:
                continue
            if kind == "event_msg" and p.get("type") == "agent_message" and p.get("message"):
                parts.append(str(p["message"]))
                emit("live", text="\n\n".join(parts))
            elif kind == "event_msg" and p.get("type") == "agent_reasoning" and p.get("text"):
                emit("think", text=str(p["text"]))
            elif kind == "response_item" and p.get("type") in ("function_call", "local_shell_call", "custom_tool_call"):
                try:
                    args = (p.get("input") or "") if p.get("type") == "custom_tool_call" else (
                        json.loads(p.get("arguments") or "{}") if isinstance(p.get("arguments"), str) else (p.get("action") or {}))
                except ValueError:
                    args = {}
                for st in _steps_of(str(p.get("name") or "shell"), args):
                    emit("step", words=st["w"], tool=st["tool"], cmd=st.get("cmd", ""))
            elif kind == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output") and p.get("output"):
                emit("step_out", out=out_of(str(p["output"])))
            elif kind == "event_msg" and p.get("type") in ("task_complete", "turn_aborted"):
                said = "\n\n".join(parts).strip() or str(p.get("last_agent_message") or "").strip()
                if said:
                    emit("text", text=said)
                    return 0
                why = str((p.get("error") or {}).get("message") or "").strip() if isinstance(p.get("error"), dict) else ""
                if why:
                    emit("error", message=f"Codex stopped: {why}"[:600])
                    return 1
                emit("error", message="Codex finished on your PC without a reply. Open the session there to see why "
                                      "(a usage limit, or it was stopped).")
                return 1
        if not started and time.time() - t0 > PICKUP_S:
            # Never a long "working" that ends in nothing: it waits in Codex's queue, and says so.
            emit("text", text="Codex has it queued on your PC and runs it when that session is free. "
                              "Its reply shows here when you open the session again.")
            return 0
        await asyncio.sleep(0.3)
    emit("error", message="Codex is still working on your PC. Its reply shows here when you open the session again.")
    return 1
