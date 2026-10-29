"""Cursor's agent CLI as the assistant's brain.

Each turn runs `cursor-agent -p --output-format stream-json` on the user's own
install and Cursor plan. Cursor has no flag for a system prompt, so the
persona goes in front of the first message of a chat; the chat's Cursor
session carries it from there.

Cursor has no hook that asks someone before a tool runs. Its print mode turns
away its own shell and every MCP call unless they are allowed in the project's
`.cursor/cli.json`, so Hearth allows exactly one thing there: its own MCP
server, which asks Hearth before anything risky. Cursor's shell stays turned
away, and on Ask so do its direct file writes, so both go through Hearth's
tools and get asked about like everything else.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from . import claude_brain as _base
from . import env as _env

PROVIDER = "cursor-agent"
SERVER = "hearth_brain"
_LIST_CACHE = os.path.join(_base._HOME, "cursor_models.json")

# Cursor's own tools shown as the Hearth tools they amount to.
_VIEW = {"readToolCall": ("read_file", "path"), "shellToolCall": ("run_command", "command"),
         "editToolCall": ("edit_file", "path"), "writeToolCall": ("write_file", "path"),
         "lsToolCall": ("list_directory", "path"), "globToolCall": ("find_file", "globPattern"),
         "grepToolCall": ("grep_search", "pattern"), "webSearchToolCall": ("web_search", "searchTerm"),
         "webFetchToolCall": ("web_fetch", "url"), "deleteToolCall": ("delete_path", "path")}
# Cursor's name for an argument -> the one Hearth's card for that tool reads.
_SHOWN_AS = {"globPattern": "name", "searchTerm": "query"}
# Cursor looking up a tool's schema: bookkeeping, not work.
_QUIET = {"getMcpToolsToolCall", "updateTodosToolCall", "todoToolCall"}


def hearth_view(call: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """(tool name, args to show, the call's own body) for one Cursor tool call."""
    kind = next((k for k in call if k.endswith("ToolCall")), "")
    body = call.get(kind) or {}
    args = body.get("args") or {}
    if kind in _QUIET:
        return "", {}, body
    if kind == "mcpToolCall":
        return str(args.get("toolName") or args.get("name") or "tool"), args.get("args") or {}, body
    if kind in _VIEW:
        name, key = _VIEW[kind]
        shown = {_SHOWN_AS.get(key, key): args.get(key, "")}
        if kind == "editToolCall" and "streamContent" in args:
            name, shown = "write_file", {"path": args.get("path", ""), "content": args.get("streamContent", "")}
        return name, shown, body
    return (kind[:-len("ToolCall")] or "tool"), args, body


def _result_text(body: Dict[str, Any]) -> str:
    res = body.get("result") or {}
    if "success" in res:
        ok = res["success"] or {}
        for key in ("content", "output", "stdout", "diffString"):
            if ok.get(key):
                val = ok[key]
                if isinstance(val, list):                      # MCP content blocks
                    val = "\n".join(str((c.get("text") or {}).get("text") if isinstance(c.get("text"), dict)
                                        else c.get("text") or "") for c in val if isinstance(c, dict))
                return str(val)
        return json.dumps(ok)[:2000] if ok else ""
    for key, val in res.items():                               # rejected, writePermissionDenied, error
        why = (val or {}).get("reason") or (val or {}).get("error") or key
        if key == "rejected" and why == "User Rejected":
            # Cursor's wording for a tool of its own that isn't allowed when
            # it runs unattended. Nobody was asked.
            why = "Cursor doesn't run this tool of its own here. Inglen's tools do the same job."
        return f"Not done: {why}"
    return ""


# Cursor puts the reasoning level in the model id: <base>-<level>, then -fast
# for its Fast tier. These are the level words its ids use, and how each is
# named in its labels.
_LEVEL_WORDS = {"none": "None", "minimal": "Minimal", "low": "Low", "medium": "Medium", "high": "High",
                "xhigh": "Extra High", "extra-high": "Extra High", "max": "Max"}
FAST_ABOUT = "Faster replies, uses more of the plan"


def split_id(mid: str) -> Tuple[str, str, bool]:
    """(base, level, fast) for one listed id; level is "" when the id has none."""
    fast = mid.endswith("-fast")
    rest = mid[:-len("-fast")] if fast else mid
    for word in sorted(_LEVEL_WORDS, key=len, reverse=True):
        if rest.endswith("-" + word) and len(rest) > len(word) + 1:
            return rest[:-len(word) - 1], ("xhigh" if word == "extra-high" else word), fast
    return rest, "", fast


def group(listed: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Listed (id, label) pairs as one row per model, its levels as efforts.

    A row's `ids` maps each level ("" for the one without a level word) to its
    id, `fast_ids` the same for the Fast tier, and `level` is the one Cursor
    uses when none is picked: the id whose label doesn't name its level."""
    import re
    rows: Dict[str, Dict[str, Any]] = {}
    for mid, label in listed:
        if mid == "auto":
            rows[""] = {"id": "", "label": "Default", "about": "Cursor picks the model", "model": "auto",
                        "ctx": None, "efforts": [], "ids": {"": "auto"}, "fast_ids": {}, "level": ""}
            continue
        base, level, fast = split_id(mid)
        row = rows.setdefault(base, {"id": base, "label": "", "about": "", "model": "", "ctx": None,
                                     "efforts": [], "ids": {}, "fast_ids": {}, "level": None, "_labels": {}})
        (row["fast_ids"] if fast else row["ids"])[level] = mid
        if " 1M" in label:
            row["ctx"] = 1000000
        if not fast:
            row["_labels"][level] = label
            named = _LEVEL_WORDS.get(level, "")
            if row["level"] is None and (not level or not re.search(r"\b" + re.escape(named) + r"\b", label)):
                row["level"], row["label"] = level, label
    out = []
    for base, row in rows.items():
        labels = row.pop("_labels", {})
        if not row["ids"] and row["fast_ids"]:              # only a Fast id listed
            row["ids"] = dict(row["fast_ids"])
        if row["level"] is None:
            first = next(iter(row["ids"]), "")
            row["level"] = first
            named = _LEVEL_WORDS.get(first, "")
            label = labels.get(first, base)
            row["label"] = re.sub(r"\s+" + re.escape(named) + r"\b", "", label).strip() if named else label
        row["label"] = row["label"] or base
        row["model"] = row["ids"].get(row["level"]) or next(iter(row["ids"].values()), base)
        order = list(_LEVEL_WORDS)
        row["efforts"] = sorted((lv for lv in row["ids"] if lv), key=order.index) if len(row["ids"]) > 1 else []
        row["fast"] = FAST_ABOUT if row["fast_ids"] and base else ""
        out.append(row)
    return out


def models() -> List[Dict[str, Any]]:
    """The models Cursor offers, one row per model with its levels."""
    exe = _base.exe_for(PROVIDER)
    return group(_listed(exe)) if exe else []


def row_for(chosen: str, rows: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """The row a saved model belongs to: by its base, or by any of its ids."""
    rows = models() if rows is None else rows
    return next((r for r in rows if r["id"] == chosen), None) or next(
        (r for r in rows if chosen and (chosen in r["ids"].values() or chosen in r["fast_ids"].values())), None)


def model_id(chosen: str, effort: str, fast: bool, rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """The id to run: the chosen model at the chosen level, on its Fast tier
    when that is on and listed. A level it doesn't list falls back to its own."""
    row = row_for(chosen, rows)
    if not row:
        return chosen
    if not row["id"]:
        # Auto: Cursor's own "auto" model, named. Sending no --model ran whatever
        # the Cursor CLI had saved last, which a free plan can refuse.
        return row["model"]
    if effort and effort in row["ids"]:
        level = effort
    elif chosen in row["ids"].values() or chosen in row["fast_ids"].values():
        # A full id saved before levels were offered keeps its level.
        level = split_id(chosen)[1]
        level = level if level in row["ids"] else row["level"]
    else:
        level = row["level"]
    if fast and level in row["fast_ids"]:
        return row["fast_ids"][level]
    return row["ids"].get(level) or row["model"]


def _listed(exe: List[str]) -> List[Tuple[str, str]]:
    """(id, label) for each model `--list-models` prints, asked once per installed version."""
    stamp = exe[-1]
    try:
        with open(_LIST_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("stamp") == stamp and cached.get("listed"):
            return [tuple(x) for x in cached["listed"]]
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(exe + ["--list-models"], capture_output=True, timeout=60,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        return []
    out = parse_list(r.stdout.decode("utf-8", "replace"))
    if out:
        try:
            os.makedirs(_base._HOME, exist_ok=True)
            with open(_LIST_CACHE, "w", encoding="utf-8") as f:
                json.dump({"stamp": stamp, "listed": out}, f)
        except OSError:
            pass
    return out


def parse_list(text: str) -> List[Tuple[str, str]]:
    import re
    out: List[Tuple[str, str]] = []
    for line in re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).splitlines():
        mid, sep, label = line.strip().partition(" - ")
        if not sep or " " in mid or not mid:
            continue
        label = re.sub(r"\s*\((current|default|current, default)\)\s*$", "", label).strip().strip("​")
        out.append((mid, label))
    return out


def _merge_json(path: str, change) -> None:
    """Apply `change(data)` to a JSON file that may hold the user's own
    settings, keeping everything else in it."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    change(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_project_config(cwd: str) -> None:
    """Hearth's MCP server and the one permission it needs, in the folder the
    turn runs in. Anything else already in those files stays."""
    launch = _base._mcp_launch(gate=True)
    try:
        from .tools import approval_mode
        ask = approval_mode() == "ask"
    except Exception:
        ask = True

    def mcp(data):
        data.setdefault("mcpServers", {})[SERVER] = launch

    def perms(data):
        p = data.setdefault("permissions", {})
        allow = [t for t in p.get("allow") or [] if t != f"Mcp({SERVER}:*)"] + [f"Mcp({SERVER}:*)"]
        deny = [t for t in p.get("deny") or [] if t != "Write(**)"] + (["Write(**)"] if ask else [])
        p["allow"], p["deny"] = allow, deny
        # Cursor reads this file strictly: permissions and nothing else, or it
        # refuses to start. Its own web search is switched on in Cursor's own
        # settings, not here; Hearth's search goes through the server above.
        data.pop("autoAcceptWebSearch", None)

    _merge_json(os.path.join(cwd, ".cursor", "mcp.json"), mcp)
    _merge_json(os.path.join(cwd, ".cursor", "cli.json"), perms)


def build_args(exe: List[str], *, session: Optional[str], model: str = "") -> List[str]:
    args = list(exe) + ["-p", "--output-format", "stream-json", "--stream-partial-output",
                        "--approve-mcps", "--trust"]
    if session:
        args += ["--resume", session]
    if model:
        args += ["--model", model]
    return args


async def run_turn(prompt: str, *, emit, history: Optional[List[Dict[str, Any]]] = None,
                   permission_check=None, should_cancel=None, voice: bool = False) -> int:
    exe = _base.exe_for(PROVIDER)
    if not exe:
        emit("error", message=("Cursor's agent isn't installed on this PC. Install it from cursor.com/cli "
                               "and sign in with `agent login`, or pick another brain."))
        return 2
    _base.ensure_bridge()
    history = history or []
    key = "cursor:" + _base._chat_key(history, prompt)
    session = _base.session_for(key, history)
    text_in = prompt
    if not session:
        persona = _base.persona_text(
            voice, host="Cursor", tools=f"the {SERVER} MCP tools",
            own_tools=("Your own shell and web fetch are turned off here: run commands with Inglen's "
                       "run_command, which asks the user, and open pages with Inglen's web_fetch."))
        earlier = _base._transcript(history) if history else ""
        text_in = (persona + "\n\n---\n\n"
                   + (f"Earlier in this conversation:\n{earlier}\n\nNow the user says:\n" if earlier else "")
                   + prompt)
    cwd = _env.get("WORKSPACE") or __import__("hearth").default_workspace()
    os.makedirs(cwd, exist_ok=True)
    try:
        write_project_config(cwd)
    except OSError as e:
        emit("error", message=f"Couldn't set Cursor up in {cwd}: {e}")
        return 1
    model = model_id(_base.chosen_model(PROVIDER), _base.chosen_effort(), _base.fast_on(PROVIDER))
    args = build_args(exe, session=session, model=model)

    t0 = time.time()
    _base._checks[_base._TOKEN] = permission_check
    started: Dict[str, Tuple[str, float]] = {}
    text_parts: List[str] = []
    sid, result, used = None, None, ""
    gap = False
    stretch = ""                                  # the text streamed since the last tool call
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        proc.stdin.write(text_in.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
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
            if t == "system" and ev.get("subtype") == "init":
                sid = ev.get("session_id") or sid
                used = str(ev.get("model") or "")
            elif t == "thinking" and ev.get("subtype") == "delta" and ev.get("text"):
                emit("thinking_chunk", content=ev["text"])
            elif t == "assistant":
                # Text comes in pieces, then the whole stretch is repeated as
                # one message: at the end without a timestamp, mid-turn with
                # one. Either way it is what was just streamed.
                if "timestamp_ms" not in ev:
                    continue
                piece = "".join(b.get("text", "") for b in (ev.get("message") or {}).get("content") or []
                                if isinstance(b, dict) and b.get("type") == "text")
                if stretch and piece == stretch:
                    stretch, gap = "", True               # what follows is a new paragraph
                    continue
                stretch += piece
                if piece:
                    chunk = ("\n\n" + piece) if (gap and text_parts) else piece
                    gap = False
                    text_parts.append(chunk)
                    emit("assistant_chunk", content=chunk)
            elif t == "tool_call":
                name, shown, body = hearth_view(ev.get("tool_call") or {})
                if not name:
                    continue
                cid = str(ev.get("call_id") or "")
                if ev.get("subtype") == "started":
                    started[cid] = (name, time.time())
                    gap, stretch = True, ""
                    emit("tool_call", name=name, args=shown, id=cid)
                elif ev.get("subtype") == "completed":
                    hit = started.pop(cid, None)
                    if hit is None:
                        emit("tool_call", name=name, args=shown, id=cid)
                    emit("tool_result", name=name, content=_result_text(body)[:20000],
                         ms=int((time.time() - hit[1]) * 1000) if hit else 0, id=cid)
            elif t == "result":
                result = ev
                sid = ev.get("session_id") or sid
        await proc.wait()
        err = (await err_task).decode("utf-8", "replace").strip()
    except Exception as e:
        emit("error", message=f"Cursor didn't run: {type(e).__name__}: {e}")
        return 1
    finally:
        _base._checks.pop(_base._TOKEN, None)

    if sid:
        _base._save_session(key, sid, last=_base.asked(prompt))
    reply = "".join(text_parts).strip() or str((result or {}).get("result") or "").strip()
    if result and result.get("is_error"):
        emit("error", message=("Cursor stopped: " + str(result.get("result") or result.get("subtype") or "error"))[:600])
    elif not reply and not (should_cancel and should_cancel()):
        emit("error", message=("Cursor gave no answer. " + (err[-400:] if err else
                               "Check it's signed in: run `agent login` in a terminal.")))
    if reply:
        emit("assistant", content=reply)
    usage = (result or {}).get("usage") or {}
    emit("done", duration_ms=int((time.time() - t0) * 1000), iterations=1,
         stats={"brain": "Cursor", "model": model or used, "tokens_in": usage.get("inputTokens"),
                "tokens_out": usage.get("outputTokens")},
         reason="finished")
    return 0
