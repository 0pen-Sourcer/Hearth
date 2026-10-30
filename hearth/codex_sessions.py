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
import sys
from typing import Any, Callable, Dict, List, Optional

Emit = Callable[..., None]

_EXEC_DEFAULT = ("never", "readOnly")
_NEXT_MODEL: Dict[str, str] = {}             # thread -> the model /model picked, for its next turn
COMMANDS = ("/compact", "/review", "/model")


def _sandbox_kind(policy: Any) -> str:
    return str(policy.get("type") or "") if isinstance(policy, dict) else str(policy or "")


def lifted(resumed: Dict[str, Any], cwd: str) -> Dict[str, Any]:
    """Overrides for turn/start: none, unless the thread came back read-only with approvals off."""
    if (str(resumed.get("approvalPolicy") or ""), _sandbox_kind(resumed.get("sandbox"))) != _EXEC_DEFAULT:
        return {}
    return {"approvalPolicy": "on-request",
            "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": False}}


async def run(exe: List[str], sid: str, cwd: str, text: str, emit: Emit, ask: Callable[[str], bool], *,
              drain: Optional[Callable[[], List[str]]] = None) -> int:
    """Resume thread `sid` (or start one in `cwd` when sid is empty) and run `text` as a turn."""
    from .agent_sessions import _step
    from .channel_core import _what, out_of
    from .codex_brain import _Rpc, _change_call, _shown_command
    from .tools import mode_allows

    proc = await asyncio.create_subprocess_exec(
        *exe, "app-server", cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
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
                                              **({"model": model} if model else {})}, 60)
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
                    run = {"command": _shown_command(prm)}
                    ok = mode_allows("run_command", run) or await may(f"Codex asks: may it {_what('run_command', run)}?")
                elif meth == "item/fileChange/requestApproval":
                    changes = (items.get(str(prm.get("itemId"))) or {}).get("changes") or []
                    ok = bool(changes)
                    for c in changes:
                        if not (mode_allows(*_change_call(c)) or await may(f"Codex asks: may it {_what(*_change_call(c))}?")):
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
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), 5)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
    reply = "\n\n".join(t for t in replies.values() if t).strip()
    if failure:
        emit("error", message=f"Codex stopped: {failure}"[:600])
    if reply:
        emit("text", text=reply)
    return 0 if reply and not failure else 1
