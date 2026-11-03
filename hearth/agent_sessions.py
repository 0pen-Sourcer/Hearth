"""The sessions the coding agents on this PC keep, readable and carried on from the phone.

Each agent program keeps its own sessions on disk (or answers for them):

    Claude Code   ~/.claude/projects/<project>/<session>.jsonl
    Codex         ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    OpenCode      `opencode session list --format json`, `opencode export <id>`
    Gemini CLI    ~/.gemini/tmp/<project hash>/chats/session-*.json
    Pi            ~/.pi/agent/sessions/<project>/<time>_<id>.jsonl

listing() lists them newest first with their project folder, transcript() reads
one as the phone shows a chat, and say() sends a message into one: the agent
program itself resumes that session in its own folder (Claude Code and Codex
headless, the others over the Agent Client Protocol), and what it does comes
back as it happens.

Nothing here writes into an agent's files; only the agent itself does, when
it carries on.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import contextvars
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Hearth's own ids for the agent programs (claude_brain.BRAINS), each with how
# its sessions are read and resumed. Only programs with a reader are listed.
AGENTS = ("claude-code", "codex", "opencode", "gemini-cli", "pi")
PREFIX = "as_"                       # a phone chat id for a session: as_<agent>_<session id>
PER_AGENT = 60                       # the newest sessions read per agent
HEAD_BYTES = 256 * 1024              # how much of a session file is read for its title
TAIL_BYTES = 4 * 1024 * 1024         # how much of its end is read for the transcript (a session with
                                     # pictures in it runs to hundreds of MB)
RECENT_S = 90                        # a session written this recently may be in use right now
_CACHE: Dict[Tuple[str, float, int], Dict[str, Any]] = {}
_OC: Dict[str, Any] = {}
# Sessions Inglen carried on itself, with their file's time as its run left it: a session written in
# the last minute and a half and not since is Inglen's own write, not another program holding it.
_RAN_HERE: Dict[Tuple[str, str], float] = {}


def _home() -> str:
    return os.path.expanduser("~")


def _exe(agent: str) -> Optional[List[str]]:
    """How to start an agent program, found the way Hearth finds its agent brains."""
    from .claude_brain import exe_for
    return exe_for(agent)


def label(agent: str) -> str:
    try:
        from .claude_brain import BRAINS
        return BRAINS.get(agent, agent)
    except Exception:
        return agent


def chat_id(agent: str, sid: str) -> str:
    return f"{PREFIX}{agent}_{re.sub(r'[^A-Za-z0-9_-]', '_', sid)}"[:80]


def parse_chat(cid: str) -> Optional[Tuple[str, str]]:
    """(agent, session id) of a phone chat id for a session, or None."""
    if not cid.startswith(PREFIX):
        return None
    agent, _, sid = cid[len(PREFIX):].partition("_")
    # An id starts with a letter or digit, so it can never be read as a flag by the agent program.
    return (agent, sid) if agent in AGENTS and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", sid) else None


# ---------------------------------------------------------------- reading lines

# How far back a transcript reads: the phone's "Show earlier" asks for more.
_DEPTH: "contextvars.ContextVar[int]" = contextvars.ContextVar("session_depth", default=1)
# Set when a read stopped before the start of the file, so there is more to show.
_CUT: "contextvars.ContextVar[Optional[List[bool]]]" = contextvars.ContextVar("session_cut", default=None)
MAX_TAIL = 96 * 1024 * 1024          # never more than this of one file at a time
BLOB_LINE = 1024 * 1024              # a line this long is a picture or a file's bytes: not parsed
ENOUGH_LINES = 400                   # lines that make a screenful of a transcript, at least


def _jsonl(path: str, limit: int = 0):
    """The objects in a JSON-lines file: the first `limit` bytes when given;
    otherwise its end, read further back until there are ENOUGH_LINES of
    conversation (or all of it). Lines holding a picture's or a file's bytes
    are skipped unread: Codex keeps each picture it views in the file, and
    a few of them filled a fixed 4 MB window, leaving one message to show."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if limit:
                raw, cut = f.read(limit), False
            else:
                want = min(MAX_TAIL, TAIL_BYTES * max(1, _DEPTH.get()))
                while True:
                    start = max(0, size - want)
                    f.seek(start)
                    raw = f.read()
                    if start:
                        raw = raw[raw.find(b"\n") + 1:]   # the first line is cut short
                    small = sum(1 for ln in raw.split(b"\n") if 0 < len(ln) < BLOB_LINE)
                    if not start or small >= ENOUGH_LINES * max(1, _DEPTH.get()) or want >= MAX_TAIL:
                        break
                    want = min(MAX_TAIL, want * 2)
                cut = bool(start)
    except OSError:
        return
    if not limit:
        mark = _CUT.get()
        if mark is not None and cut:
            mark.append(True)
    lines = raw.split(b"\n")
    if limit and len(raw) == limit:
        lines = lines[:-1]                      # the last line may be cut short
    for line in lines:
        line = line.strip()
        if not line or len(line) >= BLOB_LINE:
            continue
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _ms(v: Any) -> int:
    """A time as milliseconds: ISO text, seconds or milliseconds."""
    if isinstance(v, (int, float)):
        return int(v if v > 1e11 else v * 1000)
    if isinstance(v, str) and v:
        from datetime import datetime
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def _text_of(content: Any) -> str:
    """The words in a message's content, whatever shape the agent keeps it in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for c in content:
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text") and c.get("text"):
                out.append(str(c["text"]))
        return "\n".join(out)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


# What an agent puts in the person's turn that the person didn't type: a turn
# that is only this is left out, and a block of it before their words is taken off.
_NOT_TYPED = re.compile(r"^\s*(<(command-|local-command|environment_context|user_instructions|system-reminder)"
                        r"|Caveat: The messages below|# AGENTS\.md)", re.S)
# Blocks an agent puts before the person's words: <ide_selection>...</ide_selection>,
# <recommended_plugins>, <external_codex_apps_open_page> and the like, by their shape.
_BLOCK = re.compile(r"^\s*<([a-z][a-z0-9_:-]*)\b[^>]*>.*?</\1>\s*", re.S)
_EMPTY_TAG = re.compile(r"^\s*<[a-z][a-z0-9_:-]*\b[^>]*/>\s*", re.S)
# Codex puts what its app adds (IDE context, "# Files pasted by the user:", ...) before
# "## My request for Codex:" and shows only what follows the last one, as this does.
_CODEX_ASK = "## My request for Codex:"


def _clean(text: str) -> str:
    """The person's own words in a turn, without what their agent added before them."""
    t = text or ""
    if _NOT_TYPED.match(t):
        return ""
    if _CODEX_ASK in t:
        t = t.rsplit(_CODEX_ASK, 1)[1]
    while True:
        m = _BLOCK.match(t) or _EMPTY_TAG.match(t)
        if not m:
            break
        t = t[m.end():]
    return "" if _NOT_TYPED.match(t) else t.strip()


def _typed(text: str) -> bool:
    return bool(_clean(text))


def _title(text: str) -> str:
    t = " ".join(_clean(text).split())
    return t[:77] + "..." if len(t) > 80 else t


# Tool names each agent uses, as the Hearth tool they amount to, so a step reads
# the way the window says it ("Ran npm test", "Read app.py").
_TOOL = {"bash": "run_command", "shell": "run_command", "run_shell_command": "run_command", "exec_command": "run_command",
         "local_shell": "run_command", "read": "read_file", "read_file": "read_file", "write": "write_file",
         "write_file": "write_file", "edit": "edit_file", "replace": "edit_file", "apply_patch": "edit_file",
         "glob": "find_file", "grep": "grep_search", "search_file_content": "grep_search",
         "webfetch": "web_fetch", "web_fetch": "web_fetch", "websearch": "web_search", "google_web_search": "web_search"}


# Tools only the agents have, said plainly. None: bookkeeping, not a step.
_AGENT_WORDS = {"update_plan": "Updated its plan", "todowrite": "Updated its to-do list",
                "request_user_input": "Asked you a question", "request_user_input_async": "Asked you a question",
                "askuserquestion": "Asked you a question",
                "view_image": "Looked at an image", "spawn_agent": "Started a helper", "task": "Started a helper",
                "tool_search": "Looked for a tool", "list_mcp_resources": "Looked at its tools",
                "wait": None, "wait_agent": None, "write_stdin": None, "close_agent": None}
# Codex's code mode runs a small script ("exec") that calls its tools: tools.exec_command({cmd: "..."}).
_CODE_CALL = re.compile(r"tools\.([A-Za-z_][\w]*)\s*\(\s*\{(.*?)\}\s*\)", re.S)
_CODE_STR = r"""\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`]*)`)"""


def _result_text(content: Any) -> str:
    """A tool result's text, in whichever shape the agent wrote it."""
    if isinstance(content, str):
        try:                                 # Codex wraps some outputs as {"output": ..., "metadata": ...}
            j = json.loads(content)
            if isinstance(j, dict) and isinstance(j.get("output"), str):
                return j["output"]
        except ValueError:
            pass
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") if isinstance(c, dict) else str(c) for c in content)
    if isinstance(content, dict):
        return str(content.get("output") or content.get("text") or "")
    return ""


def _give_out(by_id: Dict[str, List[Dict[str, Any]]], cid: Any, content: Any) -> None:
    """The output of a call, onto the last step it made (for the phone's step detail)."""
    sts = by_id.get(str(cid or ""))
    if sts:
        from .channel_core import out_of
        sts[-1]["out"] = out_of(_result_text(content))


def _code_steps(src: str) -> List[Dict[str, Any]]:
    """The steps inside one code-mode script, in order; "Ran a script" when none can be read."""
    out = []
    for name, body in _CODE_CALL.findall(src or ""):
        args = {}
        for key in ("cmd", "command", "path", "file_path", "query", "url", "pattern"):
            m = re.search(r"\b" + key + _CODE_STR, body)
            if m:
                args[key] = next(g for g in m.groups() if g is not None)
        st = _step(name, args)
        if st:
            out.append(st)
    return out or [{"w": "Ran a script", "tool": "run_command"}]


def _said(desc: Any) -> str:
    """The agent's own line for a step (Claude Code's `description`, OpenCode's `title`), when it gave one."""
    d = " ".join(str(desc or "").split())
    return (d[0].upper() + d[1:])[:120] if d else ""


QUESTION_TOOLS = ("request_user_input", "request_user_input_async", "askuserquestion")


def _question_of(args: Any) -> str:
    """The words of a question an agent put to the person (Codex's request_user_input,
    Claude Code's AskUserQuestion), for the phone's notification."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return ""
    if not isinstance(args, dict):
        return ""
    qs = args.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict):
        return str(qs[0].get("question") or qs[0].get("header") or "")[:300]
    return str(args.get("question") or args.get("prompt") or "")[:300]


def _steps_of(name: str, args: Any, desc: Any = "") -> List[Dict[str, Any]]:
    """The steps one tool call amounts to. The agent's own words for it win over
    Inglen's template ("Ran ...", "Read ..."), which is only for calls it didn't describe."""
    if name == "exec" and isinstance(args, str):
        return _code_steps(args)
    st = _step(name, args)
    if st and not _said(desc) and isinstance(args, dict):
        desc = args.get("description") or args.get("title") or ""
    if st and _said(desc):
        st = dict(st, w=_said(desc))
    return [st] if st else []


_SHELL = re.compile(r"(^|[\\/])(pwsh|powershell|bash|sh|zsh|cmd)(\.exe)?$", re.I)


def _unwrap(argv: List[Any]) -> str:
    """A command as the agent meant it: ["...\\pwsh.exe", "-Command", "git status"] is "git status"."""
    words = [str(x) for x in argv]
    if len(words) >= 3 and _SHELL.search(words[0]) and words[1].lower() in ("-command", "-c", "-lc", "/c", "-noprofile"):
        rest = words[2:]
        if words[1].lower() == "-noprofile" and rest and rest[0].lower() == "-command":
            rest = rest[1:]
        return " ".join(rest)
    return " ".join(words)


def _step(name: str, args: Any) -> Optional[Dict[str, Any]]:
    from .term_render import tool_words
    low = name.lower()
    if low in _AGENT_WORDS:
        if not _AGENT_WORDS[low]:
            return None
        st = {"w": _AGENT_WORDS[low], "tool": low}
        q = _question_of(args)
        if q:
            st["cmd"] = q
        pic = _picture_of(args)
        if pic:
            st["img"] = pic                  # the picture it looked at, for the phone to show
        return st
    args = args if isinstance(args, dict) else {}
    for key in ("command", "cmd"):
        if isinstance(args.get(key), list):
            args = dict(args, **{key: _unwrap(args[key])})
    tool = _TOOL.get(name.lower(), name)
    if name.lower() in _TOOL:
        args = {"command": args.get("command") or args.get("cmd") or "",
                "path": args.get("file_path") or args.get("filePath") or args.get("path") or args.get("absolute_path") or "",
                "query": args.get("query") or "", "url": args.get("url") or "",
                "pattern": args.get("pattern") or "", "name": args.get("pattern") or ""}
        args = {k: v for k, v in args.items() if v}
    from .channel_core import detail_of
    st = {"w": tool_words(tool, args), "tool": tool}
    cmd = detail_of(args)
    if cmd:
        st["cmd"] = cmd
    if tool == "read_file" and _picture_of(args):
        st["img"] = _picture_of(args)        # Claude Code reads a picture with Read
    return st


_PICTURE = re.compile(r"\.(png|jpe?g|gif|webp|bmp)$", re.I)


def _picture_of(args: Any) -> str:
    """The path of the picture a step looked at, when it was one."""
    if not isinstance(args, dict):
        return ""
    p = str(args.get("path") or args.get("file_path") or args.get("filePath") or args.get("image_path") or "")
    return p if _PICTURE.search(p) else ""


def _msg(role: str, text: str, at: int, steps: Optional[List[Dict[str, Any]]] = None,
         error: bool = False) -> Dict[str, Any]:
    files: List[str] = []
    if role == "user":
        text = _clean(text) or text
        # Files the phone sent with it: kept on the message, as the window keeps attachments, not as text.
        files = [g.group(2) for g in _ATTACHED.finditer(text)]
        if files:
            text = _ATTACHED.sub("", text).strip()
    m = {"role": role, "text": text[-8000:], "at": at, "steps": steps or []}
    if files:
        m["files"] = files
    if error:
        m["error"] = True
    return m


def _last_model(path: str) -> str:
    """The model a session last ran on, from the end of its file (each agent writes it per turn)."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 128 * 1024))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    found = re.findall(r'"model"\s*:\s*"([^"<]{1,100})"', tail)
    return found[-1] if found else ""


def _parts(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if m["steps"]:
        out.append({"steps": list(m["steps"])})
    if m["text"]:
        out.append({"text": m["text"]})
    return out


def _fold(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One reply per turn, as a chat shows it: the agent's messages and steps
    between two of the person's join into one, and `parts` keeps them in the
    order they came (steps, words, more steps), as the window shows a turn.
    `text` and `steps` stay whole for copying and for older phones. An error
    the agent wrote stays a message of its own."""
    out: List[Dict[str, Any]] = []
    for m in msgs:
        prev = out[-1] if out else None
        if (m["role"] == "assistant" and prev and prev["role"] == "assistant"
                and not m.get("error") and not prev.get("error")):
            prev["steps"] = prev["steps"] + m["steps"]
            if m["text"]:
                prev["text"] = (prev["text"] + "\n\n" + m["text"]).strip()
            for part in _parts(m):
                last = prev["parts"][-1] if prev["parts"] else None
                if last and "steps" in part and "steps" in last:
                    last["steps"] = last["steps"] + part["steps"]
                else:
                    prev["parts"].append(part)
            prev["at"] = m["at"] or prev["at"]
        else:
            n = dict(m)
            if n["role"] == "assistant":
                n["parts"] = _parts(n)
            out.append(n)
    return [m for m in out if m["text"] or m["steps"] or m.get("files")]


# ---------------------------------------------------------------- Claude Code

def _claude_files() -> List[str]:
    return glob.glob(os.path.join(_home(), ".claude", "projects", "*", "*.jsonl"))


def _claude_read(path: str, head: bool) -> Dict[str, Any]:
    sid = os.path.basename(path)[:-6]
    cwd, title, summary, msgs, named = "", "", "", [], ""
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for o in _jsonl(path, HEAD_BYTES if head else 0):
        cwd = cwd or str(o.get("cwd") or "")
        if o.get("type") == "summary" and o.get("summary"):
            summary = summary or str(o["summary"])
            continue
        if o.get("type") == "custom-title" and o.get("customTitle"):
            named = str(o["customTitle"])          # the name they gave it (/rename): the newest wins
            continue
        m = o.get("message") or {}
        content, at = m.get("content"), _ms(o.get("timestamp"))
        sent = from_phone(o)
        if sent:
            title = title or _title(sent)
            msgs.append(_msg("user", sent, at))
            continue
        if o.get("isSidechain") or o.get("isMeta") or o.get("type") not in ("user", "assistant"):
            continue
        if o["type"] == "user":
            results = [b for b in (content if isinstance(content, list) else [])
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            text = _text_of(content)
            if results:
                # A tool's turn: what its harness wrote beside the result (an agent launched in the
                # background, where its output goes) is part of that step's detail, never the person's words.
                for n, b in enumerate(results):
                    out = _result_text(b.get("content"))
                    if text.strip() and n == len(results) - 1:
                        out = (out + "\n\n" + text).strip()
                    _give_out(by_id, b.get("tool_use_id"), out)
                continue
            if not _typed(text):
                continue                        # the program's own notes
            title = title or _title(text)
            if head and title and cwd:
                break
            msgs.append(_msg("user", text, at))
        else:
            from .claude_brain import hearth_view
            # Claude Code writes a failed call (a usage limit, an API error) as a reply of its own.
            failed = bool(o.get("isApiErrorMessage") or o.get("error")) or m.get("model") == "<synthetic>"
            if not isinstance(content, list):
                msgs.append(_msg("assistant", _text_of(content), at, [], error=failed))
                continue
            for b in content:                   # each block in its order: words, then the steps after them
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name, args = hearth_view(str(b.get("name") or ""), b.get("input") or {})
                    desc = (b.get("input") or {}).get("description") if isinstance(b.get("input"), dict) else ""
                    sts = _steps_of(name, args, desc)
                    by_id[str(b.get("id") or "")] = sts
                    msgs.append(_msg("assistant", "", at, sts, error=failed))
                elif b.get("type") == "text" and b.get("text"):
                    msgs.append(_msg("assistant", str(b["text"]), at, [], error=failed))
    return {"id": sid, "cwd": cwd, "title": named or summary or title, "messages": _fold(msgs)}


# ---------------------------------------------------------------- Codex

# Codex's own runs that aren't a conversation of the person's: the auto-review
# (guardian) checks, its sub-agents, compaction and memory passes. Codex marks
# them in the first line (source, thread_source, parent_thread_id); older files
# without the marks are known by the reviewer's own prompt.
_REVIEW = re.compile(r">>\s*APPROVAL REQUEST START|>>\s*TRANSCRIPT DELTA START|The Codex agent has requested the following"
                     r"|Continue the same review conversation")


def _codex_spawn(meta: Dict[str, Any]) -> Dict[str, Any]:
    """A Codex subagent's thread: {parent, nickname, role}, or {} for any other session
    (codex-rs protocol SessionSource::SubAgent(ThreadSpawn {parent_thread_id, ...}))."""
    src = meta.get("source")
    spawn = {}
    if isinstance(src, dict):
        sub = src.get("subagent") or src.get("SubAgent") or {}
        spawn = (sub.get("thread_spawn") or sub.get("ThreadSpawn") or {}) if isinstance(sub, dict) else {}
    # Only an explicit thread spawn is a helper. Internal runs also carry a parent.
    if not spawn:
        return {}
    parent = spawn.get("parent_thread_id") or meta.get("parent_thread_id")
    if not parent:
        return {}
    return {"parent": str(parent), "nickname": str(spawn.get("agent_nickname") or meta.get("agent_nickname") or ""),
            "role": str(spawn.get("agent_role") or spawn.get("agent_type") or meta.get("agent_role") or "")}


def _codex_not_theirs(meta: Dict[str, Any]) -> bool:
    src = meta.get("source")
    if isinstance(src, dict) and ({"subagent", "internal"} & {k.lower() for k in src}):
        return True
    if isinstance(src, str) and src.lower() in ("internal", "subagent"):
        return True
    ts = str(meta.get("thread_source") or "").lower()
    if ts and ts != "user":
        return True
    return bool(meta.get("parent_thread_id") or meta.get("agent_role") or meta.get("agent_nickname"))


def _codex_files() -> List[str]:
    return glob.glob(os.path.join(_home(), ".codex", "sessions", "**", "*.jsonl"), recursive=True)


def _codex_read(path: str, head: bool) -> Dict[str, Any]:
    sid, cwd, title = "", "", ""
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    said: List[Dict[str, Any]] = []             # what the person and Codex said, from its event lines
    items: List[Dict[str, Any]] = []            # the same from the model's own items (older files)
    for o in _jsonl(path, HEAD_BYTES if head else 0):
        kind, p = o.get("type"), o.get("payload") if isinstance(o.get("payload"), dict) else o
        at = _ms(o.get("timestamp"))
        if kind == "session_meta" or (not sid and o.get("id") and "instructions" in o):
            sid, cwd = str(p.get("id") or sid), str(p.get("cwd") or cwd)
            spawn = _codex_spawn(p)
            if spawn and head:
                # A helper Codex started: not a session of the person's own, but its parent shows it.
                return {"id": sid, "cwd": cwd, "title": "", "messages": [], "hidden": True, "spawn": spawn}
            if not spawn and _codex_not_theirs(p):
                return {"id": sid, "cwd": cwd, "title": "", "messages": [], "hidden": True}
            continue
        if kind == "turn_context" and p.get("cwd"):
            cwd = cwd or str(p["cwd"])
        if kind == "event_msg" and p.get("type") == "user_message" and _typed(str(p.get("message") or "")):
            title = title or _title(str(p["message"]))
            said.append(_msg("user", str(p["message"]), at))
        elif kind == "event_msg" and p.get("type") == "agent_message" and p.get("message"):
            said.append(_msg("assistant", str(p["message"]), at))
        elif p.get("type") in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            _give_out(by_id, p.get("call_id"), p.get("output"))
        elif kind == "event_msg" and p.get("type") in ("error", "stream_error") and p.get("message"):
            said.append(_msg("assistant", str(p["message"]), at, error=True))
        elif (kind == "event_msg" and p.get("type") == "task_complete" and isinstance(p.get("error"), dict)
              and p["error"].get("message")):
            # A turn that ended on its own error (a usage limit) without a word: that error is what it said.
            for lst in (said, items):
                lst.append(_msg("assistant", str(p["error"]["message"]), at, error=True))
        elif p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            text = _text_of(p.get("content"))
            if p["role"] == "user" and not _typed(text):
                continue
            items.append(_msg(p["role"], text, at))
        elif p.get("type") in ("function_call", "local_shell_call", "custom_tool_call"):
            try:
                if p.get("type") == "custom_tool_call":
                    args = p.get("input") or ""          # code mode: the script itself
                else:
                    args = json.loads(p.get("arguments") or "{}") if isinstance(p.get("arguments"), str) else (p.get("action") or {})
            except ValueError:
                args = {}
            sts = _steps_of(str(p.get("name") or "shell"), args)
            by_id[str(p.get("call_id") or "")] = sts
            for lst in (said, items) if sts else ():
                # Steps after the agent's words come after them: a new piece, joined in order by _fold.
                if lst and lst[-1]["role"] == "assistant" and not lst[-1].get("error") and not lst[-1]["text"]:
                    lst[-1]["steps"] += sts
                else:
                    lst.append(_msg("assistant", "", at, list(sts)))
        if head and title and cwd and sid:
            break
    if not sid:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", os.path.basename(path))
        sid = m.group(1) if m else ""
    msgs = said if any(m["role"] == "user" for m in said) else items
    first = next((m["text"] for m in msgs if m["role"] == "user"), "")
    if _REVIEW.search(first[:4000]):
        return {"id": sid, "cwd": cwd, "title": "", "messages": [], "hidden": True}
    if not title:
        title = next((_title(m["text"]) for m in msgs if m["role"] == "user"), "")
    title = _codex_names().get(sid) or title          # the name Codex shows for it, when it has one
    return {"id": sid, "cwd": cwd, "title": title, "messages": _fold(msgs)}


_NAMES: Dict[str, Any] = {"at": None, "names": {}}


def _codex_names() -> Dict[str, str]:
    """Codex's own names for its threads: ~/.codex/session_index.jsonl, appended on
    each rename, the newest entry for an id wins (codex-rs rollout/session_index.rs)."""
    path = os.path.join(_home(), ".codex", "session_index.jsonl")
    try:
        st = os.stat(path)
    except OSError:
        return {}
    if _NAMES["at"] != (st.st_mtime, st.st_size):
        names: Dict[str, str] = {}
        for o in _jsonl_all(path):
            n = str(o.get("thread_name") or "").strip()
            if o.get("id") and n:
                names[str(o["id"])] = n
        _NAMES.update(at=(st.st_mtime, st.st_size), names=names)
    return _NAMES["names"]


def _jsonl_all(path: str):
    """Every object in a small JSON-lines file, in order."""
    try:
        with open(path, "rb") as f:
            raw = f.read(32 * 1024 * 1024)
    except OSError:
        return
    for line in raw.split(b"\n"):
        try:
            obj = json.loads(line.decode("utf-8", "replace")) if line.strip() else None
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


# ---------------------------------------------------------------- Gemini CLI

def _gemini_files() -> List[str]:
    return glob.glob(os.path.join(_home(), ".gemini", "tmp", "*", "chats", "*.json"))


def _gemini_root(path: str) -> str:
    """Gemini keeps a hash of the project folder; the folder itself is found in
    a .project_root file when there is one, else among the folders other agents'
    sessions name."""
    d = os.path.dirname(os.path.dirname(path))
    try:
        with open(os.path.join(d, ".project_root"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        pass
    want = os.path.basename(d)
    for cwd in _known_folders():
        if hashlib.sha256(cwd.encode("utf-8")).hexdigest() == want:
            return cwd
    return ""


def _gemini_read(path: str, head: bool) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    msgs = []
    for m in d.get("messages") or []:
        if not isinstance(m, dict):
            continue
        text, at = _text_of(m.get("content")), _ms(m.get("timestamp"))
        if m.get("type") == "user" and _typed(text):
            msgs.append(_msg("user", text, at))
        elif m.get("type") in ("gemini", "model", "assistant"):
            steps = [s for c in m.get("toolCalls") or [] if isinstance(c, dict)
                     for s in _steps_of(str(c.get("name") or ""), c.get("args") or {})]
            msgs.append(_msg("assistant", text, at, steps))
    title = next((_title(m["text"]) for m in msgs if m["role"] == "user"), "")
    return {"id": str(d.get("sessionId") or ""), "cwd": str(d.get("cwd") or "") or _gemini_root(path), "title": title,
            "messages": _fold(msgs),
            "at": _ms(d.get("lastUpdated"))}


# ---------------------------------------------------------------- Pi

def _pi_files() -> List[str]:
    return glob.glob(os.path.join(_home(), ".pi", "agent", "sessions", "*", "*.jsonl"))


def _pi_read(path: str, head: bool) -> Dict[str, Any]:
    sid, cwd, title, msgs = "", "", "", []
    for o in _jsonl(path, HEAD_BYTES if head else 0):
        if o.get("type") == "session":
            sid, cwd = str(o.get("id") or ""), str(o.get("cwd") or "")
            continue
        if o.get("type") != "message":
            continue
        m = o.get("message") or {}
        at = _ms(o.get("timestamp") or m.get("timestamp"))
        role, content = m.get("role"), m.get("content")
        if role == "user":
            text = _text_of(content)
            if not _typed(text):
                continue
            title = title or _title(text)
            if head and title:
                break
            msgs.append(_msg("user", text, at))
        elif role == "assistant":
            steps = [s for c in content or [] if isinstance(c, dict) and c.get("type") == "toolCall"
                     for s in _steps_of(str(c.get("name") or ""), c.get("arguments") or {})]
            msgs.append(_msg("assistant", _text_of(content), at, steps))
    if not sid:
        m = re.search(r"_([0-9a-f-]{36})\.jsonl$", path)
        sid = m.group(1) if m else ""
    return {"id": sid, "cwd": cwd, "title": title, "messages": _fold(msgs)}


# ---------------------------------------------------------------- OpenCode

def _opencode(*args: str, timeout: float = 20) -> Any:
    """What the opencode program answers, as JSON; None when it isn't here or says nothing."""
    exe = _exe("opencode")
    if not exe:
        return None
    try:
        out = subprocess.run(exe + list(args), capture_output=True, timeout=timeout, cwd=_home(),
                             creationflags=0x08000000 if sys.platform == "win32" else 0)
        text = out.stdout.decode("utf-8", "replace")
        start = min([i for i in (text.find("["), text.find("{")) if i >= 0] or [-1])
        return json.loads(text[start:]) if start >= 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _opencode_list() -> List[Dict[str, Any]]:
    hit = _OC.get("list")
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    got = _opencode("session", "list", "--format", "json", timeout=8)
    rows = got if isinstance(got, list) else (got or {}).get("sessions") or [] if isinstance(got, dict) else []
    out = []
    for s in rows:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        t = s.get("time") if isinstance(s.get("time"), dict) else {}
        out.append({"agent": "opencode", "id": str(s["id"]), "cwd": str(s.get("directory") or s.get("cwd") or ""),
                    "title": _title(str(s.get("title") or "")),
                    "at": _ms(s.get("updated") or t.get("updated") or s.get("created") or t.get("created"))})
    _OC["list"] = (time.time(), out)
    return out


def _opencode_read(sid: str) -> Dict[str, Any]:
    got = _opencode("export", sid, timeout=15)
    if not isinstance(got, dict):
        return {}
    info = got.get("info") or {}
    msgs = []
    for m in got.get("messages") or []:
        mi = (m or {}).get("info") or {}
        at = _ms((mi.get("time") or {}).get("created"))
        parts = m.get("parts") or []
        text = "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
                         and not p.get("synthetic"))
        if mi.get("role") == "user":
            if _typed(text):
                msgs.append(_msg("user", text, at))
        elif mi.get("role") == "assistant":
            steps = [s for p in parts if isinstance(p, dict) and p.get("type") == "tool"
                     for s in _steps_of(str(p.get("tool") or ""), (p.get("state") or {}).get("input") or {},
                                        (p.get("state") or {}).get("title"))]
            msgs.append(_msg("assistant", text, at, steps))
    return {"id": sid, "cwd": str(info.get("directory") or ""), "title": _title(str(info.get("title") or "")),
            "messages": _fold(msgs)}


# ---------------------------------------------------------------- all of them

_FILES = {"claude-code": (_claude_files, _claude_read), "codex": (_codex_files, _codex_read),
          "gemini-cli": (_gemini_files, _gemini_read), "pi": (_pi_files, _pi_read)}


def _newest(files: List[str], n: int) -> List[Tuple[str, float, int]]:
    out = []
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((p, st.st_mtime, st.st_size))
    out.sort(key=lambda x: -x[1])
    return out[:n]


def _head(agent: str, path: str, mtime: float, size: int) -> Dict[str, Any]:
    key = (path, mtime, size)
    if key not in _CACHE:
        if len(_CACHE) > 2000:
            _CACHE.clear()
        h = _FILES[agent][1](path, True)
        if agent == "claude-code" and h.get("id"):
            named = _claude_named(path, size)
            if named:
                h = dict(h, title=named)
        _CACHE[key] = h
    return _CACHE[key]


def _claude_named(path: str, size: int) -> str:
    """The name given to a Claude Code session (/rename), written near the file's end."""
    named = ""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - 256 * 1024))
            tail = f.read()
    except OSError:
        return ""
    for line in tail.split(b"\n"):
        if b'"custom-title"' not in line:
            continue
        try:
            o = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("type") == "custom-title" and o.get("customTitle"):
            named = str(o["customTitle"])
    return named


def _known_folders() -> List[str]:
    seen = []
    for agent in ("claude-code", "codex", "pi"):
        for p, mt, sz in _newest(_FILES[agent][0](), PER_AGENT):
            cwd = _head(agent, p, mt, sz).get("cwd")
            if cwd and cwd not in seen:
                seen.append(cwd)
    return seen


# What Inglen writes into a live Claude Code session for the person. Claude Code wraps whatever
# comes in through its messaging inbox as a peer session's message ("Another Claude session sent
# a message: ..."), so the words lead with where they came from and end saying whose they are.
PHONE_MARK = "From your phone:"
PHONE_NOTE = "(Typed by the person on their phone and passed into this session by Inglen: their own message.)"
PHONE_FROM = "your phone, through Inglen"     # the sender Claude Code records for it (origin.from)
_OLD_MARKS = ("Sent by the user from their phone, through Inglen:",)


def phone_words(words: str) -> str:
    """The text of a phone message as it goes into a live Claude Code session."""
    return f"{PHONE_MARK}\n\n{words}\n\n{PHONE_NOTE}"


def from_phone(o: Dict[str, Any]) -> str:
    """The words of a message Inglen delivered into a live session for the person, or ''."""
    if not (o.get("type") == "user" and (o.get("origin") or {}).get("kind") == "peer"):
        return ""
    text = _text_of((o.get("message") or {}).get("content"))
    for mark in (PHONE_MARK,) + _OLD_MARKS:
        i = text.find(mark)
        if i >= 0:
            break
    else:
        return ""
    said = text[i + len(mark):]
    for end_at in ("\n\n" + PHONE_NOTE, "\n\nThis came from another Claude session"):
        end = said.find(end_at)
        if end >= 0:
            said = said[:end]
    return said.strip()


def claude_inbox(sid: str) -> Dict[str, Any]:
    """The messaging inbox of a Claude Code session running right now: {pid, pipe, token, busy}, or {}.
    Each live session lists its pipe in ~/.claude/sessions/<pid>.json and the token peers present
    in <pid>.<hash>.key beside it (both readable only by this user)."""
    try:
        import psutil
    except ImportError:
        return {}
    d = os.path.join(_home(), ".claude", "sessions")
    for p in glob.glob(os.path.join(d, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            pid = int(rec.get("pid") or 0)
            if str(rec.get("sessionId") or "") != sid or not rec.get("messagingSocketPath") or not psutil.pid_exists(pid):
                continue
            for k in glob.glob(os.path.join(d, f"{pid}.*.key")):
                with open(k, encoding="utf-8") as f:
                    key = json.load(f)
                # A key left by an earlier process with the same pid is not this one's.
                if key.get("peerToken") and str(key.get("procStartFt") or rec.get("procStart")) == str(rec.get("procStart")):
                    return {"pid": pid, "pipe": str(rec["messagingSocketPath"]), "token": str(key["peerToken"]),
                            "busy": rec.get("status") == "busy"}
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _claude_live() -> Dict[str, int]:
    """Claude Code sessions open right now: each running one writes ~/.claude/sessions/<pid>.json."""
    out: Dict[str, int] = {}
    try:
        import psutil
    except ImportError:
        return out
    for p in glob.glob(os.path.join(_home(), ".claude", "sessions", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            pid = int(rec.get("pid") or 0)
            if rec.get("sessionId") and pid and psutil.pid_exists(pid):
                out[str(rec["sessionId"])] = pid
        except (OSError, ValueError, TypeError):
            continue
    return out


def _held_open(paths: List[str]) -> set:
    """Which of these files an agent program has open right now (Codex keeps its session file open)."""
    want = {os.path.normcase(os.path.abspath(p)) for p in paths if p}
    held: set = set()
    if not want:
        return held
    try:
        import psutil
    except ImportError:
        return held
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if not name.startswith(("codex", "node", "opencode", "gemini", "pi")):
            continue
        try:
            for f in proc.open_files():
                n = os.path.normcase(f.path)
                if n in want:
                    held.add(n)
        except Exception:
            continue
    return held


WORKING_S = 30          # written this recently: its agent is at work
STEP_S = 15 * 60        # a turn ending on a step (a long command) is still at work for this long


def working(msgs: List[Dict[str, Any]], mtime: float) -> bool:
    """Whether the session's agent is at work on it now: written in the last WORKING_S, or
    its last message is the person's or ends on a step (a command still running) and the
    file was written within STEP_S."""
    age = time.time() - (mtime or 0)
    last = msgs[-1] if msgs else None
    parts = (last or {}).get("parts") or []
    if last and last.get("role") == "assistant" and parts and "text" in parts[-1] and not last.get("error") and age > 3:
        # It has answered: what it writes after its reply (costs, its own notes) isn't work.
        return False
    if age < WORKING_S:
        return True
    if not last or age > STEP_S:
        return False
    if last.get("role") == "user":
        return True
    return bool(parts) and "text" not in parts[-1] and not last.get("error")


SUB_SCAN = 400          # the newest Codex sessions looked through for a session's helpers


def subagents(cid: str) -> List[Dict[str, Any]]:
    """The helpers a session started, as Codex's Subagents panel lists them: newest first,
    each with its name, whether it is at work, and its own chat id to open it."""
    got = parse_chat(cid)
    if not got or got[0] != "codex":
        return []
    sid, out = got[1], []
    names = _codex_names()
    for p, mt, sz in _newest(_codex_files(), SUB_SCAN):
        h = _head("codex", p, mt, sz)
        spawn = h.get("spawn") or {}
        if spawn.get("parent") != sid or not h.get("id"):
            continue
        title = names.get(h["id"]) or " ".join(x for x in (spawn.get("nickname"), spawn.get("role")) if x) or "Helper"
        out.append({"chat": chat_id("codex", h["id"]), "title": title, "nickname": spawn.get("nickname") or "",
                    "role": spawn.get("role") or "", "at": int(mt * 1000), "working": time.time() - mt < WORKING_S})
    return out


def search(query: str, rows: List[Dict[str, Any]], budget_s: float = 3.0) -> Dict[str, str]:
    """Sessions whose conversation mentions `query` (any case), with the words around it:
    {chat: snippet}. Reads each file's last SEARCH_BYTES, newest first, for at most
    budget_s seconds, so a search answers quickly on a PC with many long sessions."""
    q = " ".join(str(query or "").split()).lower()
    if len(q) < 2:
        return {}
    needle, out, t0 = q.encode("utf-8"), {}, time.time()
    found: Dict[str, List[str]] = {}
    for r in rows:
        if time.time() - t0 > budget_s:
            break
        path = r.get("path") or ""
        if not path and r.get("agent") in _FILES:
            files = found.setdefault(r["agent"], _FILES[r["agent"]][0]())
            path = next((f for f in files if r["id"] and r["id"] in os.path.basename(f)), "")
        try:
            with open(path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                f.seek(max(0, size - SEARCH_BYTES))
                raw = f.read()
        except OSError:
            continue
        low = raw.lower()
        at = low.rfind(needle)
        while at >= 0:
            start, end = low.rfind(b"\n", 0, at) + 1, low.find(b"\n", at)
            line = raw[start:end if end >= 0 else len(raw)]
            if len(line) < BLOB_LINE:
                try:
                    o = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    o = None
                text = _said_in(o) if isinstance(o, dict) else ""
                i = text.lower().find(q)
                if i >= 0:
                    a, b = max(0, i - 50), min(len(text), i + len(q) + 70)
                    out[r["chat"]] = ("..." if a else "") + " ".join(text[a:b].split()) + ("..." if b < len(text) else "")
                    break
            at = low.rfind(needle, 0, start)
    return out


SEARCH_BYTES = 16 * 1024 * 1024


def _said_in(o: Dict[str, Any]) -> str:
    """The words of one line of a session file, the person's or the agent's, whatever the agent."""
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    if p.get("type") in ("user_message", "agent_message") and p.get("message"):
        return _clean(str(p["message"])) or str(p["message"])
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    if m.get("role") in ("user", "assistant"):
        return _clean(_text_of(m.get("content"))) or _text_of(m.get("content"))
    if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
        return _clean(_text_of(p.get("content")))
    return ""


def live_state(agent: str, sid: str, path: str = "") -> str:
    """'open' when the session is running in its program on the PC right now, 'recent' when it was
    written in the last minute and a half (it may be), else ''."""
    if agent == "claude-code" and sid in _claude_live():
        return "open"
    if path and agent == "codex" and os.path.normcase(os.path.abspath(path)) in _held_open([path]):
        return "open"
    try:
        if path and time.time() - os.path.getmtime(path) < RECENT_S:
            return "recent"
    except OSError:
        pass
    return ""


def _forms(path: str) -> set:
    """A folder as written and as Windows spells it in full (C:\\Users\\SWAGGY~1 and the long name)."""
    out = {os.path.normcase(os.path.abspath(path))}
    try:
        out.add(os.path.normcase(os.path.realpath(path)))
    except OSError:
        pass
    return out


def _ours(cwd: str) -> bool:
    """A session this app started itself (an agent brain's turn in the workspace, a temp
    folder of ours, or a helper's worktree): it is already one of the app's own chats or
    helpers, so it isn't listed again."""
    if not cwd:
        return False
    import tempfile
    from .tools import WORKSPACE
    here = _forms(cwd)
    if here & _forms(WORKSPACE):
        return True
    trees = _forms(os.path.join(os.path.expanduser("~"), ".hearth", "worktrees"))
    if any(h.startswith(w + os.sep) for h in here for w in trees):
        return True
    temp = _forms(tempfile.gettempdir())
    for h in here:
        parent, name = os.path.split(h)
        if parent in temp and name.startswith(("hearth-", "hearth_")):
            return True
    return False


def listing(limit: int = 120) -> List[Dict[str, Any]]:
    """Every agent's sessions, newest first: agent, id, chat (the phone's id for
    it), project folder, title and when it was last used."""
    rows: List[Dict[str, Any]] = []
    for agent, (files, _) in _FILES.items():
        for p, mt, sz in _newest(files(), PER_AGENT):
            h = _head(agent, p, mt, sz)
            if not h.get("id") or h.get("hidden"):
                continue
            rows.append({"agent": agent, "id": h["id"], "cwd": h.get("cwd") or "",
                         "title": h.get("title") or "", "at": int(mt * 1000), "path": p})
    claude_open = _claude_live()
    codex_open = _held_open([r["path"] for r in rows if r["agent"] == "codex" and time.time() * 1000 - r["at"] < 86400000])
    rows += _opencode_list()
    out = []
    for r in sorted((r for r in rows if not _ours(r["cwd"])), key=lambda r: -r["at"])[:limit]:
        path = r.get("path") or ""
        live = ("open" if (r["agent"] == "claude-code" and r["id"] in claude_open)
                or (path and os.path.normcase(os.path.abspath(path)) in codex_open)
                else "recent" if time.time() * 1000 - r["at"] < RECENT_S * 1000 else "")
        out.append({"agent": r["agent"], "label": label(r["agent"]), "id": r["id"], "chat": chat_id(r["agent"], r["id"]),
                    "cwd": r["cwd"], "project": os.path.basename(r["cwd"].rstrip("/\\")) or r["cwd"],
                    "title": r["title"] or "Untitled session", "at": r["at"], "live": live,
                    "model": _last_model(path) if path else ""})
    return out


def _find(agent: str, sid: str) -> Optional[str]:
    if agent not in _FILES:
        return None
    for p in _FILES[agent][0]():
        # The whole id, where each agent puts it in the file name: never a part of another id.
        if re.search(r"(^|[-_])" + re.escape(sid) + r"\.jsonl?$", os.path.basename(p)):
            return p
    # A file named some other way: its own id says which.
    for p, mt, sz in _newest(_FILES[agent][0](), 400):
        if _head(agent, p, mt, sz).get("id") == sid:
            return p
    return None


def read(agent: str, sid: str) -> Dict[str, Any]:
    if agent == "opencode":
        d = _opencode_read(sid)
    else:
        p = _find(agent, sid)
        d = _FILES[agent][1](p, False) if p else {}
        if d and p:
            # A long session is read from its end; its id, folder and title are at the start.
            try:
                st = os.stat(p)
                h = _head(agent, p, st.st_mtime, st.st_size)
            except OSError:
                h = {}
            # The tail may not include metadata. Enforce privacy from the head too.
            if h.get("hidden") and not h.get("spawn"):
                return {}
            for k in ("id", "cwd"):
                d[k] = d.get(k) or h.get(k) or ""
            d["title"] = h.get("title") or d.get("title") or ""   # what it began with, not the tail's first question
            d["path"] = p
    return d if d and d.get("id") == sid and not d.get("hidden") else {}


def transcript(cid: str, last: int = 60) -> Dict[str, Any]:
    """A session as the phone's 'open' gives a chat: its last `last` messages, and
    how many more there are (`more`), or that the file goes back further than was
    read (`earlier`), for the phone's Show earlier."""
    got = parse_chat(cid)
    if not got:
        return {"error": "not an agent session"}
    agent, sid = got
    tok_d, tok_c = _DEPTH.set(max(1, -(-int(last or 60) // 60))), _CUT.set([])
    try:
        d = read(agent, sid)
        cut = bool(_CUT.get())
    finally:
        _DEPTH.reset(tok_d)
        _CUT.reset(tok_c)
    if not d:
        return {"chat": cid, "error": "that session isn't on this PC any more"}
    msgs = d.get("messages") or []
    try:
        mtime = os.path.getmtime(d.get("path") or "") if d.get("path") else 0
    except OSError:
        mtime = 0
    return {"chat": cid, "title": d.get("title") or "", "source": agent, "agent": agent, "label": label(agent),
            "working": working(msgs, mtime), **_reach(agent, sid, d.get("path") or ""),
            **control(agent, sid, d.get("path") or ""),
            "cwd": d.get("cwd") or "", "messages": msgs[-last:], "more": max(0, len(msgs) - last),
            **({"earlier": True} if cut else {})}


# Each agent's own permission modes, as the phone names them: (short, what it means).
_CLAUDE_MODES = {
    "default": ("Asks", "asks before edits and commands"),
    "acceptEdits": ("Accepts edits", "makes edits itself and asks before commands"),
    "plan": ("Plan mode", "reads and plans, and changes nothing"),
    "auto": ("Auto", "acts on its own and asks only about risky steps"),
    "bypassPermissions": ("Bypasses permissions", "runs every step without asking"),
    "dontAsk": ("Doesn't ask", "runs what its settings allow and refuses the rest"),
}
_CODEX_SANDBOX = {
    "read-only": ("Read only", "reads files and asks before changing anything"),
    "workspace-write": ("Auto", "works in the project folder and asks before going beyond it"),
    "danger-full-access": ("Full access", "runs every step without asking"),
}


def _lines_back(path: str, most: int):
    """A file's whole lines from its end backwards, reading no more than `most` bytes."""
    try:
        f = open(path, "rb")
    except OSError:
        return
    with f:
        end = f.seek(0, 2)
        pos, rest, read = end, b"", 0
        while pos > 0 and read < most:
            step = min(512 * 1024, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step) + rest
            read += step
            lines = chunk.split(b"\n")
            rest = lines.pop(0) if pos > 0 else b""
            for raw in reversed(lines):
                if raw.strip():
                    yield raw
        if rest.strip() and pos == 0:
            yield rest


def control(agent: str, sid: str, path: str) -> Dict[str, str]:
    """Who sets how a session runs. 'pc' when it is open on the PC and the phone's words go into it there
    (Claude Code's inbox, Codex's queue): that program's own permission mode, model and effort apply,
    read from the session. 'here' when Inglen runs it: the approval switch, model and effort picked apply."""
    live = False
    if agent == "claude-code":
        live = bool(claude_inbox(sid))
    elif agent == "codex" and _exe(agent):
        state = live_state(agent, sid, path)
        live = state == "open" or (state == "recent" and _RAN_HERE.get((agent, sid)) != _mtime(path))
    if not live:
        return {"control": "here"}
    return {"control": "pc", **pc_settings(agent, path)}


def pc_settings(agent: str, path: str) -> Dict[str, str]:
    """The permission mode, model and effort a session open on the PC last ran with, from its own file."""
    mode = model = effort = ""
    for raw in _lines_back(path, 16 * 1024 * 1024):
        if mode and (model or agent != "claude-code"):
            break
        if agent == "claude-code" and b"permissionMode" not in raw and (model or b'"model"' not in raw):
            continue
        if agent == "codex" and b'"turn_context"' not in raw:
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(o, dict) or o.get("isSidechain"):
            continue
        if agent == "claude-code":
            mode = mode or str(o.get("permissionMode") or "")
            m = (o.get("message") or {}).get("model") if o.get("type") == "assistant" else ""
            if m and m != "<synthetic>":
                model = model or str(m)
            if mode and model:
                break
        else:
            p = o.get("payload") or {}
            mode = str((p.get("sandbox_policy") or {}).get("type") or "")
            model, effort = str(p.get("model") or ""), str(p.get("effort") or "")
            break
    named = (_CLAUDE_MODES if agent == "claude-code" else _CODEX_SANDBOX).get(mode)
    out = {"pc_mode": named[0] if named else (mode or ""), "pc_mode_about": named[1] if named else "",
           "pc_model": model, "pc_effort": effort}
    return {k: v for k, v in out.items() if v}


def _reach(agent: str, sid: str, path: str) -> Dict[str, str]:
    """Said when a session is opened: open on the PC in a program the phone can't send into."""
    if agent == "claude-code" and sid in _claude_live() and not claude_inbox(sid):
        return {"reach": "This session is open in Claude Code on your PC, which doesn't take messages from "
                         "outside it. Close it there, or start a new session here."}
    return {}


# ---------------------------------------------------------------- carrying on

Emit = Callable[..., None]           # emit(kind, **fields): "step", "live", "think", "text", "error"
Ask = Callable[[str], bool]          # ask(question) -> allowed


def attached(paths: List[str]) -> str:
    """What a coding agent is told about files sent with a message from the phone: where they are, to open."""
    return "".join(f"\n\n[Attached from the phone: {os.path.basename(p)}, saved at {p}. Open it to look at it.]"
                   for p in paths if p)


_ATTACHED = re.compile(r"\s*\[Attached from the phone: (.+?), saved at (.+?)\. Open it to look at it\.\]")


def _mode() -> str:
    try:
        from .tools import approval_mode
        return approval_mode()
    except Exception:
        return "ask"


async def say(cid: str, text: str, emit: Emit, ask: Ask, *, drain=None, model: str = "", effort: str = "") -> int:
    """Send `text` into a session: its agent program carries it on in the session's own
    folder, with the session's own model and permissions. What the agent asks about
    comes to `ask`; what `drain` gives while it works reaches the turn it is on (Codex
    steers it, Claude Code takes it next). A session its own app has open is never run
    a second time beside it: Codex takes the message in its own queue, the others say so."""
    got = parse_chat(cid)
    if not got:
        emit("error", message="That isn't an agent's session.")
        return 2
    agent, sid = got
    name = label(agent)
    from .redact import clean_emit
    outer, ran = clean_emit(emit), []        # what reaches the phone or window: no saved key

    def emit(kind: str, **fields: Any) -> None:
        if kind == "session":
            ran.append((str(fields.get("agent") or agent), str(fields.get("id") or "")))
        outer(kind, **fields)

    from . import agent_launch
    draft = agent_launch.DRAFTS.pop(cid, None)
    if draft:
        try:
            return await agent_launch.start(draft, text, emit, ask, drain=drain, model=model, effort=effort)
        finally:
            _remember(ran)
    d = read(agent, sid)
    cwd = d.get("cwd") or ""
    if not d or not cwd or not os.path.isdir(cwd):
        emit("error", message=f"{name} can't carry this session on: its project folder isn't on this PC.")
        return 2
    # A second program writing into a session that is open on the PC would tangle both.
    state = live_state(agent, sid, d.get("path") or "")
    if state == "recent" and _RAN_HERE.get((agent, sid)) == _mtime(d.get("path") or ""):
        state = ""
    if agent == "claude-code" and state:
        # Claude Code lists every session it is running: one that isn't is free to carry on here,
        # and one that is takes the message in its own inbox, shown there as it runs.
        inbox = claude_inbox(sid)
        if inbox:
            from . import claude_sessions
            return await claude_sessions.live(dict(inbox, sid=sid), d.get("path") or "", text, emit, drain=drain)
        if sid not in _claude_live():
            state = ""
        else:
            emit("error", message=f"This session is open in {name} on your PC, and that {name} doesn't take messages "
                                  f"from outside it. Update {name} there, or send this from the PC.")
            return 3
    if state and agent == "codex" and _exe(agent):
        # Codex takes a message for a session its app holds (codex queue): the app runs it
        # when that session is free, and the reply shows here.
        if await _queue_codex(_exe(agent), sid, cwd, text):
            emit("note", text=f"Sent to {name} on your PC, where this session is open.")
            from . import codex_sessions
            return await codex_sessions.follow(d.get("path") or "", emit, drain=drain,
                                               queue_more=lambda more: _queue_codex(_exe(agent), sid, cwd, more))
    if state == "open":
        emit("error", message=f"This session is open in {name} on your PC right now. Send it there, or close it "
                              f"there and send this again.")
        return 3
    if state == "recent":
        emit("error", message=f"{name} wrote to this session in the last minute and a half, so it may still be working "
                              f"on your PC. Try again in a moment.")
        return 3
    exe = _exe(agent)
    if not exe:
        emit("error", message=f"{name} isn't installed on this PC any more.")
        return 2
    try:
        return await run_in(agent, exe, sid, cwd, text, emit, ask, drain=drain, model=model, effort=effort)
    finally:
        _remember(ran)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path) if path else 0.0
    except OSError:
        return 0.0


def _remember(ran: List[Tuple[str, str]]) -> None:
    for agent, sid in ran:
        when = _mtime(_find(agent, sid) or "") if sid else 0.0
        if when:
            _RAN_HERE[(agent, sid)] = when


async def run_in(agent: str, exe: List[str], sid: str, cwd: str, text: str, emit: Emit, ask: Ask, *,
                 drain=None, model: str = "", effort: str = "") -> int:
    """Run `text` in the agent's own harness: session `sid`, or a new one in `cwd` when sid is empty.
    `model` (one of claude_brain.agent_models' ids) runs this turn on that model; '' keeps the session's."""
    try:
        if agent == "claude-code":
            from . import claude_sessions
            return await claude_sessions.run(exe, sid, cwd, text, emit, ask,
                                             mode="acceptEdits" if _mode() in ("edits", "auto") else "default",
                                             drain=drain, model=model, effort=effort)
        if agent == "codex":
            from . import codex_sessions
            return await codex_sessions.run(exe, sid, cwd, text, emit, ask, drain=drain, model=model, effort=effort)
        return await _say_acp(agent, sid, cwd, text, emit, ask, model=model)
    except Exception as e:
        emit("error", message=f"{label(agent)} didn't run: {type(e).__name__}: {e}")
        return 1


async def _queue_codex(exe: List[str], sid: str, cwd: str, text: str) -> bool:
    """`codex queue --thread <id> --message <text>`. False when this Codex has no
    queue command (older builds) or it refused, so the caller says why as before."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *exe, "queue", "--thread", sid, "--message", text, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode == 0
    except Exception:
        return False
    finally:
        from .aproc import reap
        await reap(proc)


def acp_tools() -> List[Dict[str, Any]]:
    """Inglen's tools for agents (memory, and call_me) as an ACP session's MCP server."""
    from .shared_memory import NAME, launch
    l = launch()
    return [{"name": NAME, "command": l["command"], "args": l["args"],
             "env": [{"name": k, "value": v} for k, v in l["env"].items()]}]


async def _say_acp(agent: str, sid: str, cwd: str, text: str, emit: Emit, ask: Ask, model: str = "") -> int:
    from . import acp_brain as A
    name = label(agent)
    proc = await A._start(agent, cwd)
    if proc is None:
        emit("error", message=f"{name} isn't installed on this PC any more.")
        return 2
    rpc = A._Rpc(proc, jsonrpc=True, who=name)
    reader = asyncio.ensure_future(rpc.read())
    parts: List[str] = []
    shown: set = set()
    stderr = asyncio.create_task(proc.stderr.read())
    turn = None
    try:
        caps = (await A._hello(rpc)).get("agentCapabilities") or {}
        if sid:
            how = ("session/resume" if "resume" in (caps.get("sessionCapabilities") or {})
                   else "session/load" if caps.get("loadSession") else "")
            if not how:
                emit("error", message=f"{name} can't reopen a session from outside it.")
                return 2
            got_s = await rpc.call(how, {"sessionId": sid, "cwd": cwd, "mcpServers": acp_tools()}, 120) or {}
        else:
            got_s = opened = await rpc.call("session/new", {"cwd": cwd, "mcpServers": acp_tools()}, 120)
            sid = str(opened.get("sessionId") or "")
            if not sid:
                raise RuntimeError(f"{name} did not return a session ID")
        emit("session", agent=agent, id=sid)
        if model:
            await A.use_model(rpc, agent, sid, got_s, model)
        while not rpc.inbox.empty():           # loading replays the old messages: already shown
            m = rpc.inbox.get_nowait()
            if m and "id" in m and "method" in m:
                rpc.send({"id": m["id"], "error": {"code": -32601, "message": "not now"}})
        turn = asyncio.ensure_future(rpc.call("session/prompt", {"sessionId": sid,
                                                                 "prompt": [{"type": "text", "text": text}]}, 86400))
        while not (turn.done() and rpc.inbox.empty()):
            try:
                msg = await asyncio.wait_for(rpc.inbox.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                break
            meth, prm = msg.get("method"), msg.get("params") or {}
            if "id" in msg:
                if meth == "session/request_permission":
                    tname, targs = A.hearth_call(prm.get("toolCall") or {})
                    from .channel_core import question_for
                    from .tools import mode_allows
                    targs = dict(targs, cwd=targs.get("cwd") or cwd) if tname in ("run_command", "start_job") else targs
                    ok = mode_allows(tname, targs) or await asyncio.to_thread(ask, question_for(name, tname, targs))
                    kinds = ("allow_once", "allow_always") if ok else ("reject_once", "reject_always")
                    pick = next((o for k in kinds for o in prm.get("options") or [] if o.get("kind") == k), None)
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
                    parts.append(piece)
                    emit("live", text="".join(parts))
            elif kind == "agent_thought_chunk":
                piece = str((up.get("content") or {}).get("text") or "")
                if piece:
                    emit("think", text=piece)
            elif kind in ("tool_call", "tool_call_update"):
                cid = str(up.get("toolCallId") or "")
                if cid and cid not in shown and (up.get("rawInput") or up.get("title")):
                    shown.add(cid)
                    tname, targs = A.hearth_call(up)
                    from .term_render import tool_words
                    emit("step", words=tool_words(tname, targs, A.says_of(up)), tool=tname)
        if turn.done() and turn.exception():
            emit("error", message=f"{name} stopped: {turn.exception()}"[:600])
    finally:
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        try:
            proc.stdin.close()
            if proc.returncode is None: proc.kill()
            await proc.wait()
            await proc.stdout.read()
            await stderr
        except ProcessLookupError:
            pass
        from .aproc import reap
        await reap(proc)
    reply = "".join(parts).strip()
    if reply:
        emit("text", text=reply)
    return 0 if reply else 1
