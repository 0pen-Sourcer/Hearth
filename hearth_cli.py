"""Hearth - local-only personal AI CLI.

Runs on Inglen's built-in engine, or any OpenAI-compatible server it finds
(LM Studio, Ollama, llama.cpp, vLLM) or a cloud provider.

Quick start:
    pip install openai
    # optional but recommended:
    pip install psutil pillow
    python jarvis.py

Env:
    LOCAL_API_BASE   default Inglen's built-in engine, http://localhost:8790/v1
    LOCAL_MODEL      default 'local-model'
    HEARTH_WORKSPACE default ~/Jarvis  (sandbox for writes)
    HEARTH_LOCKDOWN  set to 1 to also confine reads to the workspace
"""

import os
from hearth.local_engine import local_base as _local_base
from hearth import env as _env
import re
import sys
import json
import time
import shutil
import asyncio
import threading
import urllib.request
from typing import Any, Dict, List, Optional


# Answered before anything heavy loads. There was no argument handling at all,
# so --help and --version started the whole session, spawned the MCP bridges
# and sat at a prompt, which is not what either flag is asking for.
_HELP = """usage: hearth-cli [--help] [--version]

Inglen in the terminal. Runs on Inglen's built-in engine, or any
OpenAI-compatible server (LM Studio, Ollama, llama.cpp, vLLM, a cloud provider).

Everything else is set from inside the session. Type /help once it starts.

Environment (optional, each overrides the saved setting):
  LOCAL_API_BASE     server address         default Inglen's engine, http://localhost:8790/v1
  LOCAL_MODEL        model id               default: whatever is loaded
  LOCAL_API_KEY      key for that server
  HEARTH_CONTEXT     context window in tokens
  HEARTH_WORKSPACE   where writes are allowed           default ~/Jarvis
  HEARTH_LOCKDOWN    1 confines reads to the workspace as well
  HEARTH_MANUAL_TOOLS  1 sends the tool list as text instead of natively
"""


def _answer_flags(argv: List[str]) -> None:
    args = [a for a in argv[1:] if a.strip()]
    if not args:
        return
    if any(a in ("-h", "--help", "/?") for a in args):
        print(_HELP)
        sys.exit(0)
    if any(a in ("-V", "--version") for a in args):
        try:
            from hearth.updater import HEARTH_VERSION as _v
        except Exception:
            _v = "unknown"
        print(f"inglen {_v}")
        sys.exit(0)
    # Frozen builds re-launch this exe with private sentinels of their own, and
    # the migrator reads sys.argv; anything unrecognised is left for them.


_answer_flags(sys.argv)

from openai import AsyncOpenAI  # noqa: E402
from hearth.chat_call import chat_create
from hearth import anthropic_chat as _anthropic_chat
from hearth import term_render as _term_render


def _stream_client_timeout():
    """No overall request deadline + a generous per-read (per-chunk) timeout, so a
    slow local generation is never aborted mid-stream; connect/pool stay short so a
    dead endpoint still fails fast. httpx ships with openai."""
    try:
        import httpx
        return httpx.Timeout(None, connect=15.0, read=300.0, write=60.0, pool=15.0)
    except Exception:
        return 600.0

class Pastes:
    """A long paste sits in the input line as a short marker and is sent whole.
    Pasting the same text again swaps its marker for the text."""
    MIN_LINES = 4
    MIN_CHARS = 800
    _MARK = re.compile(r"\[Pasted text #(\d+)(?: \+\d+ lines)?\]")

    def __init__(self) -> None:
        self._kept: Dict[int, str] = {}

    def marker_for(self, text: str) -> Optional[str]:
        """The marker to show for this paste, or None when it is short enough
        to show as it is."""
        lines = text.count("\n") + 1
        if lines < self.MIN_LINES and len(text) < self.MIN_CHARS:
            return None
        n = len(self._kept) + 1
        self._kept[n] = text
        return f"[Pasted text #{n} +{lines} lines]" if lines > 1 else f"[Pasted text #{n}]"

    def marker_of(self, line: str, text: str) -> Optional[str]:
        """The marker in `line` that stands for exactly this text."""
        for m in self._MARK.finditer(line):
            if self._kept.get(int(m.group(1))) == text:
                return m.group(0)
        return None

    def holds(self, line: str) -> bool:
        return any(int(m.group(1)) in self._kept for m in self._MARK.finditer(line))

    def expand(self, line: str) -> str:
        return self._MARK.sub(lambda m: self._kept.get(int(m.group(1)), m.group(0)), line)


PASTES = Pastes()


def unwrap_terminal(text: str, cols: int) -> str:
    """Text copied from this terminal's own input line comes back broken at
    every row, mid-word, because each row was drawn as a line of its own.
    When every line but the last reaches (nearly) the edge, those breaks are
    the terminal's, and they are joined again. The copy drops a space that
    ended a row, so a row one short of full gets it back. The first row is
    shorter by the prompt when the prompt wasn't copied."""
    lines = text.split("\n")
    if len(lines) < 2 or cols < 20:
        return text
    if not all(cols - 5 <= len(ln) <= cols for ln in lines[:-1]):
        return text
    full = max(len(ln) for ln in lines[1:-1]) if len(lines) > 2 else cols - 1
    out = lines[0]
    for i, (prev, ln) in enumerate(zip(lines, lines[1:])):
        target = full if i or len(prev) >= full - 1 else full - 2
        out += ("" if len(prev) >= target else " ") + ln
    return out

# Soft dependency: prompt_toolkit gives proper arrow keys, history, ctrl-r.
# If installed, we use it. Otherwise we fall back to plain input().
try:
    from prompt_toolkit import PromptSession  # type: ignore
    from prompt_toolkit.history import FileHistory  # type: ignore
    from prompt_toolkit.formatted_text import ANSI  # type: ignore
    from prompt_toolkit.key_binding import KeyBindings  # type: ignore
    from prompt_toolkit.keys import Keys  # type: ignore
    from prompt_toolkit.styles import Style  # type: ignore
    from prompt_toolkit.filters import Condition  # type: ignore
    _PT_AVAILABLE = True

    class SafeFileHistory(FileHistory):  # type: ignore[misc]
        """FileHistory that strips lone UTF-16 surrogate code points before
        writing. Windows terminals occasionally emit them for emojis like 💀,
        which crashes the default FileHistory's UTF-8 encoder."""
        def store_string(self, string: str) -> None:
            # A marker means nothing in a later session, so the file gets the text.
            string = PASTES.expand(string)
            # encode/decode round-trip with 'replace' kills surrogates
            cleaned = string.encode("utf-8", "replace").decode("utf-8", "replace")
            super().store_string(cleaned)
except ImportError:
    _PT_AVAILABLE = False


def _shift_enter_is_newline() -> None:
    """Shift+Enter adds a line, as in a chat box. The Windows console says Shift
    was held on the Enter event, but prompt_toolkit drops it, so Shift+Enter
    arrives as Esc+Enter, which the input already reads as a new line."""
    try:
        from prompt_toolkit.input import win32 as _w32
        from prompt_toolkit.key_binding.key_processor import KeyPress
        reader = _w32.ConsoleInputReader
        if getattr(reader, "_hearth_shift_enter", False):
            return
        orig = reader._event_to_key_presses

        def patched(self, ev):
            out = orig(self, ev)
            if (len(out) == 1 and out[0].key == Keys.ControlM
                    and ev.ControlKeyState & self.SHIFT_PRESSED):
                return [KeyPress(Keys.Escape, "\x1b"), KeyPress(Keys.ControlM, "\r")]
            return out
        reader._event_to_key_presses = patched
        reader._hearth_shift_enter = True
    except Exception:
        pass


def _enable_windows_vt_input() -> None:
    """Turn on ENABLE_VIRTUAL_TERMINAL_INPUT on the Windows console's stdin.

    Without it the Win32 console hands prompt_toolkit each pasted line as its
    own Enter keydown, so a multi-line paste submits line by line. With it,
    Windows Terminal delivers the paste as one bracketed-paste escape sequence
    (\\x1b[200~ … \\x1b[201~) that prompt_toolkit coalesces into a single edit.
    No-op off Windows or when stdin isn't a real console. Best-effort — a
    failure just leaves the old line-splitting behavior, never crashes."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
        h = k32.GetStdHandle(STD_INPUT_HANDLE)
        if not h or h == wintypes.HANDLE(-1).value:
            return
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return  # not a real console (piped/redirected)
        k32.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT)
    except Exception:
        pass

# ---- Brain ----------------------------------------------------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

try:
    from hearth import (
        TOOL_DEFINITIONS,
        execute_tool,
        to_openai_tools,
        tools_by_category,
        WORKSPACE,
        read_locked,
        ACTIVITY_LOG,
        system_prompt,
        trim_to_budget,
        sanitize_tool_pairing,
        compact_history,
        estimate_tokens,
        CHARS_PER_TOKEN,
        set_runtime_info,
        memory,
        voice,
    )
    from hearth.loop_guard import ToolLoopGuard, MAX_TURNS
    from hearth.errors import classify_api_error
    from hearth import listen as stt
except ImportError as e:
    print(f"Fatal: hearth package not importable from {THIS_DIR}: {e}")
    sys.exit(1)

# ---- Config ---------------------------------------------------------------
# Env vars take precedence; otherwise we read ~/Jarvis/settings.json so that
# whatever endpoint the user picked in the GUI (Settings -> LLM endpoint)
# also drives the CLI. Same source of truth, no env-var duplication.
def _read_settings_endpoint():
    """Return (url, key, model, provider) from settings.json or all None.
    `provider` (when present) drives the brain_keys.json fallback below -
    settings.json stores the URL + provider name; the actual API key for
    cloud providers lives in brain_keys.json (the /brain command writes
    it there). Without reading both, restarts lose cloud auth."""
    try:
        import json as _json
        p = os.path.join(WORKSPACE, "settings.json")
        if not os.path.isfile(p):
            return None, None, None, None
        with open(p, "r", encoding="utf-8") as f:
            s = _json.load(f)
        return ((s.get("llm_url") or "").strip() or None,
                (s.get("llm_key") or "").strip() or None,
                (s.get("llm_model") or "").strip() or None,
                (s.get("llm_provider") or "").strip().lower() or None)
    except Exception:
        return None, None, None, None



def _type_out(text: str, delay: float = 0.012) -> None:
    """Print a line a character at a time, the way a person would type it.
    Escape codes go out whole, and anything not a terminal gets the line at
    once."""
    if not sys.stdout.isatty() or _env.get("NO_ANIMATION") in ("1", "true", "yes"):
        print(text)
        return
    parts = re.split(r"(\x1b\[[0-9;]*m)", text)
    for part in parts:
        if part.startswith("\x1b"):
            sys.stdout.write(part)
            continue
        for ch in part:
            sys.stdout.write(ch)
            sys.stdout.flush()
            time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _setup_rule(step: int, total: int, title: str) -> None:
    """A thin progress rule between parts of first-run setup."""
    width = 24
    filled = round(width * step / total)
    print(f"  {C_BRAND}{'━' * filled}{C_DIM}{'─' * (width - filled)}{C_RESET}  "
          f"{C_DIM}{step}/{total}{C_RESET}  {C_TOOL}{title}{C_RESET}")

def _probe_live_model(url: str, key: str = "") -> Optional[str]:
    """Ask an endpoint what it is ACTUALLY serving right now.

    settings.json is not authoritative for the loaded model: the other surface
    may have swapped it, or a saved name can outlive its download. Announcing a
    model the server doesn't have makes every following request fail with a
    confusing 404, so read it off the server instead of the file."""
    if not url:
        return None
    try:
        from hearth.headless import model_list_req
        rq = model_list_req(url, key or "hearth-builtin")
        with urllib.request.urlopen(rq, timeout=2) as r:
            ids = [m.get("id") for m in (json.load(r).get("data") or [])]
        return next((i for i in ids if i), None)
    except Exception:
        return None


# Every slash command with a few words on what it does: what the completer
# offers as you type, and what "did you mean" picks from.
SLASH_COMMANDS = {
    "/help": "every command", "/status": "what is thinking, how full this chat is, plan limits",
    "/brain": "switch what thinks: local, a provider or an agent",
    "/model": "pick a model on the current brain", "/models": "models on disk, downloads, the provider's list",
    "/tools": "the tools", "/clear": "start a new chat", "/compact": "summarize old turns now",
    "/context": "set the context window", "/tokens": "how full the context is", "/history": "the last messages",
    "/chats": "every chat, from the window too", "/chat": "carry on a chat here", "/workspace": "the workspace folder",
    "/log": "recent activity", "/mem": "my memories: what I remember", "/curate": "merge duplicate memories",
    "/rules": "the house rules file", "/name": "my name", "/me": "what I know and may do",
    "/team": "helpers working for me", "/agents": "agent programs on this PC", "/agent": "run a helper and wait",
    "/learn": "look at this PC again", "/migrate": "bring memory over from another agent",
    "/import-memory": "bring memory over from ChatGPT or Claude", "/jobs": "background jobs",
    "/mcp": "Model Context Protocol", "/skill": "skills", "/voice": "speaking: on, off, speed, mic",
    "/stt": "the listening model", "/listen": "talk instead of typing", "/sleep": "wait for the wake word",
    "/wake": "wake up", "/think": "thinking (reasoning): on, off, or how hard", "/multi": "multi-line input",
    "/mode": "what runs without asking: ask, edits, auto", "/perms": "saved permissions",
    "/allow": "let me write under a folder", "/disallow": "take that back", "/allowed": "folders I may write in",
    "/lockdown": "keep reads inside the workspace", "/about": "version and stats", "/update": "get a newer version",
    "/repair": "restore damaged files", "/undo": "put back a file change: /undo lists, /undo <n> puts one back", "/phone": "reach me from Telegram, Discord or WhatsApp", "/pair": "let someone in with their code", "/exit": "quit",
    # Other names for the same commands.
    "/endpoint": "same as /brain", "/provider": "same as /brain", "/load": "same as /model",
    "/usage": "same as /status", "/import": "same as /import-memory", "/skills": "same as /skill",
    "/voices": "same as /voice", "/quit": "same as /exit",
}
_SUB_CHOICES = {
    "/mode": [("ask", "ask before anything risky"), ("edits", "file edits run, commands ask"), ("auto", "everything runs")],
    "/think": [("on", ""), ("off", ""), ("low", ""), ("medium", ""), ("high", ""), ("xhigh", ""), ("max", "")],
    "/voice": [("on", ""), ("off", ""), ("speed", "playback rate"), ("devices", "list mics"), ("device", "pick a mic"),
               ("mic-test", "check the level"), ("duplex", "hold the mic while I speak")],
    "/models": [("disk", "models on this PC"), ("free", "free ones on this provider"), ("hf", "search Hugging Face"),
                ("get", "download"), ("use", "load one on this PC"), ("stop", "stop the local server"),
                ("helper", "a second model for helpers")],
    "/agents": [("update", "update one"), ("setup", "install and sign in")],
    "/lockdown": [("on", ""), ("off", "")], "/listen": [("on", ""), ("off", "")],
    "/mem": [("tree", "by category"), ("map", "as a graph")], "/perms": [("forget", "one tool"), ("reset", "all")],
    "/curate": [("apply", "merge them")], "/mcp": [("edit", ""), ("config", ""), ("run", "")],
    "/skill": [("install", "from GitHub or a folder")], "/migrate": [("hermes", ""), ("openclaw", "")],
}


def _team_panes() -> None:
    """A team started from the terminal opens a live pane per member."""
    try:
        from hearth import team as _team
        _team.PANES = True
    except Exception:
        pass


_team_panes()


def _brain_choices():
    from hearth.headless import PROVIDERS
    out = [("builtin", "Inglen's built-in engine"), ("local", "Inglen's engine, or a server it finds"),
           ("lmstudio", "LM Studio")]
    out += [(pid, label) for pid, (label, _url) in PROVIDERS.items()]
    out += [("custom", "any OpenAI-compatible address"), ("claude-code", "your Claude plan"),
            ("codex", "your ChatGPT plan"), ("cursor", "your Cursor plan")]
    try:
        from hearth import claude_brain as _cb
        out += [(b["id"], f"your {b['plan']} plan") for b in _cb.choices() if not b.get("core")]
    except Exception:
        pass
    return out + [("forget", "delete a saved key")]


def _agent_line() -> str:
    """The agent program thinking for JARVIS, when one is, for the banner."""
    try:
        from hearth import claude_brain as _cb
        a = _cb.current()
        return f"{_cb.BRAINS[a]} on this PC, your {_cb.PLANS.get(a, '')} plan" if a else ""
    except Exception:
        return ""


def _same_endpoint(a: str, b: str) -> bool:
    """One server, however it is written: localhost and 127.0.0.1, a trailing slash."""
    from urllib.parse import urlparse

    def norm(u: str):
        p = urlparse((u or "").strip().rstrip("/"))
        host = (p.hostname or "").lower()
        host = "127.0.0.1" if host in ("localhost", "::1", "0.0.0.0") else host
        return (p.scheme, host, p.port, p.path.rstrip("/"))
    return norm(a) == norm(b)


def _looks_like_key(s: str) -> bool:
    """An API key, as opposed to a model name typed after /brain <provider>."""
    # By shape, since providers change their prefixes (Gemini's keys went from
    # AIza to AQ.): a key carries a long random run, a model name is short words.
    s = (s or "").strip()
    if not s or " " in s or "/" in s or ":" in s:
        return False
    return max((len(r) for r in re.findall(r"[A-Za-z0-9]+", s)), default=0) >= 20


HELP_GROUPS = [
    ("Brain", ["/brain", "/model", "/models", "/think", "/mode", "/status"]),
    ("Chats", ["/chats", "/chat", "/clear", "/compact", "/context", "/tokens", "/history", "/multi"]),
    ("What I know", ["/mem", "/me", "/name", "/rules", "/learn", "/curate", "/import-memory", "/migrate"]),
    ("Helpers", ["/team", "/agents", "/agent", "/jobs"]),
    ("Voice", ["/voice", "/listen", "/stt", "/sleep", "/wake"]),
    ("This PC", ["/allow", "/disallow", "/allowed", "/lockdown", "/perms", "/workspace", "/tools", "/mcp", "/skill", "/log"]),
    ("Inglen", ["/about", "/update", "/repair", "/undo", "/phone", "/pair", "/exit"]),
]


def _suggest_command(word: str) -> str:
    """A command that fits a mistyped or made-up one: by spelling, then by
    what each one does (/reasoning finds /think)."""
    w = (word or "").lower().lstrip("/")
    hit = ""
    if len(w) >= 4:
        stem = w[:5]
        hit = next((c for c, d in SLASH_COMMANDS.items()
                    if not d.startswith("same as") and stem in d.lower()), "")
    if not hit:
        import difflib
        close = difflib.get_close_matches("/" + w, list(SLASH_COMMANDS), n=1, cutoff=0.75)
        hit = close[0] if close else ""
    return f"Did you mean {hit}?" if hit else "/help lists them."


def _did_you_mean(word: str, choices) -> str:
    import difflib
    hit = difflib.get_close_matches((word or "").lower(), list(choices), n=1, cutoff=0.6)
    return hit[0] if hit else ""


def _read_brain_key(provider: str) -> str:
    """Look up a saved cloud key: the keys kept in settings.json first (the window
    and /brain both write there), then ~/Jarvis/brain_keys.json from before, so a
    key replaced in either place is the one used in both. '' if there is none."""
    if not provider or provider in ("local", "lmstudio", "builtin"):
        return ""
    import json as _json

    def _file(name: str) -> dict:
        try:
            with open(os.path.join(WORKSPACE, name), "r", encoding="utf-8") as f:
                return _json.load(f) or {}
        except Exception:
            return {}
    s = _file("settings.json")
    key = s.get("llm_key") or "" if (s.get("llm_provider") or "").strip().lower() == provider else ""
    key = key or (s.get("provider_keys") or {}).get(provider, "")
    key = key or (_file("brain_keys.json").get(provider) or {}).get("key", "")
    return str(key or "").strip()


_S_URL, _S_KEY, _S_MODEL, _S_PROV = _read_settings_endpoint()


def _apply_saved_voice_settings():
    """Bring the CLI onto the same voice settings the window writes.

    The voice panes save to settings.json and the modules read env vars, and
    only the window ever exported them. So a whisper size, voice, speed or mic
    picked there applied to the window alone and the CLI silently ran its own
    defaults. Mirrors the window's export list. An env var already set wins,
    so an explicit launch override is left alone.
    """
    defaults = {
        "STT_MODEL": ("stt_model", "base.en"),
        "STT_DEVICE": ("stt_device", "cpu"),
        # Blank means detect per utterance, and an unset variable reads the
        # same way, so there is nothing to export in that case.
        "STT_LANGUAGE": ("stt_language", ""),
        "TTS_DEVICE": ("tts_device", "cpu"),
        "VOICE_SPEED": ("voice_speed", 1.0),
        "VOICE": ("voice_name", "am_michael"),
        "VOICE_INPUT_DEVICE": ("voice_input_device", -1),
        "VOICE_INPUT_NAME": ("voice_input_name", ""),
    }
    try:
        import json as _json
        p = os.path.join(WORKSPACE, "settings.json")
        s = {}
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                s = _json.load(f) or {}
        for env, (key, fallback) in defaults.items():
            if _env.get(env):
                continue
            val = s.get(key, fallback)
            if val is None or str(val).strip() == "":
                continue
            _env.set(env, val)
        # An older pick saved only as a device number is looked up by name,
        # since the number moves when devices are plugged in or out.
        if not _env.get("VOICE_INPUT_NAME") and s.get("voice_input_device") not in (None, -1, "-1"):
            _legacy, _ = stt.name_for_numbered_pick(s.get("voice_input_device"))
            if _legacy:
                _env.set("VOICE_INPUT_NAME", _legacy)
        # listen binds its size at import, which already happened above, so
        # exporting alone would not move it.
        want = _env.get("STT_MODEL", "")
        if want and getattr(stt, "MODEL_SIZE", "") != want:
            stt.set_model(want)
    except Exception:
        pass                                   # settings are a convenience here


_apply_saved_voice_settings()


def _save_setting(key, value) -> bool:
    """Persist one setting so a change made here survives a restart and reaches
    the window. Read-modify-write against the current file, through the same
    atomic writer the window uses, so a kill mid-write cannot leave a truncated
    settings.json."""
    try:
        import json as _json
        from hearth.web import _atomic_write_json
        p = os.path.join(WORKSPACE, "settings.json")
        cur = {}
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                cur = _json.load(f) or {}
        cur[key] = value
        _atomic_write_json(p, cur, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False
# True when the brain was pinned by an explicit launch env (e.g. grok.ps1).
# Such a session ignores live settings.json brain-sync so the GUI can't yank a
# deliberately env-pinned CLI off its endpoint mid-run.
_BRAIN_FROM_ENV = bool(os.getenv("LOCAL_API_BASE"))
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE") or _S_URL or _local_base()
LOCAL_MODEL = os.getenv("LOCAL_MODEL") or _S_MODEL or "local-model"
# Cloud endpoints need a real key. Resolution order:
#   1. LOCAL_API_KEY env (explicit override)
#   2. OPENAI_API_KEY env (legacy compat)
#   3. settings.json llm_key (rare - most cloud users have this empty)
#   4. brain_keys.json[<provider>].key (where /brain saves it)
#   5. "jarvis-local" dummy (local servers ignore the field)
# Step 4 is what was missing before: restarting on a cloud brain would
# fall straight to the dummy and 401 every request, forcing a /brain
# <provider> re-run every launch.
_BRAIN_KEY = _read_brain_key(_S_PROV)
# Local servers ignore the key field, with one exception: Hearth's own builtin
# server is started with --api-key hearth-builtin and enforces it. Using that as
# the local placeholder satisfies every case, since LM Studio and Ollama don't
# look at it. The old "jarvis-local" placeholder meant opening the CLI while the
# GUI had the builtin running was rejected with an auth error.
_LOCAL_PLACEHOLDER_KEY = "hearth-builtin"
LOCAL_API_KEY = (os.getenv("LOCAL_API_KEY") or os.getenv("OPENAI_API_KEY")
                 or _S_KEY or _BRAIN_KEY or _LOCAL_PLACEHOLDER_KEY)

# Propagate resolved values into os.environ so downstream modules
# (hearth.imagine reads os.environ["LOCAL_API_BASE"] at call time)
# see them. Only set LOCAL_API_KEY in env when we have a real key
# (not the dummy) - writing "jarvis-local" over a brain_keys.json
# entry would re-introduce the auth-fail regression.
os.environ["LOCAL_API_BASE"] = LOCAL_API_BASE
if LOCAL_API_KEY and LOCAL_API_KEY != _LOCAL_PLACEHOLDER_KEY:
    os.environ["LOCAL_API_KEY"] = LOCAL_API_KEY
if _S_MODEL:
    os.environ.setdefault("LOCAL_MODEL", LOCAL_MODEL)

# Carry a saved agent rename across CLI restarts. The GUI sets
# HEARTH_PERSONA_NAME from settings.agent_name at boot (web.py); the CLI was
# missing it, so `/name Foo` reset to JARVIS on the next launch. An explicit
# HEARTH_PERSONA_NAME env still wins. persona.NAME is read at call time
# (system_prompt + boot panel), so setting it here propagates everywhere.
if not os.getenv("HEARTH_PERSONA_NAME"):
    try:
        with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as _af:
            _saved_agent = (json.load(_af).get("agent_name") or "").strip()
        if _saved_agent:
            os.environ["HEARTH_PERSONA_NAME"] = _saved_agent
            import hearth.persona as _pmod
            _pmod.NAME = _saved_agent
    except Exception:
        pass
HISTORY_FILE = os.path.join(WORKSPACE, "logs", "jarvis_history.json")
# Append-only, never-pruned transcript of the CLI. jarvis_history.json doubles
# as the working context and gets pruned/compacted (and overwritten) to fit the
# model window - which silently destroyed old turns. This file preserves the
# full back-and-forth for search_chats / recall, independent of context limits.
CLI_TRANSCRIPT = os.path.join(WORKSPACE, "logs", "cli_transcript.jsonl")
# Sentinel the input-read returns when a BACKGROUND subagent finished while we
# were waiting for the user to type - the loop then auto-continues the task
# instead of sitting idle until the user hits enter. Null bytes so it can never
# collide with real typed input.
_AUTO_CONTINUE = "\x00__hearth_auto_continue__\x00"
# Persistent per-tool permissions ([a]lways / [N]ever) so you don't have to
# re-approve browse_click/run_command/etc on every restart. Stored next to
# memory in the workspace so it survives across CLI/GUI/bridge sessions.
PERMS_FILE = os.path.join(WORKSPACE, "permissions.json")


def _load_persisted_perms() -> Dict[str, str]:
    try:
        with open(PERMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if v in ("always", "never")}
    except (OSError, ValueError):
        return {}


def _save_persisted_perms(perms: Dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(PERMS_FILE), exist_ok=True)
        with open(PERMS_FILE, "w", encoding="utf-8") as f:
            json.dump(perms, f, indent=2)
    except OSError:
        pass
USE_LMSTUDIO_HISTORY = _env.get("LMSTUDIO_HISTORY", "0") == "1"
# Match this to the model you've loaded in LM Studio. Conservative default.
CONTEXT_TOKENS = int(_env.get("CONTEXT", "8192"))
RESERVED_OUTPUT = int(_env.get("RESERVED_OUTPUT", "2048"))
# Compact when conversation hits this fraction of context. 0.75 = compact at
# 75% of window, leaving room for the next turn.
COMPACT_AT = float(_env.get("COMPACT_AT", "0.75"))
# Below this, Hearth's own prompt (persona ~11k + tool schemas ~8k = ~20k)
# barely fits and there's no room left for conversation, so the model overflows
# almost immediately. Used to floor Hearth's builtin server and to warn when a
# user pins something too small.
MIN_USABLE_CTX = int(_env.get("MIN_CTX", "24576"))  # 24K
# (Tool-loop control + malformed-markup stripping now live in
# hearth/loop_guard.py - outcome-hash based, not a magic per-tool count.)


from hearth.updater import HEARTH_VERSION  # one source of truth; see updater.py
HEARTH_REPO = "https://github.com/0pen-sourcer/hearth"


def _tools_banner_label() -> str:
    """Banner tools line: show what's ACTUALLY in the prompt vs total. Saying
    '105 loaded' read like bloat when only ~50 ship per turn (the rest load on
    demand via load_tools)."""
    total = len(TOOL_DEFINITIONS)
    try:
        from hearth.tools import active_tool_count
        active = active_tool_count()
    except Exception:
        active = total
    if active >= total:
        return f"{total} loaded"
    return f"{active} active, {total} available on demand"


def _is_local_endpoint(base: str) -> bool:
    """True for LM Studio / Ollama / vLLM running on this machine or LAN.
    Used to decide which request quirks apply (chat_template_kwargs is
    local-only; cloud endpoints want images as a user turn, not a tool msg)."""
    b = (base or "").lower()
    return any(h in b for h in (
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
        "192.168.", "10.", "host.docker.internal",
    ))


def _is_reasoning_param_error(e: Exception) -> bool:
    """True when an API error looks like the model rejecting `reasoning_effort`
    (unsupported field / invalid value), so the caller can retry without it.
    Matches on the param name or a 400/invalid-request mentioning reasoning."""
    msg = str(getattr(e, "message", "") or e).lower()
    status = getattr(e, "status_code", None)
    if "reasoning_effort" in msg or "reasoning effort" in msg:
        return True
    if (status == 400 or "invalid" in msg or "unsupported" in msg
            or "unknown" in msg or "does not support" in msg) and "reasoning" in msg:
        return True
    return False


VOICE_ON = _env.get("VOICE_ON", "0") == "1"
# Opt-in: also mirror conversation into LM Studio's threads folder so the
# chat shows up in LM Studio's chat list. Writes to a dedicated file
# (jarvis_cli.conversation.json) - does NOT touch your other threads.
LMSTUDIO_SYNC = _env.get("LMSTUDIO_SYNC", "0") == "1"
LMSTUDIO_SYNC_PATH = os.path.expanduser(
    r"~/.lmstudio/conversations/jarvis_cli.conversation.json"
)

# ---- ANSI palette ---------------------------------------------------------
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
# One palette for the terminal, in the shades of the window's look.
from hearth.term_render import GRAD  # noqa: E402

C_BRAND = GRAD[3]
C_USER = "\033[1;97m"
BOLD_ON = "\033[1m"
C_BOT = "\033[38;5;253m"     # the reply's words: plain light grey
C_TOOL = GRAD[4]
C_OK = GRAD[4]
C_DIM = "\033[38;5;240m"
C_FRAME = GRAD[1]
C_ERR = "\033[1;31m"
C_WARN = GRAD[5]
C_ACCENT = C_BRAND
_BOLD = "\033[1m"
C_ADD = "\033[38;5;114m"   # green — added line
C_DEL = "\033[38;5;210m"   # red — removed line
C_META = "\033[38;5;246m"  # muted grey — arg values / result body (reads under the name)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _vis_len(s: str) -> int:
    """Visible length, ignoring ANSI color codes (for box padding)."""
    return len(_ANSI_RE.sub("", s))


def _render_update_box(current: str, latest: str, blurb: str = "") -> str:
    """A rounded, framed 'update available' card for /update and the boot notice."""
    import textwrap
    W = 50  # inner content width
    def _row(content: str = "") -> str:
        pad = max(0, W - _vis_len(content))
        return f"{C_FRAME}│{C_RESET}  {content}{' ' * pad}  {C_FRAME}│{C_RESET}"
    title = f"{C_ACCENT}✦{C_RESET} {_BOLD}Update available{C_RESET}"
    tlen = _vis_len(title)
    top = f"{C_FRAME}╭─ {C_RESET}{title}{C_FRAME} {'─' * (W - tlen - 1)}╮{C_RESET}"
    bot = f"{C_FRAME}╰{'─' * (W + 4)}╯{C_RESET}"
    rows = [top, _row(), _row(f"{C_DIM}v{current}{C_RESET}   {C_FRAME}→{C_RESET}   {C_OK}{_BOLD}{latest}{C_RESET}")]
    if blurb:
        rows.append(_row())
        for w in textwrap.wrap(blurb, W - 1):
            rows.append(_row(f"{C_DIM}{w}{C_RESET}"))
    rows.append(_row())
    rows.append(bot)
    return "\n".join(rows)


def _cli_show_diffs() -> bool:
    """Honor the shared show_diffs toggle (settings.json), default on — same
    switch the GUI uses, so turning file-change diffs off applies to the CLI too."""
    try:
        import json as _json
        with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as _f:
            return _json.load(_f).get("show_diffs") is not False
    except Exception:
        return True


def _print_cli_diff(name: str, args: dict) -> bool:
    """Print a green/red diff for edit_file / write_file instead of raw JSON args.
    Trims the common head/tail so only the changed region shows. Returns True if
    it printed a diff."""
    def _emit(old, new):
        oldL = str(old or "").split("\n"); newL = str(new or "").split("\n")
        s = 0
        while s < len(oldL) and s < len(newL) and oldL[s] == newL[s]:
            s += 1
        e = 0
        while e < len(oldL) - s and e < len(newL) - s and oldL[-1 - e] == newL[-1 - e]:
            e += 1
        for l in oldL[max(0, s - 2):s]:
            print(f"    {C_DIM}  {l}{C_RESET}")
        for l in oldL[s:len(oldL) - e]:
            print(f"    {C_DEL}- {l}{C_RESET}")
        for l in newL[s:len(newL) - e]:
            print(f"    {C_ADD}+ {l}{C_RESET}")
        for l in oldL[len(oldL) - e:len(oldL) - e + 2]:
            print(f"    {C_DIM}  {l}{C_RESET}")
    if name == "write_file":
        lines = str(args.get("content", "")).split("\n")
        for l in lines[:60]:
            print(f"    {C_ADD}+ {l}{C_RESET}")
        if len(lines) > 60:
            print(f"    {C_DIM}… +{len(lines) - 60} more lines{C_RESET}")
        return True
    edits = args.get("edits") if isinstance(args.get("edits"), list) else []
    if not edits:
        return False
    for i, ed in enumerate(edits):
        if len(edits) > 1:
            print(f"    {C_DIM}edit {i + 1}/{len(edits)}{C_RESET}")
        _emit(ed.get("old_text"), ed.get("new_text"))
    return True


# ---- tool-call rendering --------------------------------------------------
# A single monochrome glyph per tool family so a run reads at a glance without a
# rainbow of color. The glyph carries "what kind of action this is"; the color
# stays restrained (violet glyph, bold name, muted args + result body) so a
# transcript looks like a designed CLI, not a debug log. One accent (violet) +
# green/rose only for the ok/error result head — nothing else competes.
_TOOL_GLYPH = {
    "read": "◇", "edit": "◆", "run": "❯", "web": "◈",
    "memory": "❖", "screen": "⧉", "media": "✦", "comms": "✉",
}


def _tool_family(name: str) -> str:
    """Classify a tool into a display family by name KEYWORDS, not a hardcoded
    list of tool names. This deliberately enumerates NO specific tools — so a
    user's own plugins/custom tools are never named in shipped code and still
    classify (or fall back to the neutral dot) automatically."""
    n = (name or "").lower()

    def has(*ks):
        return any(k in n for k in ks)

    if n == "search_chats" or has("memory", "recall", "worklog"):
        return "memory"
    if has("browse", "web_", "fetch", "open_url", "open_in_browser"):
        return "web"
    if has("screenshot", "view_image", "capture", "computer", "desktop",
           "smart_click", "focus_window", "manage_window", "vision", "mouse"):
        return "screen"
    if has("image", "video", "voice", "speak", "tts", "forge", "generat"):
        return "media"
    if has("email", "inbox", "notify", "phone", "reminder", "discord",
           "telegram", "ask_user", "send"):
        return "comms"
    if n.startswith("set_") or has("write", "edit", "create", "delete", "move",
                                   "append", "install", "draft"):
        return "edit"
    if has("run_", "start", "job_kill", "spawn", "launch", "open_app",
           "end_session", "learn_env", "shutdown"):
        return "run"
    if has("read", "list", "find", "locate", "glob", "grep", "get_", "disk",
           "info", "status", "whoami", "time", "load_skill", "vibe", "clipboard_read"):
        return "read"
    return ""


def _note_activity(state: str, tool: str = "") -> None:
    """Tell the desktop window what JARVIS is doing here, for its room."""
    try:
        from hearth import activity
        activity.note("terminal", state, tool)
    except Exception:
        pass


def _tool_glyph(name: str) -> str:
    return _TOOL_GLYPH.get(_tool_family(name), "●")


def _model_name_short(model: str) -> str:
    """Short display name for a model id — the filename for a local GGUF path,
    else the id as-is. Module-level twin of JarvisCLI._model_label, so the
    brain-sync and status lines can format a model they're handed."""
    m = model or ""
    if ("\\" in m or "/" in m) and m.lower().endswith(".gguf"):
        return os.path.basename(m)
    return m


# Args most worth seeing first — a preview should say what the call is ABOUT
# (which file / which query / which command), not the first key alphabetically.
_ARG_PRIORITY = ("path", "command", "cmd", "query", "url", "pattern", "name",
                 "text", "prompt", "app", "message", "to", "title")


def _tool_arg_summary(args, max_val: int = 48, max_total: int = 96) -> str:
    """Compact, colored one-line arg preview for a tool card. Whitespace is
    collapsed, long values elided with '…', private/control keys ('_approved')
    dropped, and the most descriptive args are shown first. Returns text with
    ANSI already applied (keys dim, values muted); '' when there's nothing to
    show. Never dumps a 600-char blob or raw `\\n` soup onto one line."""
    if not isinstance(args, dict):
        sv = re.sub(r"\s+", " ", str(args)).strip()
        if not sv:
            return ""
        if len(sv) > max_total:
            sv = sv[:max_total] + "…"
        return f"{C_META}{sv}{C_RESET}"
    keys = [k for k in args.keys() if not str(k).startswith("_")]
    keys.sort(key=lambda k: (_ARG_PRIORITY.index(k) if k in _ARG_PRIORITY
                             else len(_ARG_PRIORITY), list(args).index(k)))
    out, used, hidden = [], 0, 0
    for i, k in enumerate(keys):
        v = args[k]
        sv = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        sv = re.sub(r"\s+", " ", sv).strip()
        if len(sv) > max_val:
            sv = sv[:max_val] + "…"
        seg = len(str(k)) + 1 + len(sv)
        if used and used + seg + 2 > max_total:
            hidden = len(keys) - i
            break
        out.append(f"{C_DIM}{k}={C_RESET}{C_META}{sv}{C_RESET}")
        used += seg + 2
    s = f"{C_DIM}, {C_RESET}".join(out)
    if hidden:
        s += f" {C_DIM}+{hidden} more{C_RESET}"
    return s


def _tool_result_meta(result: str, dt: float) -> str:
    """A tiny '1.2k · 3L · 42ms' footer for a tool result — size, line count,
    wall time — so big output is quantified without being dumped."""
    n = len(result)
    size = f"{n / 1000:.1f}k" if n >= 1000 else f"{n}c"
    nl = result.count("\n")
    bits = [size]
    if nl:
        bits.append(f"{nl + 1}L")
    bits.append(f"{dt:.0f}ms")
    return " · ".join(bits)


def _render_tool_result(display, result, dt: float) -> None:
    """Render a tool result under its card: a green/rose head line, a compact
    meta footer, and (for multi-line output) a bounded 3-line peek at the body
    so a 500-line read_file informs without flooding the transcript. `display`
    is a pre-built synthetic label (declined/skipped); when set we trust it and
    skip the real-result parsing."""
    if display is not None:
        print(f"  {C_DIM}└ {display} · {dt:.0f}ms{C_RESET}")
        return
    text = result if isinstance(result, str) else str(result)
    nonempty = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    head = nonempty[0].strip() if nonempty else "(no output)"
    low = head.lower()
    is_err = (low.startswith("error") or low.startswith("failed")
              or "could not" in low or "traceback" in low or low.startswith("denied"))
    color = C_WARN if is_err else C_OK
    if len(head) > 100:
        head = head[:100] + "…"
    print(f"  {C_DIM}└{C_RESET} {color}{head}{C_RESET}  "
          f"{C_DIM}· {_tool_result_meta(text, dt)}{C_RESET}")
    body = nonempty[1:]
    for ln in body[:3]:
        ln = ln.strip()
        if len(ln) > 100:
            ln = ln[:100] + "…"
        print(f"    {C_DIM}{ln}{C_RESET}")
    if len(body) > 3:
        print(f"    {C_DIM}… +{len(body) - 3} more lines{C_RESET}")


# Terminal markdown rendering. We stream raw tokens live (fast), then re-render
# the finished message once IF it contains markdown worth formatting (a table,
# code fence, or heading) — so a table no longer prints as raw `| ... |` pipes.
# Plain replies skip the re-render entirely, so they're untouched + instant.
# Same idea as the GUI's terminal renderer.
try:
    from rich.console import Console as _RichConsole
    from rich.markdown import Markdown as _RichMarkdown
    from rich.theme import Theme as _RichTheme
    # Match rendered markdown to Hearth's palette (violet 141 / lavender 183 /
    # gold 220) instead of rich's default cyan/green — so tables, headings, and
    # links read as Hearth, not a generic terminal.
    _HEARTH_MD_THEME = _RichTheme({
        "markdown.h1": "bold color(141)", "markdown.h1.border": "color(141)",
        "markdown.h2": "bold color(141)", "markdown.h3": "bold color(183)",
        "markdown.h4": "color(183)", "markdown.h5": "color(183)",
        "markdown.h6": "color(183)",
        "markdown.link": "color(141)", "markdown.link_url": "color(141)",
        "markdown.item.bullet": "color(141)", "markdown.item.number": "color(141)",
        "markdown.code": "color(183)", "markdown.block_quote": "color(146)",
        "markdown.hr": "color(240)",
        "table.header": "bold color(141)", "table.title": "color(183)",
    })
    _RICH = _RichConsole(force_terminal=True, soft_wrap=False, theme=_HEARTH_MD_THEME)
except Exception:
    _RICH = None
_MD_SIGNAL = re.compile(
    r"(^|\n)[ \t]*\|.+\|[ \t]*\n[ \t]*\|?[ \t:|-]+\|"   # GFM table
    r"|```"                                              # code fence
    r"|(^|\n)#{1,6}[ \t]+\S"                             # heading
    r"|\*\*\S",                                          # bold
    re.M)
def _has_md(t: str) -> bool:
    return bool(_RICH and t and _MD_SIGNAL.search(t))

_LOW_LATENCY_DIRECTIVE = (
    "\n\n# LOW-LATENCY MODE (active)\n"
    "Reasoning is OFF. Do NOT output `<think>` or `<thinking>` blocks.\n"
    "Skip internal deliberation. Answer DIRECTLY in plain text.\n"
    "If the question needs careful thought, think SILENTLY - never emit it.\n"
)


# When voice is on, replies are SPOKEN. Long paragraphs, file paths, URLs, code
# and markdown sound terrible read aloud and add latency. This flips the model
# into a terse, conversational assistant register for the duration.
_VOICE_MODE_DIRECTIVE = (
    "\n\n# VOICE MODE - your reply will be SPOKEN ALOUD.\n"
    "Talk like a sharp, familiar assistant, not a chatbot reading an essay:\n"
    "- ONE or TWO short sentences. No paragraphs, no bullet lists, no headers.\n"
    "- NEVER speak file paths, URLs, hashes, code, or markdown - they sound awful. "
    "Say 'saved it to your workspace', not the full path. Say 'opened the trailer', "
    "not the link.\n"
    "- If there's a lot of detail, give the one-line headline and OFFER the rest "
    "('want the specifics?') instead of dumping it.\n"
    "- Be fast and natural. Brevity is the whole point here."
)


# So the model KNOWS its reasoning is on (it kept saying "off" when asked,
# because only the OFF state was ever stated explicitly).
_REASONING_ON_DIRECTIVE = (
    "\n\n# REASONING MODE (active)\n"
    "Reasoning is ON right now: you may think step-by-step before answering, and "
    "your thinking is shown inline to the user. If they ask, your reasoning IS on.\n"
)
# Tell the model whether it can actually SEE, so it stops guessing about images.
_HAS_VISION_DIRECTIVE = (
    "\n\n# VISION (available)\n"
    "The loaded model is vision-capable — you CAN look at images. Call view_image "
    "on a screenshot/photo path to actually see it before describing it.\n"
)
_NO_VISION_DIRECTIVE = (
    "\n\n# VISION (none)\n"
    "The loaded model is text-only — it CANNOT see images. Don't pretend to; if "
    "the user needs a picture understood, say this model can't see and offer to "
    "switch to a vision-capable model.\n"
)


def fresh_system_message(think_on: bool = False, voice_on: bool = False,
                         vision: Optional[bool] = None) -> Dict:
    """Rebuilt every turn so rules.md edits + new memory entries take effect.
    Always states the reasoning mode (ON or OFF) so the model knows its own
    state. voice_on flips to a terse spoken register. `vision` (True/False/None)
    tells the model whether it can actually view images."""
    text = system_prompt()
    text += _REASONING_ON_DIRECTIVE if think_on else _LOW_LATENCY_DIRECTIVE
    if voice_on:
        text += _VOICE_MODE_DIRECTIVE
    if vision is True:
        text += _HAS_VISION_DIRECTIVE
    elif vision is False:
        text += _NO_VISION_DIRECTIVE
    return {"role": "system", "content": text}


def _prune_old_images(messages: List[Dict], keep_last: int = 1) -> int:
    """Replace base64 image_url blocks in OLD messages with a tiny text
    placeholder. Each screenshot/photo is ~300KB of base64 that otherwise rides
    in the context (and the saved history) on EVERY future turn — huge token
    waste, and vision models only need the most recent image anyway. Keeps the
    last `keep_last` images intact, strips the rest in place. Returns count
    stripped."""
    idxs = [i for i, m in enumerate(messages)
            if isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "image_url"
                    for b in m["content"])]
    if len(idxs) <= keep_last:
        return 0
    strip = set(idxs[:-keep_last] if keep_last > 0 else idxs)
    n = 0
    for i in strip:
        new = []
        for b in messages[i]["content"]:
            if isinstance(b, dict) and b.get("type") == "image_url":
                new.append({"type": "text", "text": "[earlier image dropped to save context — its file path is in the view_image result above; call view_image on it again if you need to see it]"})
                n += 1
            else:
                new.append(b)
        messages[i]["content"] = new
    return n


from hearth.tools import is_risky as _is_risky  # noqa: E402

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _sanitize(text: str) -> str:
    """Strip lone surrogate code points that crash UTF-8 encoders.
    Windows console / clipboard sometimes emits these for some emojis."""
    if not text:
        return text
    return text.encode("utf-8", "replace").decode("utf-8", "replace")


def autodetect_model() -> str:
    """Query the local server's /v1/models and pick the first non-embedding
    chat model. Falls back to LOCAL_MODEL if anything fails."""
    try:
        from hearth.headless import model_list_req
        _req = model_list_req(LOCAL_API_BASE, LOCAL_API_KEY or "hearth-builtin")
        with urllib.request.urlopen(_req, timeout=2) as r:
            data = json.loads(r.read().decode())
        for m in data.get("data", []):
            mid = m.get("id", "")
            if "embed" in mid.lower():
                continue
            return mid
    except Exception:
        pass
    return LOCAL_MODEL


def autodetect_context(model_id: str) -> Optional[int]:
    """Ask LM Studio for the loaded context length of the active model.

    LM Studio exposes two endpoints with different richness:
      1. /v1/models      - OpenAI-compatible, sometimes thin
      2. /api/v0/models  - native, almost always has loaded_context_length

    We probe both. Falls back to /api/v0/models/{id} (single-model detail)
    if the list endpoint omits the field. Returns None on total miss.
    """
    candidates = (
        f"{LOCAL_API_BASE}/models",
        # /api/v0/models lives at the SAME host but a different prefix.
        # Strip /v1 from LOCAL_API_BASE to derive the native API root.
        LOCAL_API_BASE.replace("/v1", "/api/v0") + "/models",
    )
    # Prefer the ACTUALLY-loaded context; drop max_position_embeddings (the model's
    # trained ceiling, e.g. 200K on a "1M" GGUF) — trusting it over-packs the prompt
    # into a server that only loaded a fraction of it.
    fields = ("loaded_context_length", "n_ctx",
              "max_context_length", "context_length")
    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode())
        except Exception:
            continue
        for m in data.get("data", []):
            if m.get("id") != model_id:
                continue
            for key in fields:
                v = m.get(key)
                if isinstance(v, int) and v > 0:
                    return v
    # Last try: per-model detail endpoint on the native API
    try:
        detail_url = LOCAL_API_BASE.replace("/v1", "/api/v0") + f"/models/{model_id}"
        with urllib.request.urlopen(detail_url, timeout=2) as r:
            m = json.loads(r.read().decode())
        for key in fields:
            v = m.get(key)
            if isinstance(v, int) and v > 0:
                return v
    except Exception:
        pass
    return None


class JarvisCLI:
    def __init__(self):
        self.openai_tools = to_openai_tools()
        self.client = _anthropic_chat.async_client(LOCAL_API_BASE, LOCAL_API_KEY, _stream_client_timeout())
        # Auto-detect unless the user explicitly overrode via env
        self.current_model = LOCAL_MODEL if os.getenv("LOCAL_MODEL") else autodetect_model()
        # Whether the user pinned context via env / /context. If they did,
        # we don't auto-override when they /model switch.
        self._context_pinned = _env.get("CONTEXT") is not None
        self.messages: List[Dict] = []
        self.all_chats_cache: List[Dict] = []
        self.active_chat_file: str = ""
        self.voice_on = VOICE_ON
        self.last_model_list: List[str] = []
        # Live context size - auto-detected from LM Studio if not pinned
        self.context_tokens = CONTEXT_TOKENS
        # Continuous voice-input listener (faster-whisper). When True, the
        # main loop races typed input against STT transcripts via this queue.
        # Listener thread also stops in-flight TTS when speech is detected,
        # giving the user a true interrupt experience.
        self.listen_continuous = False
        self._stt_queue: asyncio.Queue = asyncio.Queue()
        self._stt_loop: Optional[asyncio.AbstractEventLoop] = None
        # Cancel signal raised when the user wants to abort the in-flight
        # response (Esc / Ctrl-C during streaming). The respond() loop
        # checks it between stream chunks + between tool calls; subagents
        # check it via set_parent_cancel_check. Set with .set(), cleared
        # at the start of each new user turn.
        self._respond_cancel = threading.Event()
        # Ctrl-C handling: during a response, set the cancel flag (the stream
        # loop bails cleanly) instead of raising KeyboardInterrupt - a raise
        # escapes asyncio.run and kills the whole app (the "Ctrl-C closes
        # Hearth" bug). At the prompt (not responding), behave normally so the
        # user can still interrupt/exit. prompt_toolkit owns SIGINT while it's
        # reading input and restores ours after, so the two don't clash.
        self._responding = False

        def _on_sigint(_sig, _frm):
            if getattr(self, "_responding", False):
                self._respond_cancel.set()
            else:
                raise KeyboardInterrupt
        try:
            import signal as _signal
            _signal.signal(_signal.SIGINT, _on_sigint)
        except Exception:
            pass
        if not self._context_pinned:
            # Single source of truth - same helper the GUI/bridge use. Has
            # per-provider fallback (Grok 200K, Gemini 200K, Claude 200K,
            # GPT-4o 128K, etc.) for cloud endpoints that don't expose
            # loaded_context_length via /v1/models. Bare autodetect_context
            # alone returns None for cloud and CLI would silently use 8K.
            try:
                from hearth.headless import resolve_context_tokens
                tokens, _src = resolve_context_tokens(self.current_model)
                if tokens and tokens > 1024:
                    self.context_tokens = tokens
            except Exception:
                detected = autodetect_context(self.current_model)
                if detected and detected > 1024:
                    self.context_tokens = detected
        # Single-line by default: Enter = send (the normal, expected behavior).
        # Esc+Enter inserts a newline for the occasional multi-line message.
        # Multi-line paste arrives as one block via bracketed paste — enabled on
        # Windows by _enable_windows_vt_input(), native on Linux/macOS — so a
        # pasted block no longer splits into line-by-line sends. /multi stays as
        # a manual toggle for typing a multi-line message by hand.
        self.multiline_mode = False  # toggled with /multi
        self._turn = None              # the turn on screen, while one runs
        self.show_thinking = False     # ctrl+t: thinking as it comes, or folded
        self._session_use = {}         # tokens and cost this session, for the footer
        self._last_thinking = ""
        # ONE thinking toggle:
        #   /think on  → model reasons AND we show the body inline
        #   /think off → model skips reasoning entirely (no compute spent)
        # Default off. Set HEARTH_THINK=1 to start on.
        self.think_on = _env.get("THINK", "0") == "1"
        # Cloud reasoning models that 400 on `reasoning_effort` (e.g. plain
        # grok-4, grok-3 - only grok-3-mini / grok-4.3+ expose it). Populated
        # lazily the first time a model rejects the param, so we send it once,
        # learn, and never pay a failed round-trip for that model again.
        self._no_reasoning_effort: set[str] = set()
        # Most-recent image path the user/screenshot tool produced. Used
        # to auto-attach when the user references "the/that image".
        self.last_image_path: str = ""
        # Per-tool permissions ([a]lways / [N]ever) - loaded from disk so they
        # survive restarts. Mutating choices write back via _save_persisted_perms.
        self.tool_perms: Dict[str, str] = _load_persisted_perms()
        # /sleep mode - the assistant stays silent until the user says the wake word.
        # Anything else is ignored. Use /wake to come back out.
        self.sleep_mode = False
        self._sleep_wake_word = (_env.get("WAKE_WORD", "jarvis")
                                 .strip().lower())
        self.auto_approve = _env.get("AUTO_APPROVE", "0") == "1"
        # Lazy-init: prompt_toolkit needs a real terminal to construct a
        # session; we delay that until first input read so importing /
        # smoke-testing the module doesn't fail in a pipe.
        self.pt_session = None
        self._pt_init_attempted = False
        self.load_history()
        self._chat_resume()
        if self._side:
            print(f"{C_DIM}Another terminal is on your last chat, so this one starts a new chat. /chats lists them all.{C_RESET}")

    # The terminal's chat is also a chat file the window lists (hearth.chat_sync):
    # id = that file, count = turns it held when last read or written here,
    # sent = how many of self.messages are in it already, pid = the terminal on it.
    _CHAT_STATE = os.path.join(WORKSPACE, "logs", "terminal_chat.json")

    def _chat_resume(self) -> None:
        try:
            with open(self._CHAT_STATE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        # Another terminal still open on the last chat keeps it; this one starts its
        # own, so the two never write over each other's history.
        held = int(st.get("pid") or 0)
        from hearth.bridge_status import _alive
        self._side = bool(held and held != os.getpid() and _alive(held))
        if self._side:
            self.messages = [m for m in self.messages[:1] if m.get("role") == "system"]
            self._chat = {"id": None, "count": 0, "sent": len(self.messages)}
            return
        self._chat = {"id": st.get("id"), "count": int(st.get("count") or 0),
                      "sent": min(int(st.get("sent") or 0), len(self.messages)) if st.get("id") else len(self.messages)}
        self._chat_keep()

    def _chat_keep(self) -> None:
        if getattr(self, "_side", False):
            return
        self._chat["pid"] = os.getpid()
        try:
            os.makedirs(os.path.dirname(self._CHAT_STATE), exist_ok=True)
            with open(self._CHAT_STATE, "w", encoding="utf-8") as f:
                json.dump(self._chat, f)
        except OSError:
            pass

    def _chat_new(self) -> None:
        self._chat = {"id": None, "count": 0, "sent": len(self.messages)}
        self._chat_keep()

    def _chat_pull(self) -> None:
        """Turns taken in the window on this chat since the terminal last looked."""
        from hearth import chat_sync
        ch = getattr(self, "_chat", None)
        if not ch or not ch.get("id"):
            return
        new = chat_sync.newer(ch["id"], ch["count"])
        if new:
            self.messages.extend(chat_sync.from_chat(new))
            ch["count"] += len(new)
            ch["sent"] = len(self.messages)
            self._chat_keep()

    def _chat_push(self) -> None:
        """The terminal's new turns, added to its chat file."""
        from hearth import chat_sync
        ch = getattr(self, "_chat", None)
        if ch is None:
            return
        if len(self.messages) < ch["sent"]:          # summed up or trimmed: nothing new to add
            ch["sent"] = len(self.messages)
        fresh = self.messages[ch["sent"]:]
        if not any(m.get("role") == "user" for m in fresh) and not ch.get("id"):
            return
        new = not ch.get("id")
        if new:
            ch["id"] = chat_sync.new_id()
        ch["count"] = chat_sync.append(ch["id"], fresh)
        if new:
            # A real title, asked of the brain the way the window asks, off the main loop.
            opening = " ".join(str(m.get("content") or "") for m in fresh
                               if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str))
            model, cid = getattr(self, "current_model", ""), ch["id"]

            def _name():
                from hearth import headless as _hl
                chat_sync.retitle(cid, chat_sync.title_for(opening, lambda p: _hl.one_shot(p, model)))
            threading.Thread(target=_name, daemon=True).start()
        ch["sent"] = len(self.messages)
        self._chat_keep()

    def _ensure_pt_session(self) -> None:
        if self._pt_init_attempted or not _PT_AVAILABLE:
            return
        self._pt_init_attempted = True
        _enable_windows_vt_input()   # so a pasted multi-line block arrives whole
        _shift_enter_is_newline()
        try:
            hist_path = os.path.join(WORKSPACE, "logs", "input_history.txt")
            kb = KeyBindings()
            cli_self = self  # capture for keybinding closures

            @kb.add("escape", "enter")
            def _esc_enter(event):
                """Esc+Enter: submit in multi-line, insert newline in single."""
                if cli_self.multiline_mode:
                    event.current_buffer.validate_and_handle()
                else:
                    event.current_buffer.insert_text("\n")

            @kb.add("enter")
            def _plain_enter(event):
                """Enter: submit in single-line. In multi-line, normally
                insert newline - but submit if the buffer is a single-line
                slash command so /multi (and other slash commands) still
                work to toggle the mode."""
                buf = event.current_buffer
                text = buf.text
                if not cli_self.multiline_mode:
                    buf.validate_and_handle()
                    return
                if "\n" not in text and text.lstrip().startswith("/"):
                    buf.validate_and_handle()
                else:
                    buf.insert_text("\n")

            @kb.add(Keys.BracketedPaste)
            def _paste(event):
                data = event.data.replace("\r\n", "\n").replace("\r", "\n")
                data = unwrap_terminal(data, shutil.get_terminal_size().columns)
                buf = event.current_buffer
                mark = PASTES.marker_of(buf.text, data)
                if mark:
                    at = buf.text.index(mark)
                    buf.text = buf.text[:at] + data + buf.text[at + len(mark):]
                    buf.cursor_position = at + len(data)
                else:
                    buf.insert_text(PASTES.marker_for(data) or data)

            # Completion as you type, for a line that starts with "/": the
            # commands with what each does, then what each one takes (/brain
            # offers the providers and agents, then that provider's models;
            # /model offers the last list shown). Prose is left alone.
            try:
                from prompt_toolkit.completion import Completer, Completion
                cli = self

                class _SlashCompleter(Completer):
                    def get_completions(self, document, complete_event):
                        text = document.text_before_cursor
                        if not text.startswith("/") or "\n" in text:
                            return
                        parts = text.split(" ")
                        word = parts[-1]
                        if len(parts) == 1:
                            for c, d in SLASH_COMMANDS.items():
                                if c.startswith(word.lower()):
                                    yield Completion(c, -len(word), display_meta=d)
                            return
                        for c, d in cli._arg_choices(parts[0].lower(), parts[1:-1]):
                            if c.lower().startswith(word.lower()):
                                yield Completion(c, -len(word), display_meta=d)

                _completer = _SlashCompleter()
            except Exception:
                _completer = None
            @kb.add("escape", eager=True, filter=Condition(lambda: cli_self._turn is not None))
            def _esc_stop(event):
                """Esc while a reply runs: stop it now."""
                cli_self._respond_cancel.set()

            @kb.add("c-t")
            def _toggle_thinking(event):
                """Ctrl+T: show thinking as it comes, or fold it to one line."""
                cli_self.show_thinking = not cli_self.show_thinking
                if cli_self._turn is not None:
                    cli_self._turn.show_thinking = cli_self.show_thinking

            self.pt_session = PromptSession(
                history=SafeFileHistory(hist_path),
                key_bindings=kb,
                enable_history_search=True,
                mouse_support=False,
                completer=_completer,
                # Only a command line offers completions, so prose doesn't open a menu.
                complete_while_typing=Condition(lambda: cli_self.pt_session is not None
                                                and cli_self.pt_session.default_buffer.text.startswith("/")),
                reserve_space_for_menu=6,
                style=Style.from_dict({"bottom-toolbar": "noreverse #7a7a7a"}),
                erase_when_done=True,
                refresh_interval=0.15,
                bottom_toolbar=self._footer,
            )
            session = self.pt_session
            # The input doesn't stretch, so the footer sits right under it.
            try:
                from prompt_toolkit.filters import to_filter
                session.layout.current_window.dont_extend_height = to_filter(True)
            except Exception:
                pass
        except Exception:
            # No real console (piped, redirected, etc.) - fall back to input()
            self.pt_session = None

    # -- LM Studio thread bridge --------------------------------------------
    def _lmstudio_dir(self) -> str:
        return os.path.expanduser(r"~/.lmstudio/conversations")

    def get_all_lmstudio_chats(self) -> List[Dict]:
        d = self._lmstudio_dir()
        if not os.path.exists(d):
            return []
        chats = []
        for fn in os.listdir(d):
            if not fn.endswith(".conversation.json"):
                continue
            full = os.path.join(d, fn)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chats.append({
                    "path": full,
                    "name": data.get("name", "Untitled"),
                    "updated": os.path.getmtime(full),
                })
            except Exception:
                continue
        return sorted(chats, key=lambda c: c["updated"], reverse=True)

    def load_history(self, filepath: str = ""):
        """Load conversation history.
        - filepath empty → load local jarvis_history.json
        - filepath ends with .conversation.json → READ ONLY copy into our
          local store. We never write back to LM Studio's files.
        """
        # Always save target = our local file. Never LM Studio's.
        self.active_chat_file = HISTORY_FILE
        self.messages = []

        if filepath and filepath.endswith(".conversation.json") and os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for msg in data.get("messages", []):
                    versions = msg.get("versions", [])
                    if not versions:
                        continue
                    sel = msg.get("currentlySelected", 0)
                    v = versions[sel] if 0 <= sel < len(versions) else versions[0]
                    role = v.get("role") or msg.get("role")
                    blocks = v.get("content", [])
                    text = ""
                    if isinstance(blocks, list):
                        for b in blocks:
                            if isinstance(b, dict) and b.get("type") == "text":
                                text += b.get("text", "")
                    elif isinstance(blocks, str):
                        text = blocks
                    if text and role in ("user", "assistant"):
                        self.messages.append({"role": role, "content": text})
            except Exception as e:
                print(f"{C_ERR}Could not parse LM Studio thread: {e}{C_RESET}")
        elif (filepath or HISTORY_FILE) and os.path.exists(filepath or HISTORY_FILE):
            try:
                with open(filepath or HISTORY_FILE, "r", encoding="utf-8") as f:
                    self.messages = json.load(f)
            except Exception:
                self.messages = []

        # Seed the transcript-dedup set from whatever we just loaded so those
        # turns (already on disk in the transcript from prior sessions) don't get
        # re-appended. Only genuinely-new turns this session get written.
        self._transcript_seen = {
            self._transcript_key(m) for m in self.messages
            if m.get("role") in ("user", "assistant")
        }

        # Always rebuild the system message so rules.md + memory index are live.
        to = getattr(self, "think_on", False)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = fresh_system_message(think_on=to)
        else:
            self.messages.insert(0, fresh_system_message(think_on=to))

        # Boot-time prune: if the loaded history is huge, drop oldest
        # non-system turns until we're under ~50% of the context window.
        # Otherwise the footer shows 141% before you've typed anything,
        # and the next turn would auto-compact wastefully.
        target = int(self.context_tokens * 0.5)
        while estimate_tokens(self.messages) > target and len(self.messages) > 4:
            # find oldest non-system message and drop it (and its pair if user/assistant)
            for i in range(1, len(self.messages)):
                if self.messages[i].get("role") in ("user", "assistant", "tool"):
                    del self.messages[i]
                    break
            else:
                break

    @staticmethod
    def _transcript_key(msg: dict) -> str:
        """Stable identity for a message (role + content), so the append-only
        transcript dedups across saves/restarts without mutating the message
        dicts that get sent to the API."""
        import hashlib
        c = msg.get("content")
        if not isinstance(c, str):
            c = json.dumps(c, ensure_ascii=False, default=str)
        return hashlib.md5(f"{msg.get('role')}\x00{c}".encode("utf-8", "replace")).hexdigest()

    def _append_transcript(self):
        """Append any new user/assistant turns to the never-pruned transcript.
        Robust to history pruning/compaction (which mutates self.messages) -
        identity is content-based, not index-based."""
        seen = getattr(self, "_transcript_seen", None)
        if seen is None:
            seen = self._transcript_seen = set()
        new = []
        for m in self.messages:
            if m.get("role") not in ("user", "assistant"):
                continue
            c = m.get("content")
            if not c or (isinstance(c, str) and not c.strip()):
                continue
            k = self._transcript_key(m)
            if k in seen:
                continue
            seen.add(k)
            new.append(m)
        if not new:
            return
        try:
            os.makedirs(os.path.dirname(CLI_TRANSCRIPT), exist_ok=True)
            with open(CLI_TRANSCRIPT, "a", encoding="utf-8") as f:
                for m in new:
                    c = m.get("content")
                    if not isinstance(c, str):
                        c = json.dumps(c, ensure_ascii=False, default=str)
                    f.write(json.dumps(
                        {"ts": time.time(), "role": m.get("role"), "content": c},
                        ensure_ascii=False) + "\n")
        except Exception:
            pass  # transcript is best-effort; never break the turn over it

    def save_history(self):
        """Always saves to ~/Jarvis/logs/jarvis_history.json (local, safe).
        If HEARTH_LMSTUDIO_SYNC=1, also mirrors into a DEDICATED file under
        ~/.lmstudio/conversations/ so the chat shows up in LM Studio's UI.
        Never writes to your existing LM Studio threads - only the dedicated
        jarvis_cli.conversation.json file."""
        # Flush new turns to the never-pruned transcript BEFORE we overwrite the
        # (prunable) working-context file, so search/recall keeps the full record.
        self._append_transcript()
        try:
            if not getattr(self, "_side", False):        # the first terminal open owns this file
                os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.messages, f, indent=2)
        except Exception as e:
            print(f"{C_ERR}Couldn't save the chat: {e}{C_RESET}")
            return
        try:
            self._chat_push()
        except Exception:
            pass

        if not LMSTUDIO_SYNC:
            return
        try:
            os.makedirs(os.path.dirname(LMSTUDIO_SYNC_PATH), exist_ok=True)
            now_ms = int(time.time() * 1000)
            lm_messages = []
            i = 0
            for m in self.messages:
                role = m.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = m.get("content")
                if not content:
                    continue
                lm_messages.append({
                    "id": f"jarvis_msg_{i}_{now_ms}",
                    "currentlySelected": 0,
                    "role": role,
                    "versions": [{
                        "type": "singleStep",
                        "role": role,
                        "content": [{"type": "text", "text": content}],
                        "createdAt": now_ms,
                    }],
                })
                i += 1
            data = {
                "name": "Inglen CLI",
                "pinned": True,
                "createdAt": now_ms,
                "messages": lm_messages,
            }
            with open(LMSTUDIO_SYNC_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            # Don't crash the CLI on a sync failure
            print(f"{C_DIM}[LM Studio sync failed: {e}]{C_RESET}")

    # -- Boot screen --------------------------------------------------------
    def animate_intro(self):
        # The app's name in block letters.
        art = [
            "     █ █▄░█ █▀▀ █░░ █▀▀ █▄░█     ",
            "     █ █░▀█ █▄█ █▄▄ ██▄ █░▀█     ",
        ]
        try:
            from hearth import edition as _ed
            _ed_label = _ed.label()
        except Exception:
            _ed_label = "Full"
        sub = f"  local-first personal AI  ◆  inglen v{HEARTH_VERSION} ({_ed_label})"
        os.system("cls" if os.name == "nt" else "clear")
        # Two lines: the mark, and beside it what is answering and where.
        try:
            from hearth.persona import NAME as _AGENT_NAME
        except Exception:
            _AGENT_NAME = "JARVIS"
        home = os.path.expanduser("~")
        ws = WORKSPACE.replace(home, "~") if WORKSPACE.lower().startswith(home.lower()) else WORKSPACE
        beside = [
            f"{C_BOLD}inglen{C_RESET} {C_DIM}{HEARTH_VERSION} · {_ed_label}{C_RESET}",
            f"{C_BOLD}{GRAD[4]}{_AGENT_NAME}{C_RESET}{C_DIM} · {self._model_label()}{C_DIM} · {ws}{C_RESET}",
        ]
        for i, ln in enumerate(art):
            color = GRAD[3 + i % 3]
            sys.stdout.write(f"{C_BOLD}{color}{ln.rstrip()}{C_RESET}   {beside[i]}\n")
            sys.stdout.flush()
            time.sleep(0.04)
        if _agent_line() or not _is_local_endpoint(LOCAL_API_BASE):
            print(f"\n{C_DIM}  /help for commands · esc stops a reply · ctrl+t shows thinking{C_RESET}\n")
        else:
            chat_models, reachable = self._probe_local_models()
            if not reachable:
                # Nothing serving yet — try to auto-boot Hearth's OWN built-in
                # server with the last-used model (like the GUI), so the CLI runs
                # standalone. Re-probe if it came up.
                if self._try_autoboot_builtin():
                    chat_models, reachable = self._probe_local_models()
            if not reachable:
                print(f"{C_WARN}● no local model server running{C_RESET}")
                print(f"{C_DIM}  Pick one with {C_RESET}{C_TOOL}/models use <n>{C_RESET}{C_DIM} to run it locally, or {C_RESET}{C_TOOL}/brain{C_RESET}{C_DIM} for a cloud model.{C_RESET}")
                try:
                    from hearth import llmserver as _ls
                    if _ls._is_lite_edition():
                        print(f"{C_DIM}  Inglen Lite has no built-in engine yet — bring LM Studio/Ollama or a cloud key, or get the engine: {_ls.FULL_DOWNLOAD_URL}{C_RESET}")
                except Exception:
                    pass
                print()
            elif not chat_models:
                print(f"{C_WARN}● LM Studio is running, but no model is loaded{C_RESET}")
                print(f"{C_DIM}  Load a model in LM Studio, then send a message. /models to re-check.{C_RESET}\n")
            else:
                print(f"{C_OK}● online{C_RESET}{C_DIM}  ·  /help for commands  ·  type @<path> to attach a file{C_RESET}\n")

        # Context-fit pre-flight: persona + tool schemas are a fixed per-turn
        # cost. If the loaded context barely covers them, conversations will
        # trim aggressively / risk overflow. Warn proactively (this is the root
        # tightness behind the "No user query found" crash).
        warn = self._context_health_warning()
        if warn:
            overhead, headroom = warn
            print(f"{C_WARN}context is tight{C_RESET}{C_DIM} - persona+tools use ~{overhead:,} of "
                  f"{self.context_tokens:,} tok, ~{max(0, headroom):,} left for chat+reply.{C_RESET}")
            print(f"{C_DIM}  Load this model at >=24K context in LM Studio for headroom (/context to check).{C_RESET}\n")

    def _context_health_warning(self):
        """Return (overhead_tok, headroom_tok) if the loaded context barely
        fits the fixed persona + tool-schema overhead, else None."""
        try:
            sys_tok = len(system_prompt()) // CHARS_PER_TOKEN
            tool_tok = len(json.dumps(self.openai_tools)) // CHARS_PER_TOKEN
        except Exception:
            return None
        overhead = sys_tok + tool_tok
        headroom = self.context_tokens - overhead - RESERVED_OUTPUT
        return (overhead, headroom) if headroom < 2000 else None

    def _probe_local_models(self):
        """Return (chat_model_ids, reachable) for the local /v1/models endpoint.
        Used at boot to distinguish 'server down' from 'no model loaded'."""
        try:
            from hearth.headless import model_list_req
            _req = model_list_req(LOCAL_API_BASE, LOCAL_API_KEY or "hearth-builtin")
            with urllib.request.urlopen(_req, timeout=2) as r:
                data = json.loads(r.read().decode())
            ids = [m.get("id", "") for m in data.get("data", [])]
            chat = [m for m in ids if m and "embed" not in m.lower()]
            return chat, True
        except Exception:
            return [], False

    # -- Slash commands -----------------------------------------------------
    async def model_ids(self) -> List[str]:
        """The chat models the current brain's server lists."""
        from hearth.headless import model_list_req
        req = model_list_req(LOCAL_API_BASE, LOCAL_API_KEY or "hearth-builtin")
        data = await asyncio.to_thread(
            lambda: json.loads(urllib.request.urlopen(req, timeout=5).read().decode()))
        # Hide non-chat models - xAI lists grok-imagine-image/-video etc.
        # alongside chat models, but they 400 on /chat/completions (they're
        # image/video endpoints). They only clutter the picker.
        _NON_CHAT = ("imagine-image", "imagine-video", "imagine-audio",
                     "embedding", "embed-", "-tts", "whisper")
        ids = [m["id"] for m in data.get("data", [])
               if m.get("id") and not any(p in m["id"].lower() for p in _NON_CHAT)]
        self.last_model_list = ids
        return ids

    async def fetch_models(self, filter_q: str = "", free_only: bool = False):
        try:
            ids = await self.model_ids()
            free_ids = [m for m in ids if m.lower().endswith(":free")]

            # A cloud catalog (OpenRouter lists 400+) is useless when dumped. Bare
            # /models on a large list gets a summary + a search hint, not a wall
            # of 400 lines — a good CLI searches, it doesn't page everything.
            if not free_only and not filter_q and len(ids) > 30:
                print(f"\n{C_BRAND}{len(ids)} models{C_RESET} on {C_DIM}{LOCAL_API_BASE}{C_RESET}"
                      f"  {C_DIM}· {len(free_ids)} free{C_RESET}")
                print(f"  {C_DIM}current {C_RESET}{C_TOOL}{self.current_model or '?'}{C_RESET}")
                print(f"  {C_DIM}find    {C_RESET}{C_TOOL}/models <name>{C_RESET}{C_DIM}  ·  {C_RESET}"
                      f"{C_TOOL}/models free{C_RESET}{C_DIM}  ·  pick with {C_RESET}{C_TOOL}/model <name>{C_RESET}\n")
                return ids

            if free_only:
                shown, label = free_ids, "Free models"
            elif filter_q:
                toks = filter_q.lower().split()
                shown = [m for m in ids if all(t in m.lower() for t in toks)]
                label = f"Models matching {filter_q!r}"
            else:
                shown, label = ids, f"Models on {LOCAL_API_BASE}"

            if not shown:
                extra = "" if free_only else f" matching {filter_q!r}"
                print(f"\n{C_DIM}No {'free ' if free_only else ''}models{extra}.{C_RESET}\n")
                return ids
            print(f"\n{C_BRAND}{label}{C_RESET}  {C_DIM}({len(shown)}){C_RESET}")
            self.last_model_list = shown          # what /model <n> counts in
            cap = 40
            for i, m in enumerate(shown[:cap], 1):
                star = f" {C_TOOL}←{C_RESET}" if m == self.current_model else ""
                free = f" {C_DIM}free{C_RESET}" if m.lower().endswith(":free") else ""
                print(f"  {C_DIM}[{i}]{C_RESET} {m}{free}{star}")
            if len(shown) > cap:
                print(f"  {C_DIM}…{len(shown)-cap} more — narrow with /models <name>{C_RESET}")
            print(f"  {C_DIM}pick with {C_RESET}{C_TOOL}/model <n>{C_RESET}\n")
            return ids
        except Exception as e:
            print(f"{C_ERR}Could not reach {LOCAL_API_BASE}/models: {e}{C_RESET}")
            return []

    def _arg_choices(self, cmd: str, before: List[str]) -> list:
        """What can follow `cmd` and the words already typed after it."""
        if cmd in ("/brain", "/endpoint", "/provider"):
            if not before:
                return _brain_choices()
            if len(before) == 1:
                pid = before[0].lower()
                from hearth import claude_brain as _cb
                if pid in ("claude", "cursor") or pid in _cb.BRAINS:
                    return [("models", "list or search its models")]
                return [(m, "") for m in self._provider_list(pid)]
            return []
        if cmd in ("/model", "/load") and not before:
            return [(m, "") for m in self.last_model_list[:200]]
        return _SUB_CHOICES.get(cmd, []) if not before else []

    def _provider_list(self, pid: str) -> List[str]:
        """A saved provider's models for the completer: asked once in the
        background, so typing never waits on the network."""
        cache = self.__dict__.setdefault("_provider_lists", {})
        if pid in cache:
            return cache[pid] or []
        cache[pid] = None
        from hearth.headless import PROVIDERS, provider_models
        if pid not in PROVIDERS:
            return []
        key = _read_brain_key(pid)

        def fetch():
            cache[pid] = (provider_models(PROVIDERS[pid][1], key) or []) if key else []
        threading.Thread(target=fetch, daemon=True).start()
        return []

    def _print_status(self) -> None:
        """What is thinking, how full this chat is, each plan's usage windows
        and what the PC is running; the same facts as the window's /status."""
        from hearth.web import status_snapshot
        st = status_snapshot()
        now = time.time()

        def ago(sec):
            sec = max(0, int(sec))
            if sec < 90:
                return f"{sec}s"
            m = round(sec / 60)
            if m < 90:
                return f"{m}m"
            h, d = m // 60, m // 1440
            return f"{d}d {h % 24}h" if d >= 2 else f"{h}h {m % 60}m"

        def bar(pct, width=24):
            p = max(0, min(100, int(pct)))
            fill = round(width * p / 100)
            col = C_ERR if p >= 90 else C_WARN if p >= 70 else C_OK
            return f"{col}{'█' * fill}{C_DIM}{'░' * (width - fill)}{C_RESET}"

        b = st["brain"]
        print(f"\n{C_BRAND}Thinking with{C_RESET}  {C_TOOL}{b['label']}{' · ' + b['model'] if b['model'] else ''}{C_RESET}"
              f"  {C_DIM}{'on this PC' if b['kind'] == 'local' else 'through ' + b['where']}{C_RESET}")
        used = self._ctx_used()
        total = self.context_tokens or st.get("context") or 0
        if total:
            room = total * COMPACT_AT
            pct = min(100, used / room * 100) if room else 0
            print(f"{C_BRAND}This chat{C_RESET}      {bar(pct)}  {C_DIM}{used // 1000}K of {total // 1000}K, "
                  f"{max(0, 100 - int(pct))}% left before older turns are summarised{C_RESET}")
        if st["limits"]:
            print(f"{C_BRAND}Usage{C_RESET}")
        for lim in sorted(st["limits"], key=lambda u: u["id"] != b["id"]):
            whose = f"your {lim['plan']} plan" if lim["kind"] == "plan" else "your API key"
            when = f", as of {ago(now - lim['at'])} ago" if lim.get("at") and lim.get("windows") else ""
            print(f"  {C_TOOL}{lim['label']}{C_RESET} {C_DIM}{whose}{when}{C_RESET}")
            for w in lim.get("windows") or []:
                if w.get("used") is None:
                    print(f"    {w['name']:<8} {C_DIM}reset since it last reported{C_RESET}")
                    continue
                resets = f" · resets in {ago(w['resets_at'] - now)}" if w.get("resets_at") else ""
                print(f"    {w['name']:<8} {bar(w['used'])}  {w['used']}% used{C_DIM}{resets}{C_RESET}")
            c = lim.get("credits")
            if c is not None:
                left = f", ${c['left']:.2f} left of ${c['limit']:.2f}" if c.get("left") is not None and c.get("limit") else ""
                print(f"    {C_DIM}${(c.get('used') or 0):.2f} used{left}{C_RESET}")
            if lim.get("note"):
                print(f"    {C_DIM}{lim['note']}{C_RESET}")
        loc = st["local"]
        engine = (loc["model"] or "running") if loc["running"] else "not running"
        print(f"{C_BRAND}This PC{C_RESET}        {C_DIM}engine{C_RESET} {engine}"
              f"{'  · helper model ' + loc['helper'] if loc['helper'] else ''}"
              f"  {C_DIM}· approval{C_RESET} {st['mode']}"
              f"  {C_DIM}· helpers working{C_RESET} {st['helpers']['working']}"
              f"  {C_DIM}· memories{C_RESET} {st['memories']}")
        print(f"{C_DIM}Inglen {st['version']}{C_RESET}\n")

    def _keep_model(self, model: str) -> None:
        """The model picked here is the one chats use and the one the window
        and the next launch show."""
        global LOCAL_MODEL
        LOCAL_MODEL = model
        os.environ["LOCAL_MODEL"] = model
        try:
            import json as _json
            sp = os.path.join(WORKSPACE, "settings.json")
            s = _json.load(open(sp, encoding="utf-8")) if os.path.isfile(sp) else {}
            if not _is_local_endpoint(LOCAL_API_BASE):
                s["llm_model"] = model
                with open(sp, "w", encoding="utf-8") as f:
                    _json.dump(s, f, indent=2)
        except Exception:
            pass

    def _save_preferred_model(self, model_path: str) -> None:
        """Remember the last local model booted so the CLI auto-loads it next
        launch (same settings key the GUI uses)."""
        try:
            import json as _json
            sp = os.path.join(WORKSPACE, "settings.json")
            s = _json.load(open(sp, encoding="utf-8")) if os.path.isfile(sp) else {}
            if s.get("preferred_model") != model_path:
                s["preferred_model"] = model_path
                with open(sp, "w", encoding="utf-8") as f:
                    _json.dump(s, f, indent=2)
        except Exception:
            pass

    def _try_autoboot_builtin(self) -> bool:
        """Boot Hearth's OWN built-in server with the last-used local model
        (settings preferred_model) so the CLI runs standalone — no LM Studio
        needed. Returns True if it booted a reachable server."""
        try:
            from hearth import llmserver as _ls
            if _ls._is_lite_edition():
                return False
            import json as _json
            sp = os.path.join(WORKSPACE, "settings.json")
            s = _json.load(open(sp, encoding="utf-8")) if os.path.isfile(sp) else {}
            if not s.get("server_autoboot", True):
                return False
            model = (s.get("preferred_model") or "").strip()
            if not model or not os.path.isfile(model):
                return False
            print(f"{C_DIM}● booting built-in server · {os.path.basename(model)} "
                  f"(first load ~10-30s)…{C_RESET}", flush=True)
            # Honor the per-model config saved by the GUI (KV quant, GPU layers,
            # ctx, flash) so the CLI boots the model the same way, not with bare
            # defaults. Empty dict on first load → start_builtin's own defaults.
            r = _ls.start_builtin(model, **(_ls.get_model_config(model) or {}))
            if r.get("ok"):
                self._retarget_to(r.get("url"), "hearth-builtin", model)
                print(f"{C_OK}● built-in server ready{C_RESET}")
                return True
            print(f"{C_WARN}● built-in server failed: {str(r.get('error', ''))[:90]}{C_RESET}")
        except Exception:
            pass
        return False

    async def _onboard_pick_local_model(self) -> None:
        """First-run: let a local user pick + boot a model NOW (into Hearth's own
        server) so they don't finish setup with a dead endpoint. Saves it as the
        auto-load model for next launch."""
        try:
            from hearth import llmserver as _ls
            if _ls._is_lite_edition():
                print(f"  {C_DIM}Inglen Lite: after setup, run {C_RESET}{C_TOOL}/models engine install{C_RESET}"
                      f"{C_DIM} to grab the GPU engine, or {C_RESET}{C_TOOL}/brain{C_RESET}{C_DIM} a cloud model.{C_RESET}\n")
                return
            # Full edition, but on a GPU the bundled engine has no kernels for.
            try:
                _adv = _ls.runtime_advice()
                if _adv.get("nudge"):
                    print(f"  {C_WARN}{_adv['short']}{C_RESET}")
                    print(f"  {C_DIM}Run {C_RESET}{C_TOOL}/models engine install{C_RESET}"
                          f"{C_DIM} after setup.{C_RESET}\n")
            except Exception:
                pass
            disk = _ls.scan_disk_for_models()
            if not disk:
                # The same first model the window's setup offers: this month's most
                # downloaded chat model its makers publish that fits this card.
                pick = None
                try:
                    print(f"  {C_DIM}No local models on disk yet. Looking for one that fits this PC…{C_RESET}", flush=True)
                    pick = _ls.suggest_model()
                except Exception:
                    pick = None
                if pick:
                    size = f"{pick.get('size_gb')} GB" if pick.get("size_gb") else "size unknown"
                    print(f"  {C_TOOL}{pick['repo']}{C_RESET}{C_DIM}  {pick['filename']} · {size}{C_RESET}")
                    print(f"  {C_DIM}The most downloaded chat model this month that its makers publish and this card runs.{C_RESET}")
                    yes = input(f"  {C_DIM}Download it now? [Y/n]{C_RESET} ").strip().lower()
                    if yes in ("", "y", "yes"):
                        await self._cmd_models(f"/models get {pick['repo']} {pick['filename']}")
                        print()
                        return
                print(f"  {C_DIM}Run {C_RESET}{C_TOOL}/models{C_RESET}"
                      f"{C_DIM} any time to search Hugging Face and download one.{C_RESET}\n")
                return
            print(f"  {C_TOOL}Pick a model to run locally{C_RESET}{C_DIM}:{C_RESET}")
            for i, m in enumerate(disk[:6], 1):
                print(f"  {C_DIM}[{i}] {m.get('filename')}  ({m.get('size_gb')} GB · {m.get('source')}){C_RESET}")
            choice = input(f"  {C_DIM}number to load now, or Enter to skip (pick later with "
                           f"{C_RESET}{C_TOOL}/models use <n>{C_RESET}{C_DIM}){C_RESET}\n  > ").strip()
            if choice.isdigit() and 0 <= int(choice) - 1 < len(disk[:6]):
                path = disk[int(choice) - 1].get("path")
                print(f"  {C_DIM}booting {os.path.basename(path)}… (first load ~10-30s){C_RESET}", flush=True)
                r = _ls.start_builtin(path, **(_ls.get_model_config(path) or {}))
                if r.get("ok"):
                    self._retarget_to(r.get("url"), "hearth-builtin", path)
                    self._save_preferred_model(path)
                    print(f"  {C_OK}● ready — loaded, and I'll auto-load it next time.{C_RESET}")
                else:
                    print(f"  {C_WARN}couldn't start: {str(r.get('error', ''))[:80]}{C_RESET}")
            print()
        except (KeyboardInterrupt, EOFError):
            print()
        except Exception:
            pass

    def _retarget_to(self, url: str, api_key: str = "hearth-builtin",
                     model_path: Optional[str] = None) -> None:
        """Live-switch the running CLI to a new OpenAI-compatible endpoint
        without forcing a restart. Updates the global LOCAL_API_BASE +
        LOCAL_API_KEY env, rebuilds self.client, refreshes the current
        model id, and prints a one-line confirmation. Used by /models use
        and /models get after a successful builtin server start so the user
        doesn't have to manually edit env vars."""
        global LOCAL_API_BASE, LOCAL_API_KEY
        if not url:
            return
        LOCAL_API_BASE = url
        LOCAL_API_KEY = api_key or "hearth-builtin"
        os.environ["LOCAL_API_BASE"] = url
        os.environ["LOCAL_API_KEY"] = LOCAL_API_KEY
        # Mirror the headless module's globals too so any code path that
        # imports from .headless picks up the new endpoint.
        try:
            from hearth import headless as _hl
            _hl.LOCAL_API_BASE = url
            _hl.LOCAL_API_KEY = LOCAL_API_KEY
        except Exception:
            pass
        # Rebuild the OpenAI client so it uses the new base + key.
        try:
            self.client = _anthropic_chat.async_client(LOCAL_API_BASE, LOCAL_API_KEY, _stream_client_timeout())
        except Exception as e:
            print(f"{C_ERR}retarget: client rebuild failed: {e}{C_RESET}")
            return
        # Update the current_model to the basename of the loaded GGUF so the
        # next chat call addresses it correctly.
        if model_path:
            self.current_model = os.path.basename(model_path)
        else:
            try:
                detected = autodetect_model()
                if detected: self.current_model = detected
            except Exception:
                pass
        # Re-detect context unless the user pinned it. Use the shared
        # helper so cloud brains (Grok/Gemini/...) get their real ctx
        # via the per-provider table instead of silently defaulting to 8K.
        if not self._context_pinned:
            try:
                from hearth.headless import resolve_context_tokens
                tokens, _src = resolve_context_tokens(self.current_model)
                if tokens and tokens > 1024:
                    self.context_tokens = tokens
            except Exception:
                try:
                    detected_ctx = autodetect_context(self.current_model)
                    if detected_ctx:
                        self.context_tokens = detected_ctx
                except Exception:
                    pass
        if not getattr(self, "_quiet_retarget", False):
            print(f"{C_DIM}↳ now thinking with {C_RESET}{self._model_label()}")

    def _sync_brain_from_settings(self) -> None:
        """Keep the CLI on the same brain the GUI is using. If settings.json
        changed under us (the GUI switched brains while this CLI is open), adopt
        the new endpoint live. No-op when: launched with an env-pinned brain,
        the file is unchanged, or the change was OUR own /brain write (its url
        already equals the live LOCAL_API_BASE). The GUI reflects CLI changes
        the other way because it re-reads settings.json on every poll."""
        if _BRAIN_FROM_ENV:
            return
        try:
            mtime = os.path.getmtime(os.path.join(WORKSPACE, "settings.json"))
        except OSError:
            return
        if mtime == getattr(self, "_settings_mtime", None):
            return
        self._settings_mtime = mtime
        url, key, model, provider = _read_settings_endpoint()
        if not url:
            return
        if _same_endpoint(url, LOCAL_API_BASE):
            # Same endpoint, but the MODEL behind it can still have changed —
            # swapping the built-in server's model keeps the URL identical, so
            # returning here is why a GUI swap never reached the CLI. Trust the
            # server over settings.json, which may name a model it isn't serving.
            # Only a local server's list is what it has loaded; a cloud
            # provider's is its whole catalog, whose first entry is not a pick.
            live = (_probe_live_model(url, LOCAL_API_KEY) if _is_local_endpoint(url)
                    else None) or model
            same = os.path.basename(str(live or "")).lower() == os.path.basename(str(self.current_model or "")).lower()
            if live and not same:
                self.current_model = live
                print(f"{C_DIM}↳ the window switched me to {C_RESET}{self._model_label()}")
            elif live:
                self.current_model = live
            return
        if not key and provider:
            key = _read_brain_key(provider)
        self._quiet_retarget = True
        try:
            self._retarget_to(url, key or "hearth-builtin", None)
        finally:
            self._quiet_retarget = False
        live = _probe_live_model(url, key) if provider in ("local", "lmstudio", "builtin") else None
        if live or model:
            self.current_model = live or model
        print(f"{C_DIM}↳ the window switched me to {C_RESET}{self._model_label()}")

    async def _cmd_models(self, raw: str) -> None:
        """Full model picker/downloader - matches the GUI Models tab.

        Subcommands:
          /models                - overview: server and on-disk GGUFs
          /models disk           - list every .gguf found on disk + LM Studio / HF cache
          /models hf <query>     - search Hugging Face for GGUF repos
          /models get <repo|n> [file|n] - list a Hugging Face repo's files, or download one + boot it
          /models use <path|n>   - boot built-in server with a disk model (n = number from /models disk)
          /models engine         - show the inference engine; `engine install` / `engine update` to fetch llama.cpp
          /models stop           - stop the built-in server
          /models helper <n|path|off> - a second, smaller model for helpers and sub-agents
        """
        from hearth import llmserver
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""

        if sub == "helper":
            hs = llmserver.helper_status()
            if not arg:
                print(f"{C_OK}helper model: {hs['model']}{C_RESET}" if hs["running"]
                      else f"{C_DIM}no helper model. /models helper <n> loads one from /models disk.{C_RESET}")
                return
            if arg.lower() in ("off", "stop", "none"):
                llmserver.stop_helper()
                _save_setting("helper_model", "")
                print(f"{C_OK}helper model stopped{C_RESET}")
                return
            path = arg
            if arg.isdigit():
                cache = getattr(self, "_cli_disk_cache", None) or llmserver.scan_disk_for_models()
                idx = int(arg) - 1
                if not (0 <= idx < len(cache)):
                    print(f"{C_ERR}Out of range - run /models disk to see the list.{C_RESET}")
                    return
                path = cache[idx].get("path")
            print(f"{C_DIM}starting the helper model…{C_RESET}")
            r = await asyncio.to_thread(llmserver.start_helper, path)
            if r.get("ok") and r.get("shared"):
                _save_setting("helper_model", path)
                print(f"{C_OK}{r.get('note')}{C_RESET}")
            elif r.get("ok"):
                _save_setting("helper_model", path)
                print(f"{C_OK}helper model ready: {r.get('model')}{C_RESET}{C_DIM}  helpers and sub-agents use it; the main model stays on the chat{C_RESET}")
            else:
                print(f"{C_ERR}{r.get('error')}{C_RESET}")
            return
        if sub == "stop":
            r = llmserver.stop_builtin()
            if r.get("ok") and r.get("was_running"):
                print(f"{C_OK}Built-in server stopped.{C_RESET}")
            else:
                print(f"{C_DIM}{r.get('error') or 'no server was running'}{C_RESET}")
            return

        if sub == "engine":
            info = llmserver.llama_runtime_info()
            adv = llmserver.runtime_advice()
            src = {"hearth": "Inglen's own", "lmstudio": "LM Studio's (signed)",
                   "other": "detected copy", "wheel": "bundled with Inglen"}.get(
                       info.get("source"), info.get("source") or "")
            print(f"\n{C_BRAND}Inference engine{C_RESET}  {C_TOOL}{info.get('engine')}{C_RESET}"
                  f"  {C_DIM}{info.get('version') or ''} · {src}{C_RESET}")
            if info.get("exe"):
                print(f"  {C_DIM}{info['exe']}{C_RESET}")
            if adv.get("nudge"):
                print(f"  {C_WARN}{adv['short']}{C_RESET}")
            if info.get("stub"):
                print(f"  {C_WARN}An engine download is on disk but unusable "
                      f"(no runnable llama-server.exe).{C_RESET}")
                print(f"  {C_DIM}Usually antivirus quarantine. Allow ~/.hearth/llamacpp, "
                      f"then re-run with {C_RESET}{C_TOOL}install{C_RESET}{C_DIM}.{C_RESET}")
            _a = arg.strip().lower()
            if _a.startswith("use "):
                res = llmserver.set_engine_preference(_a.split(None, 1)[1])
                if not res.get("ok"):
                    print(f"{C_ERR}{res.get('error')}{C_RESET}")
                    return
                pick = res.get("preference") or "auto"
                new = llmserver.llama_runtime_info()
                print(f"{C_OK}Engine preference: {pick}{C_RESET}"
                      f"  {C_DIM}now using {new.get('source')} "
                      f"{new.get('version') or ''}{C_RESET}\n")
                return
            if _a not in ("install", "update"):
                cur = llmserver.get_engine_preference() or "auto"
                print(f"  {C_DIM}preference {C_RESET}{C_TOOL}{cur}{C_RESET}"
                      f"{C_DIM} - switch with {C_RESET}{C_TOOL}/models engine use "
                      f"hearth|lmstudio|wheel|auto{C_RESET}")
                nxt = "update" if info.get("managed") else "install"
                print(f"{C_DIM}Run {C_RESET}{C_TOOL}/models engine {nxt}{C_RESET}"
                      f"{C_DIM} to fetch the official llama.cpp CUDA build "
                      f"into ~/.hearth/llamacpp.{C_RESET}\n")
                return
            print(f"{C_DIM}Downloading llama.cpp runtime - this can take a while…{C_RESET}")
            def _cb(done: int, total: int) -> None:
                if total > 0:
                    print(f"\r  {int(100*done/total):3d}%  {done/(1024**2):7.1f}"
                          f" / {total/(1024**2):7.1f} MB", end="", flush=True)
            try:
                r = llmserver.download_llama_runtime(on_progress=_cb)
                print()
                if not r.get("ok"):
                    print(f"{C_ERR}{r.get('error') or 'download failed'}{C_RESET}")
                    return
                llmserver.reset_native_cache()  # else the old choice stays cached
                new = llmserver.llama_runtime_info()
                if new.get("source") != "hearth":
                    # Extracted, but the picker still prefers something else -
                    # don't report another engine's version as "installed".
                    print(f"{C_WARN}Extracted to {r.get('dir')}, but Inglen is still "
                          f"using {new.get('source')} ({new.get('version')}).{C_RESET}")
                    return
                print(f"{C_OK}Engine installed: {new.get('version')}{C_RESET}")
                print(f"{C_DIM}Reboot the built-in server to use it: "
                      f"{C_RESET}{C_TOOL}/models use <n>{C_RESET}\n")
            except Exception as e:
                print(f"{C_ERR}{type(e).__name__}: {e}{C_RESET}")
            return

        if sub == "disk":
            disk = llmserver.scan_disk_for_models()
            if not disk:
                print(f"{C_DIM}No .gguf files found. Find one to download with /models hf <search>.{C_RESET}")
                return
            print(f"\n{C_BRAND}{len(disk)} model{'s' if len(disk)!=1 else ''} on disk:{C_RESET}")
            for i, m in enumerate(disk, 1):
                print(f"  [{i}] {C_TOOL}{m.get('filename')}{C_RESET} "
                      f"{C_DIM}({m.get('size_gb')} GB · {m.get('source')}){C_RESET}")
                print(f"      {C_DIM}{m.get('path')}{C_RESET}")
            print(f"\n{C_DIM}Boot one with: /models use <n>{C_RESET}\n")
            self._cli_disk_cache = disk
            return

        if sub == "hf":
            if not arg:
                print(f"{C_ERR}Usage: /models hf <search query>{C_RESET}")
                return
            results = llmserver.search_huggingface(arg)
            if results and results[0].get("error"):
                print(f"{C_ERR}HF search failed: {results[0]['error']}{C_RESET}")
                return
            if not results:
                print(f"{C_DIM}No GGUF repos found for '{arg}'.{C_RESET}")
                return
            print(f"\n{C_BRAND}Hugging Face GGUF results for '{arg}':{C_RESET}")
            for i, r in enumerate(results[:12], 1):
                repo = r.get("hf_repo") or r.get("name") or "(unknown)"
                print(f"  [{i}] {C_TOOL}{repo}{C_RESET}  {C_DIM}{r.get('downloads') or 0:,} dl{C_RESET}")
            self._cli_hf_results = [r.get("hf_repo") or r.get("name") or "" for r in results[:12]]
            print(f"\n{C_DIM}See a repo's files with: /models get <n>{C_RESET}\n")
            return

        if sub == "get":
            # /models get <repo|n>           the repo's GGUF files, with size and fit
            # /models get <repo|n> <file|n>  download that file and boot it
            words = arg.split()
            if not words:
                print(f"{C_ERR}Usage: /models get <repo>  (find one with /models hf <search>){C_RESET}")
                return
            repo = words[0]
            if repo.isdigit():
                found = getattr(self, "_cli_hf_results", None) or []
                if not (1 <= int(repo) <= len(found)):
                    print(f"{C_ERR}No search result {repo}. Run /models hf <search> first.{C_RESET}")
                    return
                repo = found[int(repo) - 1]
            files = llmserver.list_hf_files(repo)
            if files and files[0].get("error"):
                print(f"{C_ERR}{files[0]['error']}{C_RESET}")
                return
            if not files:
                print(f"{C_DIM}No .gguf files in {repo}.{C_RESET}")
                return
            if len(words) < 2:
                print(f"\n{C_BRAND}{repo}{C_RESET}")
                for i, f in enumerate(files, 1):
                    size = f"{f.get('size_gb')} GB" if f.get("size_gb") is not None else "size unknown"
                    print(f"  [{i}] {C_TOOL}{f.get('filename')}{C_RESET}  {C_DIM}{size}"
                          f"{' · ' + f['fit_label'] if f.get('fit_label') else ''}{C_RESET}")
                print(f"\n{C_DIM}Download + boot one with: /models get {words[0]} <n>{C_RESET}\n")
                return
            want = words[1]
            if want.isdigit():
                if not (1 <= int(want) <= len(files)):
                    print(f"{C_ERR}No file {want} in that list.{C_RESET}")
                    return
                filename = files[int(want) - 1].get("filename")
            else:
                filename = want
            print(f"{C_DIM}Downloading {filename} - this can take a while…{C_RESET}")
            def _cb(done: int, total: int) -> None:
                if total > 0:
                    pct = int(100 * done / total)
                    mb_done = done / (1024**2)
                    mb_total = total / (1024**2)
                    print(f"\r  {pct:3d}%  {mb_done:7.1f} / {mb_total:7.1f} MB", end="", flush=True)
            try:
                r = llmserver.download_from_hf_repo(repo, filename, on_progress=_cb)
                print()
                if not r.get("ok"):
                    print(f"{C_ERR}{r.get('error') or 'download failed'}{C_RESET}")
                    return
                path = r.get("path")
                print(f"{C_OK}Saved to {path}{C_RESET}")
                r2 = await self._load_with_progress(path)
                if r2.get("ok"):
                    self._retarget_to(r2.get("url"), "hearth-builtin", path)
                    self._save_preferred_model(path)  # auto-load next launch
                else:
                    print(f"{C_ERR}{r2.get('error') or 'failed to start'}{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}{type(e).__name__}: {e}{C_RESET}")
            return

        if sub == "use":
            if not arg:
                print(f"{C_ERR}Usage: /models use <path>  or  /models use <n>  (n from /models disk){C_RESET}")
                return
            path = arg
            # 1) Numeric - index into disk cache
            if arg.isdigit():
                cache = getattr(self, "_cli_disk_cache", None) or llmserver.scan_disk_for_models()
                idx = int(arg) - 1
                if not (0 <= idx < len(cache)):
                    print(f"{C_ERR}Out of range - run /models disk to see the list.{C_RESET}")
                    return
                path = cache[idx].get("path")
            elif not os.path.isfile(path):
                # 2) Not a real file path → try matching against on-disk models
                # by filename. So `/models use harmonic-hermes-9b-q4_k_m`
                # finds Qwen3.5-9B-Harmonic.Q4_K_M.gguf instead of failing
                # with "model file not found". Same as how the GUI picker
                # silently maps pick-id → existing GGUF.
                disk = llmserver.scan_disk_for_models()
                low_arg = arg.lower().replace("-", "").replace("_", "").replace(".", "")
                match = None
                for m in disk:
                    fn = (m.get("filename") or "").lower().replace("-", "").replace("_", "").replace(".", "")
                    if low_arg in fn or fn.startswith(low_arg[:12]):
                        match = m
                        break
                if match:
                    path = match.get("path")
                    print(f"{C_DIM}↳ matched on-disk: {match.get('filename')}{C_RESET}")
                else:
                    print(f"{C_ERR}'{arg}' isn't a file path or an on-disk filename. Run /models disk "
                          f"for what is here, or /models hf <search> to find one to download.{C_RESET}")
                    return
            try:
                r = await self._load_with_progress(path)
                if r.get("ok"):
                    self._retarget_to(r.get("url"), "hearth-builtin", path)
                    self._save_preferred_model(path)  # auto-load next launch
                else:
                    print(f"{C_ERR}{r.get('error') or 'failed'}{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}{type(e).__name__}: {e}{C_RESET}")
            return

        # Free tier, or a free-text SEARCH against the catalog — so a 400-model
        # cloud endpoint is browsable instead of dumped.
        if sub == "free":
            await self.fetch_models(free_only=True)
            return
        if sub:  # any leftover word is treated as a search query
            await self.fetch_models(filter_q=(sub + (" " + arg if arg else "")).strip())
            return

        # No subcommand - overview. Cache the status (which disk-scans + probes
        # the endpoint) for 8s so hitting /models repeatedly is instant instead
        # of re-scanning every time - same idea the GUI uses.
        try:
            _now = time.time()
            _c = getattr(self, "_status_cache", None)
            if _c and (_now - _c[0]) < 8:
                s = _c[1]
            else:
                # Probing the servers and the disk takes a few seconds; say so,
                # and keep it off the loop so the terminal doesn't look frozen.
                print(f"{C_DIM}looking for models…{C_RESET}")
                s = await asyncio.to_thread(llmserver.status, LOCAL_API_BASE)
                self._status_cache = (_now, s)
        except Exception as e:
            print(f"{C_ERR}Could not fetch status: {e}{C_RESET}")
            await self.fetch_models()
            return

        await self._pick_model(disk=s.get("disk_models") or [], status=s)

    async def _load_with_progress(self, path: str) -> Dict[str, Any]:
        """Load a model file on Hearth's engine, off the main loop, with one live
        line saying how far it is (Esc doesn't apply; /models stop does)."""
        from hearth import llmserver
        os.environ["HEARTH_QUIET_ENGINE"] = "1"
        name = os.path.splitext(os.path.basename(path))[0]
        job = asyncio.ensure_future(asyncio.to_thread(
            llmserver.start_builtin, path, **(llmserver.get_model_config(path) or {})))
        t0, i = time.time(), 0
        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        while not job.done():
            p = llmserver.get_load_progress()
            phase = str(p.get("phase") or "starting").replace("_", " ")
            pct = int(p.get("percent") or 0)
            sys.stdout.write(f"\r\033[K{C_BRAND}{spin[i % 10]}{C_RESET} {C_DIM}Loading {name} · {phase}"
                             + (f" {pct}%" if pct else "") + f" · {time.time() - t0:.0f}s{C_RESET}")
            sys.stdout.flush()
            i += 1
            await asyncio.sleep(0.15)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        r = job.result()
        if r.get("ok"):
            print(f"{C_DIM}↳ loaded {name} in {time.time() - t0:.0f}s{C_RESET}")
        return r

    async def _pick_model(self, disk=None, status=None) -> None:
        """One list to pick from: what this brain's server offers, then the model
        files on this PC. Enter switches to a model or loads a file."""
        from hearth import term_pick
        cloud = not _is_local_endpoint(LOCAL_API_BASE)
        try:
            ids = await self.model_ids()
        except Exception:
            ids = []
        if cloud:
            from hearth.headless import PROVIDERS as _pv, provider_for as _pf
            _pid = _pf(LOCAL_API_BASE)
            where = _pv[_pid][0] if _pid in _pv else "your provider"
        elif status and status.get("builtin_running"):
            where = "Inglen's engine"
        elif ids:
            where = "the server on this PC"
        else:
            where = ""
        opts = []
        for m in ids[:60]:
            tag = f"  {C_DIM}in use{C_RESET}" if m == self.current_model else ""
            free = f"  {C_DIM}free{C_RESET}" if m.lower().endswith(":free") else ""
            opts.append((("model", m), f"{m}{free}{tag}"))
        shown_files = [d for d in (disk or []) if not d.get("not_chat") and not d.get("incomplete")]
        self._cli_disk_cache = disk or []
        for d in shown_files[:30]:
            opts.append((("file", d.get("path")),
                         f"{d.get('filename')}  {C_DIM}{d.get('size_gb')} GB · on this PC{C_RESET}"))
        if not opts:
            print(f"{C_DIM}No models to pick from yet. /models hf <name> searches Hugging Face, "
                  f"/brain picks a provider.{C_RESET}")
            return
        head = (f"{C_BOT}Models{C_RESET} {C_DIM}· {len(ids)} on {where}" if where else f"{C_BOT}Models{C_RESET} {C_DIM}·") \
            + (f" · {len(shown_files)} on this PC" if shown_files else "") + C_RESET
        if len(ids) > 60:
            head += f"\n{C_DIM}  showing 60; /model <name> finds the rest{C_RESET}"
        picked = await term_pick.pick(head, opts, default=("model", self.current_model))
        if not picked:
            print(f"{C_DIM}  /models hf <name> searches Hugging Face · /models disk lists every file · "
                  f"/models stop · /models helper{C_RESET}")
            return
        kind, val = picked
        if kind == "model":
            await self.handle_command(f"/model {val}")
        else:
            await self.handle_command(f"/models use {val}")

    async def handle_command(self, text: str) -> bool:
        # `global` MUST sit at the very top of this function, BEFORE any
        # read of these names - Python parses the whole function body and
        # raises SyntaxError "name 'X' is used prior to global declaration"
        # if a global decl appears anywhere AFTER a read. The /brain branch
        # below mutates these to flip the active brain. The /about and
        # /models branches read them.
        global LOCAL_API_BASE, LOCAL_API_KEY, LOCAL_MODEL
        cmd = text.strip()
        low = cmd.lower()
        if low in ("/exit", "/quit", "exit", "quit"):
            print(f"{C_DIM}Goodbye.{C_RESET}")
            sys.exit(0)
        if low == "/help" or low.startswith("/help "):
            want = low[6:].strip().lstrip("/")
            if not want:
                # A glance, not a wall: each group on one line, then what's worth knowing.
                for group, cmds in HELP_GROUPS:
                    print(f"  {C_BOT}{group:<13}{C_RESET}{'  '.join(cmds)}")
                print(f"\n  {C_DIM}Talk normally; commands are for settings. While I reply, typing steers me, Esc stops me,")
                print(f"  and /bg lets a slow command finish in the background while I carry on.")
                print(f"  @path attaches a file · /mode sets what runs without asking")
                print(f"  /help <command> explains one · /help all lists every command{C_RESET}")
                return True
            if want != "all":
                hit = "/" + want
                if hit in SLASH_COMMANDS:
                    print(f"  {C_TOOL}{hit}{C_RESET}  {SLASH_COMMANDS[hit]}")
                    for c, d in _SUB_CHOICES.get(hit, []):
                        print(f"    {C_DIM}{hit} {c}{C_RESET}" + (f"  {C_DIM}{d}{C_RESET}" if d else ""))
                else:
                    print(f"{C_DIM}no command {hit}. {_suggest_command(hit)}{C_RESET}")
                return True
            for group, cmds in HELP_GROUPS:
                print(f"\n  {C_BOT}{group}{C_RESET}")
                for c in cmds:
                    print(f"    {C_TOOL}{c:<16}{C_RESET}{C_DIM}{SLASH_COMMANDS.get(c, '')}{C_RESET}")
            print(f"\n  {C_DIM}Type / to see them as you go · /help <command> for one · @path attaches a file{C_RESET}")
            return True
        if low == "/update" or low.startswith("/update "):
            try:
                from hearth import updater
                print(f"{C_DIM}checking for updates…{C_RESET}")
                r = updater.check_for_update(HEARTH_VERSION)
                if not r.get("ok"):
                    print(f"{C_ERR}update check failed: {r.get('error')}{C_RESET}")
                    return True
                if not r.get("available"):
                    note = r.get("note", "")
                    print(f"{C_OK}You're on the latest{C_RESET} {C_DIM}(v{HEARTH_VERSION}{'; ' + note if note else ''}){C_RESET}")
                    return True
                # First line of the release body makes a decent one-line blurb.
                _blurb = ""
                if r.get("notes"):
                    _blurb = next((ln.strip() for ln in str(r["notes"]).splitlines()
                                   if ln.strip()), "")[:160]
                print()
                print(_render_update_box(HEARTH_VERSION, r["latest"], _blurb))
                if r.get("url"):
                    print(f"  {C_DIM}{r['url']}{C_RESET}")
                ans = (await self._read_choice(f"  {C_TOOL}Install now?{C_RESET} [y/N] ")).strip().lower()
                if ans.startswith("y"):
                    print(f"{C_DIM}{updater.apply_update()}{C_RESET}")
                else:
                    print(f"{C_DIM}skipped. run /update anytime.{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}update unavailable: {type(e).__name__}: {e}{C_RESET}")
            return True
        if low == "/team" or low.startswith("/team "):
            # The same helpers the window's Team view shows, newest first.
            from hearth import subagents as _sa, external_agents as _xa
            agents = sorted(_sa.team_state(since_s=7 * 86400, limit=20), key=lambda a: a.get("updated") or 0, reverse=True)
            arg = cmd.split(None, 1)[1].strip() if " " in cmd.strip() else ""
            if not agents:
                print(f"  {C_DIM}No helpers yet. Ask for a bigger job, or hand one to another agent: "
                      f"\"have Claude Code ...\".{C_RESET}")
                return True
            parts = arg.split()
            if len(parts) == 2 and parts[0] == "stop" and parts[1].isdigit() and 1 <= int(parts[1]) <= len(agents):
                a = agents[int(parts[1]) - 1]
                r = _sa.stop_subagent(a["id"])
                print(f"  {C_OK if r.get('ok') else C_ERR}{r.get('note') or r.get('error')}{C_RESET}")
                return True
            # What a helper changed on disk: see it, bring it in, throw it away, or take it back.
            acts = {"changes": "show", "bring": "bring_in", "discard": "discard", "undo": "undo"}
            if len(parts) == 2 and parts[0] in acts and parts[1].isdigit() and 1 <= int(parts[1]) <= len(agents):
                from hearth import helper_changes as _hc
                a = agents[int(parts[1]) - 1]
                r = _hc.act(a["id"], acts[parts[0]])
                if not r.get("ok"):
                    print(f"  {C_ERR}{r.get('error')}{C_RESET}")
                elif parts[0] == "changes":
                    files = r.get("files") or []
                    if not files:
                        print(f"  {C_DIM}It didn't change any files.{C_RESET}")
                        return True
                    print(f"  {C_TOOL}{len(files)} file(s){C_RESET} {C_DIM}{r.get('stat', '')}  {r.get('state', '')}{C_RESET}")
                    for f in files[:30]:
                        print(f"    {f}")
                    for ln in (r.get("diff") or "").splitlines()[:80]:
                        c = C_OK if ln.startswith("+") and not ln.startswith("+++") else C_ERR if ln.startswith("-") and not ln.startswith("---") else C_DIM
                        print(f"  {c}{ln}{C_RESET}")
                    nxt = ["bring", "discard"] if r.get("can_bring_in") else ["undo"] if r.get("can_undo") else []
                    if nxt:
                        print(f"  {C_DIM}" + ", ".join(f"/team {w} {parts[1]}" for w in nxt) + f"{C_RESET}")
                else:
                    n = len(r.get("files") or r.get("restored") or [])
                    said = {"bring": "Brought in", "discard": "Thrown away", "undo": "Undone"}[parts[0]]
                    print(f"  {C_OK}{said}: {n} file(s).{C_RESET}")
                return True
            if arg.isdigit() and 1 <= int(arg) <= len(agents):
                a = agents[int(arg) - 1]
                path = _sa._transcript_path(a["id"])
                print(f"  {C_TOOL}{a.get('name') or a.get('persona')}{C_RESET}  {C_DIM}{a.get('persona', '')}{C_RESET}")
                try:
                    lines = open(path, encoding="utf-8").read().splitlines()
                except OSError:
                    lines = []
                # Each step the way a live pane shows it.
                from hearth import team as _team
                for ln in lines[-60:]:
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    shown = _team._render_entry(rec)
                    if shown:
                        print("\n".join("  " + x for x in shown.splitlines()))
                ch = _sa.agent_story(a["id"]).get("changes") or {}
                if ch.get("files"):
                    where = {"waiting": "waiting on its own copy", "changed": "written to the folder",
                             "brought_in": "brought in", "discarded": "thrown away", "undone": "undone"}.get(ch.get("state"), "")
                    print(f"  {C_TOOL}changed {len(ch['files'])} file(s){C_RESET} {C_DIM}{where}. "
                          f"/team changes {arg} shows them.{C_RESET}")
                return True
            labels = {k: v["label"] for k, v in _xa.AGENTS.items()}
            colour = {"working": C_OK, "done": C_DIM, "failed": C_ERR, "stopped": C_WARN}
            for n, a in enumerate(agents, 1):
                st = "needs you" if a.get("blocked") and a.get("status") == "working" else a.get("status", "")
                what = labels.get(a.get("runtime") or "", a.get("persona") or "helper")
                doing = (f"using {a['tool']}" if a.get("activity") == "tool" and st == "working"
                         else (a.get("preview") or a.get("prompt") or ""))
                doing = doing.replace("\n", " ")[:90]
                print(f"  {C_DIM}{n:>2}{C_RESET}  {C_TOOL}{(a.get('name') or what):<8}{C_RESET} "
                      f"{C_DIM}{what:<14}{C_RESET} {colour.get(a.get('status'), C_WARN)}{st:<9}{C_RESET} {C_DIM}{doing}{C_RESET}")
            print(f"  {C_DIM}/team <n> shows every step of one, /team stop <n> stops one, /team changes <n> shows what it "
                  f"changed (then bring, discard or undo). Shell jobs are under /jobs.{C_RESET}")
            return True
        if low == "/me":
            # The personal part of first-run setup, any time. Enter keeps an answer.
            print(f"{C_DIM}Enter skips a question and keeps what's set. Ctrl-C stops.{C_RESET}\n")
            try:
                answers = self._ask_personal(
                    lambda q: input(f"  {C_TOOL}{q}{C_RESET}\n  > ").strip())
            except (KeyboardInterrupt, EOFError):
                print()
                return True
            self._persist_onboarding(answers)
            print(f"\n  {C_OK}saved.{C_RESET} {C_DIM}It applies from your next message.{C_RESET}")
            return True
        if low == "/repair":
            # Restore a fouled-up install by re-applying this version's patch
            # (code, skills, art, voice files) and re-downloading a missing
            # model engine.
            try:
                from hearth import updater
                if not updater.can_patch():
                    print(f"{C_DIM}This build keeps its code inside the exe, so it can't self-repair. Reinstall the release to restore it.{C_RESET}")
                    return True
                print(f"{C_DIM}Repairing: re-fetching this version's files (one-time large download)…{C_RESET}")
                def _rp(done, total):
                    if total:
                        sys.stdout.write(f"\r{C_DIM}  {done/1e6:.0f}/{total/1e6:.0f} MB{C_RESET}")
                        sys.stdout.flush()
                r = updater.repair(on_progress=_rp)
                print()
                if r.get("ok") and r.get("engine_error"):
                    print(f"{C_OK}Repaired {r.get('restored', 0)} files.{C_RESET} {C_ERR}{r.get('note')}{C_RESET}")
                elif r.get("ok"):
                    print(f"{C_OK}Repaired {r.get('restored', 0)} files.{C_RESET} {C_DIM}Restart Inglen to load them.{C_RESET}")
                else:
                    print(f"{C_ERR}Repair failed: {r.get('error')}{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}repair unavailable: {type(e).__name__}: {e}{C_RESET}")
            return True
        if low == "/phone":
            # The chat apps: what is set up, who is let in, codes waiting.
            from hearth import tools as _tools
            print(f"\n{C_BRAND}Reach Inglen from your phone{C_RESET}  {C_DIM}(Telegram, Discord, WhatsApp){C_RESET}")
            for line in _tools._connectors({"action": "status"}).splitlines():
                print(f"  {line}")
            print(f"\n  {C_DIM}set up an app in the window (Settings > Connectors), or ask: \"connect my Telegram\"")
            print(f"  someone gave you a code? /pair <code>{C_RESET}")
            return True
        if low.startswith("/pair"):
            from hearth import channel_core as _cc
            code = cmd.split(None, 1)[1].strip().upper() if len(cmd.split(None, 1)) > 1 else ""
            waiting = _cc.pending()
            if not code:
                if not waiting:
                    print(f"{C_DIM}Nobody is waiting with a code.{C_RESET}")
                for w in waiting:
                    print(f"  {w['channel']}: {w['name'] or w['sender']}  code {w['code']}")
                return True
            hit = next((w for w in waiting if w["code"] == code), None)
            if not hit:
                print(f"{C_ERR}No code {code} is waiting (they last an hour).{C_RESET}")
                return True
            r = _cc.approve(hit["channel"], code)
            print(f"{C_OK}Let in {r.get('name') or r.get('sender')} on {hit['channel'].title()}.{C_RESET}" if r.get("ok")
                  else f"{C_ERR}{r.get('error')}{C_RESET}")
            return True
        if low == "/skill" or low.startswith("/skill ") or low == "/skills" or low.startswith("/skills "):
            from hearth import skills_loader as _sl
            parts = cmd.split(None, 2)
            sub = parts[1].lower() if len(parts) > 1 else "list"
            arg = parts[2].strip() if len(parts) > 2 else ""
            if sub in ("list", "ls", ""):
                items = _sl.list_skills()
                if not items:
                    print(f"{C_DIM}no skills yet. Add one: {C_RESET}{C_TOOL}/skill install <github-url|owner/repo>{C_RESET}")
                    return True
                print(f"\n{C_BRAND}Skills{C_RESET} {C_DIM}({len(items)}){C_RESET}")
                for s in items:
                    tag = C_DIM + "bundled" + C_RESET if s.get("source") == "bundled" else C_OK + "installed" + C_RESET
                    print(f"  {C_TOOL}{s['name']}{C_RESET}  {C_DIM}{(s.get('description') or '')[:70]}{C_RESET}  [{tag}]")
                print(f"\n  {C_DIM}add:{C_RESET} /skill install <owner/repo>   {C_DIM}remove:{C_RESET} /skill remove <name>")
                return True
            if sub in ("install", "add", "get"):
                if not arg:
                    print(f"{C_DIM}usage: {C_RESET}{C_TOOL}/skill install <owner/repo | github-url | ./path>{C_RESET}")
                    return True
                from hearth import skills_install as _si
                print(f"{C_DIM}fetching + inspecting {arg}…{C_RESET}")
                m = _si.inspect_source(arg)
                if not m.get("ok"):
                    print(f"{C_ERR}{m.get('error')}{C_RESET}")
                    return True
                print(f"\n  {C_BRAND}{m['name']}{C_RESET} {C_DIM}v{m.get('version') or '?'}{C_RESET}"
                      + (f"  {C_DIM}by {m['author']}{C_RESET}" if m.get('author') else ""))
                print(f"  {m.get('description','')}")
                if m.get("scripts"):
                    print(f"  {C_TOOL}ships scripts:{C_RESET} {', '.join(m['scripts'])}")
                if m.get("risky_tools"):
                    print(f"  {C_ERR}uses risky tools:{C_RESET} {', '.join(m['risky_tools'])}")
                if m.get("risky"):
                    print(f"  {C_ERR}This skill can run shell commands / modify files. Only install ones you trust.{C_RESET}")
                if m.get("already_installed"):
                    print(f"  {C_DIM}(a skill named '{m['name']}' is already installed - this will overwrite it){C_RESET}")
                ans = (await self._read_choice(f"  {C_TOOL}Install {m['name']}?{C_RESET} [y/N] ")).strip().lower()
                if not ans.startswith("y"):
                    print(f"{C_DIM}skipped.{C_RESET}")
                    return True
                res = _si.install_from_staged(m, force=True)
                if res.get("ok"):
                    print(f"{C_OK}installed{C_RESET} {C_DIM}→ {res['folder']}{C_RESET}")
                    print(f"  {C_DIM}use it: just ask, or the model can load_skill('{m['name']}'){C_RESET}")
                else:
                    print(f"{C_ERR}{res.get('error')}{C_RESET}")
                return True
            if sub in ("remove", "rm", "uninstall", "delete"):
                if not arg:
                    print(f"{C_DIM}usage: {C_RESET}{C_TOOL}/skill remove <name>{C_RESET}")
                    return True
                from hearth import skills_install as _si
                res = _si.uninstall_skill(arg)
                print((f"{C_OK}removed {arg}{C_RESET}" if res.get("ok")
                       else f"{C_ERR}{res.get('error')}{C_RESET}"))
                return True
            if sub in ("new", "create", "scaffold"):
                if not arg:
                    print(f"{C_DIM}usage: {C_RESET}{C_TOOL}/skill new <name>{C_RESET}{C_DIM} (lower-kebab-case){C_RESET}")
                    return True
                slug = arg.split()[0]
                body = (
                    "# " + slug.replace("-", " ").title() + "\n\n"
                    "Describe WHEN to use this and the exact steps. Example:\n\n"
                    "1. `list_directory` on the target folder.\n"
                    "2. Decide what to do with each item.\n"
                    "3. Use existing tools (run_command, write_file, move_path...) to do it.\n"
                    "4. Report what changed.\n\n"
                    "Drop any helper scripts in this skill's scripts/ folder and "
                    "reference them by path.\n")
                res = _sl.create_skill(name=slug,
                    description="TODO: one-line when-to-use (edit me)", body=body)
                if res.get("ok"):
                    print(f"{C_OK}scaffolded{C_RESET} {C_DIM}→ {res['folder']}\\SKILL.md{C_RESET}")
                    print(f"  {C_DIM}edit the description + steps, then it's live. Share it: push to GitHub, others run /skill install <you>/<repo>{C_RESET}")
                else:
                    print(f"{C_ERR}{res.get('error')}{C_RESET}")
                return True
            print(f"{C_DIM}usage: /skill [list | install <src> | new <name> | remove <name>]{C_RESET}")
            return True
        if low in ("/status", "/usage"):
            self._print_status()
            return True
        if low == "/undo" or low.startswith("/undo "):
            # File changes the agent made (written, changed, moved, deleted), newest first; each can be put back.
            from hearth import undo as _undo
            rows = _undo.recent(15)
            arg = low[5:].strip()
            if arg:
                if not arg.isdigit() or not 1 <= int(arg) <= len(rows):
                    print(f"{C_DIM}/undo lists the changes; /undo <number> puts one back{C_RESET}")
                    return True
                r = _undo.undo(rows[int(arg) - 1]["id"])
                print(f"{C_OK}{r['said']}{C_RESET}" if r.get("ok") else f"{C_ERR}{r.get('error')}{C_RESET}")
                return True
            if not rows:
                print(f"{C_DIM}nothing to put back: file changes show here for a week{C_RESET}")
                return True
            import time as _t
            for i, c in enumerate(rows, 1):
                ago = int(_t.time() - c["at"])
                when = f"{ago // 60} min ago" if ago < 3600 else f"{ago // 3600} h ago" if ago < 86400 else f"{ago // 86400} d ago"
                print(f"  {i:>2}. {c['what']}  {C_DIM}{when} · {c['where']}{'' if c['can'] else ' · too big to keep'}{C_RESET}")
            print(f"{C_DIM}/undo <number> puts one back{C_RESET}")
            return True
        if low == "/about":
            kind = "local" if _is_local_endpoint(LOCAL_API_BASE) else "cloud"
            try:
                mem_lines = 0 if memory.list_index().startswith("(no") else memory.list_index().count("\n") + 1
            except Exception:
                mem_lines = 0
            print(f"\n{C_BRAND}Inglen v{HEARTH_VERSION}{C_RESET}  {C_DIM}local-first personal AI{C_RESET}")
            print(f"  {C_TOOL}model{C_RESET}       {self.current_model}  {C_DIM}({kind}){C_RESET}")
            print(f"  {C_TOOL}endpoint{C_RESET}    {LOCAL_API_BASE}")
            print(f"  {C_TOOL}context{C_RESET}     {self.context_tokens:,} tokens")
            print(f"  {C_TOOL}tools{C_RESET}       {len(TOOL_DEFINITIONS)} across {len(tools_by_category())} categories  {C_DIM}(/tools){C_RESET}")
            print(f"  {C_TOOL}memories{C_RESET}    {mem_lines}")
            print(f"  {C_TOOL}workspace{C_RESET}   {WORKSPACE}")
            print(f"  {C_TOOL}repo{C_RESET}        {HEARTH_REPO}")
            print(f"  {C_DIM}MIT licensed · by 0pen-sourcer{C_RESET}\n")
            return True
        if low == "/models" or low.startswith("/models "):
            await self._cmd_models(cmd)
            return True
        if low == "/model" or low == "/load":
            await self._pick_model()
            return True
        if low.startswith("/model ") or low.startswith("/load "):
            arg = cmd.split(None, 1)[1].strip().strip("[]")
            # Guard against `/model use 1` / `/model use X` etc. - easy typos
            # because GUI users see `/models use` and reach for the singular.
            # Hint at the right command instead of saving garbage as a model id.
            if arg.lower().startswith("use ") or arg.lower() == "use":
                print(f"{C_ERR}Did you mean {C_TOOL}/models use{C_RESET}{C_ERR}? "
                      f"(plural). /model <id|n> picks a CURRENT-server model; "
                      f"/models use <n> boots a local GGUF.{C_RESET}")
                return True
            # Numeric: index into last /models result
            if arg.isdigit():
                idx = int(arg) - 1
                if not self.last_model_list:
                    await self.fetch_models()
                if 0 <= idx < len(self.last_model_list):
                    self.current_model = self.last_model_list[idx]
                    self._keep_model(self.current_model)
                    print(f"{C_DIM}↳ now thinking with {C_RESET}{self._model_label()}")
                else:
                    print(f"{C_ERR}out of range - /models to see the list{C_RESET}")
                    return True
            else:
                # Validate against the current server's actual model list before
                # accepting an id. Stops garbage like `/model use 1` from being
                # silently saved as a model name and then 400'ing every chat.
                # If the server is unreachable (cloud 401, etc.) and the user
                # passed a string, accept it but warn - cloud endpoints often
                # don't expose /v1/models even with a valid key.
                if not self.last_model_list:
                    try:
                        await self.fetch_models()
                    except Exception:
                        pass
                from hearth.headless import find_model as _fm0
                if self.last_model_list and not _fm0(self.last_model_list, arg):
                    print(f"{C_ERR}'{arg}' is not in the server's model list.{C_RESET}")
                    print(f"{C_DIM}  Available: {', '.join(self.last_model_list[:6])}"
                          f"{'…' if len(self.last_model_list) > 6 else ''}{C_RESET}")
                    print(f"{C_DIM}  Run /models to list, or /brain to switch endpoint.{C_RESET}")
                    return True
                from hearth.headless import find_model as _fm
                self.current_model = _fm(self.last_model_list, arg) or arg
                self._keep_model(self.current_model)
                print(f"{C_DIM}↳ now thinking with {C_RESET}{self._model_label()}")
            # Re-detect context for the new model (unless pinned). Uses
            # the shared per-provider helper so cloud brains land at their
            # real ctx instead of the 8K default.
            if not self._context_pinned:
                try:
                    from hearth.headless import resolve_context_tokens
                    tokens, src = resolve_context_tokens(self.current_model)
                except Exception:
                    tokens = autodetect_context(self.current_model)
                    src = "v1/models probe"
                if tokens and tokens != self.context_tokens and tokens > 1024:
                    self.context_tokens = tokens
                    print(f"{C_DIM}context auto-set to {tokens} tokens ({src}){C_RESET}")
            return True
        # /endpoint is the natural-language alias for /brain - every cloud
        # model + every user I've watched reaches for "endpoint" first. Treat
        # them as the same command. Same with /provider.
        if low.startswith("/endpoint") or low.startswith("/provider"):
            # rewrite into a /brain command and fall through
            parts = cmd.split(None, 1)
            rest = parts[1] if len(parts) > 1 else ""
            cmd = ("/brain " + rest).rstrip()
            low = cmd.lower()
        if low == "/brain" or low.startswith("/brain "):
            # Switch LLM endpoint (local <-> cloud) without restarting Hearth.
            # Mirrors the GUI's Settings → Chat brain switcher.
            #
            # KEY STORAGE: per-provider keys live in ~/Jarvis/brain_keys.json
            # so users type each API key ONCE. After that, `/brain grok` with
            # no arg auto-loads the saved key. To replace a saved key, pass a
            # new one (`/brain grok <new-key>`); to wipe one, use
            # `/brain forget <provider>`.
            # (`global LOCAL_API_BASE / KEY / MODEL` already declared at the
            # top of handle_command - Python requires it before any read.)
            import json as _json
            keys_path = os.path.join(WORKSPACE, "brain_keys.json")
            settings_path = os.path.join(WORKSPACE, "settings.json")

            def _load_keys() -> dict:
                try:
                    with open(keys_path, "r", encoding="utf-8") as f:
                        return _json.load(f) or {}
                except (OSError, _json.JSONDecodeError):
                    return {}
            def _save_keys(d: dict) -> None:
                before = _load_keys()
                try:
                    os.makedirs(WORKSPACE, exist_ok=True)
                    with open(keys_path, "w", encoding="utf-8") as f:
                        _json.dump(d, f, indent=2)
                except OSError:
                    pass
                # The window keeps the same keys in settings.json: one set of keys,
                # so a key replaced here isn't shadowed by the old one there.
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        st = _json.load(f) or {}
                    vault = dict(st.get("provider_keys") or {})
                    for p, v in d.items():
                        if isinstance(v, dict) and v.get("key"):
                            vault[p] = v["key"]
                    for p in set(before) - set(d):
                        vault.pop(p, None)
                    st["provider_keys"] = vault
                    tmp = settings_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        _json.dump(st, f, indent=2)
                    os.replace(tmp, settings_path)
                except (OSError, ValueError):
                    pass

            parts = cmd.split(None, 2)
            if len(parts) == 1:
                from hearth import term_pick
                saved = _load_keys()
                opts = [(pid, f"{pid}  {C_DIM}{about}{' · key saved' if pid in saved else ''}{C_RESET}")
                        for pid, about in _brain_choices() if pid not in ("forget", "lmstudio")]
                picked = await term_pick.pick(f"{C_BOT}What thinks{C_RESET} {C_DIM}· now {self._model_label()}{C_RESET}", opts)
                if picked is None:
                    return True
                return await self.handle_command(f"/brain {picked}")
            if len(parts) == 1:
                # Just /brain - show current + how to switch + which keys are saved
                provider_label = "cloud" if not _is_local_endpoint(LOCAL_API_BASE) else "local"
                try:
                    from hearth import claude_brain as _cb
                    _agent = _cb.label()
                except ImportError:
                    _agent = ""
                if _agent:
                    print(f"\n{C_BOT}Current brain:{C_RESET} {C_TOOL}{_agent}{C_RESET}  "
                          f"{C_DIM}(helpers use {self.current_model} via {LOCAL_API_BASE}){C_RESET}\n")
                else:
                    print(f"\n{C_BOT}Current brain:{C_RESET} {C_TOOL}{provider_label}{C_RESET}  "
                          f"{C_DIM}({self.current_model} via {LOCAL_API_BASE}){C_RESET}\n")
                from hearth.headless import PROVIDERS as _known
                have = sorted(p for p in {*_known, *_load_keys()} if _read_brain_key(p))
                if have:
                    print(f"{C_DIM}Saved keys: {', '.join(have)}{C_RESET}")
                print(f"\n{C_BOT}Switch with:{C_RESET}")
                print(f"  {C_TOOL}/brain builtin{C_RESET}                   Inglen's built-in engine")
                print(f"  {C_TOOL}/brain local{C_RESET}                     Inglen's engine, or a local server it finds")
                print(f"  {C_TOOL}/brain lmstudio{C_RESET}                  LM Studio")
                print(f"  {C_TOOL}/brain grok [api-key]{C_RESET}            xAI Grok       (key optional if saved)")
                print(f"  {C_TOOL}/brain gemini [api-key]{C_RESET}          Google Gemini  (key optional if saved)")
                print(f"  {C_TOOL}/brain openai [api-key]{C_RESET}          OpenAI         (key optional if saved)")
                print(f"  {C_TOOL}/brain anthropic [api-key]{C_RESET}       Anthropic      (key optional if saved)")
                print(f"  {C_TOOL}/brain openrouter [api-key]{C_RESET}      OpenRouter     (key optional if saved)")
                print(f"  {C_TOOL}/brain groq [api-key]{C_RESET}            Groq           (very fast, paid tier)")
                print(f"  {C_TOOL}/brain custom <url> <api-key>{C_RESET}    any OpenAI-compatible server")
                print(f"  {C_TOOL}/brain claude-code{C_RESET}               Claude Code on this PC, your Claude plan")
                print(f"  {C_TOOL}/brain codex{C_RESET}                     Codex on this PC, your ChatGPT plan")
                print(f"  {C_TOOL}/brain cursor{C_RESET}                    Cursor's agent on this PC, your Cursor plan")
                print(f"  {C_TOOL}/brain forget <provider>{C_RESET}         delete a saved key")
                print(f"\n{C_DIM}Keys saved to ~/Jarvis/brain_keys.json (gitignored, 0600 on POSIX). "
                      f"A key saved in the window works here too.{C_RESET}\n")
                return True

            provider = parts[1].strip().lower()

            # /brain claude-code or /brain codex: an agent program installed
            # here thinks for JARVIS. The local address stays, since helpers
            # keep using the local model.
            from hearth import claude_brain as _cb
            if provider in ("claude", "cursor") or provider in _cb.BRAINS:
                provider = {"claude": "claude-code", "cursor": "cursor-agent"}.get(provider, provider)
                # /brain claude-code opus: one of the agent's own models, by
                # its name or id. Without one, the saved choice stays.
                want = parts[2].strip().lower() if len(parts) > 2 else None
                model = None
                # /brain cursor models [words]: the agent's models, searchable,
                # instead of hundreds of names in one paragraph.
                if want is not None and (want == "models" or want.startswith("models ")):
                    q = want[len("models"):].split()
                    offered = _cb.agent_models(provider)["models"]
                    hits = [m for m in offered if all(w in (m["label"] + " " + m["id"]).lower() for w in q)]
                    print(f"{C_BRAND}{_cb.BRAINS[provider]}{C_RESET} {C_DIM}· {len(hits)} of {len(offered)} models"
                          f"{' matching ' + ' '.join(q) if q else ''}{C_RESET}")
                    for m in hits[:40]:
                        print(f"  {m['label']}{C_DIM}{'  · ' + m['about'] if m.get('about') else ''}{C_RESET}")
                    if len(hits) > 40:
                        print(f"  {C_DIM}…{len(hits) - 40} more; add a word: /brain {provider} models opus{C_RESET}")
                    print(f"{C_DIM}Pick one with /brain {provider} <name>.{C_RESET}")
                    return True
                if want is not None:
                    offered = _cb.agent_models(provider)["models"]
                    hit = next((m for m in offered if want in (m["id"].lower(), m["label"].lower(), m["label"].lower().split()[0])), None)
                    if not hit:
                        print(f"{C_ERR}{_cb.BRAINS[provider]} doesn't list '{want}'. It has: "
                              f"{', '.join(m['label'] for m in offered)}{C_RESET}")
                        return True
                    model = hit["id"]
                err = _cb.set_brain(provider, model)
                if err:
                    print(f"{C_ERR}{err}{C_RESET}")
                    return True
                _info = _cb.agent_models(provider)
                _cur = next((m for m in _info["models"] if m["id"] == _info["model"]), None)
                if _cur:
                    print(f"{C_DIM}model: {_cur['label']}"
                          + (f" ({_cur['about']})" if _cur.get('about') else "")
                          + f". {len(_info['models']) - 1} others: /brain {provider} models [search] lists them; "
                          f"/brain {provider} <name> picks one; /think <level> sets effort.{C_RESET}")
                _ctx = _cb.context_window(provider)
                if _ctx and not self._context_pinned:
                    self.context_tokens = _ctx
                self.current_model = _cb.BRAINS[provider] + (f" · {_cur['label']}" if _cur and _cur["id"] else "")
                plan = {"codex": "on your ChatGPT plan (it counts toward your Codex limit)",
                        "claude-code": "on your Claude plan (it counts toward its usage limits)"}.get(
                            provider, f"on your {_cb.PLANS.get(provider, '')} plan")
                from hearth.persona import NAME as _me
                print(f"{C_OK}{_me} now thinks with {_cb.BRAINS[provider]}{C_RESET} {C_DIM}{plan}. "
                      f"Helpers keep using the local model. /brain local to switch back.{C_RESET}")
                return True

            # /brain forget <provider>
            if provider == "forget":
                if len(parts) < 3 or not parts[2].strip():
                    print(f"{C_ERR}/brain forget needs a provider: /brain forget grok{C_RESET}")
                    return True
                target = parts[2].strip().lower()
                saved_keys = _load_keys()
                if target in saved_keys:
                    saved_keys.pop(target, None)
                    _save_keys(saved_keys)
                    print(f"{C_OK}forgot saved key for {target}{C_RESET}")
                else:
                    print(f"{C_DIM}no saved key for {target}{C_RESET}")
                return True

            saved_keys = _load_keys()
            arg = parts[2].strip() if len(parts) > 2 else ""
            # Three flavors of "local":
            #   - local     : whatever's at localhost:1234 (most permissive; LM Studio if up, builtin if booted, etc.)
            #   - lmstudio  : alias of local, but assumes LM Studio specifically. Hint message tells user what to load.
            #   - builtin   : Hearth's bundled llama-cpp-python server. Doesn't START anything - `/models use <n>` does that.
            # We route lmstudio + builtin through the same URL but the
            # confirmation print differs so first-timers know which one they
            # just picked. The conflict detection in llmserver.start_builtin
            # keeps them from colliding at runtime.
            # A local server is asked what it has loaded (below), so no model
            # name is pinned: a pinned one made LM Studio load a model that may
            # not be there. None of the three needs a key.
            try:
                from hearth.llmserver import BUILTIN_PORT as _bport
            except Exception:
                from hearth.local_engine import DEFAULT_PORT as _bport
            _LOCAL = ("local", "lmstudio", "builtin")
            # Where each cloud provider answers is written down once, in
            # headless. The model beside it is only a first guess.
            from hearth.headless import PROVIDERS as _known
            _first_guess = {"grok": "grok-4.3", "gemini": "gemini-2.5-flash", "openai": "gpt-4o-mini",
                            "anthropic": "claude-sonnet-5-5", "openrouter": "anthropic/claude-3.5-sonnet",
                            "groq": "openai/gpt-oss-120b"}
            presets_url_model = {
                "local":      (_local_base(), ""),
                "lmstudio":   ("http://localhost:1234/v1", ""),
                "builtin":    (f"http://localhost:{_bport}/v1", ""),
                **{p: (u, _first_guess.get(p, "")) for p, (_label, u) in _known.items()},
            }

            if provider == "custom":
                # custom needs URL + key explicit each time (no good default)
                if len(parts) < 3 or " " not in parts[2]:
                    # Try to recover from saved
                    sc = saved_keys.get("custom") or {}
                    if sc.get("url") and sc.get("key"):
                        url, key = sc["url"], sc["key"]
                        model_hint = sc.get("model", "")
                        print(f"{C_DIM}using saved custom: {url}{C_RESET}")
                    else:
                        print(f"{C_ERR}/brain custom needs URL and key: /brain custom <url> <key>{C_RESET}")
                        return True
                else:
                    url, _, key = parts[2].partition(" ")
                    key = key.strip()
                    model_hint = ""
            elif provider in presets_url_model:
                url, model_hint = presets_url_model[provider]
                # What follows the provider is a key, a model, or both, in any
                # order ("/brain openrouter qwen/qwen3.8-27b:free", "/brain
                # gemini AIza… gemini-3.5-flash"). Only a key is saved as one.
                words = [w for w in arg.split() if w.lower() != "use"]
                given_key = next((w for w in words if _looks_like_key(w)), "")
                given_model = next((w for w in words if not _looks_like_key(w)), "")
                key = given_key or _read_brain_key(provider)
                if not key and provider not in _LOCAL:
                    print(f"{C_ERR}no saved key for {provider}. Use: /brain {provider} <api-key>{C_RESET}")
                    print(f"{C_DIM}(saved next time; type just /brain {provider} after that){C_RESET}")
                    return True
                if provider not in _LOCAL:
                    from hearth.headless import find_model, provider_models, settle_model
                    if given_model:
                        listed = provider_models(url, key) or []
                        model_hint = find_model(listed, given_model) or given_model
                        if listed and not find_model(listed, given_model):
                            print(f"{C_WARN}{provider} doesn't list {given_model}; trying it anyway. "
                                  f"/models <name> searches what it has.{C_RESET}")
                    elif saved_keys.get(provider, {}).get("model"):
                        model_hint = saved_keys[provider]["model"]
                    elif model_hint:
                        # A name from the table above is a starting guess, and
                        # what the provider lists decides.
                        settled = settle_model(url, key, model_hint)
                        if settled and settled.rsplit("/", 1)[-1] != model_hint.rsplit("/", 1)[-1]:
                            print(f"{C_DIM}{provider} doesn't list {model_hint}; using {settled}. "
                                  f"/models shows the rest.{C_RESET}")
                        model_hint = settled or model_hint
            else:
                _all = ["local", "lmstudio", "builtin", *_known, "custom", "claude-code", "codex", "cursor", "forget"]
                _guess = _did_you_mean(provider, _all)
                print(f"{C_ERR}Unknown provider {provider!r}."
                      + (f" Did you mean {C_TOOL}/brain {_guess}{C_RESET}{C_ERR}?" if _guess else "")
                      + f" Try: {', '.join(_all)}.{C_RESET}")
                return True

            # Mutate the runtime + env so the next chat call picks up the new
            # endpoint. (`global` already declared above.) The shared runner
            # module follows too: the context size and the persona read it.
            LOCAL_API_BASE = url
            LOCAL_API_KEY = key or "hearth-builtin"
            try:
                import hearth.headless as _hlm
                _hlm.LOCAL_API_BASE, _hlm.LOCAL_API_KEY = url, LOCAL_API_KEY
            except Exception:
                pass
            if model_hint:
                LOCAL_MODEL = model_hint
                self.current_model = model_hint
            elif provider in _LOCAL:
                LOCAL_MODEL = ""                 # the server says what it has
                os.environ.pop("LOCAL_MODEL", None)
            os.environ["LOCAL_API_BASE"] = url
            if key:
                os.environ["LOCAL_API_KEY"] = key
            else:
                os.environ.pop("LOCAL_API_KEY", None)
            if model_hint:
                os.environ["LOCAL_MODEL"] = model_hint

            # Rebuild the AsyncOpenAI client so the next request uses new creds
            try:
                self.client = _anthropic_chat.async_client(LOCAL_API_BASE, LOCAL_API_KEY, _stream_client_timeout())
            except Exception as e:
                print(f"{C_WARN}client rebuild warning: {e}{C_RESET}")

            # Persist: save the key under its provider key so future
            # `/brain <provider>` (no arg) auto-loads it.
            if provider not in _LOCAL and key and (provider == "custom" or _looks_like_key(key)):
                saved_keys[provider] = {"url": url, "key": key, "model": model_hint}
                _save_keys(saved_keys)
                # tighten file perms on POSIX (no-op on Windows; NTFS ACL
                # already restricts to the user)
                try:
                    if os.name == "posix":
                        os.chmod(keys_path, 0o600)
                except OSError:
                    pass

            # Also update settings.json so the GUI Settings → Chat brain pane
            # reflects this choice on next open (matches /api/llm-endpoint).
            try:
                saved = {}
                if os.path.exists(settings_path):
                    try:
                        with open(settings_path, "r", encoding="utf-8") as f:
                            saved = _json.load(f) or {}
                    except Exception:
                        pass
                # A local server says what it has loaded, so no name is kept
                # for it; a cloud model name left over would be sent to it.
                saved.update({
                    "llm_provider": provider,
                    "llm_url": url,
                    "llm_key": key,
                    "llm_model": "" if provider in _LOCAL else (model_hint or saved.get("llm_model", "")),
                })
                os.makedirs(WORKSPACE, exist_ok=True)
                with open(settings_path, "w", encoding="utf-8") as f:
                    _json.dump(saved, f, indent=2)
            except Exception:
                pass

            # For a local brain the configured model name is often stale: it
            # lingers in settings.json from an earlier session, so the CLI would
            # cheerfully announce a model the user doesn't have and then fail
            # every request against it. Ask the endpoint what it's actually
            # serving and report that, or say plainly that nothing is up yet.
            _shown = model_hint
            if provider in _LOCAL:
                _live = _probe_live_model(url, LOCAL_API_KEY or "hearth-builtin")
                if _live:
                    LOCAL_MODEL = _live
                    os.environ["LOCAL_MODEL"] = _live
                self.current_model = _live or ""
                _shown = _live
            print(f"{C_OK}brain → {provider}{C_RESET}  "
                  f"{C_DIM}({url}"
                  f"{' · ' + _shown if _shown else ' · no model server running yet'}){C_RESET}")
            # Re-detect the context window for the NEW brain. Without this the
            # budget kept the previous model's value (e.g. a 32K local default),
            # so a 1M-ctx cloud model like grok-4.3 got compacted at 32K. /model
            # already does this; /brain didn't - this is that fix.
            try:
                from hearth.headless import resolve_context_tokens
                _tok, _src = resolve_context_tokens(self.current_model)
                if _tok and _tok > 1024:
                    self.context_tokens = _tok
                    print(f"{C_DIM}  context auto-set to {_tok:,} tokens ({_src}){C_RESET}")
            except Exception:
                pass
            # Friendly first-timer hints - tells the user what to DO next
            # depending on which "local" variant they picked.
            if provider == "lmstudio":
                print(f"{C_DIM}  Expecting LM Studio at {url}. If you don't have LM Studio:{C_RESET}")
                print(f"{C_DIM}    • Download from https://lmstudio.ai (free)  · OR{C_RESET}")
                print(f"{C_DIM}    • Use Inglen's built-in server: /brain builtin then /models use <n>{C_RESET}")
            elif provider == "builtin":
                print(f"{C_DIM}  Built-in mode picked. To load a model:{C_RESET}")
                print(f"{C_DIM}    1. /models           → see what's on disk{C_RESET}")
                print(f"{C_DIM}    2. /models use <n>   → boot Inglen's llama.cpp server with that model{C_RESET}")
                print(f"{C_DIM}    Notes:{C_RESET}")
                print(f"{C_DIM}      • Inglen re-uses any GGUFs you have in LM Studio's cache - no double-download.{C_RESET}")
            elif provider not in _LOCAL and provider != "custom" and given_key:
                print(f"{C_DIM}(key saved - next time just type /brain {provider}){C_RESET}")
            return True
        if low == "/tools":
            cats = tools_by_category()
            print(f"\n{C_BRAND}{len(TOOL_DEFINITIONS)} tools across {len(cats)} categories:{C_RESET}")
            for cat, tools in cats:
                print(f"\n{C_BOT}{cat}{C_DIM} ({len(tools)}){C_RESET}")
                for t in tools:
                    print(f"  {C_TOOL}{t['name']:<22}{C_RESET}{C_DIM}{t['description'][:74]}{C_RESET}")
            print()
            return True
        if low == "/clear":
            self.messages = [self.messages[0]]
            self._chat_new()
            # Deferred tools unlocked earlier are process-global, so without this
            # a cleared session keeps carrying whatever the last one opened and
            # the prompt never shrinks back.
            try:
                from hearth.tools import reset_unlocked_tools
                reset_unlocked_tools()
            except Exception:
                pass
            self.save_history()
            print(f"{C_OK}New chat.{C_RESET}")
            return True
        if low == "/chats" or (low.startswith("/chat ") and not low.startswith("/chat lm")):
            from hearth import chat_sync
            chats = chat_sync.listing(30)
            arg = cmd.split(None, 1)[1].strip() if low.startswith("/chat ") else ""
            if not arg:
                if not chats:
                    print(f"  {C_DIM}No chats yet.{C_RESET}")
                    return True
                from hearth import term_pick
                mine = (self._chat or {}).get("id")
                opts = []
                for i, c in enumerate(chats, 1):
                    where = {"terminal": "terminal", "reminder": "reminder"}.get(c["source"], "window")
                    here = " · this one" if c["id"] == mine else ""
                    opts.append((str(i), f"{c['title'][:70]}  {C_DIM}{where} · {c['count']} messages{here}{C_RESET}"))
                arg = await term_pick.pick(f"{C_BOT}Chats{C_RESET} {C_DIM}· from the window and here{C_RESET}", opts)
                if arg is None:
                    return True
            pick = chats[int(arg) - 1] if arg.isdigit() and 1 <= int(arg) <= len(chats) else next(
                (c for c in chats if arg.lower() in c["title"].lower()), None)
            if not pick:
                print(f"  {C_ERR}No chat matches '{arg}'. /chats lists them.{C_RESET}")
                return True
            d = chat_sync.load(pick["id"]) or {}
            turns = d.get("messages") or []
            self.messages = [self.messages[0]] + chat_sync.from_chat(turns)
            self._chat = {"id": pick["id"], "count": len(turns), "sent": len(self.messages)}
            self._chat_keep()
            print(f"  {C_OK}Carrying on: {pick['title']}{C_RESET} {C_DIM}({len(turns)} messages){C_RESET}")
            return True
        if low == "/chats lmstudio":
            self.all_chats_cache = self.get_all_lmstudio_chats()
            print(f"\n{C_BRAND}LM Studio threads:{C_RESET}")
            for i, c in enumerate(self.all_chats_cache[:25], 1):
                here = " (active)" if c["path"] == self.active_chat_file else ""
                print(f"  [{i}] {c['name']}{C_DIM}{here}{C_RESET}")
            print()
            return True
        if low.startswith("/chat lm "):
            arg = cmd.split(None, 2)[2].strip().strip("[]")
            if not self.all_chats_cache:
                self.all_chats_cache = self.get_all_lmstudio_chats()
            chosen = None
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(self.all_chats_cache):
                    chosen = self.all_chats_cache[idx]
                else:
                    print(f"{C_ERR}out of range - /chats to see the list{C_RESET}")
                    return True
            else:
                # partial name match (case-insensitive)
                q = arg.lower()
                for c in self.all_chats_cache:
                    if q in c["name"].lower():
                        chosen = c
                        break
                if not chosen:
                    print(f"{C_ERR}no thread matches '{arg}'. /chats for the list{C_RESET}")
                    return True
            self.load_history(chosen["path"])
            print(f"{C_OK}loaded (read-only copy): {chosen['name']}{C_RESET}")
            print(f"{C_DIM}saves go to: {HISTORY_FILE}{C_RESET}")
            return True
        if low == "/history":
            for m in self.messages[-5:]:
                role = m.get("role", "?")
                content = m.get("content") or ""
                if isinstance(content, list):   # text and image parts
                    content = " ".join(
                        b.get("text", "") if b.get("type") == "text" else "[image]"
                        for b in content if isinstance(b, dict))
                content = str(content)[:140].replace("\n", " ")
                print(f"  [{role}] {content}")
            return True
        if low == "/workspace":
            print(f"  {WORKSPACE}")
            from hearth import tools as _t
            extras = _t.list_extra_workspaces()
            if extras:
                print(f"{C_DIM}  extra writeable:{C_RESET}")
                for p in extras:
                    print(f"    {p}")
            return True
        if low in ("/import-memory", "/import") or low.startswith("/import-memory ") or low.startswith("/import "):
            parts = cmd.split(None, 1)
            if len(parts) < 2:
                # No file given - show the user how to pull their memory out of
                # another AI and import it. File-based on purpose: pasting a
                # multi-line dump into Windows Terminal only sends line 1.
                print(f"{C_TOOL}Import your memory from ChatGPT / Claude / any AI:{C_RESET}")
                print(f"  {C_DIM}1.{C_RESET} Paste this into that AI:")
                print(f'     {C_DIM}"List everything you know or remember about me as plain'
                      f' bullet points\n      - name, work, preferences, projects, important dates,'
                      f' people.\n      One fact per line, no preamble."{C_RESET}')
                print(f"  {C_DIM}2.{C_RESET} Save its reply into a text file (e.g. {C_TOOL}dump.txt{C_RESET}).")
                print(f"  {C_DIM}3.{C_RESET} Run:  {C_TOOL}/import-memory dump.txt{C_RESET}")
                return True
            path = os.path.expanduser(parts[1].strip().strip('"').strip("'"))
            if not os.path.isfile(path):
                print(f"{C_ERR}no file at {path}{C_RESET}")
                return True
            try:
                text = open(path, encoding="utf-8", errors="replace").read().strip()
            except OSError as e:
                print(f"{C_ERR}couldn't read it: {e}{C_RESET}")
                return True
            if not text:
                print(f"{C_DIM}that file is empty.{C_RESET}")
                return True
            print(f"{C_DIM}  importing {len(text)} chars through the fact extractor (with dedup)...{C_RESET}")
            try:
                from hearth import memory_extract as _mx
                import openai as _oai
                _sync = _oai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
                _llm = _mx.make_openai_llm_call(_sync, self.current_model)
                _msgs = [{"role": "user",
                          "content": "Here is everything another AI remembered about me - "
                                     "save the durable facts:\n\n" + text}]
                saved, _warns = _mx.extract_and_save(_msgs, _llm, recent_turns=1)
                if saved:
                    print(f"{C_OK}  imported {len(saved)} memory(ies):{C_RESET}")
                    for f in saved:
                        print(f"    • {f.get('title', '')}")
                else:
                    print(f"{C_DIM}  nothing new saved (already known, or no durable facts found).{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}  import failed: {type(e).__name__}: {e}{C_RESET}")
            return True
        if low == "/allowed":
            from hearth import tools as _t
            extras = _t.list_extra_workspaces()
            if not extras:
                print(f"{C_DIM}(no extra paths - writes confined to {WORKSPACE}){C_RESET}")
            else:
                print(f"{C_DIM}main workspace:{C_RESET} {WORKSPACE}")
                print(f"{C_DIM}extra writeable:{C_RESET}")
                for p in extras:
                    print(f"  {p}")
            return True
        if low.startswith("/allow "):
            from hearth import tools as _t
            path = cmd.split(None, 1)[1].strip().strip('"').strip("'")
            result = _t.add_extra_workspace(path)
            if result.startswith("Error"):
                print(f"{C_ERR}{result}{C_RESET}")
            else:
                print(f"{C_OK}{result}{C_RESET}")
            return True
        if low.startswith("/disallow "):
            from hearth import tools as _t
            path = cmd.split(None, 1)[1].strip().strip('"').strip("'")
            result = _t.remove_extra_workspace(path)
            print(f"{C_OK}{result}{C_RESET}")
            return True
        if low == "/log" or low.startswith("/log "):
            n = 15
            parts = cmd.split()
            if len(parts) > 1 and parts[1].isdigit():
                n = int(parts[1])
            try:
                with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-n:]
                for ln in lines:
                    try:
                        rec = json.loads(ln)
                        ts = rec.get("ts", "")
                        ev = rec.get("event", "")
                        tool = rec.get("tool", "")
                        extra = ""
                        if ev == "call":
                            extra = json.dumps(rec.get("args", {}), ensure_ascii=False)[:80]
                        elif ev == "result":
                            extra = f"{rec.get('chars', 0)}c {rec.get('ms', 0)}ms"
                        elif ev == "error":
                            extra = rec.get("error", "")
                        print(f"  {C_DIM}{ts}{C_RESET} {C_TOOL}{ev:<7}{C_RESET} {tool}  {C_DIM}{extra}{C_RESET}")
                    except json.JSONDecodeError:
                        continue
            except FileNotFoundError:
                print(f"{C_DIM}(no activity yet){C_RESET}")
            return True
        if low == "/tokens":
            est = self._ctx_used()
            pct = est / self.context_tokens * 100
            how = "measured" if getattr(self, "_ctx_measured", False) else "estimated"
            print(f"  {est} tokens / {self.context_tokens} ({pct:.0f}%, {how})")
            return True
        if low.startswith("/context"):
            parts = cmd.split()
            # Sanity ceiling - no real provider serves >1M ctx today; values
            # above this are almost certainly a typo (a transcript showed
            # /context 100000000000 being accepted silently, which made the
            # ring math nonsense and confused downstream trim logic).
            _CTX_MAX = 1_048_576
            _CTX_MIN = 1024
            if len(parts) >= 2 and parts[1].lower() in ("auto", "detect"):
                try:
                    from hearth.headless import resolve_context_tokens
                    detected, src = resolve_context_tokens(self.current_model)
                except Exception:
                    detected = autodetect_context(self.current_model)
                    src = "v1/models probe"
                if detected:
                    self.context_tokens = detected
                    self._context_pinned = False
                    print(f"{C_OK}context auto-detected: {detected} tokens ({src}){C_RESET}")
                else:
                    print(f"{C_ERR}could not detect ctx - endpoint didn't expose loaded_context_length{C_RESET}")
            elif len(parts) >= 2:
                # Accept 20480, "32k", "1M", "1.5m", "128K" - not just bare digits.
                _raw = parts[1].lower().replace(",", "").strip()
                _mult = 1
                if _raw.endswith("k"):
                    _mult, _raw = 1000, _raw[:-1]
                elif _raw.endswith("m"):
                    _mult, _raw = 1_000_000, _raw[:-1]
                try:
                    requested = int(float(_raw) * _mult)
                except ValueError:
                    print(f"{C_ERR}usage: /context <number> (e.g. 20480, 32k, 1M) or /context auto{C_RESET}")
                    return True
                if requested > _CTX_MAX:
                    print(f"{C_ERR}context {requested:,} is above the 1M ceiling - capping to {_CTX_MAX:,}.{C_RESET}")
                    print(f"{C_DIM}  no real model accepts >1M tokens. If you wanted N thousand, drop the extra zeros.{C_RESET}")
                    requested = _CTX_MAX
                elif requested < _CTX_MIN:
                    print(f"{C_ERR}context {requested} is below the {_CTX_MIN} floor - capping to {_CTX_MIN}.{C_RESET}")
                    requested = _CTX_MIN
                self.context_tokens = requested
                self._context_pinned = True
                print(f"{C_OK}context window → {self.context_tokens:,} tokens (pinned){C_RESET}")
                if requested < MIN_USABLE_CTX:
                    print(f"{C_WARN}  heads up: Inglen's persona + tools are ~20K tokens, so under "
                          f"~{MIN_USABLE_CTX//1024}K leaves almost no room for chat and the model "
                          f"will overflow fast. 24K+ is comfortable.{C_RESET}")
            else:
                # Live usage, not just the limit — persona + tools + chat.
                used = self._ctx_used()
                try:
                    tool_tok = len(json.dumps(self.openai_tools)) // CHARS_PER_TOKEN
                except Exception:
                    tool_tok = 0
                pct = int(100 * used / max(1, self.context_tokens))
                free = max(0, self.context_tokens - used - tool_tok)
                bar_n = min(20, pct * 20 // 100)
                bar = "█" * bar_n + "·" * (20 - bar_n)
                print(f"  window:  {self.context_tokens:,} tokens ({'pinned' if self._context_pinned else 'auto-detected'})")
                print(f"  used:    {used:,} in messages + ~{tool_tok:,} tools = ~{used+tool_tok:,} ({pct}%)")
                print(f"  free:    ~{free:,} tokens for more conversation  {C_DIM}{bar}{C_RESET}")
                if self.context_tokens < MIN_USABLE_CTX:
                    print(f"{C_WARN}  window is below ~{MIN_USABLE_CTX//1024}K — tight for Inglen's ~20K prompt; 24K+ recommended.{C_RESET}")
                print(f"{C_DIM}  set: /context <number> (20480, 32k, 1M)  ·  /context auto (re-detect){C_RESET}")
            return True
        if low == "/listen" or low.startswith("/listen "):
            parts = cmd.split()
            arg = parts[1].lower() if len(parts) > 1 else ""
            if arg in ("on", "1", "true", "start"):
                # Continuous mode - background listener races with typed input
                if self.listen_continuous:
                    print(f"{C_DIM}already listening{C_RESET}")
                    return True
                self._stt_loop = asyncio.get_event_loop()

                def _on_utterance(text: str):
                    # Called from STT thread. Hand off to the asyncio loop.
                    if self._stt_loop:
                        self._stt_loop.call_soon_threadsafe(
                            self._stt_queue.put_nowait, text
                        )

                # If the model isn't cached yet, the first call downloads it
                # (~150MB). Tell the user so the silence doesn't look frozen.
                st = stt.status()
                if not st.get("model_loaded"):
                    print(f"{C_DIM}getting ready to listen ({st.get('model_size', 'base.en')}; the first time downloads it){C_RESET}")
                status_msg = stt.start_continuous(_on_utterance)
                if status_msg.startswith("Error"):
                    print(f"{C_ERR}{status_msg}{C_RESET}")
                else:
                    self.listen_continuous = True
                    # Show the desktop voice HUD — a topmost, click-through dot
                    # grid that swells when Hearth speaks (driven by
                    # voice.is_speaking()), so you SEE it's listening/talking even
                    # with no window focused. Best-effort, Windows-only.
                    try:
                        from hearth import voice_overlay as _vov
                        _vov.start()
                    except Exception:
                        pass
                    print(f"{C_OK}listening: ON - talk any time. type to interrupt.{C_RESET}")
                return True
            if arg in ("off", "0", "false", "stop"):
                stt.stop_continuous()
                self.listen_continuous = False
                try:
                    from hearth import voice_overlay as _vov
                    _vov.stop()
                except Exception:
                    pass
                print(f"{C_OK}listening: off{C_RESET}")
                return True
            if arg == "status":
                import json as _json
                print(_json.dumps(stt.status(), indent=2))
                return True
            # No arg → one-shot record
            if not stt.is_available():
                st = stt.status()
                print(f"{C_ERR}Listening isn't ready.{C_RESET}")
                if st.get("last_load_error"):
                    print(f"{C_DIM}  {st['last_load_error']}{C_RESET}")
                else:
                    print(f"{C_DIM}  install: pip install faster-whisper sounddevice numpy{C_RESET}")
                return True
            print(f"{C_DIM}listening… speak, then pause{C_RESET}")
            text = await asyncio.to_thread(stt.listen_once)
            if text:
                print(f"{C_OK}heard: {C_RESET}{text}")
                # Treat as a user message immediately
                expanded = self._resolve_attachments(_sanitize(text))
                self.messages.append({"role": "user", "content": expanded})
                if self.voice_on:
                    voice.stop()
                self._responding = True
                try:
                    await self.respond()
                finally:
                    self._responding = False
                asyncio.create_task(self._maybe_extract_facts())
            else:
                print(f"{C_DIM}(nothing heard){C_RESET}")
            return True
        if low == "/agents" or low.startswith("/agents "):
            # /agents: the agent CLIs installed here and whether a newer one is
            # out. /agents update <id> updates one. /agents setup <id> installs
            # one and opens its sign-in.
            from hearth import external_agents as _xa
            words = cmd.split()
            if len(words) >= 3 and words[1].lower() == "setup":
                aid = {"claude": "claude-code", "cursor": "cursor-agent"}.get(words[2].lower(), words[2].lower())
                if aid not in _xa.SETUP:
                    print(f"{C_ERR}I can set up: {', '.join(_xa.SETUP)}{C_RESET}")
                    return True
                label = _xa.AGENTS[aid]["label"]
                if not _xa.find(aid):
                    print(f"{C_DIM}installing {label} with its own installer… (a minute or two){C_RESET}")
                    r = _xa.install(aid)
                    if not r.get("ok"):
                        print(f"{C_ERR}{r.get('error')}{C_RESET}")
                        return True
                    print(f"{C_OK}{label} is installed.{C_RESET}")
                if _xa.signed_in(aid, fresh=True):
                    print(f"{C_OK}{label} is signed in. Use it with /brain {aid}{C_RESET}")
                    return True
                r = _xa.sign_in(aid)
                print(f"{C_DIM}A window opened with {label}'s own sign-in. Finish there, then: /brain {aid}{C_RESET}"
                      if r.get("ok") else f"{C_ERR}{r.get('error')}{C_RESET}")
                return True
            if len(words) >= 3 and words[1].lower() == "update":
                print(f"{C_DIM}updating {words[2]}…{C_RESET}")
                r = _xa.update(words[2].lower())
                print(f"{C_OK}now on {r.get('version') or 'the latest'}{C_RESET}" if r.get("ok")
                      else f"{C_ERR}{r.get('error')}{C_RESET}")
                return True
            newer = {u["id"]: u for u in _xa.updates()}
            found = _xa.available()
            if not found:
                print(f"{C_DIM}No agent CLIs are installed here.{C_RESET}")
            for a in found:
                u = newer.get(a["id"])
                print(f"  {C_TOOL}{a['id']:<14}{C_RESET}{a['label']}"
                      + (f"  {C_OK}{u['latest']} is out (you have {u['installed']}): /agents update {a['id']}{C_RESET}"
                         if u else ""))
            missing = [aid for aid in _xa.SETUP if not _xa.find(aid)]
            if missing:
                print(f"  {C_DIM}Not here yet, and I can set them up: " + ", ".join(
                    f"/agents setup {m}" for m in missing) + f"{C_RESET}")
            return True
        if low.startswith("/think"):
            # What this brain's model offers: off (where it can be), its own
            # default, and the effort levels its source lists. Bare /think picks.
            parts = cmd.split()
            arg = parts[1].lower() if len(parts) >= 2 else ""
            try:
                from hearth import claude_brain as _cb
                _agent = _cb.current()
            except ImportError:
                _agent = None
            if _agent:
                info = _cb.agent_models(_agent)
                levels = next((m["efforts"] for m in info["models"] if m["id"] == info["model"]), [])
                can_off, now = False, (info.get("effort") or "default")
                if not levels:
                    print(f"{C_DIM}{info.get('label') or _agent} takes no thinking level for this model; "
                          f"it decides how hard to think on its own.{C_RESET}")
                    return True
            else:
                _facts = {}
                try:
                    from hearth import headless as _hl
                    # A cloud model's levels from its provider's list and the catalog;
                    # a local server's thinking is on or off.
                    _facts = (_hl.effort_facts(LOCAL_API_BASE, self.current_model or "")
                              if _hl.provider_for(LOCAL_API_BASE) != "local" else {})
                except Exception:
                    pass
                if _facts.get("reasoning") is False:
                    print(f"{C_DIM}{self._model_label()} doesn't reason, so there is no thinking to set.{C_RESET}")
                    return True
                levels = list(_facts.get("efforts") or [])
                can_off = _facts.get("can_off") is not False
                now = "off" if not self.think_on else (getattr(self, "think_level", "") or "default")
            opts = ([("off", "Off: answer straight away")] if can_off else []) \
                + [("default", "On: as hard as the model decides")] \
                + [(lv, f"{lv.capitalize()}") for lv in levels]
            if arg in ("on", "1", "true", "yes"):
                arg = "default"
            if arg in ("0", "false", "no"):
                arg = "off"
            if not arg:
                from hearth import term_pick
                arg = await term_pick.pick(f"{C_BOT}Thinking{C_RESET} {C_DIM}· {self._model_label()}{C_RESET}",
                                           [(v, l) for v, l in opts], default=now)
                if arg is None:
                    return True
            if arg not in [v for v, _ in opts]:
                print(f"{C_DIM}This model takes: {', '.join(v for v, _ in opts)}.{C_RESET}")
                return True
            if _agent:
                _save_setting("think_default", True)
                _save_setting("think_level", "" if arg == "default" else arg)
            elif arg == "off":
                self.think_on = False
            else:
                self.think_on = True
                self.think_level = "" if arg == "default" else arg
            said = {"off": "Thinking off", "default": "Thinking on, as hard as the model decides"}.get(
                arg, f"Thinking at {arg}")
            print(f"{C_DIM}↳ {said}{C_RESET}")
            return True
        if low == "/multi":
            self.multiline_mode = not self.multiline_mode
            if self.multiline_mode:
                print(f"{C_OK}multi-line mode: ON{C_RESET}")
                print(f"{C_DIM}  Enter = newline · Esc+Enter = submit{C_RESET}")
            else:
                print(f"{C_OK}multi-line mode: off{C_RESET}")
                print(f"{C_DIM}  Enter = submit · Esc+Enter = newline · paste = single block{C_RESET}")
            return True
        if low == "/mode" or low.startswith("/mode "):
            from hearth.tools import approval_mode, APPROVAL_MODES
            words = {"ask": "Ask: asks before anything that acts on your machine",
                     "edits": "Auto-edit: file edits in the workspace go through; commands still ask",
                     "auto": "Auto: routine work runs; deletes, sends, uploads, pushes and installs still ask"}
            arg = low[5:].strip()
            if not arg:
                from hearth import term_pick
                arg = await term_pick.pick(f"{C_BOT}What runs without asking{C_RESET}",
                                           [(m, words[m]) for m in APPROVAL_MODES], default=approval_mode())
                if arg is None:
                    return True
            arg = {"auto-edit": "edits", "autoedit": "edits", "edit": "edits"}.get(arg, arg)
            if arg not in APPROVAL_MODES:
                print(f"{C_ERR}/mode ask | edits | auto{C_RESET}")
                return True
            _save_setting("approval_mode", arg)
            print(f"{C_DIM}↳ {words[arg]}{C_RESET}")
            return True
        if low == "/perms":
            if not self.tool_perms:
                print(f"{C_DIM}(no per-tool permissions saved yet){C_RESET}")
            else:
                for tool, perm in self.tool_perms.items():
                    color = C_OK if perm == "always" else C_ERR
                    print(f"  {C_TOOL}{tool:<22}{C_RESET}{color}{perm}{C_RESET}")
            print(f"{C_DIM}  /perms forget <tool> to drop one; /perms reset to clear all{C_RESET}")
            return True
        if low == "/perms reset":
            self.tool_perms.clear()
            _save_persisted_perms(self.tool_perms)
            print(f"{C_OK}per-tool permissions cleared (also wiped from disk){C_RESET}")
            return True
        if low.startswith("/perms forget "):
            tool = cmd.split(None, 2)[2].strip() if len(cmd.split(None, 2)) > 2 else ""
            if tool in self.tool_perms:
                self.tool_perms.pop(tool)
                _save_persisted_perms(self.tool_perms)
                print(f"{C_OK}forgot {tool} - it'll ask again next time{C_RESET}")
            elif tool:
                print(f"{C_DIM}no saved permission for {tool} (try /perms){C_RESET}")
            else:
                print(f"{C_DIM}usage: /perms forget <tool>{C_RESET}")
            return True
        if low == "/compact":
            n_before = len(self.messages)
            self.messages = await asyncio.to_thread(
                compact_history, self.messages, self._summarize, 8
            )
            n_after = len(self.messages)
            print(f"{C_OK}Compacted: {n_before} → {n_after} messages.{C_RESET}")
            self.save_history()
            return True
        if low == "/curate" or low.startswith("/curate "):
            # /curate         - dry-run: list duplicate-topic memory clusters
            # /curate apply   - keep newest of each, archive the rest (recoverable)
            from hearth import memory as _mem
            do_apply = low.startswith("/curate ") and "apply" in low
            res = _mem.curate(apply=do_apply)
            clusters = res.get("clusters", [])
            if not clusters:
                print(f"{C_OK}memory's clean{C_RESET} {C_DIM}- no duplicate-topic clusters found.{C_RESET}")
                return True
            print(f"\n{C_BRAND}Duplicate-topic memories{C_RESET} {C_DIM}({len(clusters)} cluster"
                  f"{'s' if len(clusters) != 1 else ''}){C_RESET}")
            for c in clusters:
                print(f"  {C_OK}keep{C_RESET} {c['kept']}  {C_DIM}· dup(s): {', '.join(c['dups'])}{C_RESET}")
            if do_apply:
                print(f"\n{C_OK}archived {len(res.get('archived', []))} dup(s){C_RESET} "
                      f"{C_DIM}→ memory/_archive (recoverable). Kept the newest of each.{C_RESET}")
            else:
                print(f"\n  {C_DIM}dry run. Run {C_RESET}{C_TOOL}/curate apply{C_RESET}{C_DIM} to "
                      f"archive the older dups (kept newest; recoverable from _archive).{C_RESET}")
            return True
        if low == "/mem" or low.startswith("/mem "):
            # /mem            - flat index (legacy)
            # /mem tree       - ASCII tree (type → sub-category → fact)
            # /mem map        - open the GUI memory tab in default browser
            parts = cmd.split(None, 1)
            sub = (parts[1].strip().lower() if len(parts) > 1 else "").split()[0] if len(parts) > 1 else ""
            if sub in ("tree", "t"):
                self._print_memory_tree()
            elif sub in ("map", "graph", "g"):
                self._open_memory_map()
            else:
                idx = memory.list_index()
                print(idx if idx else "(empty)")
                print(f"\n{C_DIM}/mem tree - ASCII tree by category"
                      f"  ·  /mem map - open visual graph in browser{C_RESET}")
            return True
        if low == "/rules":
            memory.ensure_rules_exist()
            print(f"  {memory.RULES_PATH}")
            print(f"  {C_DIM}edit freely - the assistant re-reads it every turn{C_RESET}")
            return True
        if low == "/name" or low.startswith("/name "):
            # /name             - show current agent name
            # /name <new>       - rename the agent (persona only, no folder
            #                     rename here - that's a GUI-only flow because
            #                     it requires a tray respawn). For CLI this
            #                     just hot-swaps the persona NAME constant +
            #                     persists to settings so the next launch
            #                     uses it. Folder rename via GUI Settings.
            import re as _re
            parts = cmd.split(None, 1)
            new_name = (parts[1].strip() if len(parts) > 1 else "")
            from hearth import persona as _persona
            if not new_name:
                print(f"  current: {C_OK}{_persona.NAME}{C_RESET}")
                print(f"  {C_DIM}usage: /name <NewName> "
                      f"(letters/digits/spaces, 1-20 chars){C_RESET}")
                print(f"  {C_DIM}for full rename incl. workspace folder "
                      f"~/{_persona.NAME} → ~/<NewName>, use the GUI "
                      f"Settings → Behavior → Rename button.{C_RESET}")
                return True
            if not _re.match(r"^[A-Za-z0-9 ]{1,20}$", new_name):
                print(f"  {C_ERR}name must be 1-20 chars, letters/digits/spaces only{C_RESET}")
                return True
            try:
                old = _persona.NAME
                _persona.NAME = new_name
                os.environ["HEARTH_PERSONA_NAME"] = new_name
                self._agent_name = new_name
                # Persist so next CLI launch picks it up. Use the settings
                # API path the GUI shares.
                from pathlib import Path as _P
                settings_path = _P(WORKSPACE) / "settings.json"
                if settings_path.is_file():
                    import json as _json
                    try:
                        with open(settings_path, "r", encoding="utf-8") as f:
                            saved = _json.load(f)
                    except Exception:
                        saved = {}
                    saved["agent_name"] = new_name
                    with open(settings_path, "w", encoding="utf-8") as f:
                        _json.dump(saved, f, indent=2)
                print(f"  {C_OK}I'm {new_name} now{C_RESET} "
                      f"{C_DIM}(was {old}){C_RESET}")
                print(f"  {C_DIM}persona hot-swapped; next chat turn uses new name. "
                      f"Workspace folder ~/{old} unchanged - use GUI to move it.{C_RESET}")
            except Exception as e:
                print(f"  {C_ERR}rename failed: {e}{C_RESET}")
            return True
        if low == "/jobs" or low.startswith("/jobs "):
            # /jobs                  -> list active + recently-finished
            # /jobs all              -> include old completed
            # /jobs <id>             -> tail that job's log + result
            # /jobs kill <id>        -> stop a running job
            from hearth import jobs as _jobs
            parts = cmd.split()
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "kill" and len(parts) > 2:
                r = _jobs.kill_job(parts[2])
                print(f"  {C_OK if r.get('ok') else C_ERR}{r}{C_RESET}")
                return True
            if sub and sub.startswith("j-"):
                # Treat as a job id - show meta + result + log tail
                g = _jobs.get_job(sub, tail_lines=40)
                if not g.get("ok"):
                    print(f"  {C_ERR}{g.get('error', 'no such job')}{C_RESET}")
                    return True
                print(f"  {C_TOOL}{g.get('status', '?')}{C_RESET}  "
                      f"{C_DIM}{g.get('description', '')}{C_RESET}")
                gr = _jobs.get_job_result(sub)
                if gr.get("ok") and gr.get("status") == "completed":
                    r = gr.get("result")
                    if isinstance(r, str):
                        print(f"\n{r[:2000]}")
                    else:
                        import json as _json
                        print(f"\n{_json.dumps(r, indent=2, default=str)[:2000]}")
                else:
                    print(f"  {C_DIM}{gr.get('note', gr.get('error', ''))}{C_RESET}")
                tail = g.get("tail", "")
                if tail:
                    print(f"\n{C_DIM}--- log tail ---{C_RESET}")
                    print(tail[-1500:])
                return True
            include_done = (sub == "all")
            active_only = not include_done
            items = _jobs.list_jobs(active_only=active_only)
            if not items:
                print(f"  {C_DIM}no {'active' if active_only else ''} jobs{C_RESET}")
                print(f"  {C_DIM}(disk_usage on a drive root auto-backgrounds - "
                      f"check this after asking the assistant to scan something){C_RESET}")
                return True
            for j in items:
                status = j.get("status", "?")
                color = (C_OK if status == "completed" else
                         C_WARN if status == "running" else
                         C_ERR if status == "failed" else C_DIM)
                desc = (j.get("description", "") or "")[:60]
                elapsed = ""
                if j.get("started_at"):
                    end = j.get("ended_at") or time.time()
                    elapsed = f"{end - j['started_at']:.1f}s"
                print(f"  {color}{status:10}{C_RESET}  "
                      f"{C_TOOL}{j.get('job_id', '')}{C_RESET}  "
                      f"{C_DIM}{elapsed:>8} {desc}{C_RESET}")
            print()
            print(f"  {C_DIM}/jobs <id>     show one job's result/log{C_RESET}")
            print(f"  {C_DIM}/jobs kill <id>  stop a running job{C_RESET}")
            print(f"  {C_DIM}/jobs all      include old completed jobs{C_RESET}")
            return True
        if low == "/mcp" or low.startswith("/mcp "):
            # /mcp                 -> status + how to use
            # /mcp edit            -> open ~/Jarvis/mcp.json in $EDITOR
            # /mcp config          -> print the snippet to paste into LM Studio / Claude Desktop
            # /mcp run             -> run hearth.mcp_server in this terminal (Ctrl-C to stop)
            parts = cmd.split()[1:]
            sub = parts[0].lower() if parts else ""
            from pathlib import Path as _P
            # Respect relocated workspace (pointer file + env) — not just ~/Jarvis
            try:
                from hearth.tools import WORKSPACE as _WS
                mcp_path = _P(_WS) / "mcp.json"
            except Exception:
                mcp_path = _P.home() / "Jarvis" / "mcp.json"
            if not sub:
                # Status: count Hearth's exported tools + show client-config file location
                try:
                    from hearth import TOOL_DEFINITIONS as _td
                    print(f"  {C_OK}Inglen is an MCP server{C_RESET}  "
                          f"{C_DIM}({len(_td)} tools exported via hearth.mcp_server){C_RESET}")
                except Exception:
                    pass
                exists = mcp_path.is_file()
                print(f"  {C_DIM}client config: {mcp_path} "
                      f"({'exists' if exists else 'not configured'}){C_RESET}")
                print()
                print(f"  {C_TOOL}/mcp run{C_RESET}        run as MCP server in this terminal (Ctrl-C to stop)")
                print(f"  {C_TOOL}/mcp edit{C_RESET}       open ~/Jarvis/mcp.json (outbound client config)")
                print(f"  {C_TOOL}/mcp config{C_RESET}     print the snippet to paste into LM Studio / Claude Desktop / Cursor")
                # Show outbound bridge status (the v0.8 runtime, not v0.7 stub).
                try:
                    from hearth import mcp_client as _mc
                    bridges = _mc.list_bridges()
                except Exception:
                    bridges = []
                if bridges:
                    print()
                    print(f"  {C_DIM}outbound bridges (Inglen USING other MCP servers):{C_RESET}")
                    for b in bridges:
                        color = (C_OK if b.get("state") == "connected"
                                 else C_WARN if b.get("state") in ("starting", "pending")
                                 else C_ERR)
                        ntools = len(b.get("tools", []))
                        err = b.get("error", "")
                        print(f"  {color}● {b.get('raw_name') or b['name']:20}{C_RESET} "
                              + (f"{C_DIM}{err}{C_RESET}" if err
                                 else f"{C_DIM}{ntools} tools{C_RESET}" if b.get("state") == "connected"
                                 else f"{C_DIM}starting{C_RESET}"))
                else:
                    print()
                    print(f"  {C_DIM}no outbound MCP servers configured yet. "
                          f"Run /mcp edit to add some.{C_RESET}")
                return True
            if sub == "edit":
                mcp_path.parent.mkdir(parents=True, exist_ok=True)
                if not mcp_path.is_file():
                    mcp_path.write_text(__import__("json").dumps({"mcpServers": {}}, indent=2), encoding="utf-8")
                editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "nano")
                try:
                    __import__("subprocess").Popen([editor, str(mcp_path)])
                    print(f"  {C_OK}opened {mcp_path} in {editor}{C_RESET}")
                except Exception as e:
                    print(f"  {C_ERR}could not open editor: {e}{C_RESET}")
                    print(f"  {C_DIM}path: {mcp_path}{C_RESET}")
                return True
            if sub == "config":
                import sys as _sys, os as _os
                if getattr(_sys, "frozen", False):
                    _launch = {"command": _sys.executable,
                               "args": ["--hearth-run-python", "-m", "hearth.mcp_server"]}
                else:
                    _launch = {"command": _sys.executable,
                               "args": [_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                                      "hearth", "mcp_server.py")]}
                snippet = __import__("json").dumps({"mcpServers": {"hearth": _launch}}, indent=2)
                print(f"  {C_DIM}Paste into the OTHER tool's mcp.json:{C_RESET}")
                print()
                for ln in snippet.split("\n"):
                    print(f"  {C_TOOL}{ln}{C_RESET}")
                return True
            if sub == "run":
                print(f"  {C_OK}starting hearth.mcp_server (Ctrl-C to stop){C_RESET}")
                try:
                    import runpy
                    runpy.run_module("hearth.mcp_server", run_name="__main__")
                except KeyboardInterrupt:
                    print(f"\n  {C_DIM}stopped{C_RESET}")
                except Exception as e:
                    print(f"  {C_ERR}mcp_server failed: {type(e).__name__}: {e}{C_RESET}")
                return True
            print(f"  {C_ERR}unknown /mcp subcommand: {sub}{C_RESET}")
            return True
        if low == "/agent" or low.startswith("/agent "):
            # /agent                           -> list available personas
            # /agent <slug> "<prompt text>"    -> spawn synchronously
            from hearth import subagents as _sa
            parts = cmd.split(None, 2)
            if len(parts) < 2:
                personas = _sa.list_personas()
                if not personas:
                    print(f"  {C_DIM}no personas under hearth/subagents/{C_RESET}")
                else:
                    print(f"  {C_OK}available subagent personas:{C_RESET}")
                    for p in personas:
                        if "error" in p:
                            print(f"  {C_ERR}  ! {p['slug']}: {p['error']}{C_RESET}")
                            continue
                        print(f"  {C_TOOL}  {p['slug']}{C_RESET} "
                              f"{C_DIM}({p['cost_class']}, max {p['max_turns']} turns){C_RESET}")
                        print(f"  {C_DIM}    {p['description']}{C_RESET}")
                        print(f"  {C_DIM}    tools: {', '.join(p['allowed_tools'])}{C_RESET}")
                print()
                print(f"  {C_DIM}usage: /agent <slug> \"<prompt>\"{C_RESET}")
                return True
            slug = parts[1]
            sub_prompt = parts[2].strip().strip('"').strip("'") if len(parts) > 2 else ""
            if not sub_prompt:
                print(f"  {C_ERR}prompt required: /agent {slug} \"<prompt>\"{C_RESET}")
                return True
            print(f"  {C_DIM}spawning {slug} sync...{C_RESET}")
            try:
                r = _sa.spawn_subagent(slug, sub_prompt)
            except Exception as e:
                print(f"  {C_ERR}spawn failed: {type(e).__name__}: {e}{C_RESET}")
                return True
            if r.get("ok"):
                elapsed = r.get("elapsed_s", "?")
                print(f"  {C_OK}done in {elapsed}s "
                      f"({r.get('turns', '?')} turns, "
                      f"used: {', '.join(r.get('used_tools', [])) or 'no tools'}){C_RESET}")
                print()
                print(r.get("text", "(empty)"))
            else:
                print(f"  {C_ERR}subagent failed: {r.get('error', 'unknown')}{C_RESET}")
            return True
        if low == "/migrate" or low.startswith("/migrate "):
            # /migrate                    -> usage hint
            # /migrate hermes             -> dry-run from $HERMES_HOME / ~/.hermes
            # /migrate openclaw           -> dry-run from $OPENCLAW_WORKSPACE_DIR / ~/.openclaw/workspace
            # /migrate hermes apply       -> actually write
            # /migrate hermes apply skills config  -> also park skills + import model/provider
            parts = cmd.split()[1:]
            if not parts:
                print(f"  {C_TOOL}/migrate hermes{C_RESET}                 dry-run from ~/.hermes (or HERMES_HOME)")
                print(f"  {C_TOOL}/migrate openclaw{C_RESET}               dry-run from ~/.openclaw/workspace")
                print(f"  {C_TOOL}/migrate <src> apply{C_RESET}            actually write to ~/Jarvis/memory")
                print(f"  {C_TOOL}/migrate <src> apply skills{C_RESET}     also park SKILL.md dirs under ~/Jarvis/imported_skills/")
                print(f"  {C_TOOL}/migrate <src> apply config{C_RESET}     also import the source's model/provider (no API keys)")
                print(f"  {C_DIM}for one-off markdown imports use: python -m hearth.migrate --from md --path FILE --apply{C_RESET}")
                return True
            source = parts[0].lower()
            if source not in ("hermes", "openclaw"):
                print(f"  {C_ERR}unknown source: {source}. Try hermes or openclaw.{C_RESET}")
                return True
            argv = ["--from", source]
            flags = {p.lower() for p in parts[1:]}
            if "apply" in flags:        argv.append("--apply")
            if "skills" in flags:       argv.append("--include-skills")
            if "config" in flags:       argv.append("--include-config")
            try:
                from hearth.migrate import main as _migrate_main
                # The migrator's argparse reads sys.argv, so patch it for this call.
                old_argv = sys.argv
                sys.argv = ["hearth.migrate"] + argv
                try:
                    _migrate_main()
                finally:
                    sys.argv = old_argv
            except Exception as e:
                print(f"  {C_ERR}migrate failed: {type(e).__name__}: {e}{C_RESET}")
            return True
        if low == "/stt" or low.startswith("/stt "):
            arg = cmd.split(None, 1)[1].strip() if len(cmd.split(None, 1)) > 1 else ""
            print(f"{C_OK}{stt.set_model(arg)}{C_RESET}")
            if arg:
                # The size is read from the environment by the push-to-talk path
                # and by voice mode, and from settings.json on the next launch
                # and by the window. Setting only the module left all three on
                # the old size.
                _env.set("STT_MODEL", stt.MODEL_SIZE)
                if _save_setting("stt_model", stt.MODEL_SIZE):
                    print(f"{C_DIM}  saved, and the window uses it too{C_RESET}")
            return True
        if low == "/sleep":
            self.sleep_mode = True
            print(f"{C_OK}sleep mode ON{C_RESET}  "
                  f"{C_DIM}The assistant is silent until you say '{self._sleep_wake_word}' "
                  f"(or type /wake).{C_RESET}")
            return True
        if low == "/wake":
            self.sleep_mode = False
            print(f"{C_OK}awake.{C_RESET}")
            return True
        if low == "/learn":
            print(f"{C_DIM}re-scanning your machine (hardware, models, drives)...{C_RESET}")
            try:
                from hearth.environment import learn_environment
                print(f"{C_OK}{learn_environment(endpoint=LOCAL_API_BASE)}{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}scan failed: {e}{C_RESET}")
            return True
        if low == "/lockdown" or low.startswith("/lockdown "):
            # Toggle read confinement at runtime. Writes are ALWAYS workspace-
            # confined; this controls whether READS can reach outside it too.
            import hearth.tools as _t
            parts = cmd.split()
            arg = parts[1].lower() if len(parts) > 1 else ""
            if arg in ("on", "1", "true"):
                _t.set_lockdown(True)
            elif arg in ("off", "0", "false"):
                _t.set_lockdown(False)
            elif arg == "":
                _t.set_lockdown(not _t.read_locked())
            elif arg != "status":
                print(f"{C_DIM}usage: /lockdown [on|off|status]{C_RESET}")
                return True
            if _t.read_locked():
                print(f"{C_OK}lockdown ON{C_RESET} {C_DIM}— reads confined to the workspace ({_t.WORKSPACE}). Kept across restarts, shared with the window.{C_RESET}")
            else:
                print(f"{C_OK}lockdown OFF{C_RESET} {C_DIM}— reads unrestricted (any file on disk). Writes stay workspace-confined regardless.{C_RESET}")
            return True
        if low in ("/voice", "/voices") or low.startswith("/voice ") or low.startswith("/voices "):
            # Mic picking, a level check and the speaker guard existed only in
            # the window, so a CLI user on the wrong input device had no way to
            # see it, let alone change it.
            parts = cmd.split()
            if len(parts) == 1:
                st = voice.status()
                print(json.dumps(st, indent=2))
                print(f"  toggle: {'ON' if self.voice_on else 'off'}")
                if not st["ready"] and st.get("last_load_error"):
                    print(f"{C_WARN}  → {st['last_load_error']}{C_RESET}")
            elif parts[1].lower() in ("reload", "refresh", "rescan"):
                voice.reload()
                st = voice.status()
                if st["ready"]:
                    print(f"{C_OK}voice re-detected: {st['engine']}{C_RESET}")
                else:
                    print(f"{C_ERR}still no engine.{C_RESET}")
                    if st.get("last_load_error"):
                        print(f"{C_WARN}  → {st['last_load_error']}{C_RESET}")
            elif parts[1].lower() in ("on", "1", "true"):
                voice.reload()  # always re-check in case files were just dropped
                if voice.is_available():
                    self.voice_on = True
                    print(f"{C_OK}voice: ON ({voice.status()['engine']}){C_RESET}")
                else:
                    print(f"{C_ERR}no voice engine ready. /voice for status.{C_RESET}")
            elif parts[1].lower() in ("off", "0", "false"):
                self.voice_on = False
                print(f"{C_OK}voice: off{C_RESET}")
            elif parts[1].lower() == "speed":
                if len(parts) >= 3:
                    new_speed = voice.set_speed(parts[2])
                    _env.set("VOICE_SPEED", str(new_speed))
                    _saved = _save_setting("voice_speed", new_speed)
                    print(f"{C_OK}TTS speed → {new_speed:g}x{C_RESET}"
                          + (f"  {C_DIM}saved{C_RESET}" if _saved else ""))
                    if self.voice_on and voice.is_available():
                        voice.stop()
                        voice.speak("speed updated", blocking=False)
                else:
                    print(f"{C_DIM}current TTS speed: {voice.status().get('default_speed')}x  "
                          f"(usage: /voice speed 1.2){C_RESET}")
            elif parts[1].lower() in ("devices", "mics"):
                self._voice_devices()
            elif parts[1].lower() == "device":
                self._voice_pick_device(parts[2] if len(parts) >= 3 else "")
            elif parts[1].lower() in ("mic-test", "mictest", "test"):
                self._voice_mic_test(parts[2] if len(parts) >= 3 else "")
            elif parts[1].lower() == "duplex":
                self._voice_duplex(parts[2].lower() if len(parts) >= 3 else "")
            elif parts[1].lower() in ("set", "voice", "name") and len(parts) >= 3:
                vname = parts[2]
                voice.set_default_voice(vname)
                _env.set("VOICE", vname)
                _saved = _save_setting("voice_name", vname)
                print(f"{C_OK}voice → {vname}{C_RESET}"
                      + (f"  {C_DIM}saved{C_RESET}" if _saved else ""))
                # Speak a sample so the user hears it immediately
                if self.voice_on and voice.is_available():
                    voice.stop()
                    voice.speak(voice.sample_line(vname)["text"], blocking=False)
            elif len(parts) >= 2 and (parts[1].startswith(("am_", "af_", "bm_", "bf_"))
                                       or parts[1].endswith(".onnx")):
                # bare /voice <name> shortcut
                vname = parts[1]
                voice.set_default_voice(vname)
                _env.set("VOICE", vname)
                _saved = _save_setting("voice_name", vname)
                print(f"{C_OK}voice → {vname}{C_RESET}"
                      + (f"  {C_DIM}saved{C_RESET}" if _saved else ""))
                if self.voice_on and voice.is_available():
                    voice.stop()
                    voice.speak(voice.sample_line(vname)["text"], blocking=False)
            return True
        if cmd.startswith("/"):
            _w = low.split()[0] if low.split() else low
            print(f"{C_ERR}No command {_w}.{C_RESET} {C_DIM}{_suggest_command(_w)}{C_RESET}")
            return True
        return False

    # -- Spinner ------------------------------------------------------------
    async def spinner(self, label: str = "thinking"):
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        dots = ["", ".", "..", "..."]
        i = 0
        t0 = time.time()
        # Esc-to-interrupt: prompt_toolkit isn't active mid-turn, so nothing
        # reads the keyboard while we stream. Poll the console directly (Windows
        # msvcrt) and set _respond_cancel — the respond() loop already bails on
        # it between chunks/tools (same flag Ctrl-C sets). This is what actually
        # makes the "esc to interrupt" hint true.
        try:
            import msvcrt as _kb
        except Exception:
            _kb = None
        try:
            while True:
                dt = time.time() - t0
                d = dots[(i // 4) % len(dots)]
                color = GRAD[i % len(GRAD)]
                if _kb is not None:
                    try:
                        while _kb.kbhit():
                            if _kb.getch() == b"\x1b":  # Esc
                                self._respond_cancel.set()
                                sys.stdout.write("\r\033[K" + C_DIM + "· interrupting…" + C_RESET + "\n")
                                sys.stdout.flush()
                                return
                    except Exception:
                        pass
                hint = f"  {C_DIM}· esc to interrupt{C_RESET}" if dt > 2 else ""
                sys.stdout.write(
                    f"\r{color}{frames[i % len(frames)]}{C_RESET} "
                    f"{C_DIM}{label}{d:<3} {dt:.1f}s{C_RESET}{hint}"
                )
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    # -- Voice input device, level check and the speaker guard ---------------
    def _voice_devices(self):
        try:
            devs = stt.list_input_devices()
        except Exception as e:
            print(f"{C_ERR}could not read the input devices: {e}{C_RESET}")
            return
        if not devs:
            print(f"{C_WARN}no input devices found. Plug a mic in, then "
                  f"/voice devices again.{C_RESET}")
            return
        try:
            picked = stt.input_device_index()
        except Exception:
            picked = None
        # None means no device was picked and the OS default is used, which may
        # not be any row below, so state it rather than leaving no marker and
        # letting the list imply nothing is selected.
        print(f"{C_DIM}in use: "
              + (f"device {picked}" if picked is not None
                 else "whichever mic Windows calls the default")
              + f"{C_RESET}")
        for d in devs:
            mark = "*" if (picked is not None and d.get("index") == picked) else " "
            tag = f" {C_DIM}(system default){C_RESET}" if d.get("default") else ""
            print(f" {mark} [{d.get('index')}] {d.get('name')}{tag}")
        # Device numbers are assigned by the OS and shift when hardware is
        # unplugged or reordered, so a saved pick can point at nothing. That
        # reads as "the mic stopped working" with no clue why.
        if picked is not None and picked not in {d.get("index") for d in devs}:
            print(f"{C_WARN}  device {picked} is saved but is not plugged in now, "
                  f"so nothing is being recorded. Pick one above, or "
                  f"/voice device default.{C_RESET}")
        print(f"{C_DIM}  /voice device <n> to pick one, /voice device default to "
              f"go back, /voice mic-test to check it.{C_RESET}")

    def _voice_pick_device(self, arg: str):
        arg = (arg or "").strip().lower()
        if not arg:
            print(f"{C_DIM}usage: /voice device <n|default>   "
                  f"(/voice devices lists them){C_RESET}")
            return
        if arg in ("default", "auto", "-1"):
            idx = -1
        else:
            try:
                idx = int(arg)
            except ValueError:
                print(f"{C_ERR}'{arg}' is not a device number. "
                      f"/voice devices lists them.{C_RESET}")
                return
            known = {d.get("index") for d in (stt.list_input_devices() or [])}
            if known and idx not in known:
                print(f"{C_ERR}no input device numbered {idx}. "
                      f"/voice devices lists them.{C_RESET}")
                return
        _env.set("VOICE_INPUT_DEVICE", str(idx))
        saved = _save_setting("voice_input_device", idx)
        # Kept by name as well: the number changes when hardware comes and goes.
        name = next((d.get("name", "") for d in (stt.list_input_devices() or [])
                     if d.get("index") == idx), "") if idx >= 0 else ""
        _env.set("VOICE_INPUT_NAME", name)
        _save_setting("voice_input_name", name)
        try:
            stt.rescan_devices()
        except Exception:
            pass
        where = "the system default" if idx < 0 else f"device {idx}"
        print(f"{C_OK}mic → {where}{C_RESET}"
              + (f"  {C_DIM}saved, and the window uses it too{C_RESET}" if saved else ""))

    def _voice_mic_test(self, arg: str):
        try:
            secs = float(arg) if arg else 1.6
        except ValueError:
            secs = 1.6
        secs = max(0.5, min(secs, 10.0))
        print(f"{C_DIM}listening for {secs:g}s, say something...{C_RESET}")
        try:
            r = stt.mic_test(secs)
        except Exception as e:
            print(f"{C_ERR}mic test failed: {e}{C_RESET}")
            return
        if not r.get("ok"):
            print(f"{C_ERR}mic test failed: {r.get('error', 'unknown')}{C_RESET}")
            return
        peak, rms = float(r.get("peak", 0.0)), float(r.get("rms", 0.0))
        bar = "#" * max(0, min(int(peak * 40), 40))
        print(f"  peak {peak:.3f}  rms {rms:.3f}  |{bar:<40}|")
        if r.get("heard"):
            print(f"{C_OK}heard you. This mic works.{C_RESET}")
        else:
            print(f"{C_WARN}nothing above the speech threshold. Either the wrong "
                  f"device is picked (/voice devices) or the input level is "
                  f"turned down in Windows sound settings.{C_RESET}")

    def _voice_duplex(self, arg: str):
        if arg in ("on", "1", "true", "yes"):
            want = True
        elif arg in ("off", "0", "false", "no"):
            want = False
        else:
            cur = voice.half_duplex_enabled()
            pinned = os.environ.get("HEARTH_VOICE_HALF_DUPLEX") is not None
            print(f"{C_DIM}half-duplex is {'ON' if cur else 'off'}"
                  f"{' (pinned by the environment)' if pinned else ''}.  "
                  f"ON holds the mic closed while Inglen speaks, which stops a "
                  f"speaker setup answering itself; saying the name first still cuts in. Off lets you interrupt by "
                  f"voice, which is what you want on a headset.  "
                  f"usage: /voice duplex on|off{C_RESET}")
            return
        os.environ["HEARTH_VOICE_HALF_DUPLEX"] = "1" if want else "0"
        saved = _save_setting("voice_half_duplex", want)
        print(f"{C_OK}half-duplex: {'ON' if want else 'off'}{C_RESET}  "
              + (f"{C_DIM}{'mic stays closed while Inglen speaks' if want else 'you can interrupt by voice'}"
                 f"{', saved' if saved else ''}{C_RESET}"))

    # -- Status footer ------------------------------------------------------
    def _model_label(self) -> str:
        """Short name for display. When Hearth boots a local GGUF the "model id"
        is its full path, which swallows the whole prompt line and the banner
        box. Show the filename; the full path is still in /about."""
        try:
            from hearth import claude_brain as _cb
            if _cb.active():
                return _cb.label()
        except ImportError:
            pass
        m = self.current_model or ""
        if not m or m == "local-model":
            return f"{C_BOLD}{C_WARN}no model{C_RESET}"
        if ("\\" in m or "/" in m) and m.lower().endswith(".gguf"):
            return os.path.basename(m)
        return m

    def _ctx_used(self) -> int:
        """Tokens the conversation takes: our estimate, corrected by what the
        server last reported it actually received."""
        return max(0, estimate_tokens(self.messages) + getattr(self, "_ctx_bias", 0))

    def _live_message(self):
        """Above the input: the line being written and the working line while a
        reply runs, then the prompt mark."""
        t = self._turn
        return ANSI((t.live() if t is not None else "") + f"{C_USER}❯ {C_RESET}")

    def _footer(self):
        """Under the input: what answers, how hard it thinks, what runs without
        asking, how full this chat is, and what this session has used."""
        if self.pt_session is not None and PASTES.holds(self.pt_session.default_buffer.text):
            return ANSI(f"{C_DIM}  paste again to expand{C_RESET}")
        parts = [self._model_label()]
        if self.think_on:
            parts.append("think " + (getattr(self, "think_level", "") or "on"))
        else:
            parts.append("think off")
        try:
            from hearth.tools import approval_mode
            parts.append({"ask": "Ask", "edits": "Auto-edit", "auto": "Auto"}.get(approval_mode(), approval_mode()))
        except Exception:
            pass
        try:
            pct = int(self._ctx_used() / self.context_tokens * 100)
            parts.append(f"ctx {pct}%")
        except Exception:
            pass
        u = self._session_use
        if u.get("in"):
            parts.append(f"↑{_term_render._k(u['in'])} ↓{_term_render._k(u.get('out', 0))}"
                         + (f" ${u['cost']:.3f}" if u.get("cost") else "")
                         + (f" ${u['plan_cost']:.3f} on plan" if u.get("plan_cost") else ""))
        try:
            from hearth import subagents as _sa
            # The same count the window's status uses: helpers actually working now,
            # not every transcript without an end line.
            n = sum(1 for a in _sa.team_state() if a.get("status") == "working")
            if n:
                parts.append(f"{n} helper{'s' if n > 1 else ''} working")
        except Exception:
            pass
        if self.voice_on:
            parts.append("voice on")
        if self.multiline_mode:
            parts.append("multi-line")
        return ANSI(f"{C_DIM} " + " · ".join(p for p in parts if p) + f"{C_RESET}")

    def _footer_prompt(self) -> str:
        try:
            est = self._ctx_used()
            pct = int(est / self.context_tokens * 100)
        except Exception:
            pct = 0
        bar_w = 12
        filled = max(0, min(bar_w, int(bar_w * pct / 100)))
        bar_color = C_OK if pct < 60 else C_WARN if pct < 85 else C_ERR
        bar = bar_color + "█" * filled + C_DIM + "·" * (bar_w - filled) + C_RESET
        v_on = "♪" if self.voice_on and voice.is_available() else " "
        l_on = " listening" if self.listen_continuous else ""
        multi = "  multi" if self.multiline_mode else ""
        # Lead with the agent's name (JARVIS) - it's who the user is talking to;
        # the model id rides along, dimmer, for transparency.
        an = getattr(self, "_agent_name", "")
        if not an:
            try:
                from hearth.persona import NAME as an
            except Exception:
                an = "JARVIS"
            self._agent_name = an
        # Compose status line then prompt symbol on next visual chunk
        return (
            f"{C_DIM}┌─ {C_RESET}{C_BOT}{an}{C_RESET}{C_DIM} · {self._model_label()} {C_RESET}"
            f"{bar} {C_DIM}{pct}%  {v_on}{l_on}{multi}{C_RESET}\n"
            f"{C_USER}❯ {C_RESET}"
        )

    # Match Windows/Unix-style absolute paths to real files in plain text:
    #  C:\foo\bar.png  /  "C:\foo bar.png"  /  /home/user/foo.jpg
    # Used to auto-attach images even without the @ prefix.
    _PATH_RE = re.compile(
        r'(?:"([A-Za-z]:[\\/][^"\n]+)"'      # "C:\path with spaces"
        r"|'([A-Za-z]:[\\/][^'\n]+)'"        # 'C:\...'
        r"|([A-Za-z]:[\\/][^\s\"']+)"        # bare C:\...
        r"|(/[^\s\"']+))"                     # bare /unix/path
    )

    def _resolve_attachments(self, text: str):
        """Pull file/image attachments out of the user's text.

        Two ways to attach:
          1. `@<path>` - explicit, anywhere in the message.
          2. Bare absolute path that points to an existing image file -
             auto-attached. Works for drag-drop in Windows terminals
             (which paste a quoted path).

        Text files only attach via `@`. Bare text-file paths are left
        alone since they're often legitimate references in conversation.

        Returns a string (no images) or a multimodal content list (text
        + image_url blocks) for the OpenAI vision format.
        """
        import base64
        import mimetypes

        images: List[Dict] = []
        text_parts: List[str] = [text]
        seen_paths: set = set()

        def _attach(raw_path: str, force_image: bool = False) -> None:
            cand = os.path.expanduser(raw_path.strip().strip('"').strip("'"))
            if not os.path.isabs(cand):
                cand = os.path.join(os.getcwd(), cand)
            cand = os.path.normpath(cand)
            if cand in seen_paths or not os.path.isfile(cand):
                return
            seen_paths.add(cand)
            ext = os.path.splitext(cand)[1].lower()
            if ext in IMAGE_EXTS:
                try:
                    from hearth.headless import image_for_model
                    url, scale = image_for_model(cand)
                    images.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                    })
                    text_parts.append(f"\n[attached image: {cand}]{scale}")
                except OSError:
                    text_parts.append(f"\n[could not read image {cand}]")
            elif force_image:
                # @<path> on a non-image file: extract + splice as text. Use the
                # smart read_file dispatcher (handles PDF/DOCX/XLSX/CSV/EPUB/…),
                # not a raw text read — parity with the GUI attach. Raw read is the
                # fallback for odd types the dispatcher declines.
                body = ""
                try:
                    from hearth import execute_tool as _et
                    _r = _et("read_file", {"path": cand})
                    if isinstance(_r, str) and _r.strip() and not _r.lstrip().startswith("Error"):
                        body = _r
                except Exception:
                    body = ""
                if not body:
                    try:
                        with open(cand, "r", encoding="utf-8", errors="replace") as f:
                            body = f.read()
                    except OSError:
                        text_parts.append(f"\n[could not read {cand}]")
                        return
                if len(body) > 8000:
                    body = body[:8000] + f"\n…[truncated, full {len(body)} chars]"
                text_parts.append(f"\n\n[attached file: {cand}]\n```\n{body}\n```")
            # else: bare text-file path mentioned in a sentence - leave alone

        # Pass 1: explicit @<path> tokens (any file type, force inline)
        for tok in text.split():
            if tok.startswith("@") and len(tok) > 1:
                _attach(tok[1:], force_image=True)

        # Pass 2: bare absolute paths anywhere in the text - only attach
        # if they're images, since a chat sentence often references text
        # paths conversationally and we don't want to splice every one.
        for m in self._PATH_RE.finditer(text):
            path = next(g for g in m.groups() if g)
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTS:
                _attach(path, force_image=False)

        # Pass 3: deictic references like "see that screenshot", "the image",
        # "that picture". If no image was attached above and we have a
        # remembered last_image_path, auto-attach it. Lets the user say
        # "what's in that screenshot?" after taking one without re-typing
        # the path.
        if not images and self.last_image_path and os.path.isfile(self.last_image_path):
            if re.search(
                r"\b(that|the|this|it|its|the one)\s+"
                r"(screenshot|image|picture|photo|pic|shot|cap)\b",
                text,
                re.I,
            ) or re.search(
                r"\b(see|view|look at|describe|analyze|read)\s+"
                r"(it|this|that|the\s+(screenshot|image|picture|photo|pic))\b",
                text,
                re.I,
            ):
                _attach(self.last_image_path, force_image=False)

        text_part = "".join(text_parts)
        if not images:
            return text_part
        return [{"type": "text", "text": text_part}] + images

    async def _read_choice(self, prompt: str) -> str:
        """Read a short answer (y/n/a/N) without conflicting with the main
        prompt_toolkit session. We construct a tiny one-shot pt session
        when pt is available, else fall back to threaded input()."""
        self._ensure_pt_session()
        if self.pt_session is not None:
            try:
                # prompt_async on the existing session - it temporarily
                # takes the terminal back and releases it cleanly.
                return await self.pt_session.prompt_async(ANSI(prompt))
            except (KeyboardInterrupt, EOFError):
                return "n"
        try:
            return await asyncio.to_thread(input, prompt)
        except (KeyboardInterrupt, EOFError):
            return "n"

    async def _read_input_or_listen(self) -> str:
        """Continuous-listen mode: race a typed prompt against the STT
        queue. Whichever produces input first wins; the other is cancelled.

        Important: cancelling prompt_toolkit's prompt_async cleanly restores
        the terminal. The listener thread keeps running regardless.
        """
        prompt_task = asyncio.create_task(self._read_input())
        listen_task = asyncio.create_task(self._stt_queue.get())
        done, pending = await asyncio.wait(
            [prompt_task, listen_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # If both finished simultaneously, prefer the typed input - user
        # intent is clearer when they actually type.
        if prompt_task in done:
            try:
                return prompt_task.result()
            except EOFError:
                raise
            except Exception:
                pass
        if listen_task in done:
            try:
                heard = listen_task.result()
                # Echo what we heard so the user has visual confirmation
                sys.stdout.write(f"{C_DIM}heard: {C_RESET}{heard}\n")
                sys.stdout.flush()
                return heard
            except Exception:
                return ""
        return ""

    async def _read_next(self) -> str:
        """Read the next turn's trigger: race typed input against (a) the STT
        queue when listening, and (b) the background-subagent-done event. If a
        background subagent finishes first, return _AUTO_CONTINUE so the loop
        picks up the result WITHOUT waiting for the user to type - the parent
        keeps working on its own, like a real agent should."""
        tasks: dict = {}
        prompt_task = asyncio.create_task(self._read_input())
        tasks[prompt_task] = "prompt"
        stt_task = None
        if self.listen_continuous:
            stt_task = asyncio.create_task(self._stt_queue.get())
            tasks[stt_task] = "stt"
        evt = getattr(self, "_subagent_done_evt", None)
        evt_task = asyncio.create_task(evt.wait()) if evt is not None else None
        if evt_task is not None:
            tasks[evt_task] = "evt"
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        def _cancel(*ts):
            for t in ts:
                if t is not None and not t.done():
                    t.cancel()

        async def _drain(*ts):
            for t in ts:
                if t is None:
                    continue
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        # Subagent-done won the race - but DON'T interrupt if the user is
        # mid-typing (that would overlap their line, defeating parallel work).
        # Auto-continue ONLY when the input buffer is empty (truly idle). If
        # they're typing, defer: keep their prompt alive; the notification stays
        # queued and surfaces when they submit.
        if evt_task is not None and evt_task in done and prompt_task not in done:
            typing = False
            try:
                app = getattr(self.pt_session, "app", None) if self.pt_session else None
                if app is not None and (app.current_buffer.text or "").strip():
                    typing = True
            except Exception:
                typing = False
            if evt is not None:
                evt.clear()
            if not typing:
                _cancel(prompt_task, stt_task, evt_task)
                await _drain(prompt_task, stt_task, evt_task)
                return _AUTO_CONTINUE
            # User is typing - let them finish; ignore this wake.
            _cancel(stt_task, evt_task)
            await _drain(stt_task, evt_task)
            try:
                return await prompt_task
            except EOFError:
                raise
            except Exception:
                return ""

        # Otherwise: typed input > STT. Cancel the losers.
        _cancel(prompt_task, stt_task, evt_task)
        await _drain(*(t for t in (prompt_task, stt_task, evt_task) if t not in done))
        if prompt_task in done:
            # End of input is how Ctrl+Z / Ctrl+D ask to leave, and it is also
            # what a closed console or a finished pipe looks like. EOFError is
            # an Exception, so the catch-all below turned it into an empty line:
            # the loop re-prompted, hit the same end of input at once, and spun,
            # measured at twenty thousand prompts in twenty-five seconds, never
            # reaching the exit that was written for it. It goes up untouched.
            try:
                return prompt_task.result()
            except EOFError:
                raise
            except Exception:
                return ""
        if stt_task is not None and stt_task in done:
            try:
                heard = stt_task.result()
                sys.stdout.write(f"{C_DIM}heard: {C_RESET}{heard}\n")
                sys.stdout.flush()
                return heard
            except Exception:
                return ""
        return ""

    # -- Main loop ----------------------------------------------------------
    async def _read_input(self) -> str:
        """Async-safe prompt read. With prompt_toolkit:
          - arrow keys / history (↑/↓) / ctrl-r reverse search
          - bracketed paste: pasted multi-line text comes in as one block
          - Esc+Enter adds a newline (single-line submit on plain Enter)
          - /multi toggles full multi-line mode (Enter = newline,
            Esc+Enter = submit). Useful for very long text.
        Plain input() fallback wrapped in a thread otherwise.

        IMPORTANT: must use prompt_async() - prompt() calls asyncio.run()
        internally and we're already inside the main event loop."""
        self._ensure_pt_session()
        if self.pt_session is not None:
            carry, self._carry = getattr(self, "_carry", ""), ""
            text = PASTES.expand(await self.pt_session.prompt_async(
                self._live_message, multiline=self.multiline_mode, default=carry))
            if text.strip():
                shown = text if len(text) < 400 else text[:400] + f"… ({len(text)} chars)"
                print(f"{C_USER}❯ {C_RESET}{BOLD_ON}{shown}{C_RESET}")
            return text
        return await asyncio.to_thread(input, self._footer_prompt())

    def _is_vision_capable(self) -> bool:
        """Whether the model this terminal talks to takes images, asked the
        same way the tools ask, cached per model."""
        cache = getattr(self, "_vision_cap_cache", {})
        mid = self.current_model or ""
        if mid not in cache:
            try:
                from hearth.tools import _loaded_model_is_vision
                cache[mid] = _loaded_model_is_vision(model=mid, base=LOCAL_API_BASE)
            except Exception:
                cache[mid] = False
            self._vision_cap_cache = cache
        return cache[mid]

    def _is_first_run(self) -> bool:
        """True if the user has essentially no memory yet - fresh install.
        Crude heuristic that avoids running the wizard every relaunch."""
        # First: honor the GUI's onboarded flag. If the user already
        # finished onboarding in the desktop app, don't make them sit
        # through the CLI wizard on first CLI launch - that was the
        # "double onboarding" pain.
        # Gate on the explicit `onboarded` flag ONLY, set at the END of a
        # successful run. The old fallback counted MEMORY.md entries, but
        # learn_environment writes ~3 entries near the START of onboarding, so a
        # run that CRASHED after that (e.g. the Linux tray crash on a fresh VM)
        # left MEMORY.md non-empty with no flag set, and every later launch of
        # BOTH the CLI and GUI then skipped onboarding entirely. Flag not set =
        # onboarding never completed = run it.
        try:
            from hearth.tools import WORKSPACE
            settings_path = os.path.join(WORKSPACE, "settings.json")
            if os.path.isfile(settings_path):
                with open(settings_path, "r", encoding="utf-8") as _sf:
                    if json.load(_sf).get("onboarded"):
                        return False
        except Exception:
            pass
        return True

    def _persist_onboarding(self, answers: Dict[str, str]) -> None:
        """Apply the answers the same way the window's setup does: who they are
        and how they want replies go to profile.md, choices to settings."""
        from hearth import onboarding
        try:
            onboarding.apply(answers)
        except Exception as e:
            print(f"  {C_ERR}could not save setup: {e}{C_RESET}")
            return
        # A new name applies to this session from the next message on.
        _agent = (answers.get("agent_name") or "").strip()
        if _agent:
            os.environ["HEARTH_PERSONA_NAME"] = _agent
            self._agent_name = _agent          # the prompt bar caches it
            try:
                from hearth import persona as _p
                _p.NAME = _agent
            except Exception:
                pass

    def _ask_personal(self, ask) -> Dict[str, str]:
        """The questions about the person and how Hearth should behave. Used by
        first-run setup and by /me. Enter skips a question and leaves it as is."""
        from hearth import onboarding
        answers: Dict[str, str] = {}

        def keep(key: str, value: str) -> None:
            if value:
                answers[key] = value

        keep("name", ask("What should I call you?"))
        keep("role", ask("What do you do? (e.g. student, web developer, video editor)"))
        agent = ask("What would you like to call me? (Enter to keep the current name)")
        if agent:
            if re.fullmatch(r"[A-Za-z0-9 ]{1,20}", agent):
                answers["agent_name"] = agent
            else:
                print(f"  {C_DIM}names are letters, digits and spaces, up to 20. Keeping the current one.{C_RESET}")
        print()
        tones = list(onboarding.TONE_LABELS.items())
        print(f"  {C_TOOL}How should I sound?{C_RESET}")
        for n, (_k, label) in enumerate(tones, 1):
            print(f"  {C_DIM}    {n}  {label}{C_RESET}")
        t = ask("Pick a number, or describe it in your own words")
        if t.isdigit() and 1 <= int(t) <= len(tones):
            answers["tone"] = tones[int(t) - 1][0]
        elif t:
            answers["tone"] = t
        keep("avoid", ask("Anything I should avoid, topics or language? (Enter for nothing)"))
        b = ask("Which browser should I open links in? (chrome / brave / edge / firefox, Enter for the system default)")
        if b:
            answers["browser"] = b
            keep("profile", ask(f"Which {b} profile? (its name in the profile picker, Enter for the default)"))
        print()
        a = ask("Will you hear me on headphones or speakers? (h / s, Enter to skip)").lower()
        if a[:1] in ("h", "s"):
            answers["audio"] = "headphones" if a[:1] == "h" else "speakers"
        lang = ask("What language will you speak to me in? (Enter for English, or a name like hindi, or 'any' to work it out each time)")
        if lang:
            code = "" if lang.lower() in ("any", "detect", "auto") else onboarding.language_code(lang)
            if code is None:
                print(f"  {C_DIM}I don't know that one by name. Settings, Voice lists the languages I can listen in.{C_RESET}")
            else:
                answers["language"] = code
                voice = onboarding.voice_for(code, _env.get("VOICE", "") or "")
                if voice:
                    answers["voice_name"] = voice
                    print(f"  {C_DIM}I'll listen for {onboarding.LANGUAGES[code]} and answer in a {onboarding.LANGUAGES[code]} voice.{C_RESET}")
                elif code and code != "en" and code not in onboarding._VOICE_LETTER:
                    print(f"  {C_DIM}I'll listen for {onboarding.LANGUAGES[code]}. No voice speaks it yet, so replies read out loud use an English voice.{C_RESET}")
        print()
        print(f"  {C_TOOL}Before I change things on your PC:{C_RESET}")
        print(f"  {C_DIM}    1  ask me every time{C_RESET}")
        print(f"  {C_DIM}    2  just edit files in my folder, ask before commands{C_RESET}")
        print(f"  {C_DIM}    3  go ahead, ask only before deleting or risky commands{C_RESET}")
        m = ask("Pick 1-3 (Enter for 1)")
        answers["approval_mode"] = {"2": "edits", "3": "auto"}.get(m, "ask")
        return answers

    async def _maybe_extract_facts(self) -> None:
        """Passive memory extraction at the end of a turn - auto-saves durable
        facts (name, preferences, projects, deadlines) WITHOUT the user saying
        'remember that', matching the GUI/headless behavior the CLI was missing.
        Fire-and-forget (zero added latency), guarded against overlapping runs,
        silent on failure. The extractor itself filters jokes/quotes + dedupes."""
        if os.environ.get("HEARTH_OLD_TERMINAL") != "1":
            return                       # the shared engine notes facts itself after each turn
        if getattr(self, "_extracting", False):
            return
        self._extracting = True
        try:
            from hearth import memory_extract as _mx
            import openai as _oai
            _sync = _oai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            _llm = _mx.make_openai_llm_call(_sync, self.current_model)
            _msgs = list(self.messages)
            # Save SILENTLY - this runs fire-and-forget, so any print lands at a
            # random time (mid-prompt) and is jarring in a terminal. Facts still
            # persist; check them with /memory. (The GUI shows its own toast,
            # where timing isn't an issue.)
            await asyncio.to_thread(
                _mx.extract_and_save, _msgs, _llm, recent_turns=4)
        except Exception:
            pass
        finally:
            self._extracting = False

    async def _maybe_run_onboarding(self) -> None:
        """First-run wizard. Asks ~5 quick questions, writes prefs to memory
        and rules.md so the persona has user context from message #1. Skip
        with HEARTH_NO_ONBOARDING=1 or by hitting Ctrl-C."""
        if _env.get("NO_ONBOARDING") in ("1", "true", "yes"):
            return
        if not self._is_first_run():
            return

        from hearth.persona import NAME as _me
        print()
        _type_out(f"  {C_BRAND}Hey, I'm {_me}.{C_RESET}")
        print(f"  {C_DIM}I run on your PC and do real things on it. I think with the AI you already pay for "
              f"(an agent like Claude Code or Codex) or a model of your own, remember what matters, send "
              f"helpers for bigger jobs, answer from your phone, and ask before anything risky.{C_RESET}")
        _type_out(f"  {C_DIM}A minute of setup so I fit how you work. Enter skips a question, "
                  f"Ctrl-C skips the rest.{C_RESET}", delay=0.004)
        print()

        # WHERE to keep files - ask BEFORE anything writes to disk, so a user
        # whose C: drive is full (or who just wants it elsewhere) can put the
        # workspace on another drive. Skip if already chosen (pointer exists).
        try:
            from hearth.tools import _WORKSPACE_POINTER, set_workspace_location, WORKSPACE as _CUR_WS
            if not os.path.isfile(_WORKSPACE_POINTER) and not _env.get("WORKSPACE"):
                _path_eg = "like D:\\Hearth" if os.name == "nt" else "like ~/hearth or /mnt/disk/hearth"
                loc = input(
                    f"  {C_TOOL}Where should I keep my files - memory, documents, models?{C_RESET}\n"
                    f"  {C_DIM}Enter for default ({_CUR_WS}), or a path on another drive "
                    f"{_path_eg}{C_RESET}\n  > "
                ).strip()
                if loc and os.path.abspath(os.path.expanduser(loc)) != os.path.abspath(_CUR_WS):
                    new = set_workspace_location(loc)
                    print(f"\n  {C_OK}Workspace set to {new}.{C_RESET}")
                    print(f"  {C_DIM}Close and reopen Inglen once to use it - then we'll "
                          f"finish setup there. (Move that folder anytime; just update "
                          f"this location to match.){C_RESET}\n")
                    raise SystemExit(0)
        except SystemExit:
            raise
        except (KeyboardInterrupt, EOFError):
            print()
        except Exception:
            pass

        # Learn the machine FIRST - hardware, installed models, drive map - so the
        # model walks in with real context (and never wastes 2 minutes disk-scanning
        # for things it could have just known). Same call backs the /learn command.
        _setup_rule(1, 4, "your machine")
        print(f"  {C_DIM}getting to know your machine (hardware, models, drives)...{C_RESET}")
        try:
            from hearth.environment import learn_environment
            print(f"  {C_OK}{learn_environment(endpoint=LOCAL_API_BASE)}{C_RESET}")
        except Exception as e:
            print(f"  {C_DIM}(machine scan skipped: {e}){C_RESET}")
        print()

        def ask(prompt: str) -> str:
            return input(f"  {C_TOOL}{prompt}{C_RESET}\n  > ").strip()

        # Brain choice - parity with the GUI's local-vs-cloud onboarding step.
        # Reuses the /brain command so key storage + endpoint switch + context
        # re-detect all happen through one code path. Default stays local.
        try:
            # Never name a model from settings here: it may not be loaded, and
            # a phantom name in the FIRST thing a new user reads makes every
            # later failure look like Hearth lying. Ask the endpoint instead.
            _live = _probe_live_model(LOCAL_API_BASE, LOCAL_API_KEY)
            _setup_rule(2, 4, "a brain")
            print(f"  {C_TOOL}Which model should I think with?{C_RESET}")
            print(f"  {C_DIM}    Local means it runs on your own GPU. Free, private, "
                  f"nothing leaves this machine.{C_RESET}")
            print(f"  {C_DIM}    Cloud means a hosted model. Faster and stronger, but "
                  f"it costs money and your messages go to that provider.{C_RESET}")
            if _live:
                print(f"  {C_DIM}    Running locally right now: {_model_name_short(_live)}{C_RESET}")
            else:
                print(f"  {C_DIM}    No local model is loaded yet. Pick local and I'll "
                      f"help you get one with /models.{C_RESET}")
            # An agent program already on this PC can be the brain on the plan
            # its owner pays for, with nothing to download.
            agents = {}
            try:
                from hearth import claude_brain as _cb
                agents = {c["id"].split("-")[0]: c for c in _cb.choices() if c["installed"]}
            except Exception:
                pass
            if agents:
                print(f"  {C_DIM}    Or an agent you already use, on your own plan: "
                      f"{', '.join(c['label'] for c in agents.values())}. My tools, memory and voice stay the same.{C_RESET}")
            brain = ask("Enter to stay local, or type: " + " / ".join(list(agents) + [
                "grok", "gemini", "openai", "openrouter", "groq", "custom"])).lower()
            if brain in agents:
                await self.handle_command(f"/brain {agents[brain]['id']}")
            elif brain in ("grok", "gemini", "openai", "openrouter", "groq"):
                key = ask(f"Paste your {brain} API key (saved locally, asked once)")
                if key:
                    await self.handle_command(f"/brain {brain} {key}")
                else:
                    print(f"  {C_DIM}no key - staying local. Switch anytime with /brain {brain} <key>.{C_RESET}")
            elif brain == "custom":
                # Any OpenAI-compatible endpoint.
                url = ask("Endpoint URL (e.g. https://api.together.xyz/v1)").strip()
                key = ask("Paste your API key (saved locally)")
                if url and key:
                    await self.handle_command(f"/brain custom {url} {key}")
                else:
                    print(f"  {C_DIM}need URL + key - staying local. Switch anytime with /brain custom <url> <key>.{C_RESET}")
            print()
            # Staying local -> help them pick + boot a model right now, so setup
            # ends with a WORKING server instead of a dead endpoint.
            if brain not in agents and _is_local_endpoint(LOCAL_API_BASE):
                await self._onboard_pick_local_model()
        except (KeyboardInterrupt, EOFError):
            print()

        answers: Dict[str, str] = {}
        _setup_rule(3, 4, "about you")
        try:
            answers = self._ask_personal(ask)
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C_DIM}  ok, skipping the rest. /me asks these again any time.{C_RESET}\n")
            self._persist_onboarding(answers)
            return

        self._persist_onboarding(answers)
        print()
        print(f"  {C_OK}saved.{C_RESET}  "
              f"{C_DIM}/me changes any of it later.{C_RESET}")
        print()
        _setup_rule(4, 4, "bring your history")
        # Offer importing memory from another AI - most people already have
        # years of context in ChatGPT/Claude. /import-memory walks them through it.
        print(f"  {C_TOOL}Already use ChatGPT or Claude?{C_RESET}  "
              f"{C_DIM}Run {C_RESET}{C_TOOL}/import-memory{C_RESET}{C_DIM} anytime - it gives you a "
              f"prompt to\n  paste into that AI, then pulls your facts in here.{C_RESET}")
        print()

        # Migrate prompt - only shown when a prior agent install is detected.
        # Mirrors the GUI onboarding step 6 logic so CLI users get parity.
        try:
            from hearth import migrate as _mig
            sources_found = []
            hh = _mig._hermes_home()
            hmem = _mig._hermes_active_memory_dir(hh)
            if (hmem / "USER.md").is_file() or (hmem / "MEMORY.md").is_file():
                sources_found.append(("hermes", str(hh)))
            ows = _mig._openclaw_workspace_dir()
            if (ows / "MEMORY.md").is_file() or (ows / "memory").is_dir():
                sources_found.append(("openclaw", str(ows)))
            if sources_found:
                print(f"  {C_TOOL}Found a prior agent install:{C_RESET}")
                for i, (src, path) in enumerate(sources_found, 1):
                    print(f"  {C_DIM}  [{i}] {src} ({path}){C_RESET}")
                print(f"  {C_DIM}Import its memory into Inglen? "
                      f"(API keys never copied){C_RESET}")
                pick = ask("Pick 1-N or press Enter to skip")
                if pick.isdigit():
                    n = int(pick)
                    if 1 <= n <= len(sources_found):
                        src = sources_found[n - 1][0]
                        print(f"  {C_DIM}importing from {src}...{C_RESET}")
                        try:
                            import sys as _sys
                            old_argv = _sys.argv
                            _sys.argv = ["hearth.migrate", "--from", src, "--apply"]
                            try:
                                _mig.main()
                            finally:
                                _sys.argv = old_argv
                        except Exception as e:
                            print(f"  {C_ERR}migrate failed: {e}{C_RESET}")
                print()
        except Exception:
            pass  # migrate is opt-in, never block onboarding on its failure

        # Land on something to DO. Setup used to end on the migrate prompt,
        # which leaves a first-timer at a blank cursor with no idea what this
        # thing accepts — the moment most people close it and don't come back.
        print(f"  {C_BRAND}─ you're set ─{C_RESET}")
        print(f"  {C_DIM}Talk to me in your own words. I act on this machine, remember, and can "
              f"send helpers, so try:{C_RESET}")
        print(f"  {C_TOOL}    what's taking up space on my C drive?{C_RESET}")
        print(f"  {C_TOOL}    summarise the newest PDF in my Downloads{C_RESET}")
        print(f"  {C_TOOL}    compare three note-taking apps and remember which one I pick{C_RESET}")
        print(f"  {C_DIM}Anything that touches your system asks first. "
              f"{C_RESET}{C_TOOL}/help{C_RESET}{C_DIM} lists commands, "
              f"{C_RESET}{C_TOOL}/models{C_RESET}{C_DIM} manages models, "
              f"{C_RESET}{C_TOOL}/phone{C_RESET}{C_DIM} connects a chat app; the phone app pairs from the "
              f"window's Settings, Plus.{C_RESET}")
        print()

    async def _ask_user_interactive(self, question: str, options: list, allow_other: bool) -> dict:
        """CLI surface for the ask_user tool. Renders a numbered list, reads
        the user's pick. Runs on the main asyncio loop (the tool dispatcher
        bounces here via run_coroutine_threadsafe)."""
        print()
        print(f"{C_FRAME}╭─ {C_WARN}? {question}{C_RESET}")
        for i, opt in enumerate(options, 1):
            print(f"{C_FRAME}│  {C_ACCENT}[{i}]{C_RESET} {opt}")
        if allow_other:
            print(f"{C_FRAME}│  {C_DIM}[other] type a free-text answer instead{C_RESET}")
        print(f"{C_FRAME}╰─{C_RESET}")
        raw = (await self._read_choice(f"{C_FRAME}│ > {C_RESET}")).strip()
        if not raw:
            return {"ok": False, "error": "user gave no answer"}
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return {"ok": True, "choice": options[n - 1], "other": False}
        # Substring match - "y" picks "Yes", "first" picks "First option", etc.
        low = raw.lower()
        matches = [o for o in options if o.lower() == low] \
               or [o for o in options if o.lower().startswith(low)] \
               or [o for o in options if low in o.lower()]
        if len(matches) == 1:
            return {"ok": True, "choice": matches[0], "other": False}
        if allow_other:
            return {"ok": True, "choice": raw, "other": True}
        return {"ok": False, "error": f"ambiguous answer {raw!r}; pick a number 1-{len(options)}"}

    def _make_ask_user_bridge(self, loop: asyncio.AbstractEventLoop):
        """Return the sync callback the tool dispatcher will invoke. The
        callback runs in a worker thread (execute_tool is wrapped in
        asyncio.to_thread); it bounces the actual prompt back to the main
        event loop so prompt_toolkit doesn't fight with the worker."""
        def _bridge(question: str, options: list, allow_other: bool) -> dict:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._ask_user_interactive(question, options, allow_other), loop)
                return fut.result(timeout=180)
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return _bridge

    async def _extend_workspace_interactive(self, path: str) -> bool:
        """Ask whether to allow writes to a path outside the workspace.
        [y]es allows for this turn; [a]lways adds to EXTRA_WORKSPACES."""
        parent = os.path.dirname(path) or path
        print()
        print(f"{C_FRAME}╭─ {C_WARN}? extend workspace to write here?{C_RESET}")
        print(f"{C_FRAME}│ {C_DIM}path:   {C_RESET}{path}")
        print(f"{C_FRAME}│ {C_DIM}parent: {C_RESET}{parent}")
        print(f"{C_FRAME}│ {C_DIM}[y]es (this write only) / [a]lways "
              f"(add parent to EXTRA_WORKSPACES) / [n]o{C_RESET}")
        print(f"{C_FRAME}╰─{C_RESET}")
        raw = (await self._read_choice(f"{C_FRAME}│ > {C_RESET}")).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("a", "always"):
            return True  # tools._resolve_write adds parent on True; same effect
        return False

    def _make_extend_workspace_bridge(self, loop: asyncio.AbstractEventLoop):
        """Sync callback wired into tools.set_extend_workspace_callback. Same
        worker-thread -> main-loop bridge pattern as the ask_user one."""
        def _bridge(path: str) -> bool:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._extend_workspace_interactive(path), loop)
                return bool(fut.result(timeout=180))
            except Exception:
                return False
        return _bridge

    async def run(self):
        # The model can flip this to True via the end_session tool when it
        # judges the user is wrapping up. We finish the current response,
        # then exit on the next loop iteration.
        self._exit_requested = False
        # Register the CLI as the interactive surface for ask_user - the tool
        # handler bounces back to this loop via run_coroutine_threadsafe.
        try:
            from hearth.tools import set_ask_user_callback, set_extend_workspace_callback
            _loop = asyncio.get_running_loop()
            set_ask_user_callback(self._make_ask_user_bridge(_loop))
            set_extend_workspace_callback(self._make_extend_workspace_bridge(_loop))
        except Exception:
            pass  # ask_user / extend-workspace stay inert if registration fails
        # Bootstrap MCP client (spawns the servers configured in
        # ~/Jarvis/mcp.json so their tools appear in to_openai_tools()).
        try:
            from hearth import mcp_client
            r = mcp_client.bootstrap()
            if r.get("servers", 0) > 0:
                n = r["servers"]
                print(f"{C_DIM}● MCP: starting {n} server{'' if n == 1 else 's'} (/mcp shows them){C_RESET}")
        except Exception as e:
            print(f"{C_DIM}● MCP client bootstrap skipped: {e}{C_RESET}")
        # Passive update nudge: the GUI pops a modal on launch, the CLI only
        # ever checked on /update. Do a background, non-blocking check and print
        # ONE dim line if a newer release exists — never interrupt input, never
        # auto-install. Throttled to once/day and skippable via
        # HEARTH_NO_UPDATE_CHECK=1 (both handled in updater).
        if not os.environ.get("HEARTH_NO_UPDATE_CHECK"):
            import threading as _th
            def _upd_nudge():
                try:
                    import time as _t, json as _j
                    stamp = os.path.expanduser("~/.hearth/cli_update_check.json")
                    try:
                        if _t.time() - _j.load(open(stamp)).get("at", 0) < 86400:
                            return
                    except Exception:
                        pass
                    from hearth import updater
                    try:
                        os.makedirs(os.path.dirname(stamp), exist_ok=True)
                        _j.dump({"at": _t.time()}, open(stamp, "w"))
                    except Exception:
                        pass
                    # A git-clone install updates with `git pull`, not an installer —
                    # nudge only on a tagged RELEASE, never on day-to-day commits.
                    src = updater.check_source_update()
                    if src.get("source"):
                        if src.get("available"):
                            print(f"\n  {C_TOOL}● flagged update on main{C_RESET}{C_DIM}: "
                                  f"{src.get('subject', '')} ({src.get('sha', '')}). Run "
                                  f"{C_RESET}{C_TOOL}git pull{C_RESET}{C_DIM} to update this clone.{C_RESET}",
                                  flush=True)
                        return
                    r = updater.check_for_update(HEARTH_VERSION)
                    if r.get("ok") and r.get("available"):
                        _blurb = ""
                        if r.get("notes"):
                            _blurb = next((ln.strip() for ln in str(r["notes"]).splitlines()
                                           if ln.strip()), "")[:160]
                        print("\n" + _render_update_box(HEARTH_VERSION, r["latest"], _blurb))
                        print(f"  {C_DIM}run {C_RESET}{C_TOOL}/update{C_RESET}{C_DIM} to install{C_RESET}",
                              flush=True)
                except Exception:
                    pass
            _th.Thread(target=_upd_nudge, daemon=True).start()
        # Sync subagents inherit the CLI's cancel signal so a Ctrl-C
        # during a respond() stream also aborts any active child loop.
        try:
            from hearth import subagents as _sa
            _sa.set_parent_cancel_check(self._respond_cancel.is_set)
            # Auto-continue: when a BACKGROUND subagent finishes, signal the
            # input-wait so the parent picks up the result immediately instead
            # of idling until the user types. The completion cb runs in the
            # subagent's worker thread, so hop back onto this loop thread-safely.
            self._subagent_done_evt = asyncio.Event()
            _loop = asyncio.get_running_loop()

            def _on_bg_subagent_done(agent_id, result):
                _sa._default_completion_cb(agent_id, result)  # keep enqueue + toast
                try:
                    _loop.call_soon_threadsafe(self._subagent_done_evt.set)
                except Exception:
                    pass
            _sa.set_background_completion_callback(_on_bg_subagent_done)
        except Exception:
            pass
        # Reminder watcher - fire desktop notifications for due reminders,
        # including catch-up for any that came due while Hearth was closed.
        # The GUI starts this; the CLI never did, so reminders set in the CLI
        # (e.g. study reminders) were saved but NOTHING ever fired them. This is
        # why notifications never appeared in CLI-only use.
        try:
            from hearth import reminders as _rem

            def _reminder_fire(title, body):
                # ALWAYS print into the terminal - Windows silently suppresses OS
                # toasts (Focus Assist / notification settings), so a missed
                # reminder caught up at a late PC-boot showed nothing. Printing
                # here guarantees you SEE "while you were away..." in the chat.
                try:
                    print(f"\n{C_BOT}\U0001f514 {title}{C_RESET}  {C_DIM}{body}{C_RESET}")
                except Exception:
                    pass
                # Inject into the conversation so the reminder PERSISTS in history
                # and the model actually knows it fired - without this it was a
                # transient print that scrolled away on the next prompt redraw,
                # and asking "do I have anything?" returned nothing because the
                # model never saw it.
                try:
                    self.messages.append({
                        "role": "user",
                        "content": f"[Reminder fired at {title}] {body}",
                    })
                except Exception:
                    pass
                try:
                    _rem.desktop_notify(title, body)
                except Exception:
                    pass
            _rem.start_watcher(_reminder_fire)
        except Exception:
            pass
        # Memory upkeep (stale facts decay, duplicates merge) on the same timer
        # the window runs, so it doesn't wait for a fact to be saved.
        try:
            from hearth.web import _start_memory_maintenance_loop
            _start_memory_maintenance_loop()
        except Exception:
            pass
        await self._maybe_run_onboarding()

        # Every boot (after the one-time onboarding), cheaply re-check the machine
        # in the background and relearn into memory ONLY if it changed since last
        # launch (drive plugged in/out, GPU swap) - so context never goes stale.
        if _env.get("NO_ONBOARDING") not in ("1", "true", "yes"):
            def _env_refresh():
                try:
                    from hearth import environment as _envmod
                    msg = _envmod.refresh_environment_if_changed(endpoint=LOCAL_API_BASE)
                    if msg:
                        print(f"\n{C_DIM}● machine changed since last time — {msg}{C_RESET}")
                except Exception:
                    pass
            threading.Thread(target=_env_refresh, daemon=True).start()

        last_interrupt = 0.0
        while True:
            if self._exit_requested:
                self.save_history()
                print(f"{C_DIM}● session ended.{C_RESET}")
                sys.exit(0)
            try:
                # _read_next races typed input vs STT (if listening) vs a
                # background-subagent-done signal, so a finished subagent
                # auto-continues the task without the user having to type.
                # A command typed while the last reply ran goes first.
                waiting = getattr(self, "_after_turn", None)
                if waiting:
                    _raw_input = waiting.pop(0)
                    print(f"{C_USER}❯ {C_RESET}{_raw_input}")
                else:
                    _raw_input = await self._read_next()
            except KeyboardInterrupt:
                # Two ctrl+c within 2s = exit. Single ctrl+c = abandon line.
                now = time.time()
                if now - last_interrupt < 2.0:
                    print(f"\n{C_DIM}● bye.{C_RESET}")
                    self.save_history()
                    sys.exit(0)
                last_interrupt = now
                print(f"\n{C_DIM}(ctrl+c again to exit, or type /exit){C_RESET}")
                continue
            except EOFError:
                # ctrl+d / ctrl+z = exit straight away
                print(f"\n{C_DIM}● bye.{C_RESET}")
                self.save_history()
                sys.exit(0)
            if _raw_input == _AUTO_CONTINUE:
                # A background subagent finished while we were idle. Only act if
                # there's actually a pending notification (guard spurious wakes).
                try:
                    from hearth import subagents as _sa
                    if not _sa.peek_pending_notifications():
                        continue
                except Exception:
                    continue
                print(f"{C_DIM}● subagent finished - continuing automatically{C_RESET}")
                # Synthetic user trigger. The drain below injects the
                # <task-notification> as a SYSTEM message; this user-role line is
                # what makes the model continue (and keeps the sequence ending on
                # a user turn for strict local chat templates).
                user_input = ("A background task you started just finished - its "
                              "result is in the system notification above. Continue "
                              "the original task with it now; don't wait for me.")
            else:
                user_input = _raw_input.strip()
            if not user_input:
                continue
            # Strip lone surrogate chars before any string ops - Windows
            # terminals occasionally emit them for emojis and they crash
            # the UTF-8 encoder downstream.
            user_input = _sanitize(user_input)
            # Keep CLI/GUI on the same brain: if the GUI switched endpoints
            # while this session was open, adopt it now (no-op otherwise).
            self._sync_brain_from_settings()
            # New user turn = stop any in-flight TTS so audio doesn't
            # trail into the next reply, then clear the abort flag so the
            # assistant's response can actually speak.
            if self.voice_on:
                voice.stop()
                voice.reset_abort()
            if user_input.startswith("/") or user_input.lower() in ("exit", "quit"):
                if await self.handle_command(user_input):
                    continue
            # /sleep mode: drop input that doesn't start with the wake word.
            # Keeps the assistant silent at the desk until you actually call it.
            if self.sleep_mode:
                w = self._sleep_wake_word
                low = user_input.lower().lstrip()
                if w and (low.startswith(w + " ") or low == w
                          or low.startswith(w + ",") or low.startswith(w + ":")
                          or low.startswith(w + "?") or low.startswith(w + "!")):
                    # Strip the wake word, wake up, fall through to respond.
                    user_input = user_input[len(w):].lstrip(" ,:?!").lstrip()
                    self.sleep_mode = False
                    if not user_input:
                        print(f"{C_DIM}(awake - what's up?){C_RESET}")
                        continue
                else:
                    print(f"{C_DIM}(sleeping - say '{w}' or /wake){C_RESET}")
                    continue
            # Drain background subagent completion notifications BEFORE
            # the user's new prompt so the model sees them in arrival
            # order. Each notification is a synthetic user-role message
            # with a <task-notification> block.
            try:
                from hearth import subagents as _sa
                for notif in _sa.drain_pending_notifications():
                    xml = _sa.format_notification_as_user_message(notif)
                    # role=system, NOT user - a background subagent reporting
                    # back is a system event, not the user talking. It's
                    # immediately followed by the user's real prompt below, so
                    # the message sequence still ends on a user turn (keeps
                    # LM Studio's Jinja happy).
                    self.messages.append({"role": "system", "content": xml})
                    print(f"{C_DIM}● subagent done: "
                          f"{notif.get('persona', '?')} "
                          f"({notif.get('status', '?')}){C_RESET}")
            except Exception:
                pass
            # Expand @<path> file attachments (text or image)
            content = self._resolve_attachments(user_input)
            try:
                self._chat_pull()
            except Exception:
                pass
            self.messages.append({"role": "user", "content": content})
            # Ctrl-C during the response sets the cancel signal; the
            # stream loop checks it between chunks and bails cleanly
            # without killing the CLI. A second Ctrl-C inside 1s
            # falls through to the outer "exit?" handler.
            self._responding = True
            try:
                await self.respond()
                asyncio.create_task(self._maybe_extract_facts())
            except KeyboardInterrupt:
                self._respond_cancel.set()
                print(f"\n{C_DIM}● cancelling…{C_RESET}")
                # Give the stream + any tool a beat to notice + clean up
                await asyncio.sleep(0.2)
                last_interrupt = time.time()
            finally:
                self._responding = False

    def _extract_facts_to_memory(self, transcript: str) -> int:
        """Before summarizing, ask the LLM to surface durable facts from the
        chunk we're about to drop. Save each as a memory entry so it survives
        compaction. Returns number of facts saved."""
        try:
            import openai
            sync_client = openai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            r = chat_create(sync_client,
                model=self.current_model,
                messages=[
                    {"role": "system", "content":
                        "Extract durable facts about the USER (preferences, "
                        "setup, projects, contacts, important links) from "
                        "this transcript. Output ONLY a JSON array, no "
                        "prose. Each item: {\"title\": str, \"type\": "
                        "\"user\"|\"feedback\"|\"project\"|\"reference\", "
                        "\"description\": str, \"body\": str}. Skip "
                        "ephemeral chat. Empty array if nothing worth "
                        "saving. Max 5 items."},
                    {"role": "user", "content": transcript},
                ],
                temperature=0.0,
                max_tokens=600,
            )
            raw = r.choices[0].message.content or ""
            # Strip code fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            facts = json.loads(raw.strip() or "[]")
        except Exception:
            return 0
        if not isinstance(facts, list):
            return 0
        saved = 0
        for f in facts[:5]:
            if not isinstance(f, dict):
                continue
            title = (f.get("title") or "").strip()
            if not title:
                continue
            try:
                memory.save(
                    title=title,
                    mtype=f.get("type", "user"),
                    description=(f.get("description") or "")[:140],
                    body=f.get("body") or "",
                )
                saved += 1
            except Exception:
                pass
        return saved

    def _print_memory_tree(self) -> None:
        """Render the memory bank as an ASCII tree: type → sub-category → facts.
        Colors match the GUI's left-border stripes (4 type colors). Mirrors
        what `/mem map` shows visually, for terminal users."""
        from hearth.memory_classify import classify_or_default
        import os as _os
        mem_dir = memory.MEM_DIR
        if not _os.path.isdir(mem_dir):
            print(f"{C_DIM}(no memory dir yet - first save creates it){C_RESET}")
            return
        # Build {type: {sub: [name, ...]}} from the same logic as the GUI
        tree: Dict[str, Dict[str, list]] = {}
        for fn in sorted(_os.listdir(mem_dir)):
            if not fn.endswith(".md") or fn == "MEMORY.md":
                continue
            path = _os.path.join(mem_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[:15]
            except OSError:
                continue
            name = fn[:-3]; desc = ""; typ = ""; sub = ""
            in_fm = False
            for ln in lines:
                s = ln.strip()
                if s == "---":
                    if in_fm: break
                    in_fm = True; continue
                if in_fm:
                    if s.startswith("name:"):         name = s.split(":", 1)[1].strip()
                    elif s.startswith("description:"): desc = s.split(":", 1)[1].strip()
                    elif s.startswith("type:"):       typ = s.split(":", 1)[1].strip()
                    elif s.startswith("sub_category:"): sub = s.split(":", 1)[1].strip()
            if not sub:
                sub = classify_or_default(typ or "user", desc)
            t = typ if typ in ("user", "feedback", "project", "reference") else "other"
            tree.setdefault(t, {}).setdefault(sub or "casual", []).append((name, desc))
        if not tree:
            print(f"{C_DIM}(no memories yet - type something casual and the assistant will start saving facts){C_RESET}")
            return
        # Render in canonical order - same as the GUI
        type_order = ["user", "project", "reference", "feedback", "other"]
        type_colors = {
            "user":      C_BOT,    # blue
            "project":   C_OK,     # green
            "reference": C_TOOL,   # tool color
            "feedback":  C_BRAND,  # brand violet
            "other":     C_DIM,
        }
        type_labels = {
            "user": "User facts", "project": "Projects",
            "reference": "Reference", "feedback": "Feedback", "other": "Other",
        }
        for t in type_order:
            subs = tree.get(t)
            if not subs:
                continue
            total = sum(len(v) for v in subs.values())
            color = type_colors.get(t, C_RESET)
            print(f"\n{color}● {type_labels[t]}{C_RESET}  {C_DIM}({total}){C_RESET}")
            sub_keys = sorted(subs.keys())
            for i, sub in enumerate(sub_keys):
                leaves = subs[sub]
                is_last_sub = (i == len(sub_keys) - 1)
                sub_branch = "└─" if is_last_sub else "├─"
                print(f"  {C_DIM}{sub_branch}{C_RESET} {C_TOOL}{sub}{C_RESET} {C_DIM}({len(leaves)}){C_RESET}")
                for j, (name, desc) in enumerate(leaves):
                    is_last_leaf = (j == len(leaves) - 1)
                    leaf_branch = "└─" if is_last_leaf else "├─"
                    indent = "    " if is_last_sub else "  │ "
                    desc_short = (desc[:60] + "…") if len(desc) > 60 else desc
                    print(f"{indent}{C_DIM}{leaf_branch}{C_RESET} {name}  "
                          f"{C_DIM}{desc_short}{C_RESET}")
        print()

    def _open_memory_map(self) -> None:
        """Open the GUI's Memory tab (tree view) in the default browser.
        If the Hearth web backend isn't already running, spawn it on a free
        port and open that. The user gets the full visual graph without
        needing to switch to the GUI app."""
        import urllib.request, webbrowser, threading, time
        # Probe the standard GUI port first - if it answers, just open it.
        for port in (8765, 8766):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1):
                    url = f"http://127.0.0.1:{port}/?view=memory"
                    print(f"{C_OK}↳ opening memory map at {url}{C_RESET}")
                    webbrowser.open(url)
                    return
            except Exception:
                continue
        # Nothing running - spawn the web backend in a background thread
        print(f"{C_DIM}(no GUI server running - booting one on :8765 for you){C_RESET}")
        from hearth import web as _w
        try:
            threading.Thread(
                target=lambda: _w.serve(host="127.0.0.1", port=8765),
                daemon=True,
            ).start()
            time.sleep(1.2)
            url = "http://127.0.0.1:8765/?view=memory"
            print(f"{C_OK}↳ opening memory map at {url}{C_RESET}")
            webbrowser.open(url)
        except Exception as e:
            print(f"{C_ERR}Could not start GUI backend: {e}{C_RESET}")

    def _summarize(self, transcript: str) -> str:
        """Sync summarizer used by compact_history. Saves durable facts to
        memory FIRST (so they survive past the compaction), then produces
        a tight digest of what's left."""
        n_saved = self._extract_facts_to_memory(transcript)
        if n_saved:
            print(f"{C_DIM}[compact: saved {n_saved} fact(s) to memory]{C_RESET}")
        try:
            import openai
            sync_client = openai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            r = chat_create(sync_client,
                model=self.current_model,
                messages=[
                    {"role": "system", "content":
                        "You compress conversations. Read the transcript and "
                        "produce a tight digest (under 300 words) covering: "
                        "what the user wanted, what was tried, what was "
                        "learned, and the current state. Bullet points "
                        "fine. No fluff. Note: durable facts about the user "
                        "have already been saved to memory separately.\n\n"
                        "# IDENTIFIER PRESERVATION (CRITICAL)\n"
                        "Reproduce the FOLLOWING types of strings VERBATIM in "
                        "the digest, never paraphrased and never abbreviated:\n"
                        "  - file paths (C:\\..., /home/..., relative ./src/x.py)\n"
                        "  - URLs (https://..., file://...)\n"
                        "  - IP addresses, MAC addresses, ports\n"
                        "  - UUIDs and any *_id strings (request_id, session_id, etc)\n"
                        "  - exact error messages, stack-trace excerpts, exit codes\n"
                        "  - SHA hashes, git commit IDs, semver versions\n"
                        "  - tool names called and their key arguments\n"
                        "Hallucinated-reconstructed identifiers WILL break the "
                        "next turn. When in doubt, copy the original token."},
                    {"role": "user", "content": transcript},
                ],
                temperature=0.2,
                max_tokens=600,
            )
            return r.choices[0].message.content or "[empty summary]"
        except Exception as e:
            return f"[summary failed: {e}]"

    def _prepare_context(self) -> List[Dict]:
        """Refresh system prompt, auto-compact if too long, then trim to fit
        the model's context window. Returns the message list to send."""
        # 0) Publish live runtime info so the whoami tool reports the CURRENT
        # model/endpoint/context (stays in sync across /model + /context).
        set_runtime_info(model=self.current_model, endpoint=LOCAL_API_BASE,
                         context_tokens=self.context_tokens)

        # 1) Refresh system message every turn so rules.md + memory index are live
        # (and the voice-mode register flips with /voice on|off).
        try:
            _vision = self._is_vision_capable()  # cached per model id
        except Exception:
            _vision = None
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = fresh_system_message(think_on=self.think_on, voice_on=self.voice_on, vision=_vision)
        else:
            self.messages.insert(0, fresh_system_message(think_on=self.think_on, voice_on=self.voice_on, vision=_vision))
        # Strip stale base64 images from earlier turns (keep the most recent) so
        # they stop bloating the context + the saved history on every turn.
        _prune_old_images(self.messages, keep_last=1)

        # The tool schemas (~6K tok for 46 tools) ride in EVERY prompt but are
        # NOT in self.messages - so all budgeting must reserve room for them.
        # Without this, a restored/long history "fits" the message budget while
        # the REAL prompt (messages + tool schemas + output) overflows the
        # model's context; LM Studio then drops the user turn and crashes with
        # "No user query found in messages." (the mid-session model-switch bug).
        try:
            tool_tokens = len(json.dumps(self.openai_tools)) // CHARS_PER_TOKEN
        except Exception:
            tool_tokens = 0
        effective_ctx = max(2048, self.context_tokens - tool_tokens)

        # 2) Auto-compact at any SAFE boundary - i.e. not in the middle of an
        # in-flight tool chain. Safe = last message is a user turn, OR an
        # assistant text reply with no tool_calls (chain finished). Unsafe = the
        # last message is a `tool` reply or an assistant message with pending
        # tool_calls - compacting then can break the tool_call_id pairing or
        # strip the user query, crashing LM Studio's Jinja template with
        # "No user query found in messages.".
        # Older logic only allowed `last_role == 'user'`, which meant a long
        # tool chain that *finished* with an assistant answer never got
        # compacted until the next user turn - and on the way there
        # trim_to_budget would silently drop messages. That's why "compact at
        # 75%" wasn't firing in long sessions.
        est = estimate_tokens(self.messages)
        last_msg = self.messages[-1] if self.messages else {}
        last_role = last_msg.get("role")
        last_has_tool_calls = bool(last_msg.get("tool_calls"))
        safe_to_compact = (
            last_role == "user"
            or (last_role == "assistant" and not last_has_tool_calls)
        )
        if (est > effective_ctx * COMPACT_AT
                and len(self.messages) > 12
                and safe_to_compact):
            print(f"{C_DIM}[auto-compact: ~{est}+{tool_tokens}tools/{self.context_tokens} tokens]{C_RESET}")
            # target_chars = compact aggressively enough that the SUM of
            # head + summary + recent fits comfortably under the chat
            # budget. Without this, compact "succeeded" but the kept
            # tail still held 4 huge browse results - server wedged.
            target_chars = max(2000, effective_ctx * CHARS_PER_TOKEN // 2)
            self.messages = compact_history(self.messages, self._summarize,
                                            keep_recent=8,
                                            target_chars=target_chars)

        # 3) Hard trim to fit. Budget against effective_ctx so the tool schemas
        # + reserved output always have room.
        sent = trim_to_budget(self.messages, effective_ctx, RESERVED_OUTPUT)

        # 4) Invariant: the prompt must contain a user turn. LM Studio's chat
        # template raises "No user query found in messages" otherwise. Compaction
        # + trim on a very long single-turn tool chain can edge it out, so if no
        # user turn survived, re-attach the most recent real one.
        if not any(m.get("role") == "user" for m in sent):
            last_user = next((m for m in reversed(self.messages)
                              if m.get("role") == "user"), None)
            sent.append(last_user or {"role": "user",
                                      "content": "Continue using the results above."})

        # 5) Proactive memory: surface the saved facts most relevant to THIS
        # turn - fenced + authoritative - appended to the system message so
        # the model actually uses what it knows instead of ignoring the
        # passive index or re-asking/disk-scanning. Adds zero tokens when
        # nothing matches; bounded otherwise. See memory.recall_for_prompt.
        from hearth import memory as _mem
        # Precise local time + prompt-specific recalled memory ride the LAST USER
        # turn, not the system head, so the system prompt + prior history stay
        # byte-stable across turns and the server reuses its KV cache instead of
        # re-prefilling everything each message. (A minute-resolution stamp in the
        # system prompt invalidated the whole prefix every 60s.) Time lets the
        # model reason "what should I do?" / "remind me tomorrow" for free; recall
        # surfaces the most relevant saved facts. Replacing the list slot with a
        # fresh dict (not mutating in place) keeps the suffix out of self.messages,
        # so it never accumulates turn over turn.
        _iu = next((i for i in range(len(sent) - 1, -1, -1)
                    if sent[i].get("role") == "user"
                    and isinstance(sent[i].get("content"), str)), None)
        if _iu is not None:
            import datetime as _dt
            _now = _dt.datetime.now().astimezone()
            suffix = (f"\n\n[Current local time: {_now.strftime('%Y-%m-%d %H:%M')} "
                      f"({_now.strftime('%A')}, tz {_now.tzname() or _now.strftime('%z')}).]")
            block = _mem.recall_for_prompt(sent[_iu]["content"])
            if block:
                suffix += "\n\n" + block
            sent[_iu] = {**sent[_iu], "content": sent[_iu]["content"] + suffix}
        # Final send-boundary guard: enforce tool_call<->tool_result pairing
        # unconditionally, no matter which trim/compact path ran. This is the
        # authoritative fix — validate at the boundary, like a mature agent
        # loop, instead of relying on a budget-gated trimmer that early-returns.
        sent = sanitize_tool_pairing(sent)
        return sent

    def _cloud_reasoning_effort(self) -> Optional[str]:
        """The `reasoning_effort` value to send for the current cloud model,
        or None to send nothing.

        - /think ON  → None (reason at the provider's default).
        - /think OFF → the provider's lowest setting so the model skips the
          reasoning pass entirely. xAI/Gemini/OpenRouter accept "none";
          OpenAI's o-series/gpt-5 bottom out at "minimal" (no "none").
        - Model previously rejected the param → None (don't re-send)."""
        model = (self.current_model or "").lower()
        if model in self._no_reasoning_effort:
            return None
        # The same rule as the window: the provider's own levels, and nothing
        # sent to switch off a model that can't stop reasoning.
        from hearth import headless as _hl
        return _hl._cloud_reasoning_effort(LOCAL_API_BASE, self.think_on, self.current_model or "",
                                           getattr(self, "think_level", "") if self.think_on else "")

    async def _open_stream(self, create_kwargs: Dict):
        """Open the streaming completion, with two recoveries:
        1. If a model 400s on `reasoning_effort` (plain grok-4/grok-3 etc. don't
           expose it), drop the param and retry.
        2. Retry transient/server errors with a short exponential backoff — a
           local model server commonly drops the socket during a model reload or
           KV-cache warmup, and a one-line "server errored" death there is a bad
           experience. A hiccup becomes a brief pause instead of a dead turn."""
        _is_local = ("localhost" in (LOCAL_API_BASE or "")
                     or "127.0.0.1" in (LOCAL_API_BASE or ""))
        for attempt in range(3):
            try:
                return await chat_create(self.client, **create_kwargs)
            except Exception as e:
                if "stream_options" in str(e).lower() and "stream_options" in create_kwargs:
                    create_kwargs.pop("stream_options", None)
                    continue  # provider rejects usage-on-stream — retry without it
                if "reasoning_effort" in create_kwargs and _is_reasoning_param_error(e):
                    self._no_reasoning_effort.add((self.current_model or "").lower())
                    create_kwargs.pop("reasoning_effort", None)
                    continue  # immediate retry without the unsupported param
                try:
                    _info = classify_api_error(e, _is_local)
                    retryable = _info.retryable
                except Exception:
                    _info, retryable = None, False
                if (retryable and attempt < 2
                        and getattr(_info, "category", "") == "context_overflow"):
                    # The same prompt overflows again however long we wait; one
                    # big tool result can do it in a step. Shrink it, harder
                    # each time (1/2, then 1/4 of the budget), as the window does.
                    from hearth.tools import _truncate_kept_tool_results
                    _msgs = _truncate_kept_tool_results(
                        list(create_kwargs.get("messages") or []),
                        max_chars=max(400, 1200 // (attempt + 1)))
                    _msgs = trim_to_budget(
                        _msgs, max(2048, int(self.context_tokens * 0.5 ** (attempt + 1))),
                        RESERVED_OUTPUT)
                    create_kwargs["messages"] = sanitize_tool_pairing(_msgs)
                    print(f"{C_DIM}↻ prompt overflowed the context; trimmed it and "
                          f"retrying{C_RESET}")
                    continue
                if retryable and attempt < 2:
                    delay = 0.5 * (2 ** attempt)
                    # Name the actual failure. "hiccup" reads as a transient
                    # blip and hides conditions a retry will never fix, like
                    # nothing listening on the endpoint at all.
                    _why = {
                        "unreachable": "no answer from the model server",
                        "timeout": "the model server is taking too long",
                        "rate_limit": "rate-limited",
                        "quota": "out of usage",
                        "server_error": "the model server returned an error",
                        "context_overflow": "prompt overflowed the context",
                    }.get(getattr(_info, "category", ""), "the request failed")
                    print(f"{C_DIM}↻ {_why}, retrying in {delay:.0f}s{C_RESET}")
                    await asyncio.sleep(delay)
                    continue
                raise

    # A turn running here shows in the desktop window's room too.
    @property
    def _responding(self) -> bool:
        return getattr(self, "_responding_now", False)

    @_responding.setter
    def _responding(self, on: bool) -> None:
        self._responding_now = on
        _note_activity("thinking" if on else "idle")

    async def _respond_claude(self) -> None:
        """One turn with an agent program as the brain: its text streams here, its
        tool calls print like Hearth's own, and anything it needs permission
        for is asked at this prompt."""
        from hearth import claude_brain as _cb
        from hearth.tools import mode_allows
        loop = asyncio.get_running_loop()
        convo = [m for m in self.messages if m.get("role") in ("user", "assistant")]
        prompt = str(convo[-1].get("content") or "") if convo else ""
        history = convo[:-1]
        state = {"open": False}

        def ask(name, args):
            if mode_allows(name, args) or self.tool_perms.get(name) == "always":
                return "allow"
            if self.tool_perms.get(name) == "never":
                return "deny"
            try:
                from hearth.auto_review import why as _why
                because = _why(name, args)
            except Exception:
                because = ""
            async def _q():
                shown = args.get("command") or args.get("path") or json.dumps(args)[:120]
                print(f"\n  {C_WARN}{name}{C_RESET} {C_DIM}{str(shown)[:160]}{C_RESET}")
                if because:
                    print(f"    {C_DIM}{because}{C_RESET}")
                print(f"    {C_WARN}allow this call?{C_RESET} "
                      f"{C_DIM}[y]es / [n]o / [a]lways / [N]ever / or type what to do instead{C_RESET}")
                return await self._read_choice(f"    {C_TOOL}>{C_RESET} ")
            raw = asyncio.run_coroutine_threadsafe(_q(), loop).result(timeout=240)
            c = raw.strip().lower()
            if c in ("", "y", "yes"):
                return "allow"
            if c in ("a", "always"):
                self.tool_perms[name] = "always"
                _save_persisted_perms(self.tool_perms)
                return "allow"
            if c in ("never", "n!"):
                self.tool_perms[name] = "never"
                _save_persisted_perms(self.tool_perms)
                return "deny"
            if c in ("n", "no"):
                return "deny"
            return {"decision": "deny", "reason": raw.strip()}

        reply = []

        def emit(t, **f):
            if t == "assistant_chunk":
                chunk = f.get("content", "")
                if not state["open"]:
                    sys.stdout.write(f"\n{C_BOT}")
                    state["open"] = True
                    chunk = chunk.lstrip("\n")         # the line break is already there
                sys.stdout.write(chunk)
                sys.stdout.flush()
                reply.append(f.get("content", ""))
            elif t == "tool_call":
                if state["open"]:
                    sys.stdout.write(C_RESET + "\n")
                    state["open"] = False
                _note_activity("calling " + str(f.get("name") or ""), str(f.get("name") or ""))
                args = f.get("args") or {}
                shown = args.get("command") or args.get("path") or args.get("query") or args.get("url") or ""
                print(f"  {C_BRAND}{_tool_glyph(f.get('name', ''))}{C_RESET} {f.get('name')}"
                      + (f"  {C_DIM}{str(shown)[:120]}{C_RESET}" if shown else ""))
            elif t == "tool_result":
                first = " ".join(str(f.get("content") or "").split())[:110]
                print(f"    {C_DIM}↳ {first}{C_RESET}")
            elif t == "auto_review" and f.get("words"):
                print(f"    {C_WARN if f.get('verdict') == 'ask' else C_DIM}↳ {f['words']}{C_RESET}")
            elif t == "error":
                print(f"\n{C_ERR}{f.get('message')}{C_RESET}")
            elif t == "done":
                if state["open"]:
                    sys.stdout.write(C_RESET + "\n")
                    state["open"] = False
                st = f.get("stats") or {}
                if st.get("cost_usd") is not None:
                    print(f"{C_DIM}  {st.get('brain') or 'Claude Code'} · ${st['cost_usd']:.3f}"
                          + (" on your plan" if st.get("plan") else "") + C_RESET)
                elif st.get("tokens_in"):
                    print(f"{C_DIM}  {st.get('brain')} · {st['tokens_in']:,} in, {st.get('tokens_out') or 0:,} out{C_RESET}")

        from hearth import auto_review as _ar          # Auto's reviewer checks steps against this turn's words
        _ar.begin_turn((getattr(self, "_chat", None) or {}).get("id"), prompt, history, emit)
        try:
            await _cb.run_turn(prompt, emit=emit, history=history, permission_check=ask,
                               should_cancel=self._respond_cancel.is_set)
        finally:
            _ar.end_turn()
        text = "".join(reply).strip()
        if text:
            self.messages.append({"role": "assistant", "content": text})

    async def respond(self, depth: int = 0):
        """One turn on the shared engine (the window's and the phone bots'),
        drawn here; typing while it runs steers it, Esc stops it."""
        if os.environ.get("HEARTH_OLD_TERMINAL") == "1":
            return await self._respond_legacy(depth)
        from hearth import headless as _hl
        convo = [m for m in self.messages[1:] if m.get("role") in ("user", "assistant", "tool")]
        if not convo or convo[-1].get("role") != "user":
            return
        content = convo[-1].get("content")
        if isinstance(content, list):
            prompt = "\n".join(str(p.get("text") or "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
            for p in content:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    prompt += "\n\nAttached image is in this message; call view_image on it if you need to see it again."
        else:
            prompt = str(content or "")
        history = []
        for m in convo[:-1]:
            h = {"role": m["role"], "content": m["content"] if isinstance(m.get("content"), (str, type(None)))
                 else _term_render.plain(json.dumps(m.get("content")))}
            for k in ("tool_calls", "tool_call_id", "name"):
                if m.get(k):
                    h[k] = m[k]
            history.append(h)
        turn = _term_render.Turn(out=lambda line: print(line, flush=True),
                                 show_thinking=self.show_thinking, model=self._model_label())
        self._turn = turn
        self._respond_cancel.clear()
        self._steer_q: List[str] = []
        steer_task = asyncio.create_task(self._steer_loop())
        failed: List[str] = []

        def _emit(kind, **fields):
            if kind == "error":
                failed.append(str(fields.get("message") or ""))
            return turn.emit(kind, **fields)
        prior, self._last_failure = getattr(self, "_last_failure", ""), ""
        try:
            await _hl.run_once(
                prompt, emit=_emit, model=self.current_model, think=self.think_on,
                think_level=getattr(self, "think_level", "") or "", history=history,
                permission_check=self._ask_permission, should_cancel=self._respond_cancel.is_set,
                drain_steer=self._drain_steer, voice=bool(self.voice_on),
                chat_id=(getattr(self, "_chat", None) or {}).get("id"), prior_failure=prior)
            # The next turn hears that this one failed (headless.failure_note).
            self._last_failure = failed[-1] if failed else ""
        finally:
            turn.running = False
            self._turn = None
            # Close the open input the normal way, so it erases itself instead
            # of leaving the working line and a bare prompt in the scrollback.
            self._steer_over = True
            app = self.pt_session.app if self.pt_session is not None else None
            if app is not None and app.is_running:
                # Half-typed text carries over to the next prompt instead of being lost.
                self._carry = self.pt_session.default_buffer.text
                app.exit(result="")
            try:
                await asyncio.wait_for(steer_task, timeout=2)
            except BaseException:
                steer_task.cancel()
            self._steer_over = False
        self._last_thinking = turn.thinking or getattr(self, "_last_thinking", "")
        if turn.ended:
            self._exit_requested = True
        st = turn.stats
        u = self._session_use
        u["in"] = u.get("in", 0) + int(st.get("tokens_in") or st.get("prompt_tokens") or 0)
        u["out"] = u.get("out", 0) + int(st.get("tokens_out") or st.get("gen_tokens") or 0)
        if st.get("cost_usd"):
            # Billed and plan-covered amounts are kept apart.
            k = "plan_cost" if st.get("plan") else "cost"
            u[k] = u.get(k, 0.0) + float(st["cost_usd"])
        reply = turn.reply()
        kept = getattr(turn, "turn_messages", None)
        if kept:
            # The tool calls and what they returned stay in the history, so the
            # next turn knows what was already run.
            self.messages.extend(kept)
        elif reply:
            self.messages.append({"role": "assistant", "content": reply})
        # The engine's count is what the server said it read; keep how far our
        # own estimate is from it, so the footer, /tokens and /context read it.
        ctx = turn.context
        if ctx.get("measured") and ctx.get("used"):
            self._ctx_bias = int(ctx["used"]) - estimate_tokens(self.messages)
            self._ctx_measured = True
        if reply:
            if self.voice_on and voice.is_available():
                try:
                    voice.speak(reply, blocking=False)
                except Exception:
                    pass
        try:
            self._chat_push()
        except Exception:
            pass

    def _drain_steer(self) -> List[str]:
        q, self._steer_q = getattr(self, "_steer_q", []), []
        return q

    async def _steer_loop(self) -> None:
        """While a reply runs the input stays open: a line typed is sent to the
        reply between its steps."""
        if self.pt_session is None:
            return
        while True:
            try:
                text = (await self.pt_session.prompt_async(self._live_message)).strip()
            except asyncio.CancelledError:
                if not getattr(self, "_steer_paused", False):
                    raise
                while self._steer_paused and self._turn is not None:
                    await asyncio.sleep(0.05)
                continue
            if getattr(self, "_steer_over", False):
                return
            if not text:
                continue
            if text in ("/stop", "/esc"):
                self._respond_cancel.set()
                continue
            if text == "/bg":
                # A command the reply is waiting on carries on as a background job.
                from hearth import jobs as _jobs
                moved = _jobs.background_now()
                print(f"{C_DIM}↳ " + ("moved to the background; you'll hear when it ends" if moved
                                      else "nothing is running that can move") + f"{C_RESET}")
                continue
            if text.startswith("/"):
                if not hasattr(self, "_after_turn"):
                    self._after_turn = []
                self._after_turn.append(text)
                print(f"{C_DIM}↳ {text.split()[0]} runs once this reply is finished (esc stops it){C_RESET}")
                continue
            self._steer_q.append(text)
            print(f"{C_USER}❯ {C_RESET}{BOLD_ON}{text}{C_RESET}  {C_DIM}(sent to the reply){C_RESET}")

    async def _ask_permission(self, name: str, args: Dict[str, Any]):
        """Approval for one tool call, as a picker. The open input waits meanwhile."""
        from hearth.tools import mode_allows
        if mode_allows(name, args) or self.tool_perms.get(name) == "always":
            return "allow"
        if self.tool_perms.get(name) == "never":
            return "deny"
        from hearth import term_pick
        app = self.pt_session.app if self.pt_session is not None else None
        self._steer_paused = True
        if app is not None and app.is_running:
            app.exit(exception=asyncio.CancelledError())     # the steer prompt yields the screen
            await asyncio.sleep(0.05)
        try:
            return await self._ask_permission_picker(name, args)
        finally:
            self._steer_paused = False

    async def _ask_permission_picker(self, name: str, args: Dict[str, Any]):
        from hearth import term_pick
        what = _term_render.tool_words(name, args if isinstance(args, dict) else {})
        try:
            from hearth.auto_review import why as _why
            because = _why(name, args)          # the reviewer's or the floor's reason
        except Exception:
            because = ""
        if because:
            print(f"  {C_DIM}{because}{C_RESET}")
        picked = await term_pick.pick(
            f"{C_WARN}Allow this?{C_RESET}",
            [("allow", "Yes, this once"), ("always", f"Yes, and don't ask again for {name}"),
             ("deny", "No"), ("why", "No, and tell it what to do instead")], default="allow")
        said = {"allow": "allowed", "always": f"allowed, and won't ask again for {name}",
                "deny": "declined", "why": "declined"}.get(picked, "declined")
        print(f"  {C_DIM}↳ {said}{C_RESET}")
        if picked == "always":
            self.tool_perms[name] = "always"
            _save_persisted_perms(self.tool_perms)
            return "allow"
        if picked == "why":
            reason = (await self.pt_session.prompt_async(ANSI(f"  {C_DIM}what instead?{C_RESET} "))).strip() \
                if self.pt_session is not None else ""
            return {"decision": "deny", "reason": reason}
        return "allow" if picked == "allow" else "deny"

    async def _respond_legacy(self, depth: int = 0):  # noqa: C901
        if depth > MAX_TURNS:
            # Generous safety ceiling (the loop guard handles real spirals long
            # before this); reaching it means something genuinely runaway.
            print(f"{C_DIM}● reached the turn limit - wrapping up{C_RESET}")
            return

        # Once per user turn, not per tool round. This block once sat under the
        # return above with its own condition deleted, so none of it ran: the
        # loop guard was never created and the first tool call of every session
        # crashed, and one interrupt was never cleared, so every later reply
        # stopped at its first word.
        if depth == 0:
            try:
                from hearth import claude_brain as _cb
                if _cb.active():
                    return await self._respond_claude()
            except ImportError:
                pass
            # Tool-loop guard: outcome-hash based, tiered (skip dup / warn /
            # stop). Fresh per user turn. See hearth/loop_guard.py for the full
            # rationale.
            self._loop_guard = ToolLoopGuard()
            self._force_answer = False
            # Cloud vision: image to attach as a user turn after this round of
            # tool calls (set by the view_image handler on cloud endpoints).
            self._pending_image_block = None
            # Fresh cancel signal - clear any stale state from the prior turn.
            self._respond_cancel.clear()

        send_messages = await asyncio.to_thread(self._prepare_context)

        # Qwen3 `/no_think` injection - `chat_template_kwargs.enable_thinking=False`
        # doesn't reliably suppress thinking through LM Studio's compat layer
        # (the flag gets passed but Qwen's chat template sometimes ignores it).
        # The Qwen3 chat template DOES honor an in-prompt `/no_think` directive
        # appended to the last user message. So when /think is off and the
        # model is in the Qwen3 family, inject the directive into the outbound
        # copy of the user's last turn. Doesn't mutate self.messages.
        if not self.think_on and ("qwen3" in (self.current_model or "").lower()
                                  or "qwen-3" in (self.current_model or "").lower()):
            for i in range(len(send_messages) - 1, -1, -1):
                msg = send_messages[i]
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and "/no_think" not in content:
                        send_messages[i] = {**msg, "content": content + "\n\n/no_think"}
                    break

        # Spinner label honors /think state. "Thinking" only when the model
        # is actually allowed to reason; otherwise "Working" - because the
        # 5s spinner saying "Thinking" looked like the model was reasoning
        # even when reasoning was disabled, which was misleading.
        spinner_task = asyncio.create_task(
            self.spinner("Thinking" if self.think_on else "Working")
        )

        try:
            create_kwargs: Dict = {
                "model": self.current_model,
                "messages": send_messages,
                "stream": True,
                "temperature": 0.7,
                # Real usage on the final stream chunk — feeds the tok/s footer's
                # token count and the /context readout. Harmless where ignored.
                "stream_options": {"include_usage": True},
            }
            if getattr(self, "_force_answer", False):
                # Spiral guard tripped last turn: withhold tools so the model
                # must produce text. One-shot - clear so tools return next turn.
                self._force_answer = False
            else:
                # Rebuilt each turn so tools unlocked via `load_tools`
                # (tool-diet) show up on the next request.
                self.openai_tools = to_openai_tools()
                create_kwargs["tools"] = self.openai_tools
                create_kwargs["tool_choice"] = "auto"
            # Thinking gate:
            #   - chat_template_kwargs.enable_thinking is the PROPER way for
            #     Qwen3+ and other reasoning-aware models - flips the Jinja
            #     branch in the chat template so the model never enters the
            #     thinking phase at all.
            #   - stop=["<think>"] is a safety net for models that ignore
            #     the chat-template kwarg (older / non-reasoning models that
            #     happen to emit <think> tags via system prompt).
            # `chat_template_kwargs` is LM-Studio/llama.cpp-specific. Cloud
            # OpenAI-compat endpoints (Gemini, OpenAI, OpenRouter) reject
            # unknown fields with a 400 - only send it to local servers.
            _base = (LOCAL_API_BASE or "").lower()
            _is_local = any(h in _base for h in ("localhost", "127.0.0.1", "0.0.0.0",
                                                 "::1", "192.168.", "10.", "host.docker.internal"))
            if _is_local:
                # enable_thinking=False is the PROPER, non-destructive way to skip
                # reasoning on models that respect it (Qwen3+ etc.). We deliberately
                # do NOT add stop=["<think>"] anymore: on a reasoning-FORCED model it
                # halted generation at the first think tag — truncating the reply to
                # empty — and it fought showing the reasoning we now surface live.
                # repeat_penalty ~1.1 so thinking models don't degenerate into a
                # repeating reasoning loop. 1.0 = off; HEARTH_REPEAT_PENALTY tunes it.
                _rep = float(os.environ.get("HEARTH_REPEAT_PENALTY", "1.1") or "1.1")
                create_kwargs["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": self.think_on},
                    "repeat_penalty": _rep,
                }
            else:
                # Cloud: actually disable reasoning at the API when /think is
                # off - not just hide it. Without this the model (e.g. Grok)
                # still runs a full reasoning pass server-side, so you pay the
                # latency with zero visibility. `reasoning_effort` is the lever
                # that reasoning-capable cloud models expose.
                eff = self._cloud_reasoning_effort()
                if eff is not None:
                    create_kwargs["reasoning_effort"] = eff
                if isinstance(self.client, _anthropic_chat.AsyncAnthropicChat):
                    create_kwargs["hearth_think"] = self.think_on
            stream = await self._open_stream(create_kwargs)
        except Exception as e:
            spinner_task.cancel()
            info = classify_api_error(e, _is_local)
            # One clean line - not the raw HTML/stack. (run.txt showed a raw 500
            # HTML wall cascading; this turns it into "the model server errored
            # - try /compact or reload the model".)
            print(f"\n{C_ERR}● {info.hint}{C_RESET}")
            print(f"{C_DIM}({info.category}{' · retryable' if info.retryable else ''}) "
                  f"Your message is preserved - resend or type to retry.{C_RESET}")
            # Keep the last user message in self.messages so a follow-up
            # turn naturally retries the same context, instead of silently
            # losing what they typed.
            return

        tool_calls_dict: Dict[int, Dict] = {}
        content_captured = ""
        # tok/s timing for the footer — real llama.cpp timings if the last chunk
        # carries them, else wall-clock decode rate + TTFT.
        _s_t0 = time.time()
        _s_first: Optional[float] = None
        _s_last_chunk = None
        # Count the PHYSICAL terminal lines we actually print for the reply, so
        # the markdown re-render erases exactly those (recomputing from string
        # width after the fact mis-counts when a reasoning/tool frame sat above).
        _emit_lines = 0
        _emit_col = 0
        _term_w = max(getattr(_RICH, "width", 80) or 80, 20) if _RICH else 80
        first = True
        in_think = False
        # Streaming voice state - we flush sentences to TTS as they
        # complete during the delta loop, instead of waiting for the
        # whole turn. Audio kicks in ~1 sentence after text starts.
        speak_buffer = ""
        speak_cursor = 0  # how much of speak_buffer has been pushed to TTS
        in_code_block = False  # don't speak fenced code

        def _maybe_flush_speech(force_tail: bool = False):
            """Look at unspoken portion of speak_buffer; if a sentence
            boundary is past, flush that prefix to voice.speak() and
            advance the cursor. force_tail=True flushes whatever's left.

            Goal: voice catches up to text within ~1 short sentence so
            the user hears the reply STREAM in, not after the full turn.
            Previously the floor was 60 chars which meant short replies
            ('Hey bro. What's good?' = 21 chars) never spoke until
            force_tail at end-of-turn - felt like a non-streaming voice."""
            nonlocal speak_cursor
            if not (self.voice_on and voice.is_available()):
                return
            tail = speak_buffer[speak_cursor:]
            if not tail.strip():
                return
            # Same reason as the GUI: a model that writes its tool calls as text
            # streams the tag in pieces, so flushing mid-marker speaks the raw
            # arguments. Wait for the close tag; at end-of-turn nothing more is
            # coming, so cut the marker and everything after it.
            if re.search(r"<\|?tool_call\|?>", tail, re.I) and not re.search(
                    r"<\s*/\s*\|?tool_call\|?>|<\|?/tool_call\|?>", tail, re.I):
                if not force_tail:
                    return
                tail = re.sub(r"<\|?tool_call\|?>[\s\S]*$", "", tail, flags=re.I)
                if not tail.strip():
                    return
            # Find the FIRST sentence boundary in the tail. Earlier code
            # looked at the LAST boundary, which batched multi-sentence
            # chunks together - defeated streaming. Now: flush each
            # sentence as soon as its terminator arrives, but only if the
            # next char is whitespace OR end-of-tail (avoids cutting on
            # decimals like "v0.6" or filenames). force_tail flushes
            # whatever remains regardless.
            cut = -1
            for i, ch in enumerate(tail):
                if ch in ".!?":
                    nxt = tail[i + 1] if i + 1 < len(tail) else ""
                    if nxt == "" or nxt.isspace():
                        cut = i + 1
                        break
                elif ch == "\n":
                    cut = i + 1
                    break
            if force_tail:
                cut = len(tail)
            if cut < 0:
                return  # no boundary yet - wait for more chunks
            chunk = tail[:cut].strip()
            if not chunk:
                return
            # Strip markdown noise that sounds awful as audio
            chunk = re.sub(r"`{1,3}[^`]*`{1,3}", "", chunk)
            chunk = re.sub(r"[#*_`]+", "", chunk).strip()
            # Strip Windows file paths - Kokoro reads "C:\Users\you\file"
            # as "see colon users... ". Keep just the filename.
            chunk = re.sub(r"[A-Za-z]:[\\/](?:[^\s\\/]+[\\/])*([^\s\\/]+)", r"\1", chunk)
            # Long digit runs (timestamps like 20260521_233527) get pronounced
            # as "twenty trillion two hundred sixty billion..." - gibberish.
            chunk = re.sub(r"\b\d{8,}\b", "the file", chunk)
            # Collapse runs of newlines - `\n\n` in source text otherwise
            # creates 4-5 second silent pauses between paragraphs in TTS.
            chunk = re.sub(r"\s*\n\s*\n\s*", ". ", chunk)
            chunk = re.sub(r"\s*\n\s*", " ", chunk)
            chunk = re.sub(r"\s{2,}", " ", chunk).strip()
            if chunk:
                voice.speak(chunk, blocking=False)
            speak_cursor += cut
        # Reasoning UI state: handles both inline <think>...</think> blocks
        # AND OpenAI's separate `reasoning_content` channel (LM Studio
        # streams reasoning models like Qwen3.5, DeepSeek-R1 this way).
        # Auto-collapse by default - show "▶ thought for X.Xs" instead of
        # the body. /think toggles inline display.
        reasoning_open = False
        reasoning_chars = 0
        reasoning_t0 = 0.0

        # Reasoning renders as dim ITALIC "thoughts" — distinct from the rich
        # Markdown answer, no heavy hand-drawn box. Kept as raw ANSI (not rich)
        # because it streams live token-by-token; rich's buffered print would
        # race the raw stream and misorder the frame.
        _ITAL, _ITAL_OFF = "\033[3m", "\033[23m"

        # Reasoning is shown live WHENEVER it streams. With /think off we already
        # asked the model to skip it; if it reasons anyway it's a reasoning-FORCED
        # model, and hiding a 30s think behind a blind spinner is the worst option.
        # So these render the body the same way regardless of the toggle.
        def _open_reasoning():
            nonlocal reasoning_open, reasoning_chars, reasoning_t0
            if not reasoning_open:
                reasoning_open = True
                reasoning_chars = 0
                reasoning_t0 = time.time()
                sys.stdout.write(f"\n{C_DIM}{_ITAL}thinking · ")
                sys.stdout.flush()

        def _close_reasoning():
            nonlocal reasoning_open, reasoning_chars, reasoning_t0
            if reasoning_open:
                dt = time.time() - reasoning_t0
                sys.stdout.write(f"{_ITAL_OFF}{C_RESET}\n{C_DIM}{dt:.1f}s · {reasoning_chars}c{C_RESET}\n{C_BOT}")
                sys.stdout.flush()
                reasoning_open = False

        def _stream_reasoning(text: str):
            nonlocal reasoning_chars
            reasoning_chars += len(text)
            # dim + italic, re-armed each chunk so wrapped lines stay styled
            sys.stdout.write(C_DIM + _ITAL + text)
            sys.stdout.flush()

        async for chunk in stream:
            # Ctrl-C during the stream sets _respond_cancel; bail cleanly
            # so prompt_toolkit re-takes the terminal without killing the
            # CLI. Any in-flight subagent also sees this via the parent
            # cancel hook.
            if self._respond_cancel.is_set():
                try: spinner_task.cancel()
                except Exception: pass
                try: await stream.close()
                except Exception: pass
                print(f"\r\033[K{C_DIM}● interrupted by user.{C_RESET}")
                return
            _s_last_chunk = chunk
            if not chunk.choices:
                continue
            if first:
                # BUG-FIX: We must AWAIT the spinner task after cancelling
                # so its CancelledError handler (which writes "\r\033[K")
                # runs BEFORE we write our own content. Without this, the
                # spinner's cleanup runs on the next event-loop tick and
                # erases the first few characters of our streamed reply -
                # that's why responses after a tool call used to start
                # missing the first word.
                spinner_task.cancel()
                try:
                    await spinner_task
                except (asyncio.CancelledError, Exception):
                    pass
                sys.stdout.write("\r\033[K" + C_BOT)
                sys.stdout.flush()
                first = False

            delta = chunk.choices[0].delta

            # OpenAI-extension: dedicated reasoning channel. Read it REGARDLESS of
            # /think. With /think off we already asked the model to skip reasoning
            # (enable_thinking=False / reasoning_effort=none); if it streams anyway
            # it's a reasoning-FORCED model, and showing the live think beats a
            # blind 30s spinner. So: it arrives → the user sees it.
            reasoning = getattr(delta, "reasoning_content", None) \
                or getattr(delta, "reasoning", None)
            if reasoning:
                if _s_first is None:
                    _s_first = time.time()
                _open_reasoning()
                _stream_reasoning(reasoning)
                # don't add to content_captured - reasoning isn't part of
                # the assistant message we send back next turn

            if delta.content:
                if _s_first is None:
                    _s_first = time.time()
                text = delta.content
                # Inline <think>...</think> path (Qwen3.5, etc.). When
                # think_on=False we still need to swallow the bytes that
                # arrive between the tags (some models emit them despite
                # our stop= + chat_template_kwargs gates) so they don't
                # leak into content_captured or the visible reply.
                if "<think>" in text:
                    pre, _, after = text.partition("<think>")
                    if pre:
                        if reasoning_open:
                            _close_reasoning()
                        content_captured += pre  # buffered; whole reply rendered once at end
                    _open_reasoning()
                    text = after
                    in_think = True
                if "</think>" in text and in_think:
                    body, _, post = text.partition("</think>")
                    if body:
                        _stream_reasoning(body)
                    _close_reasoning()
                    in_think = False
                    text = post

                # Reasoning channel (no <think> tags) ends when content arrives
                if reasoning_open and not in_think:
                    _close_reasoning()

                if in_think:
                    # Stream the thinking body live (shown regardless of /think —
                    # a forced model's reasoning is worth seeing, not dropping).
                    if text:
                        _stream_reasoning(text)
                else:
                    if text:
                        # Buffer the body — don't stream it live. We render the
                        # whole reply ONCE at the end as rich markdown. Streaming
                        # raw then re-rendering can't erase lines that scrolled off,
                        # so a tall table used to duplicate. Buffering = always
                        # pretty, any height, any terminal. Speech still flows.
                        content_captured += text
                        if "```" in text:
                            for _ in range(text.count("```")):
                                in_code_block = not in_code_block
                        if not in_code_block:
                            speak_buffer += text
                            _maybe_flush_speech()

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    slot = tool_calls_dict.setdefault(idx, {
                        "id": tc.id or f"call_{idx}",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["function"]["arguments"] += tc.function.arguments

        # If a stream ended while still in reasoning (rare but possible),
        # close the frame so the next print isn't dimmed forever.
        if reasoning_open:
            _close_reasoning()

        # Flush any trailing speech that didn't end with a sentence boundary
        _maybe_flush_speech(force_tail=True)

        # The body was buffered (not streamed), so nothing's on screen to erase.
        # Render the whole reply ONCE: rich markdown if it has any, else plain.
        # No cursor-up → the scrolled-off-table duplication can't happen, and it
        # renders correctly at ANY height in ANY terminal (WT, VS Code, Linux).
        if _RICH is not None and content_captured and _has_md(content_captured):
            try:
                _RICH.print(_RichMarkdown(content_captured, code_theme="monokai"))
            except Exception:
                sys.stdout.write(C_BOT + content_captured + C_RESET + "\n")
                sys.stdout.flush()
        elif content_captured:
            sys.stdout.write(C_BOT + content_captured + C_RESET + "\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(C_RESET)

        # tok/s footer — only under the final visible reply (not tool-call turns),
        # muted, one line. Real prompt-eval vs decode split when llama.cpp reports
        # timings, else a wall-clock decode rate. Honors the GUI's shared
        # "gen_stats" toggle (settings.json) so turning stats off applies here too.
        _stats_on = True
        try:
            import json as _json
            with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as _sf:
                _stats_on = (_json.load(_sf).get("gen_stats") is not False)
        except Exception:
            _stats_on = True
        # The server's prompt_tokens is what the model actually saw. Keep the
        # difference from our own estimate of the same request, so the footer,
        # /tokens and /context read the real context rather than a guess.
        try:
            _u = getattr(_s_last_chunk, "usage", None)
            _pt = int(getattr(_u, "prompt_tokens", 0) or 0) if _u else 0
            if _pt > 0:
                _tools_tok = (len(json.dumps(create_kwargs.get("tools") or []))
                              // CHARS_PER_TOKEN)
                self._ctx_bias = (_pt - _tools_tok) - estimate_tokens(send_messages)
                self._ctx_measured = True
        except Exception:
            pass
        if _stats_on and not tool_calls_dict and (content_captured or _s_first):
            try:
                from hearth.headless import _compute_gen_stats
                _st = _compute_gen_stats(
                    _s_t0, _s_first, len(content_captured), 0, _s_last_chunk)
                bits = []
                if _st.get("prompt_tps") is not None:
                    bits.append(f"{_st['prompt_tps']} tok/s prompt")
                if _st.get("decode_tps") is not None:
                    bits.append(f"{_st['decode_tps']} tok/s gen")
                if _st.get("ttft_s") is not None:
                    bits.append(f"{_st['ttft_s']}s ttft")
                if bits:
                    print(f"{C_DIM}{' · '.join(bits)}{C_RESET}")
            except Exception:
                pass

        if first:
            spinner_task.cancel()
        _unparsed_markup = False

        # Recover tool calls the server did not parse. Some models emit the call
        # as text (a ChatML <tool_call> block and friends) instead of structured
        # tool_calls. The bridge has always recovered these; the terminal did
        # not, so the same model lost every call here while working in the GUI.
        if not tool_calls_dict and content_captured:
            try:
                from hearth.tool_call_parser import has_tool_call, parse as _tcparse
                if has_tool_call(content_captured):
                    _names = [t["name"] for t in TOOL_DEFINITIONS]
                    _cleaned, _recovered = _tcparse(content_captured, _names)
                    if _recovered:
                        content_captured = _cleaned
                        for _i, _fc in enumerate(_recovered):
                            tool_calls_dict[_i] = {
                                "id": _fc["id"], "type": "function",
                                "function": {"name": _fc["function"]["name"],
                                             "arguments": _fc["function"]["arguments"]},
                            }
                        print(f"{C_DIM}(recovered {len(_recovered)} tool call"
                              f"{'s' if len(_recovered) != 1 else ''} the model wrote as text){C_RESET}")
                        # Calls that read run, but a reply of twenty calls with
                        # one bracket wrong ran the nineteen and dropped the
                        # twentieth without a word, and the model went on as if
                        # all twenty had run. Markup the parse could not excise
                        # is a call that never ran. The bridge has had this
                        # check; the terminal did not.
                        if has_tool_call(_cleaned):
                            _unparsed_markup = True
                    else:
                        # Markers present but nothing extracted. Saying nothing
                        # here is what let the model believe the call ran.
                        _unparsed_markup = True
            except Exception as _pe:
                # A parser that throws on real markup is the same outcome as one
                # that reads nothing from it: no tool ran. Swallowing it meant the
                # turn ended on work the model believed it had done. Only when the
                # markup is really there, or a plain answer would be sent back
                # round as though it were a broken call.
                print(f"{C_DIM}(tool-call parser error: {type(_pe).__name__}: {_pe}){C_RESET}")
                try:
                    _unparsed_markup = bool(has_tool_call(content_captured))
                except Exception:
                    pass

        if content_captured:
            entry: Dict = {"role": "assistant", "content": content_captured}
        else:
            # Empty string, NOT None: Gemini's OpenAI-compat layer rejects a
            # null assistant content with tool_calls (400 INVALID_ARGUMENT).
            # OpenAI/LM Studio accept "" just as happily.
            entry = {"role": "assistant", "content": ""}

        _calls: List[Dict] = []
        if tool_calls_dict:
            from hearth.tool_call_parser import split_calls as _split_calls
            _calls = _split_calls(list(tool_calls_dict.values()))
            entry["tool_calls"] = [{k: v for k, v in c.items() if k != "raw_arguments"} for c in _calls]

        self.messages.append(entry)

        # Voice already streamed sentence-by-sentence during the delta
        # loop above - no end-of-turn speak needed.

        if not tool_calls_dict:
            if _unparsed_markup and depth < MAX_TURNS:
                # The reply carried tool-call markup that nothing could read, so
                # no tool ran. Say so instead of ending the turn, otherwise the
                # model treats its own unexecuted call as done and reports work
                # that never happened.
                note = ("Your last message contained tool-call markup that could "
                        "not be parsed, so NOTHING was executed. No file was "
                        "written and no command ran. Re-send it as a single "
                        "well-formed tool call.")
                print(f"{C_WARN}-> {note}{C_RESET}")
                self.messages.append({"role": "user", "content": note})
                self.save_history()
                return await self._respond_legacy(depth + 1)
            self.save_history()
            return

        for tc in _calls:
            name = tc["function"]["name"]
            args_raw = tc.get("raw_arguments") or tc["function"]["arguments"] or "{}"
            try:
                # Same lenient read the bridge uses, so a call the bridge
                # recovers is not rejected here. Strict JSON first, then a
                # string-aware repair; never a blind string replace.
                from hearth.tool_call_parser import loads_lenient as _lenient
                _parsed = _lenient(args_raw)
                if _parsed is None:
                    raise json.JSONDecodeError("could not be read as a JSON object", args_raw, 0)
                # These came from the model, so anything the tool layer reads
                # as control rather than as an argument is dropped here.
                from hearth.tools import strip_control_args as _strip_ctl
                args = _strip_ctl(_parsed)
            except json.JSONDecodeError as _je:
                _snippet = (args_raw[:500] + "…") if len(args_raw) > 500 else args_raw
                print(f"{C_ERR}Tool JSON error for {name} at col {_je.colno}: {_je.msg}{C_RESET}")
                print(f"{C_DIM}Raw: {_snippet}{C_RESET}")
                # Feed the error back so the next turn can retry with fixed JSON.
                err = (
                    f"Error: your tool call JSON for '{name}' is malformed and was not executed. "
                    f"json.loads failed at col {_je.colno}: {_je.msg}. Raw: {_snippet} "
                    f"Fix: escape inner quotes and ensure valid JSON object."
                )
                self.messages.append({"role": "tool", "tool_call_id": tc.get("id") or "call_err", "name": name, "content": err})
                print(f"{C_WARN}→ {err[:300]}{C_RESET}")
                continue
            # Args preview. We dump the FULL argument set only when we're
            # about to ASK you to approve - that's when a hidden ">D:\evil.bat"
            # tail matters and you need to read it. When the call runs
            # unprompted (auto-approve, an [a]lways grant, or a write inside
            # the workspace), we show a tidy one-line preview cut with "…" -
            # no 600-char code blob (e.g. create_plugin) dumped to the screen.
            preview_full = json.dumps(args, ensure_ascii=False)
            is_risky = _is_risky(name)

            # Will a [y/n/a/N] prompt actually fire? Only then do we need the
            # full args on screen.
            try:
                from hearth.tools import mode_allows as _mode_allows
                _mode_ok = _mode_allows(name, args)
            except Exception:
                _mode_ok = False
            _will_prompt = (is_risky
                            and not self.auto_approve
                            and not _mode_ok
                            and self.tool_perms.get(name) not in ("always", "never"))

            # Tool card: a per-family violet glyph + bold name + a compact,
            # muted "key=val, …" arg preview (whitespace collapsed, long values
            # elided) then a dim "└ result" continuation. Distinct at a glance
            # from thinking (dim italic) and the rich-rendered answer. The full
            # pretty-printed args only unfurl when we're about to prompt.
            _unfurl = _will_prompt and len(preview_full) > 100
            _argstr = _tool_arg_summary(args)
            _note_activity("calling " + name, name)
            print(f"\n  {C_BRAND}{_tool_glyph(name)}{C_RESET} {_BOLD}{name}{C_RESET}"
                  + (f"  {_argstr}" if _argstr else ""))
            # File writes → green/red diff (parity with the GUI), gated by the same
            # show_diffs toggle. Falls back to the JSON unfurl for other tools.
            _showed_diff = False
            if name in ("edit_file", "write_file") and _cli_show_diffs():
                try:
                    _showed_diff = _print_cli_diff(name, args)
                except Exception:
                    _showed_diff = False
            if _unfurl and not _showed_diff:
                try:
                    pretty = json.dumps(args, ensure_ascii=False, indent=2)
                except Exception:
                    pretty = preview_full
                for ln in pretty.split("\n"):
                    print(f"    {C_DIM}{ln}{C_RESET}")

            # Permission gate for risky tools - uses prompt_async if pt is
            # active so we don't deadlock with prompt_toolkit's stdin grab.
            denied = False
            decline_reason = ""

            if (_is_risky(name)
                    and not self.auto_approve
                    and not _mode_ok
                    and self.tool_perms.get(name) != "always"):
                if self.tool_perms.get(name) == "never":
                    denied = True
                else:
                    print(f"    {C_WARN}allow this call?{C_RESET} "
                          f"{C_DIM}[y]es / [n]o / [a]lways / [N]ever / or type what to do instead{C_RESET}")
                    raw_choice = await self._read_choice(f"    {C_TOOL}>{C_RESET} ")
                    choice = raw_choice.strip().lower()
                    if choice in ("", "y", "yes"):
                        pass  # blank Enter or y = allow once (less punishing in flow)
                    elif choice in ("a", "always"):
                        self.tool_perms[name] = "always"
                        _save_persisted_perms(self.tool_perms)
                    elif choice in ("never",) or choice == "n!":
                        self.tool_perms[name] = "never"
                        _save_persisted_perms(self.tool_perms)
                        denied = True
                    elif choice in ("n", "no"):
                        denied = True
                    else:
                        # Anything else = decline + free-text instruction. The
                        # typed text is handed to the model as what to do instead
                        # of the call it just tried (preserve original casing).
                        denied = True
                        decline_reason = raw_choice.strip()

            # `result` is model-facing (firm directives ok). `display` is the
            # clean one-liner shown on screen - internal control directives must
            # NEVER leak to the user as raw text.
            display = None
            skipped = False
            if denied:
                if decline_reason:
                    result = (f"The user declined '{name}' and asked for this instead: "
                              f"\"{decline_reason}\". Follow that; don't retry the original call.")
                    display = f"declined -> {decline_reason[:60]}"
                else:
                    result = (f"The user declined '{name}'. NOTHING ran - no file was created, "
                              f"written, or changed. Do NOT claim it succeeded. Don't retry the "
                              f"same call - pick a different approach, ask what they'd prefer, or "
                              f"answer without it.")
                    display = "declined"
                dt = 0.0
            elif (skip := self._loop_guard.before(name, args)) is not None:
                # Identical MUTATING repeat - skip execution (no dup side effect).
                result = skip.note
                display = "skipped (duplicate call)"
                dt = 0.0
                skipped = True
            else:
                t0 = time.time()
                # The user has already approved this specific call via the
                # [y]es/[a]lways prompt above (or it's an always-allowed
                # tool). Mark _approved so the inner destructive-pattern
                # guard doesn't refuse it a SECOND time - the redundant
                # gate would make echo>file fail after user already said y.
                from hearth.tools import APPROVAL_TOKEN as _APPROVAL_TOKEN
                _approved_args = (dict(args, _approved=_APPROVAL_TOKEN)
                                  if isinstance(args, dict) else args)
                result = await asyncio.to_thread(execute_tool, name, _approved_args)
                dt = (time.time() - t0) * 1000

            # Loop-guard outcome check. Includes DENIED calls now: an
            # identical retry after the user said no is the worst kind of
            # spiral (the model is fighting the user, not the data). The
            # 'skipped' branch is the only one we skip because skip-call
            # results are synthetic notes, not real outcomes worth tracking.
            if not skipped:
                decision = self._loop_guard.after(name, args, result)
                if decision.action == "warn":
                    result = f"{result}\n\n{decision.note}"
                elif decision.action == "stop":
                    result = f"{result}\n\n{decision.note}"
                    self._force_answer = True
                    # Say why out loud. A turn that ends itself in silence looks
                    # like it quit for no reason.
                    if decision.reason:
                        print(f"{C_WARN}● stopped early, {decision.reason} "
                              f"say continue if it should keep going.{C_RESET}")

            # Screen preview: clean dim label for synthetic results, else the
            # real result head (green ok / rose error) plus a bounded body peek.
            _render_tool_result(display, result, dt)

            tool_msg: Dict = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": result,
            }

            # If view_image named a picture (tools.media_line), decide based
            # on whether the loaded model is vision-capable:
            #   - Vision model (id contains vl/vision/gemma-3/llava/moondream)
            #     → rewrite tool content as a multimodal block so the model
            #       actually sees the image
            #   - Text-only model → keep as text, but make the message tell
            #     the model EXPLICITLY that it can't describe images so it
            #     doesn't try (which used to stall for 80+ seconds or
            #     hallucinate generic "VS Code on left, terminals in middle")
            from hearth.tools import media_in as _media_in
            _seen = _media_in(result) if (name == "view_image" and not denied and isinstance(result, str)) else []
            img_path = _seen[0][1] if (_seen and _seen[0][0] == "image") else ""
            if img_path and os.path.isfile(img_path):
                self.last_image_path = img_path
                if self._is_vision_capable():
                    try:
                        from hearth.headless import image_for_model
                        _url, _scale = image_for_model(img_path)
                        image_block = {"type": "image_url", "image_url": {"url": _url}}
                        if _is_local_endpoint(LOCAL_API_BASE):
                            # A local server takes the picture inline in the tool result.
                            tool_msg["content"] = [
                                {"type": "text", "text": f"The picture {os.path.basename(img_path)}.{_scale}"},
                                image_block,
                            ]
                        else:
                            # Cloud endpoints refuse a picture in a tool message: it
                            # goes on a turn of its own after the tool loop.
                            tool_msg["content"] = (f"Opened {os.path.basename(img_path)}; the picture "
                                                   f"follows in the next message.{_scale}")
                            self._pending_image_block = image_block
                    except OSError:
                        pass
                else:
                    tool_msg["content"] = (
                        f"The picture is at {img_path}, but the model in use ({self.current_model}) "
                        f"can't see pictures, so nothing about what it shows can be said. Tell them, "
                        f"and offer to open it for them (open_app with that path) or to switch to a "
                        f"model that can see."
                    )

            self.messages.append(tool_msg)

            # generate_image / generate_video / check_video_task → pop the
            # file open in the OS default viewer + show a clickable file:// path
            # so the CLI user has the same "look, here's the image" moment
            # the GUI gets via inline render. The result names each on an
            # "Image: <path>" or "Video: <path>" line (tools.media_in reads them).
            if (
                name in ("generate_image", "generate_video", "check_video_task")
                and not denied
                and isinstance(result, str)
            ):
                from hearth.tools import media_in as _media_in
                for _kind, _media_path in _media_in(result):
                    if _media_path and os.path.isfile(_media_path):
                        print(f"{C_FRAME}│ {C_OK}↳ saved:{C_RESET} {_media_path}")
                        # Open in the OS default viewer (image viewer / mp4 player).
                        # Non-blocking - we don't want to lock up the CLI.
                        try:
                            if sys.platform == "win32":
                                os.startfile(_media_path)  # type: ignore[attr-defined]
                            elif sys.platform == "darwin":
                                import subprocess as _sp
                                _sp.Popen(["open", _media_path], close_fds=True)
                            else:
                                import subprocess as _sp
                                _sp.Popen(["xdg-open", _media_path], close_fds=True)
                        except Exception:
                            pass
                        # Remember the last image for "see that screenshot" recall
                        if _media_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            self.last_image_path = _media_path

            # Model-driven session shutdown
            if name == "end_session" and not denied:
                self._exit_requested = True

            # Track the most recent image so we can auto-attach when the
            # user says "see that screenshot/image" without an @-path.
            if name == "screenshot" and not denied:
                m = re.search(r"Saved:\s*([^\(\n]+\.png)", result)
                if m:
                    self.last_image_path = m.group(1).strip()

        # Cloud vision: attach the captured image as a user turn now that all
        # tool results for this assistant turn are in place (keeps tool_call_id
        # pairing intact - we must NOT inject this mid-loop).
        if getattr(self, "_pending_image_block", None) is not None:
            self.messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "(Inglen, not the person: the picture the last step opened.)"},
                    self._pending_image_block,
                ],
            })
            self._pending_image_block = None

        # Some of the reply was a call that could not be read. The ones that
        # could have run and their results are in place above, so this goes
        # after them, where the ordering is valid, and says which part ran.
        if _unparsed_markup:
            _ran = len(entry.get("tool_calls") or [])
            note = (f"Part of your last message was not a readable tool call. The "
                    f"{_ran} call(s) that could be read did run, and their results "
                    f"are above. The unreadable part did NOT run. Send it again as "
                    f"one well-formed call, one JSON object per block:\n"
                    f'<tool_call>\n{{"name": "read_file", "arguments": '
                    f'{{"path": "notes.txt"}}}}\n</tool_call>')
            print(f"{C_WARN}-> part of that reply was not a readable tool call; "
                  f"{_ran} ran, the rest did not{C_RESET}")
            self.messages.append({"role": "user", "content": note})

        # Spiral guard tripped: append a hard "answer now" directive AFTER all
        # tool results (valid ordering). The next respond() call withholds
        # tools (see create_kwargs gate), forcing a plain-text answer.
        if getattr(self, "_force_answer", False):
            print(f"{C_DIM}● wrapping up - using what we have{C_RESET}")
            self.messages.append({
                "role": "user",
                "content": (
                    "STOP. You've already called tools enough times this turn to "
                    "have what you need. Do NOT call any more tools. Answer the "
                    "original request NOW in plain text using the results you "
                    "already have. If something genuinely failed, say so plainly "
                    "and stop."
                ),
            })

        print()
        await self._respond_legacy(depth + 1)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            os.system("")  # enable ANSI on legacy consoles
        except Exception:
            pass
        # Force UTF-8 on stdout so the cinematic banner/spinner don't crash on cp1252.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    os.environ.setdefault("HEARTH_QUIET_ENGINE", "1")   # the terminal draws its own load line
    app = JarvisCLI()
    app.animate_intro()
    # Suppress the httpx/anyio cleanup spam that happens when the asyncio
    # loop closes before background openai clients finish their TLS
    # teardown. The spam looks like 30+ "Event loop is closed" tracebacks
    # at the bottom of every session. It's purely cosmetic - the loop is
    # closing because we asked it to - so hide that one ExceptionGroup
    # without hiding real errors.
    import logging as _logging
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)
    # The asyncio-logger silence above covers the loop's own exception handler,
    # but a background subagent runs in a thread via asyncio.run(); when its
    # httpx/openai client is garbage-collected AFTER that loop closes, the
    # transport's __del__ raises "Event loop is closed" - which Python reports
    # through sys.unraisablehook, NOT the asyncio logger. That's the ugly
    # traceback users saw every time a subagent finished. Swallow just that one.
    _orig_unraisable = sys.unraisablehook
    def _quiet_unraisable(args):  # noqa: ANN001
        exc = getattr(args, "exc_value", None)
        if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
            return
        _orig_unraisable(args)
    sys.unraisablehook = _quiet_unraisable
    try:
        from hearth.crashlog import enable as _crashlog
        _crashlog("terminal")
    except Exception:
        pass
    # Top-level exit handling: Ctrl-C anywhere (even mid-tool-call) should
    # surface as a clean exit, not the full asyncio traceback the user was
    # seeing when run_command got stuck on a daemon batch file.
    try:
        # Output printed while the input is on screen goes above it instead of
        # through it (a reply streaming while the prompt waits to steer).
        if _PT_AVAILABLE and sys.stdout.isatty():
            from prompt_toolkit.patch_stdout import patch_stdout
            with patch_stdout(raw=True):
                asyncio.run(app.run())
        else:
            asyncio.run(app.run())
    except KeyboardInterrupt:
        print(f"\n{C_DIM}● interrupted. goodbye.{C_RESET}")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as _e:
        # Swallow the "Event loop is closed" race that fires when a
        # background httpx client tries to aclose() after we've already
        # torn the loop down. Not a real failure.
        if "Event loop is closed" in str(_e):
            sys.exit(0)
        print(f"\n{C_ERR}● fatal: {type(_e).__name__}: {_e}{C_RESET}")
        sys.exit(1)
