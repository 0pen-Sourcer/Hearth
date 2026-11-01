"""A step said in words, the same in the window, the terminal and the phone.

Where the brain says what a step is for (Claude Code's `description` on a
command, an ACP agent's title), its words are used, in the present while the
step runs ("Checking the work") and the past once it is done ("Checked the
work"). Otherwise Inglen's own words for the tool and what it was pointed at.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

# tool -> (while running, when done, the argument it names)
_OWN: Dict[str, Tuple[str, str, str]] = {
    "run_command": ("Running", "Ran", "command"), "start_job": ("Starting", "Started", "command"),
    "read_file": ("Reading", "Read", "path"), "write_file": ("Writing", "Wrote", "path"),
    "edit_file": ("Editing", "Edited", "path"), "list_dir": ("Listing", "Listed", "path"),
    "delete_file": ("Deleting", "Deleted", "path"), "delete_path": ("Deleting", "Deleted", "path"),
    "move_file": ("Moving", "Moved", "src"), "move_path": ("Moving", "Moved", "source"),
    "find_files": ("Searching for", "Searched for", "pattern"), "grep": ("Searching for", "Searched for", "pattern"),
    "grep_search": ("Searching for", "Searched for", "pattern"), "find_file": ("Looking for", "Looked for", "name"),
    "web_search": ("Searching the web for", "Searched the web for", "query"),
    "fetch_url": ("Opening", "Opened", "url"), "browse": ("Opening", "Opened", "url"),
    "web_fetch": ("Reading", "Read", "url"), "open_url": ("Opening", "Opened", "url"),
    "search_chats": ("Searching past chats for", "Searched past chats for", "query"),
    "read_chat": ("Reading an earlier chat", "Read an earlier chat", ""),
    "load_model": ("Loading", "Loaded", "model"), "list_models": ("Listing the models", "Listed the models", ""),
    "memory_save": ("Remembering", "Remembered", "name"), "memory_recall": ("Looking up", "Looked up", "query"),
    "memory_forget": ("Forgetting", "Forgot", "name"), "memory_list": ("Listing memories", "Listed memories", ""),
    "set_reminder": ("Setting a reminder:", "Set a reminder:", "what"), "open_app": ("Opening", "Opened", "name"),
    "screenshot": ("Taking a screenshot", "Took a screenshot", ""), "view_image": ("Looking at", "Looked at", "path"),
    "spawn_subagent": ("Starting a helper:", "Started a helper:", "persona"),
    "generate_image": ("Making an image:", "Made an image:", "prompt"), "disk_usage": ("Measuring", "Measured", "path"),
    "load_tools": ("Getting tools for", "Got tools for", "query"),
    "what_can_i_do": ("Checking what's set up", "Checked what's set up", ""),
    "set_up": ("Setting up", "Set up", "what"), "undo": ("Taking back a change", "Took back a change", ""),
    "end_session": ("Saying goodbye", "Said goodbye", ""),
    "worklog": ("Taking notes", "Took notes", ""), "ask_user": ("Asking you", "Asked you", "question"),
}
_ARG_KEYS = ("command", "path", "query", "url", "name", "pattern", "prompt", "what", "text", "app")

# Verbs that change their spelling. Regular ones follow the rules below.
_IRREGULAR = {
    "run": ("running", "ran"), "rerun": ("rerunning", "reran"), "read": ("reading", "read"),
    "reread": ("rereading", "reread"), "find": ("finding", "found"), "write": ("writing", "wrote"),
    "rewrite": ("rewriting", "rewrote"), "make": ("making", "made"), "get": ("getting", "got"),
    "build": ("building", "built"), "rebuild": ("rebuilding", "rebuilt"), "set": ("setting", "set"),
    "reset": ("resetting", "reset"), "put": ("putting", "put"), "take": ("taking", "took"),
    "give": ("giving", "gave"), "see": ("seeing", "saw"), "do": ("doing", "did"), "redo": ("redoing", "redid"),
    "undo": ("undoing", "undid"), "go": ("going", "went"), "begin": ("beginning", "began"),
    "send": ("sending", "sent"), "keep": ("keeping", "kept"), "leave": ("leaving", "left"),
    "hold": ("holding", "held"), "bring": ("bringing", "brought"), "think": ("thinking", "thought"),
    "say": ("saying", "said"), "tell": ("telling", "told"), "split": ("splitting", "split"),
    "cut": ("cutting", "cut"), "let": ("letting", "let"), "feed": ("feeding", "fed"),
    "seek": ("seeking", "sought"), "spin": ("spinning", "spun"), "dig": ("digging", "dug"),
    "lay": ("laying", "laid"), "pay": ("paying", "paid"), "sit": ("sitting", "sat"),
    "upset": ("upsetting", "upset"), "draw": ("drawing", "drew"), "drive": ("driving", "drove"),
    "choose": ("choosing", "chose"), "fix": ("fixing", "fixed"),
}
# One-syllable (or stressed-last) verbs that double their last letter.
_DOUBLE = {"stop", "drop", "plan", "ship", "skip", "tap", "zip", "swap", "scan", "prep", "chat", "log",
           "tag", "pin", "grep", "commit", "submit", "wrap", "trim", "strip", "step", "map", "nap",
           "pad", "snap", "flag", "plug", "debug", "shut", "clip", "chop", "dim", "jog", "rip", "sum"}
# The verbs a step's own words start with, so a noun ("Git status") is left as said.
_VERBS = set(_IRREGULAR) | _DOUBLE | {
    "add", "apply", "ask", "back", "build", "bump", "call", "change", "check", "clean", "clear", "clone",
    "close", "compare", "compile", "configure", "confirm", "convert", "copy", "count", "create", "delete",
    "deploy", "describe", "detect", "diff", "disable", "download", "enable", "ensure", "explore", "export",
    "extract", "fetch", "fill", "filter", "format", "generate", "grab", "import", "inspect", "install",
    "kill", "launch", "link", "lint", "list", "load", "locate", "look", "measure", "merge", "move", "navigate",
    "note", "open", "parse", "patch", "pause", "ping", "preview", "print", "probe", "publish", "pull", "push",
    "query", "record", "refresh", "remove", "rename", "render", "repair", "replace", "restart", "restore",
    "resume", "retry", "review", "save", "scaffold", "search", "select", "serve", "show", "sort", "start",
    "stash", "stage", "test", "touch", "try", "type", "uninstall", "unpack", "update", "upgrade", "upload",
    "use", "validate", "verify", "view", "visit", "wait", "watch", "work",
}
_VOWELS = "aeiou"


def _ing(v: str) -> str:
    if v in _IRREGULAR:
        return _IRREGULAR[v][0]
    if v in _DOUBLE:
        return v + v[-1] + "ing"
    if v.endswith("ie"):
        return v[:-2] + "ying"
    if v.endswith("e") and not v.endswith(("ee", "ye", "oe")):
        return v[:-1] + "ing"
    return v + "ing"


def _ed(v: str) -> str:
    if v in _IRREGULAR:
        return _IRREGULAR[v][1]
    if v in _DOUBLE:
        return v + v[-1] + "ed"
    if v.endswith("e"):
        return v + "d"
    if v.endswith("y") and len(v) > 1 and v[-2] not in _VOWELS:
        return v[:-1] + "ied"
    return v + "ed"


def _base_of_ing(w: str) -> Optional[str]:
    """The verb an -ing word comes from, when it is one we know."""
    for base, (ing, _past) in _IRREGULAR.items():
        if ing == w:
            return base
    for v in _VERBS:
        if _ing(v) == w:
            return v
    return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def tenses(said: str) -> Tuple[str, str]:
    """(present, past) of a step's own words: "Check the work" -> ("Checking the
    work", "Checked the work"). Words that don't start with a known verb stay as said."""
    said = " ".join(str(said or "").split()).rstrip(".")
    if not said:
        return "", ""
    first, _, rest = said.partition(" ")
    low = first.lower()
    tail = (" " + rest) if rest else ""
    if low in _VERBS:
        return _cap(_ing(low)) + tail, _cap(_ed(low)) + tail
    base = _base_of_ing(low) if low.endswith("ing") else None
    if base:
        return _cap(low) + tail, _cap(_ed(base)) + tail
    return _cap(said), _cap(said)


def lead(name: str, finished: bool = True) -> str:
    """The words before what a step was pointed at: "Read", "Searching the web for"."""
    if name in _OWN:
        return _OWN[name][1 if finished else 0]
    plain = re.sub(r"^mcp_+[a-z0-9]+_+", "", str(name or "a tool")).replace("_", " ").strip() or "a tool"
    return ("Used " if finished else "Using ") + plain


def _what(name: str, args: Dict[str, Any], key: str) -> str:
    val = args.get(key) if key else None
    if val is None and name not in _OWN:
        val = next((args[k] for k in _ARG_KEYS if args.get(k)), None)
    val = " ".join(str(val).split()) if val else ""
    if key == "path" and val:
        val = val.replace(os.path.expanduser("~"), "~")
    if key in ("pattern", "query") and val and name not in ("web_search", "search_chats", "memory_recall", "load_tools"):
        val = f"“{val[:88]}”"          # a pattern like round( reads as what it is
    if key == "url" and val:
        val = re.sub(r"^https?://(www\.)?", "", val).rstrip("/")
    return val[:90]


def _session_agent(cid: Any) -> str:
    try:
        from .agent_sessions import label, parse_chat
        got = parse_chat(str(cid or ""))
        return label(got[0]) if got else "a coding agent"
    except Exception:
        return "a coding agent"


def words(name: str, args: Optional[Dict[str, Any]] = None, says: str = "") -> Dict[str, str]:
    """{"doing": while it runs, "done": once it has}."""
    args = args if isinstance(args, dict) else {}
    if says:
        doing, done = tenses(says)
        if doing:
            return {"doing": doing, "done": done, "own": True}     # the brain's words: what it ran shows beside them
    if name == "agent_sessions":
        act = str(args.get("action") or "list")
        who = _session_agent(args.get("session"))
        pair = {"read": (f"Reading {who}'s session", f"Read {who}'s session"),
                "send": (f"Sending a message to {who}", f"Sent a message to {who}")}.get(
            act, ("Looking at the coding agents' sessions", "Looked at the coding agents' sessions"))
        return {"doing": pair[0], "done": pair[1]}
    if name in _OWN:
        doing, done, key = _OWN[name]
    else:
        # A tool with no words of its own: "Using system vibe", "Used system vibe".
        doing, done, key = lead(name, False), lead(name, True), ""
    what = _what(name, args, key)
    return {"doing": f"{doing} {what}".strip(), "done": f"{done} {what}".strip()}


def done(name: str, args: Optional[Dict[str, Any]] = None, says: str = "") -> str:
    return words(name, args, says)["done"]


def doing(name: str, args: Optional[Dict[str, Any]] = None, says: str = "") -> str:
    return words(name, args, says)["doing"]


class Wrap:
    """Wraps a turn's emit: every tool call carries its words, so each surface
    says the step the same way."""

    def __init__(self, emit):
        self.emit = emit

    def __call__(self, kind: str, **f) -> Any:
        if kind == "tool_call" and f.get("name") and not f.get("words"):
            try:
                f["words"] = words(f["name"], f.get("args"), str(f.get("says") or ""))
            except Exception:
                pass
        return self.emit(kind, **f)

    def __getattr__(self, item):
        return getattr(self.emit, item)
