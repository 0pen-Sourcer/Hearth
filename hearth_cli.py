"""J.A.R.V.I.S. — local-only personal AI CLI.

Runs against any OpenAI-compatible local server (LM Studio, Ollama with the
OpenAI compat layer, llama.cpp, vLLM, etc.). No paid APIs. No cloud.

Quick start:
    pip install openai
    # optional but recommended:
    pip install psutil pillow
    python jarvis.py

Env:
    LOCAL_API_BASE   default http://localhost:1234/v1
    LOCAL_MODEL      default 'local-model'
    JARVIS_WORKSPACE default ~/Jarvis  (sandbox for writes)
    JARVIS_LOCKDOWN  set to 1 to also confine reads to the workspace
"""

import os
import re
import sys
import json
import time
import asyncio
import threading
import urllib.request
from typing import Dict, List, Optional

from openai import AsyncOpenAI

# Soft dependency: prompt_toolkit gives proper arrow keys, history, ctrl-r.
# If installed, we use it. Otherwise we fall back to plain input().
try:
    from prompt_toolkit import PromptSession  # type: ignore
    from prompt_toolkit.history import FileHistory  # type: ignore
    from prompt_toolkit.formatted_text import ANSI  # type: ignore
    from prompt_toolkit.key_binding import KeyBindings  # type: ignore
    _PT_AVAILABLE = True

    class SafeFileHistory(FileHistory):  # type: ignore[misc]
        """FileHistory that strips lone UTF-16 surrogate code points before
        writing. Windows terminals occasionally emit them for emojis like 💀,
        which crashes the default FileHistory's UTF-8 encoder."""
        def store_string(self, string: str) -> None:
            # encode/decode round-trip with 'replace' kills surrogates
            cleaned = string.encode("utf-8", "replace").decode("utf-8", "replace")
            super().store_string(cleaned)
except ImportError:
    _PT_AVAILABLE = False

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
        SAFE_READ_ONLY,
        ACTIVITY_LOG,
        system_prompt,
        trim_to_budget,
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
    `provider` (when present) drives the brain_keys.json fallback below —
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


def _read_brain_key(provider: str) -> str:
    """Look up a saved cloud key from ~/Jarvis/brain_keys.json.
    File shape: {"<provider>": {"url": ..., "key": "<api-key>", "model": ...}}
    Returns '' if no key for that provider. Mirrors what /brain reads/writes."""
    if not provider or provider in ("local", "lmstudio", "builtin"):
        return ""
    try:
        import json as _json
        p = os.path.join(WORKSPACE, "brain_keys.json")
        if not os.path.isfile(p):
            return ""
        with open(p, "r", encoding="utf-8") as f:
            data = _json.load(f) or {}
        return (data.get(provider) or {}).get("key", "") or ""
    except Exception:
        return ""


_S_URL, _S_KEY, _S_MODEL, _S_PROV = _read_settings_endpoint()
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE") or _S_URL or "http://localhost:1234/v1"
LOCAL_MODEL = os.getenv("LOCAL_MODEL") or _S_MODEL or "local-model"
# Cloud endpoints need a real key. Resolution order:
#   1. LOCAL_API_KEY env (explicit override)
#   2. OPENAI_API_KEY env (legacy compat)
#   3. settings.json llm_key (rare — most cloud users have this empty)
#   4. brain_keys.json[<provider>].key (where /brain saves it)
#   5. "jarvis-local" dummy (local servers ignore the field)
# Step 4 is what was missing before: restarting on a cloud brain would
# fall straight to the dummy and 401 every request, forcing a /brain
# <provider> re-run every launch.
_BRAIN_KEY = _read_brain_key(_S_PROV)
LOCAL_API_KEY = (os.getenv("LOCAL_API_KEY") or os.getenv("OPENAI_API_KEY")
                 or _S_KEY or _BRAIN_KEY or "jarvis-local")

# Propagate resolved values into os.environ so downstream modules
# (hearth.imagine reads os.environ["LOCAL_API_BASE"] at call time)
# see them. Only set LOCAL_API_KEY in env when we have a real key
# (not the dummy) — writing "jarvis-local" over a brain_keys.json
# entry would re-introduce the auth-fail regression.
os.environ["LOCAL_API_BASE"] = LOCAL_API_BASE
if LOCAL_API_KEY and LOCAL_API_KEY != "jarvis-local":
    os.environ["LOCAL_API_KEY"] = LOCAL_API_KEY
if _S_MODEL:
    os.environ.setdefault("LOCAL_MODEL", LOCAL_MODEL)
HISTORY_FILE = os.path.join(WORKSPACE, "logs", "jarvis_history.json")
# Append-only, never-pruned transcript of the CLI. jarvis_history.json doubles
# as the working context and gets pruned/compacted (and overwritten) to fit the
# model window — which silently destroyed old turns. This file preserves the
# full back-and-forth for search_chats / recall, independent of context limits.
CLI_TRANSCRIPT = os.path.join(WORKSPACE, "logs", "cli_transcript.jsonl")
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
USE_LMSTUDIO_HISTORY = os.getenv("JARVIS_LMSTUDIO_HISTORY", "0") == "1"
# Match this to the model you've loaded in LM Studio. Conservative default.
CONTEXT_TOKENS = int(os.getenv("JARVIS_CONTEXT", "8192"))
RESERVED_OUTPUT = int(os.getenv("JARVIS_RESERVED_OUTPUT", "2048"))
# Compact when conversation hits this fraction of context. 0.75 = compact at
# 75% of window, leaving room for the next turn.
COMPACT_AT = float(os.getenv("JARVIS_COMPACT_AT", "0.75"))
# (Tool-loop control + malformed-markup stripping now live in
# hearth/loop_guard.py — outcome-hash based, not a magic per-tool count.)


HEARTH_VERSION = "0.7.0-preview"
HEARTH_REPO = "https://github.com/0pen-sourcer/hearth"


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


VOICE_ON = os.getenv("JARVIS_VOICE_ON", "0") == "1"
# Opt-in: also mirror conversation into LM Studio's threads folder so the
# chat shows up in LM Studio's chat list. Writes to a dedicated file
# (jarvis_cli.conversation.json) — does NOT touch your other threads.
LMSTUDIO_SYNC = os.getenv("JARVIS_LMSTUDIO_SYNC", "0") == "1"
LMSTUDIO_SYNC_PATH = os.path.expanduser(
    r"~/.lmstudio/conversations/jarvis_cli.conversation.json"
)

# ---- ANSI palette ---------------------------------------------------------
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
# Brand gradient — Hearth's violet → lavender → soft-rose theme. Matches the
# GUI's --accent (#8b5cf6 → #a78bfa) so the CLI feels like the same product.
GRAD = ["\033[38;5;99m",  "\033[38;5;141m", "\033[38;5;177m",
        "\033[38;5;183m", "\033[38;5;219m", "\033[38;5;225m"]
C_BRAND = "\033[38;5;141m"   # bright violet
C_USER = "\033[1;97m"
C_BOT = "\033[38;5;183m"     # lavender (matches assistant bubble vibe)
C_TOOL = "\033[38;5;220m"
C_OK = "\033[38;5;120m"
C_DIM = "\033[38;5;240m"
C_FRAME = "\033[38;5;103m"
C_ERR = "\033[1;31m"
C_WARN = "\033[38;5;215m"
C_ACCENT = C_BRAND

_LOW_LATENCY_DIRECTIVE = (
    "\n\n# LOW-LATENCY MODE (active)\n"
    "Reasoning is OFF. Do NOT output `<think>` or `<thinking>` blocks.\n"
    "Skip internal deliberation. Answer DIRECTLY in plain text.\n"
    "If the question needs careful thought, think SILENTLY — never emit it.\n"
)


# When voice is on, replies are SPOKEN. Long paragraphs, file paths, URLs, code
# and markdown sound terrible read aloud and add latency. This flips the model
# into a terse, conversational Jarvis register for the duration.
_VOICE_MODE_DIRECTIVE = (
    "\n\n# VOICE MODE — your reply will be SPOKEN ALOUD.\n"
    "Talk like Jarvis to Tony, not a chatbot reading an essay:\n"
    "- ONE or TWO short sentences. No paragraphs, no bullet lists, no headers.\n"
    "- NEVER speak file paths, URLs, hashes, code, or markdown — they sound awful. "
    "Say 'saved it to your workspace', not the full path. Say 'opened the trailer', "
    "not the link.\n"
    "- If there's a lot of detail, give the one-line headline and OFFER the rest "
    "('want the specifics?') instead of dumping it.\n"
    "- Be fast and natural. Brevity is the whole point here."
)


def fresh_system_message(think_on: bool = False, voice_on: bool = False) -> Dict:
    """Rebuilt every turn so rules.md edits + new memory entries take effect.
    think_on=False adds the low-latency directive so the model knows not to
    emit reasoning blocks even if its chat template would default to thinking.
    voice_on flips the model into a terse, spoken-aloud register."""
    text = system_prompt()
    if not think_on:
        text += _LOW_LATENCY_DIRECTIVE
    if voice_on:
        text += _VOICE_MODE_DIRECTIVE
    return {"role": "system", "content": text}


# Tools that touch the system / user files / the network in non-trivial
# ways. Asks the user before running unless they pick "always".
RISKY_TOOLS = {
    "write_file", "edit_file", "delete_path", "move_path",
    "create_directory", "run_command", "open_app", "open_url",
    "open_in_browser",
    "memory_forget",
    # Writes + registers executable Python as a new tool — gate it.
    "create_plugin", "delete_plugin",
    # Browser actions can submit forms / click "buy" — gate the action ones.
    "browse_click", "browse_type",
    # Slow / scanning tools — also gated so the user can deny full-drive scans.
    "disk_usage",
    # VRAM-eating heavy ops — generation can take minutes and shuts down LLM
    "forge_generate", "forge_shutdown",
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _sanitize(text: str) -> str:
    """Strip lone surrogate code points that crash UTF-8 encoders.
    Windows console / clipboard sometimes emits these for some emojis."""
    if not text:
        return text
    return text.encode("utf-8", "replace").decode("utf-8", "replace")


# Phrases that indicate the model is announcing an action without taking it.
# Catches the most common Qwen/Gemma yield patterns. Case-insensitive,
# substring match against the assistant's content. Used by the anti-yield
# wrapper in JarvisCLI.respond() — see _yielded_this_turn handling.
_YIELD_TRIGGERS = (
    "i'll search", "i'll check", "i'll look", "i'll find",
    "i'll open", "i'll run", "i'll fetch", "i'll grab",
    "let me search", "let me check", "let me look", "let me find",
    "let me open", "let me run", "let me fetch",
    "i'm going to search", "i'm going to check", "i'm going to look",
    "i'm going to run", "i'm going to open",
    "going to run a", "going to check that", "going to look that up",
    "i will search", "i will check", "i will run",
    "one moment", "one sec while i", "one second",
    "hold on while i", "give me a sec",
)


def _looks_like_yield(text: str) -> bool:
    """True if the assistant message announces an action but didn't call
    a tool to do it. Conservative — only fires on clear-cut patterns to
    avoid nudging legitimate "I'll add that to memory" type responses.
    """
    if not text:
        return False
    t = text.lower().strip()
    # Must end with something that suggests "...and I'm about to do it"
    # rather than a completed statement. Trailing periods + short length
    # are good signals.
    if len(t) > 500:
        return False
    return any(trigger in t for trigger in _YIELD_TRIGGERS)


def autodetect_model() -> str:
    """Query the local server's /v1/models and pick the first non-embedding
    chat model. Falls back to LOCAL_MODEL if anything fails."""
    try:
        _req = urllib.request.Request(
            f"{LOCAL_API_BASE}/models",
            headers={"Authorization": f"Bearer {LOCAL_API_KEY or 'hearth-builtin'}"})
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
      1. /v1/models      — OpenAI-compatible, sometimes thin
      2. /api/v0/models  — native, almost always has loaded_context_length

    We probe both. Falls back to /api/v0/models/{id} (single-model detail)
    if the list endpoint omits the field. Returns None on total miss.
    """
    candidates = (
        f"{LOCAL_API_BASE}/models",
        # /api/v0/models lives at the SAME host but a different prefix.
        # Strip /v1 from LOCAL_API_BASE to derive the native API root.
        LOCAL_API_BASE.replace("/v1", "/api/v0") + "/models",
    )
    fields = ("loaded_context_length", "max_context_length",
              "context_length", "n_ctx", "max_position_embeddings")
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
        self.client = AsyncOpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
        # Auto-detect unless the user explicitly overrode via env
        self.current_model = LOCAL_MODEL if os.getenv("LOCAL_MODEL") else autodetect_model()
        # Whether the user pinned context via env / /context. If they did,
        # we don't auto-override when they /model switch.
        self._context_pinned = os.getenv("JARVIS_CONTEXT") is not None
        self.messages: List[Dict] = []
        self.all_chats_cache: List[Dict] = []
        self.active_chat_file: str = ""
        self.voice_on = VOICE_ON
        self.last_model_list: List[str] = []
        # Live context size — auto-detected from LM Studio if not pinned
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
        # loop bails cleanly) instead of raising KeyboardInterrupt — a raise
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
            # Single source of truth — same helper the GUI/bridge use. Has
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
        self.multiline_mode = False  # toggled with /multi
        # ONE thinking toggle:
        #   /think on  → model reasons AND we show the body inline
        #   /think off → model skips reasoning entirely (no compute spent)
        # Default off. Set JARVIS_THINK=1 to start on.
        self.think_on = os.getenv("JARVIS_THINK", "0") == "1"
        # Cloud reasoning models that 400 on `reasoning_effort` (e.g. plain
        # grok-4, grok-3 — only grok-3-mini / grok-4.3+ expose it). Populated
        # lazily the first time a model rejects the param, so we send it once,
        # learn, and never pay a failed round-trip for that model again.
        self._no_reasoning_effort: set[str] = set()
        # Most-recent image path the user/screenshot tool produced. Used
        # to auto-attach when the user references "the/that image".
        self.last_image_path: str = ""
        # Per-tool permissions ([a]lways / [N]ever) — loaded from disk so they
        # survive restarts. Mutating choices write back via _save_persisted_perms.
        self.tool_perms: Dict[str, str] = _load_persisted_perms()
        # /sleep mode - Jarvis stays silent until the user says the wake word.
        # Anything else is ignored. Use /wake to come back out.
        self.sleep_mode = False
        self._sleep_wake_word = (os.environ.get("JARVIS_WAKE_WORD", "jarvis")
                                 .strip().lower())
        self.auto_approve = os.getenv("JARVIS_AUTO_APPROVE", "0") == "1"
        # Lazy-init: prompt_toolkit needs a real terminal to construct a
        # session; we delay that until first input read so importing /
        # smoke-testing the module doesn't fail in a pipe.
        self.pt_session = None
        self._pt_init_attempted = False
        self.load_history()

    def _ensure_pt_session(self) -> None:
        if self._pt_init_attempted or not _PT_AVAILABLE:
            return
        self._pt_init_attempted = True
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
                insert newline — but submit if the buffer is a single-line
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

            self.pt_session = PromptSession(
                history=SafeFileHistory(hist_path),
                key_bindings=kb,
                enable_history_search=True,
                mouse_support=False,
            )
        except Exception:
            # No real console (piped, redirected, etc.) — fall back to input()
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
        Robust to history pruning/compaction (which mutates self.messages) —
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
        If JARVIS_LMSTUDIO_SYNC=1, also mirrors into a DEDICATED file under
        ~/.lmstudio/conversations/ so the chat shows up in LM Studio's UI.
        Never writes to your existing LM Studio threads — only the dedicated
        jarvis_cli.conversation.json file."""
        # Flush new turns to the never-pruned transcript BEFORE we overwrite the
        # (prunable) working-context file, so search/recall keeps the full record.
        self._append_transcript()
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2)
        except Exception as e:
            print(f"{C_ERR}Save failed: {e}{C_RESET}")
            return

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
                "name": "Jarvis CLI",
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
        # Boot banner — was JARVIS, now HEARTH (the framework).
        art = [
            "     █░█ █▀▀ ▄▀█ █▀█ ▀█▀ █░█     ",
            "     █▀█ ██▄ █▀█ █▀▄ ░█░ █▀█     ",
        ]
        sub = f"  local-first personal AI  ◆  hearth v{HEARTH_VERSION}"
        os.system("cls" if os.name == "nt" else "clear")
        # Gradient cascade banner
        for i, ln in enumerate(art):
            color = GRAD[i % len(GRAD)]
            sys.stdout.write(f"{C_BOLD}{color}{ln}{C_RESET}\n")
            sys.stdout.flush()
            time.sleep(0.04)
        sys.stdout.write(f"{C_DIM}{sub}{C_RESET}\n\n")

        # Boot status panel
        try:
            idx = memory.list_index()
            mem_lines = 0 if idx.startswith("(no") else idx.count("\n") + 1
        except Exception:
            mem_lines = 0
        v_status = voice.status()
        v_label = (
            f"on ({v_status['engine']})" if self.voice_on and v_status["ready"]
            else "ready, off" if v_status["ready"]
            else "no engine — see /voice"
        )
        rows = [
            ("model",     f"{self.current_model}"),
            ("endpoint",  f"{LOCAL_API_BASE}"),
            ("context",   f"{self.context_tokens} tokens, compact @ {int(COMPACT_AT*100)}%"),
            ("workspace", f"{WORKSPACE}"),
            ("reads",     "unrestricted" if not SAFE_READ_ONLY else "sandbox only"),
            ("tools",     f"{len(TOOL_DEFINITIONS)} loaded"),
            ("memories",  f"{mem_lines}"),
            ("voice",     v_label),
        ]
        # frame
        width = 64
        sys.stdout.write(f"{C_FRAME}╭{'─' * (width - 2)}╮{C_RESET}\n")
        for label, value in rows:
            line = f"{label:<10} {value}"
            line = line[: width - 4]
            pad = width - 4 - len(line)
            sys.stdout.write(
                f"{C_FRAME}│ {C_DIM}{label:<10}{C_RESET} {C_BOT}{value}{C_RESET}"
                f"{' ' * (width - 4 - 11 - len(value))}{C_FRAME} │{C_RESET}\n"
                if len(value) <= width - 16
                else f"{C_FRAME}│ {C_DIM}{label:<10}{C_RESET} {C_BOT}{value[:width-16]}…{C_RESET}{C_FRAME} │{C_RESET}\n"
            )
            _ = pad  # placeholder; visual padding handled in the f-string
        sys.stdout.write(f"{C_FRAME}╰{'─' * (width - 2)}╯{C_RESET}\n")
        # Connectivity + model sanity. A fresh user should instantly know
        # whether to go start LM Studio, load a model, or just start chatting.
        if not _is_local_endpoint(LOCAL_API_BASE):
            print(f"{C_OK}● online{C_RESET}{C_DIM}  ·  cloud: {self.current_model}  ·  /help for commands{C_RESET}\n")
        else:
            chat_models, reachable = self._probe_local_models()
            if not reachable:
                print(f"{C_WARN}● can't reach LM Studio at {LOCAL_API_BASE}{C_RESET}")
                print(f"{C_DIM}  Start LM Studio and load a model (or run a cloud model). /help for commands.{C_RESET}\n")
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
            print(f"{C_WARN}⚠ context is tight{C_RESET}{C_DIM} — persona+tools use ~{overhead:,} of "
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
            _req = urllib.request.Request(
                f"{LOCAL_API_BASE}/models",
                headers={"Authorization": f"Bearer {LOCAL_API_KEY or 'hearth-builtin'}"})
            with urllib.request.urlopen(_req, timeout=2) as r:
                data = json.loads(r.read().decode())
            ids = [m.get("id", "") for m in data.get("data", [])]
            chat = [m for m in ids if m and "embed" not in m.lower()]
            return chat, True
        except Exception:
            return [], False

    # -- Slash commands -----------------------------------------------------
    async def fetch_models(self):
        try:
            req = urllib.request.Request(
                f"{LOCAL_API_BASE}/models",
                headers={"Authorization": f"Bearer {LOCAL_API_KEY or 'hearth-builtin'}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            # Hide non-chat models — xAI lists grok-imagine-image/-video etc.
            # alongside chat models, but they 400 on /chat/completions (they're
            # image/video endpoints). They only clutter the picker.
            _NON_CHAT = ("imagine-image", "imagine-video", "imagine-audio",
                         "embedding", "embed-", "-tts", "whisper")
            ids = [m["id"] for m in data.get("data", [])
                   if m.get("id") and not any(p in m["id"].lower() for p in _NON_CHAT)]
            self.last_model_list = ids
            print(f"\n{C_BRAND}Models on {LOCAL_API_BASE}:{C_RESET}")
            for i, m in enumerate(ids, 1):
                star = " ←" if m == self.current_model else ""
                print(f"  [{i}] {m}{star}")
            print()
            return ids
        except Exception as e:
            print(f"{C_ERR}Could not reach {LOCAL_API_BASE}/models: {e}{C_RESET}")
            return []

    def _retarget_to(self, url: str, api_key: str = "not-needed",
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
        LOCAL_API_KEY = api_key or "not-needed"
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
            self.client = AsyncOpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
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
        print(f"{C_OK}↳ switched to {LOCAL_API_BASE}{C_RESET}  "
              f"{C_DIM}(model: {self.current_model}){C_RESET}")

    async def _cmd_models(self, raw: str) -> None:
        """Full model picker/downloader — matches the GUI Models tab.

        Subcommands:
          /models                — overview: server, on-disk GGUFs, recommended picks
          /models disk           — list every .gguf found on disk + LM Studio / HF cache
          /models picks          — show recommended downloads (PC-aware)
          /models hf <query>     — search Hugging Face for GGUF repos
          /models get <pick-id>  — download a recommended pick + boot built-in server
          /models use <path|n>   — boot built-in server with a disk model (n = number from /models disk)
          /models stop           — stop the built-in server
        """
        from hearth import llmserver
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""

        if sub == "stop":
            r = llmserver.stop_builtin()
            if r.get("ok") and r.get("was_running"):
                print(f"{C_OK}Built-in server stopped.{C_RESET}")
            else:
                print(f"{C_DIM}{r.get('error') or 'no server was running'}{C_RESET}")
            return

        if sub == "disk":
            disk = llmserver.scan_disk_for_models()
            if not disk:
                print(f"{C_DIM}No .gguf files found. Try '/models picks' to download one.{C_RESET}")
                return
            print(f"\n{C_BRAND}{len(disk)} model{'s' if len(disk)!=1 else ''} on disk:{C_RESET}")
            for i, m in enumerate(disk, 1):
                print(f"  [{i}] {C_TOOL}{m.get('filename')}{C_RESET} "
                      f"{C_DIM}({m.get('size_gb')} GB · {m.get('source')}){C_RESET}")
                print(f"      {C_DIM}{m.get('path')}{C_RESET}")
            print(f"\n{C_DIM}Boot one with: /models use <n>{C_RESET}\n")
            self._cli_disk_cache = disk
            return

        if sub == "picks":
            s = llmserver.status(LOCAL_API_BASE)
            picks = s.get("picks") or []
            rec_id = s.get("recommended_pick_id") or ""
            why = s.get("recommendation_reason") or ""
            print(f"\n{C_BRAND}Recommended downloads:{C_RESET} {C_DIM}{why}{C_RESET}")
            for p in picks:
                star = " (recommended)" if p.get("id") == rec_id else ""
                print(f"  {C_TOOL}{p.get('id')}{C_RESET}{C_OK}{star}{C_RESET}")
                print(f"      {p.get('name')} · {p.get('size_gb')} GB · {p.get('context'):,} ctx")
                print(f"      {C_DIM}{p.get('description')}{C_RESET}")
            print(f"\n{C_DIM}Download + boot: /models get <id>{C_RESET}\n")
            return

        if sub == "hf":
            if not arg:
                print(f"{C_ERR}Usage: /models hf <search query>{C_RESET}")
                return
            results = llmserver.search_huggingface(arg)
            if not results:
                print(f"{C_DIM}No GGUF repos found for '{arg}'.{C_RESET}")
                return
            print(f"\n{C_BRAND}Hugging Face GGUF results for '{arg}':{C_RESET}")
            for i, r in enumerate(results[:12], 1):
                print(f"  [{i}] {C_TOOL}{r.get('id')}{C_RESET}  {C_DIM}{r.get('downloads') or 0:,} dl{C_RESET}")
            print()
            return

        if sub == "get":
            if not arg:
                print(f"{C_ERR}Usage: /models get <pick-id>  (see /models picks){C_RESET}")
                return
            print(f"{C_DIM}Downloading + booting {arg} — this can take a while…{C_RESET}")
            def _cb(done: int, total: int) -> None:
                if total > 0:
                    pct = int(100 * done / total)
                    mb_done = done / (1024**2)
                    mb_total = total / (1024**2)
                    print(f"\r  {pct:3d}%  {mb_done:7.1f} / {mb_total:7.1f} MB", end="", flush=True)
            try:
                r = llmserver.download_model(arg, on_progress=_cb)
                print()
                if not r.get("ok"):
                    print(f"{C_ERR}{r.get('error') or 'download failed'}{C_RESET}")
                    return
                path = r.get("path")
                print(f"{C_OK}Saved to {path}{C_RESET}")
                print(f"{C_DIM}Starting built-in server…{C_RESET}")
                r2 = llmserver.start_builtin(path)
                if r2.get("ok"):
                    print(f"{C_OK}Built-in server up at {r2.get('url')}{C_RESET}")
                    self._retarget_to(r2.get("url"), "hearth-builtin", path)
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
            # 1) Numeric — index into disk cache
            if arg.isdigit():
                cache = getattr(self, "_cli_disk_cache", None) or llmserver.scan_disk_for_models()
                idx = int(arg) - 1
                if not (0 <= idx < len(cache)):
                    print(f"{C_ERR}Out of range — run /models disk to see the list.{C_RESET}")
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
                    # 3) No disk match either → try the recommended-pick
                    # download path. This was the missing arm that caused
                    # /models use harmonic-hermes-9b-q4_k_m to dead-end.
                    try:
                        st = llmserver.status(LOCAL_API_BASE)
                        picks = st.get("picks") or []
                        pick = next((p for p in picks if p.get("id") == arg), None)
                    except Exception:
                        pick = None
                    if pick:
                        print(f"{C_DIM}'{arg}' isn't on disk — kicking download then boot…{C_RESET}")
                        try:
                            dl = llmserver.download_model(arg)
                            if not dl.get("ok"):
                                print(f"{C_ERR}{dl.get('error') or 'download failed'}{C_RESET}")
                                return
                            path = dl.get("path")
                        except Exception as e:
                            print(f"{C_ERR}download failed: {type(e).__name__}: {e}{C_RESET}")
                            return
                    else:
                        print(f"{C_ERR}'{arg}' isn't a file path, an on-disk filename, "
                              f"or a known pick id. Run /models disk for paths, "
                              f"or /models picks for downloadable ids.{C_RESET}")
                        return
            print(f"{C_DIM}Booting llama.cpp built-in server with {path}…{C_RESET}")
            try:
                r = llmserver.start_builtin(path)
                if r.get("ok"):
                    print(f"{C_OK}Up at {r.get('url')}{C_RESET}")
                    self._retarget_to(r.get("url"), "hearth-builtin", path)
                else:
                    print(f"{C_ERR}{r.get('error') or 'failed'}{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}{type(e).__name__}: {e}{C_RESET}")
            return

        # No subcommand — overview. Cache the status (which disk-scans + probes
        # the endpoint) for 8s so hitting /models repeatedly is instant instead
        # of re-scanning every time — same idea the GUI uses.
        try:
            _now = time.time()
            _c = getattr(self, "_status_cache", None)
            if _c and (_now - _c[0]) < 8:
                s = _c[1]
            else:
                s = llmserver.status(LOCAL_API_BASE)
                self._status_cache = (_now, s)
        except Exception as e:
            print(f"{C_ERR}Could not fetch status: {e}{C_RESET}")
            await self.fetch_models()
            return

        print()
        if s.get("builtin_running"):
            print(f"{C_OK}● Built-in server running{C_RESET}  {C_DIM}{s.get('builtin_url') or ''}{C_RESET}")
        elif s.get("external_running"):
            print(f"{C_OK}● External server detected{C_RESET}  {C_DIM}{LOCAL_API_BASE}{C_RESET}")
        else:
            print(f"{C_DIM}○ No server reachable at {LOCAL_API_BASE}{C_RESET}")

        if s.get("external_running"):
            await self.fetch_models()

        disk = s.get("disk_models") or []
        if disk:
            print(f"\n{C_BRAND}On your disk{C_RESET}  {C_DIM}({len(disk)}){C_RESET}")
            for i, m in enumerate(disk[:6], 1):
                print(f"  [{i}] {m.get('filename')}  {C_DIM}({m.get('size_gb')} GB · {m.get('source')}){C_RESET}")
            if len(disk) > 6:
                print(f"  {C_DIM}…and {len(disk)-6} more — /models disk{C_RESET}")
            self._cli_disk_cache = disk

        rec_id = s.get("recommended_pick_id")
        rec = next((p for p in (s.get("picks") or []) if p.get("id") == rec_id), None)
        if rec and not s.get("builtin_running"):
            print(f"\n{C_BRAND}Recommended for your PC{C_RESET}  {C_DIM}{s.get('recommendation_reason') or ''}{C_RESET}")
            print(f"  {C_TOOL}{rec.get('id')}{C_RESET}  {rec.get('size_gb')} GB · {rec.get('context'):,} ctx")
            print(f"  {C_DIM}{rec.get('description')}{C_RESET}")

        print()
        print(f"{C_DIM}Subcommands:  /models disk  ·  picks  ·  hf <q>  ·  get <id>  ·  use <path|n>  ·  stop{C_RESET}\n")

    async def handle_command(self, text: str) -> bool:
        # `global` MUST sit at the very top of this function, BEFORE any
        # read of these names — Python parses the whole function body and
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
        if low == "/help":
            print(f"{C_BOT}Commands:{C_RESET}")
            print(f"  {C_DIM}── Endpoint / model ─────────────────────────{C_RESET}")
            print(f"  {C_TOOL}/brain{C_RESET} (or {C_TOOL}/endpoint{C_RESET})       switch brain: local / grok / gemini / openai / openrouter / custom")
            print(f"  {C_TOOL}/models{C_RESET}                model dashboard: server, disk, picks")
            print(f"  {C_TOOL}/models disk|picks|hf|get|use|stop{C_RESET}  full picker. `use`/`get` retarget THIS session — no restart.")
            print(f"  {C_TOOL}/model <id|n>{C_RESET}          switch model on current server (alias: /load)")
            print(f"  {C_DIM}── Chat / context ───────────────────────────{C_RESET}")
            print(f"  {C_TOOL}/tools{C_RESET}                 list available tools")
            print(f"  {C_TOOL}/clear{C_RESET}                 wipe context (keep system)")
            print(f"  {C_TOOL}/chats{C_RESET}                 list LM Studio threads")
            print(f"  {C_TOOL}/chat <n|name>{C_RESET}         load thread (read-only copy)")
            print(f"  {C_TOOL}/history{C_RESET}               last 5 messages")
            print(f"  {C_TOOL}/workspace{C_RESET}             show workspace path")
            print(f"  {C_TOOL}/log [n]{C_RESET}               tail activity log")
            print(f"  {C_TOOL}/tokens{C_RESET}                context usage estimate")
            print(f"  {C_TOOL}/compact{C_RESET}               summarize old turns now")
            print(f"  {C_TOOL}/mem{C_RESET}                   show memory index")
            print(f"  {C_TOOL}/rules{C_RESET}                 show rules.md path")
            print(f"  {C_TOOL}/name [NewName]{C_RESET}        show / set agent name (JARVIS, Cortana, Friday…)")
            print(f"  {C_TOOL}/migrate <hermes|openclaw>{C_RESET}  import memory/skills/config from another agent")
            print(f"  {C_TOOL}/import-memory [file]{C_RESET}      pull your memory out of ChatGPT/Claude (paste reply to a file, then import)")
            print(f"  {C_TOOL}/jobs [all|<id>|kill <id>]{C_RESET}    background jobs (disk_usage / start_job / etc.)")
            print(f"  {C_TOOL}/mcp [edit|config|run]{C_RESET}     Model Context Protocol — Hearth as server / configure outbound")
            print(f"  {C_TOOL}/agent [<slug> \"<prompt>\"]{C_RESET}  list personas / spawn a sub-agent synchronously")
            print(f"  {C_TOOL}/voice [on|off]{C_RESET}        TTS toggle (alias: /voices)")
            print(f"  {C_TOOL}/voice speed <n>{C_RESET}       TTS playback rate (e.g. 1.2)")
            print(f"  {C_TOOL}/stt [model]{C_RESET}           show/switch STT model (tiny.en/base.en/small.en/...)")
            print(f"  {C_TOOL}/listen{C_RESET}                one-shot voice input (record → transcribe)")
            print(f"  {C_TOOL}/listen on|off{C_RESET}         continuous voice-in with TTS interrupt")
            print(f"  {C_TOOL}/context <n|auto>{C_RESET}      set context window (or auto-detect)")
            print(f"  {C_TOOL}/think [on|off]{C_RESET}        toggle reasoning (off = fast, no thinking)")
            print(f"  {C_TOOL}/multi{C_RESET}                 toggle multi-line input mode")
            print(f"  {C_TOOL}/perms{C_RESET}                 show saved tool permissions (persist across restarts)")
            print(f"  {C_TOOL}/perms forget <tool>{C_RESET}   forget the saved decision for one tool")
            print(f"  {C_TOOL}/perms reset{C_RESET}           forget ALL saved tool decisions")
            print(f"  {C_TOOL}/allow <path>{C_RESET}          let Jarvis write under <path> this session")
            print(f"  {C_TOOL}/disallow <path>{C_RESET}       revoke an /allow")
            print(f"  {C_TOOL}/allowed{C_RESET}               list paths Jarvis can write to")
            print(f"  {C_TOOL}/about{C_RESET}                 version, endpoint, repo, stats")
            print(f"  {C_TOOL}/exit{C_RESET}                  quit (or just say bye)")
            print()
            print(f"  {C_DIM}@<path>{C_RESET}                attach a file inline in your prompt")
            print(f"  {C_DIM}Esc+Enter{C_RESET}              insert newline (paste multi-line works)")
            return True
        if low == "/about":
            kind = "local" if _is_local_endpoint(LOCAL_API_BASE) else "cloud"
            try:
                mem_lines = 0 if memory.list_index().startswith("(no") else memory.list_index().count("\n") + 1
            except Exception:
                mem_lines = 0
            print(f"\n{C_BRAND}Hearth v{HEARTH_VERSION}{C_RESET}  {C_DIM}local-first personal AI{C_RESET}")
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
        if low.startswith("/model ") or low.startswith("/load "):
            arg = cmd.split(None, 1)[1].strip().strip("[]")
            # Guard against `/model use 1` / `/model use X` etc. — easy typos
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
                    print(f"{C_OK}model → {self.current_model}{C_RESET}")
                else:
                    print(f"{C_ERR}out of range — /models to see the list{C_RESET}")
                    return True
            else:
                # Validate against the current server's actual model list before
                # accepting an id. Stops garbage like `/model use 1` from being
                # silently saved as a model name and then 400'ing every chat.
                # If the server is unreachable (cloud 401, etc.) and the user
                # passed a string, accept it but warn — cloud endpoints often
                # don't expose /v1/models even with a valid key.
                if not self.last_model_list:
                    try:
                        await self.fetch_models()
                    except Exception:
                        pass
                if self.last_model_list and arg not in self.last_model_list:
                    print(f"{C_ERR}'{arg}' is not in the server's model list.{C_RESET}")
                    print(f"{C_DIM}  Available: {', '.join(self.last_model_list[:6])}"
                          f"{'…' if len(self.last_model_list) > 6 else ''}{C_RESET}")
                    print(f"{C_DIM}  Run /models to list, or /brain to switch endpoint.{C_RESET}")
                    return True
                self.current_model = arg
                print(f"{C_OK}model → {self.current_model}{C_RESET}")
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
        # /endpoint is the natural-language alias for /brain — every cloud
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
            # top of handle_command — Python requires it before any read.)
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
                try:
                    os.makedirs(WORKSPACE, exist_ok=True)
                    with open(keys_path, "w", encoding="utf-8") as f:
                        _json.dump(d, f, indent=2)
                except OSError:
                    pass

            parts = cmd.split(None, 2)
            if len(parts) == 1:
                # Just /brain — show current + how to switch + which keys are saved
                provider_label = "cloud" if not _is_local_endpoint(LOCAL_API_BASE) else "local"
                print(f"\n{C_BOT}Current brain:{C_RESET} {C_TOOL}{provider_label}{C_RESET}  "
                      f"{C_DIM}({self.current_model} via {LOCAL_API_BASE}){C_RESET}\n")
                saved_keys = _load_keys()
                if saved_keys:
                    have = sorted(k for k in saved_keys if saved_keys[k].get("key"))
                    print(f"{C_DIM}Saved keys: {', '.join(have) if have else '(none)'}{C_RESET}")
                print(f"\n{C_BOT}Switch with:{C_RESET}")
                print(f"  {C_TOOL}/brain local{C_RESET}                     auto: whatever's at localhost:1234")
                print(f"  {C_TOOL}/brain lmstudio{C_RESET}                  use LM Studio (download.lmstudio.ai)")
                print(f"  {C_TOOL}/brain builtin{C_RESET}                   use Hearth's bundled llama.cpp (no LM Studio needed)")
                print(f"  {C_TOOL}/brain grok [api-key]{C_RESET}            xAI Grok       (key optional if saved)")
                print(f"  {C_TOOL}/brain gemini [api-key]{C_RESET}          Google Gemini  (key optional if saved)")
                print(f"  {C_TOOL}/brain openai [api-key]{C_RESET}          OpenAI         (key optional if saved)")
                print(f"  {C_TOOL}/brain openrouter [api-key]{C_RESET}      OpenRouter     (key optional if saved)")
                print(f"  {C_TOOL}/brain custom <url> <api-key>{C_RESET}    any OpenAI-compatible server")
                print(f"  {C_TOOL}/brain forget <provider>{C_RESET}         delete a saved key")
                print(f"\n{C_DIM}Keys saved to ~/Jarvis/brain_keys.json (gitignored, 0600 on POSIX).{C_RESET}\n")
                return True

            provider = parts[1].strip().lower()

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
            #   - builtin   : Hearth's bundled llama-cpp-python server. Doesn't START anything — `/models use <n>` does that.
            # We route lmstudio + builtin through the same URL but the
            # confirmation print differs so first-timers know which one they
            # just picked. The conflict detection in llmserver.start_builtin
            # keeps them from colliding at runtime.
            presets_url_model = {
                "local":      ("http://localhost:1234/v1", "qwen2.5-7b-instruct"),
                "lmstudio":   ("http://localhost:1234/v1", "qwen2.5-7b-instruct"),
                "builtin":    ("http://localhost:1234/v1", "qwen2.5-7b-instruct"),
                "grok":       ("https://api.x.ai/v1",       "grok-4.3"),
                "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
                "openai":     ("https://api.openai.com/v1", "gpt-4o-mini"),
                "openrouter": ("https://openrouter.ai/api/v1", "anthropic/claude-3.5-sonnet"),
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
                # Key resolution: explicit arg > saved > error (local doesn't need one)
                if arg:
                    key = arg  # user provided new key; this also UPDATES the saved one
                else:
                    saved_entry = saved_keys.get(provider) or {}
                    key = saved_entry.get("key", "")
                    if not key and provider != "local":
                        print(f"{C_ERR}no saved key for {provider}. Use: /brain {provider} <api-key>{C_RESET}")
                        print(f"{C_DIM}(saved next time; type just /brain {provider} after that){C_RESET}")
                        return True
                # Allow saved entry to override the default model hint
                if provider in saved_keys and saved_keys[provider].get("model"):
                    model_hint = saved_keys[provider]["model"]
            else:
                print(f"{C_ERR}Unknown provider {provider!r}. "
                      f"Try: local, grok, gemini, openai, openrouter, custom, forget.{C_RESET}")
                return True

            # Mutate the runtime + env so the next chat call picks up the new
            # endpoint. (`global` already declared above.)
            LOCAL_API_BASE = url
            LOCAL_API_KEY = key or "not-needed"
            if model_hint:
                LOCAL_MODEL = model_hint
                self.current_model = model_hint
            os.environ["LOCAL_API_BASE"] = url
            if key:
                os.environ["LOCAL_API_KEY"] = key
            else:
                os.environ.pop("LOCAL_API_KEY", None)
            if model_hint:
                os.environ["LOCAL_MODEL"] = model_hint

            # Rebuild the AsyncOpenAI client so the next request uses new creds
            try:
                self.client = AsyncOpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            except Exception as e:
                print(f"{C_WARN}client rebuild warning: {e}{C_RESET}")

            # Persist: save the key under its provider key so future
            # `/brain <provider>` (no arg) auto-loads it.
            if provider != "local" and key:
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
                saved.update({
                    "llm_provider": provider,
                    "llm_url": url,
                    "llm_key": key,
                    "llm_model": model_hint or saved.get("llm_model", ""),
                })
                os.makedirs(WORKSPACE, exist_ok=True)
                with open(settings_path, "w", encoding="utf-8") as f:
                    _json.dump(saved, f, indent=2)
            except Exception:
                pass

            print(f"{C_OK}brain → {provider}{C_RESET}  "
                  f"{C_DIM}({url}{' · ' + model_hint if model_hint else ''}){C_RESET}")
            # Re-detect the context window for the NEW brain. Without this the
            # budget kept the previous model's value (e.g. a 32K local default),
            # so a 1M-ctx cloud model like grok-4.3 got compacted at 32K. /model
            # already does this; /brain didn't — this is that fix.
            try:
                from hearth.headless import resolve_context_tokens
                _tok, _src = resolve_context_tokens(self.current_model)
                if _tok and _tok > 1024:
                    self.context_tokens = _tok
                    print(f"{C_DIM}  context auto-set to {_tok:,} tokens ({_src}){C_RESET}")
            except Exception:
                pass
            # Friendly first-timer hints — tells the user what to DO next
            # depending on which "local" variant they picked.
            if provider == "lmstudio":
                print(f"{C_DIM}  Expecting LM Studio at {url}. If you don't have LM Studio:{C_RESET}")
                print(f"{C_DIM}    • Download from https://lmstudio.ai (free)  · OR{C_RESET}")
                print(f"{C_DIM}    • Use Hearth's built-in server: /brain builtin then /models use <n>{C_RESET}")
            elif provider == "builtin":
                print(f"{C_DIM}  Built-in mode picked. To load a model:{C_RESET}")
                print(f"{C_DIM}    1. /models           → see what's on disk{C_RESET}")
                print(f"{C_DIM}    2. /models use <n>   → boot Hearth's llama.cpp server with that model{C_RESET}")
                print(f"{C_DIM}    Notes:{C_RESET}")
                print(f"{C_DIM}      • If LM Studio is running on port 1234, stop it first.{C_RESET}")
                print(f"{C_DIM}      • Hearth re-uses any GGUFs you have in LM Studio's cache — no double-download.{C_RESET}")
            elif provider != "local" and arg:
                print(f"{C_DIM}(key saved — next time just type /brain {provider}){C_RESET}")
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
            self.save_history()
            print(f"{C_OK}Context cleared.{C_RESET}")
            return True
        if low == "/chats":
            self.all_chats_cache = self.get_all_lmstudio_chats()
            print(f"\n{C_BRAND}LM Studio threads:{C_RESET}")
            for i, c in enumerate(self.all_chats_cache[:25], 1):
                here = " (active)" if c["path"] == self.active_chat_file else ""
                print(f"  [{i}] {c['name']}{C_DIM}{here}{C_RESET}")
            print()
            return True
        if low.startswith("/chat "):
            arg = cmd.split(None, 1)[1].strip().strip("[]")
            if not self.all_chats_cache:
                self.all_chats_cache = self.get_all_lmstudio_chats()
            chosen = None
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(self.all_chats_cache):
                    chosen = self.all_chats_cache[idx]
                else:
                    print(f"{C_ERR}out of range — /chats to see the list{C_RESET}")
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
                content = (m.get("content") or "")[:140].replace("\n", " ")
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
                # No file given — show the user how to pull their memory out of
                # another AI and import it. File-based on purpose: pasting a
                # multi-line dump into Windows Terminal only sends line 1.
                print(f"{C_TOOL}Import your memory from ChatGPT / Claude / any AI:{C_RESET}")
                print(f"  {C_DIM}1.{C_RESET} Paste this into that AI:")
                print(f'     {C_DIM}"List everything you know or remember about me as plain'
                      f' bullet points\n      — name, work, preferences, projects, important dates,'
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
                _llm = _mx.make_openai_llm_call(_sync, self.current_model, max_tokens=900)
                _msgs = [{"role": "user",
                          "content": "Here is everything another AI remembered about me — "
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
                print(f"{C_DIM}(no extra paths — writes confined to {WORKSPACE}){C_RESET}")
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
            est = estimate_tokens(self.messages)
            pct = est / self.context_tokens * 100
            print(f"  ~{est} tokens / {self.context_tokens} ({pct:.0f}%)")
            return True
        if low.startswith("/context"):
            parts = cmd.split()
            # Sanity ceiling — no real provider serves >1M ctx today; values
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
                    print(f"{C_ERR}could not detect ctx — endpoint didn't expose loaded_context_length{C_RESET}")
            elif len(parts) >= 2:
                # Accept 20480, "32k", "1M", "1.5m", "128K" — not just bare digits.
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
                    print(f"{C_ERR}context {requested:,} is above the 1M ceiling — capping to {_CTX_MAX:,}.{C_RESET}")
                    print(f"{C_DIM}  no real model accepts >1M tokens. If you wanted N thousand, drop the extra zeros.{C_RESET}")
                    requested = _CTX_MAX
                elif requested < _CTX_MIN:
                    print(f"{C_ERR}context {requested} is below the {_CTX_MIN} floor — capping to {_CTX_MIN}.{C_RESET}")
                    requested = _CTX_MIN
                self.context_tokens = requested
                self._context_pinned = True
                print(f"{C_OK}context window → {self.context_tokens:,} tokens (pinned){C_RESET}")
            else:
                print(f"  current: {self.context_tokens:,} tokens ({'pinned' if self._context_pinned else 'auto-detected'})")
                print(f"  usage:   /context <number>   (e.g. 20480, 32k, 1M; capped at {_CTX_MAX:,})")
                print(f"           /context auto       (re-detect via per-provider table + endpoint probe)")
            return True
        if low == "/listen" or low.startswith("/listen "):
            parts = cmd.split()
            arg = parts[1].lower() if len(parts) > 1 else ""
            if arg in ("on", "1", "true", "start"):
                # Continuous mode — background listener races with typed input
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
                    print(f"{C_DIM}warming up STT (whisper {st.get('model_size', 'base.en')}; first run downloads ~150MB){C_RESET}")
                status_msg = stt.start_continuous(_on_utterance)
                if status_msg.startswith("Error"):
                    print(f"{C_ERR}{status_msg}{C_RESET}")
                else:
                    self.listen_continuous = True
                    print(f"{C_OK}listening: ON — talk any time. type to interrupt.{C_RESET}")
                return True
            if arg in ("off", "0", "false", "stop"):
                stt.stop_continuous()
                self.listen_continuous = False
                print(f"{C_OK}listening: off{C_RESET}")
                return True
            if arg == "status":
                import json as _json
                print(_json.dumps(stt.status(), indent=2))
                return True
            # No arg → one-shot record
            if not stt.is_available():
                st = stt.status()
                print(f"{C_ERR}STT not ready.{C_RESET}")
                if st.get("last_load_error"):
                    print(f"{C_DIM}  {st['last_load_error']}{C_RESET}")
                else:
                    print(f"{C_DIM}  install: pip install faster-whisper sounddevice numpy{C_RESET}")
                return True
            print(f"{C_DIM}🎙 listening… speak then pause{C_RESET}")
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
        if low.startswith("/think"):
            parts = cmd.split()
            if len(parts) >= 2 and parts[1].lower() in ("on", "1", "true", "yes"):
                self.think_on = True
            elif len(parts) >= 2 and parts[1].lower() in ("off", "0", "false", "no"):
                self.think_on = False
            else:
                # bare /think toggles
                self.think_on = not self.think_on
            if self.think_on:
                print(f"{C_OK}thinking: ON — model reasons, body shown inline{C_RESET}")
            else:
                print(f"{C_OK}thinking: OFF — no reasoning compute, no <think> blocks{C_RESET}")
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
                print(f"{C_OK}forgot {tool} — it'll ask again next time{C_RESET}")
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
        if low == "/mem" or low.startswith("/mem "):
            # /mem            — flat index (legacy)
            # /mem tree       — ASCII tree (type → sub-category → fact)
            # /mem map        — open the GUI memory tab in default browser
            parts = cmd.split(None, 1)
            sub = (parts[1].strip().lower() if len(parts) > 1 else "").split()[0] if len(parts) > 1 else ""
            if sub in ("tree", "t"):
                self._print_memory_tree()
            elif sub in ("map", "graph", "g"):
                self._open_memory_map()
            else:
                idx = memory.list_index()
                print(idx if idx else "(empty)")
                print(f"\n{C_DIM}/mem tree — ASCII tree by category"
                      f"  ·  /mem map — open visual graph in browser{C_RESET}")
            return True
        if low == "/rules":
            memory.ensure_rules_exist()
            print(f"  {memory.RULES_PATH}")
            print(f"  {C_DIM}edit freely — Jarvis re-reads it every turn{C_RESET}")
            return True
        if low == "/name" or low.startswith("/name "):
            # /name             — show current agent name
            # /name <new>       — rename the agent (persona only, no folder
            #                     rename here — that's a GUI-only flow because
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
                # Persist so next CLI launch picks it up. Use the settings
                # API path the GUI shares.
                from pathlib import Path as _P
                settings_path = _P.home() / "Jarvis" / "settings.json"
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
                      f"Workspace folder ~/{old} unchanged — use GUI to move it.{C_RESET}")
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
                print(f"  {C_DIM}(disk_usage on a drive root auto-backgrounds — "
                      f"check this after asking JARVIS to scan something){C_RESET}")
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
            mcp_path = _P.home() / "Jarvis" / "mcp.json"
            if not sub:
                # Status: count Hearth's exported tools + show client-config file location
                try:
                    from hearth import TOOL_DEFINITIONS as _td
                    print(f"  {C_OK}Hearth is an MCP server{C_RESET}  "
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
                    print(f"  {C_DIM}outbound bridges (Hearth USING other MCP servers):{C_RESET}")
                    for b in bridges:
                        color = (C_OK if b.get("state") == "connected"
                                 else C_WARN if b.get("state") in ("starting", "pending")
                                 else C_ERR)
                        ntools = len(b.get("tools", []))
                        err = b.get("error", "")
                        print(f"  {color}● {b['name']:20}{C_RESET} {b.get('state', '?'):12} "
                              f"{C_DIM}{ntools} tools{C_RESET}"
                              + (f"  {C_ERR}{err[:60]}{C_RESET}" if err else ""))
                else:
                    print()
                    print(f"  {C_DIM}no outbound MCP servers configured yet. "
                          f"Run /mcp edit to add some.{C_RESET}")
                return True
            if sub == "edit":
                mcp_path.parent.mkdir(parents=True, exist_ok=True)
                if not mcp_path.is_file():
                    sample = {"mcpServers": {
                        "example-filesystem": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", str(_P.home() / "Documents")],
                            "env": {},
                        }
                    }}
                    mcp_path.write_text(__import__("json").dumps(sample, indent=2), encoding="utf-8")
                editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "nano")
                try:
                    __import__("subprocess").Popen([editor, str(mcp_path)])
                    print(f"  {C_OK}opened {mcp_path} in {editor}{C_RESET}")
                except Exception as e:
                    print(f"  {C_ERR}could not open editor: {e}{C_RESET}")
                    print(f"  {C_DIM}path: {mcp_path}{C_RESET}")
                return True
            if sub == "config":
                snippet = __import__("json").dumps({
                    "mcpServers": {
                        "hearth": {
                            "command": "python",
                            "args": ["-m", "hearth.mcp_server"],
                        }
                    }
                }, indent=2)
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
            return True
        if low == "/sleep":
            self.sleep_mode = True
            print(f"{C_OK}sleep mode ON{C_RESET}  "
                  f"{C_DIM}Jarvis is silent until you say '{self._sleep_wake_word}' "
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
        if low in ("/voice", "/voices") or low.startswith("/voice ") or low.startswith("/voices "):
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
                    print(f"{C_OK}TTS speed → {new_speed:g}x{C_RESET}")
                    if self.voice_on and voice.is_available():
                        voice.stop()
                        voice.speak("speed updated", blocking=False)
                else:
                    print(f"{C_DIM}current TTS speed: {voice.status().get('default_speed')}x  "
                          f"(usage: /voice speed 1.2){C_RESET}")
            elif parts[1].lower() in ("set", "voice", "name") and len(parts) >= 3:
                vname = parts[2]
                voice.set_default_voice(vname)
                print(f"{C_OK}voice → {vname}{C_RESET}")
                # Speak a sample so the user hears it immediately
                if self.voice_on and voice.is_available():
                    voice.stop()
                    voice.speak(f"voice set to {vname}", blocking=False)
            elif len(parts) >= 2 and (parts[1].startswith(("am_", "af_", "bm_", "bf_"))
                                       or parts[1].endswith(".onnx")):
                # bare /voice <name> shortcut
                vname = parts[1]
                voice.set_default_voice(vname)
                print(f"{C_OK}voice → {vname}{C_RESET}")
                if self.voice_on and voice.is_available():
                    voice.stop()
                    voice.speak(f"voice set to {vname}", blocking=False)
            return True
        if cmd.startswith("/"):
            print(f"{C_ERR}Unknown command. /help for list.{C_RESET}")
            return True
        return False

    # -- Spinner ------------------------------------------------------------
    async def spinner(self, label: str = "thinking"):
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        dots = ["", ".", "..", "..."]
        i = 0
        t0 = time.time()
        try:
            while True:
                dt = time.time() - t0
                d = dots[(i // 4) % len(dots)]
                color = GRAD[i % len(GRAD)]
                sys.stdout.write(
                    f"\r{color}{frames[i % len(frames)]}{C_RESET} "
                    f"{C_DIM}{label}{d:<3} {dt:.1f}s{C_RESET}"
                )
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    # -- Status footer ------------------------------------------------------
    def _footer_prompt(self) -> str:
        try:
            est = estimate_tokens(self.messages)
            pct = int(est / self.context_tokens * 100)
        except Exception:
            pct = 0
        bar_w = 12
        filled = max(0, min(bar_w, int(bar_w * pct / 100)))
        bar_color = C_OK if pct < 60 else C_WARN if pct < 85 else C_ERR
        bar = bar_color + "█" * filled + C_DIM + "·" * (bar_w - filled) + C_RESET
        v_on = "♪" if self.voice_on and voice.is_available() else " "
        l_on = " 🎙" if self.listen_continuous else ""
        multi = "  multi" if self.multiline_mode else ""
        # Compose status line then prompt symbol on next visual chunk
        return (
            f"{C_DIM}┌─ {self.current_model} {C_RESET}"
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
          1. `@<path>` — explicit, anywhere in the message.
          2. Bare absolute path that points to an existing image file —
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
                    with open(cand, "rb") as f:
                        data = f.read()
                    mime = mimetypes.guess_type(cand)[0] or "image/png"
                    b64 = base64.b64encode(data).decode("ascii")
                    images.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
                    text_parts.append(f"\n[attached image: {cand}]")
                except OSError:
                    text_parts.append(f"\n[could not read image {cand}]")
            elif force_image:
                # @<path> on a non-image file: splice as text
                try:
                    with open(cand, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                    if len(body) > 8000:
                        body = body[:8000] + f"\n…[truncated, full {len(body)} chars]"
                    text_parts.append(f"\n\n[attached file: {cand}]\n```\n{body}\n```")
                except OSError:
                    text_parts.append(f"\n[could not read {cand}]")
            # else: bare text-file path mentioned in a sentence — leave alone

        # Pass 1: explicit @<path> tokens (any file type, force inline)
        for tok in text.split():
            if tok.startswith("@") and len(tok) > 1:
                _attach(tok[1:], force_image=True)

        # Pass 2: bare absolute paths anywhere in the text — only attach
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
                # prompt_async on the existing session — it temporarily
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
        # If both finished simultaneously, prefer the typed input — user
        # intent is clearer when they actually type.
        if prompt_task in done:
            try:
                return prompt_task.result()
            except Exception:
                pass
        if listen_task in done:
            try:
                heard = listen_task.result()
                # Echo what we heard so the user has visual confirmation
                sys.stdout.write(f"{C_DIM}🎙 {C_RESET}{heard}\n")
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

        IMPORTANT: must use prompt_async() — prompt() calls asyncio.run()
        internally and we're already inside the main event loop."""
        self._ensure_pt_session()
        prompt_str = self._footer_prompt()
        if self.pt_session is not None:
            return await self.pt_session.prompt_async(
                ANSI(prompt_str),
                multiline=self.multiline_mode,
            )
        return await asyncio.to_thread(input, prompt_str)

    def _is_vision_capable(self) -> bool:
        """Probe LM Studio's /api/v0/models for the loaded model's true
        capabilities. Cached per model id so we don't probe every view_image.

        The signals (in priority order):
          1. `type == "vlm"` field — explicit Vision-Language-Model marker.
             This is the cleanest answer LM Studio gives us; Qwen3.5-9B has
             this even though its id doesn't contain "vl".
          2. "vision" / "image_input" in the `capabilities` array.
          3. Model id substring match (fallback for non-LM-Studio backends
             like Ollama where v0 may not exist).
        """
        cache = getattr(self, "_vision_cap_cache", {})
        mid = self.current_model or ""
        if mid in cache:
            return cache[mid]
        result = False
        try:
            import urllib.request
            # LOCAL_API_BASE is .../v1 — strip it to get the host
            host = LOCAL_API_BASE.rsplit("/v1", 1)[0]
            with urllib.request.urlopen(f"{host}/api/v0/models", timeout=2) as r:
                data = json.loads(r.read())
            for m in data.get("data", []):
                if m.get("id") == mid:
                    if m.get("type") == "vlm":
                        result = True
                    caps = m.get("capabilities") or []
                    if any(c in caps for c in ("vision", "image_input", "image")):
                        result = True
                    break
        except Exception:
            pass
        if not result:
            # Fallback heuristic — useful when not on LM Studio (Ollama, vLLM)
            # or on cloud endpoints (Gemini/OpenAI/Anthropic) where the
            # /api/v0/models probe doesn't exist. These families are multimodal.
            mlc = mid.lower()
            if any(s in mlc for s in ("vl", "vision", "gemma-3", "llava",
                                      "moondream", "internvl", "minicpm-v",
                                      "gemini", "gpt-4o", "gpt-4.1", "gpt-4-turbo",
                                      "o3", "o4-mini", "claude-3", "claude-4",
                                      "claude-sonnet", "claude-opus", "claude-haiku",
                                      "pixtral", "grok-4", "grok-build", "grok-2-vision")):
                result = True
        cache[mid] = result
        self._vision_cap_cache = cache
        return result

    def _is_first_run(self) -> bool:
        """True if the user has essentially no memory yet — fresh install.
        Crude heuristic that avoids running the wizard every relaunch."""
        # First: honor the GUI's onboarded flag. If the user already
        # finished onboarding in the desktop app, don't make them sit
        # through the CLI wizard on first CLI launch — that was the
        # "double onboarding" pain.
        try:
            from hearth.tools import WORKSPACE
            settings_path = os.path.join(WORKSPACE, "settings.json")
            if os.path.isfile(settings_path):
                with open(settings_path, "r", encoding="utf-8") as _sf:
                    if json.load(_sf).get("onboarded"):
                        return False
        except Exception:
            pass
        from hearth.tools import MEMORY_DIR
        idx = os.path.join(MEMORY_DIR, "MEMORY.md")
        if not os.path.exists(idx):
            return True
        try:
            with open(idx, "r", encoding="utf-8") as f:
                entries = [ln for ln in f if ln.strip().startswith("-")]
            return len(entries) < 2
        except OSError:
            return False

    def _persist_onboarding(self, answers: Dict[str, str]) -> None:
        """Write wizard answers to memory + rules.md additively."""
        from hearth import memory
        from hearth.tools import WORKSPACE

        if answers.get("name"):
            memory.save(
                title="User name",
                mtype="user",
                description=f"Call me {answers['name']}",
                body=f"My name (or what I prefer to be called): {answers['name']}",
            )
        if answers.get("role"):
            memory.save(
                title="User role",
                mtype="user",
                description=f"User is a {answers['role']}",
                body=(
                    f"What I do: {answers['role']}\n\n"
                    f"Use this to tune explanations and references appropriately."
                ),
            )
        if answers.get("browser"):
            body = f"Preferred browser: {answers['browser']}"
            if answers.get("profile"):
                body += f"\nDefault profile name: {answers['profile']}"
            desc = f"Default browser is {answers['browser']}" + (
                f" / profile '{answers['profile']}'" if answers.get("profile") else ""
            )
            memory.save(
                title="Preferred browser",
                mtype="reference",
                description=desc,
                body=body,
            )

        extra_rules = []
        if answers.get("tone"):
            extra_rules.append(f"- Tone preference: {answers['tone']}.")
        if answers.get("avoid"):
            if answers["avoid"].strip().lower() not in ("none", "n/a", "nothing", "no"):
                extra_rules.append(f"- Topics/language to avoid: {answers['avoid']}.")
        if extra_rules:
            rules_path = os.path.join(WORKSPACE, "rules.md")
            existed = os.path.isfile(rules_path)
            with open(rules_path, "a", encoding="utf-8") as f:
                if existed:
                    f.write("\n")
                f.write("# Onboarding preferences (set by first-run wizard)\n")
                f.write("\n".join(extra_rules) + "\n")

        # Set the shared `onboarded` flag in settings.json so the GUI
        # doesn't re-onboard if the user launches the desktop app later.
        try:
            settings_path = os.path.join(WORKSPACE, "settings.json")
            cur: Dict = {}
            if os.path.isfile(settings_path):
                with open(settings_path, "r", encoding="utf-8") as _sf:
                    cur = json.load(_sf) or {}
            cur["onboarded"] = True
            # Agent rename from the wizard — persist + apply to this session so
            # the persona signature / banner use the new name from message #1.
            _agent = (answers.get("agent_name") or "").strip()
            if _agent and _agent.lower() != "jarvis":
                cur["agent_name"] = _agent
                os.environ["HEARTH_PERSONA_NAME"] = _agent
                try:
                    from hearth import persona as _p
                    _p.NAME = _agent
                except Exception:
                    pass
            with open(settings_path, "w", encoding="utf-8") as _sf:
                json.dump(cur, _sf, indent=2)
        except Exception:
            pass

    async def _maybe_extract_facts(self) -> None:
        """Passive memory extraction at the end of a turn — auto-saves durable
        facts (name, preferences, projects, deadlines) WITHOUT the user saying
        'remember that', matching the GUI/headless behavior the CLI was missing.
        Fire-and-forget (zero added latency), guarded against overlapping runs,
        silent on failure. The extractor itself filters jokes/quotes + dedupes."""
        if getattr(self, "_extracting", False):
            return
        self._extracting = True
        try:
            from hearth import memory_extract as _mx
            import openai as _oai
            _sync = _oai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            _llm = _mx.make_openai_llm_call(_sync, self.current_model, max_tokens=600)
            _msgs = list(self.messages)
            # Save SILENTLY — this runs fire-and-forget, so any print lands at a
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
        with JARVIS_NO_ONBOARDING=1 or by hitting Ctrl-C."""
        if os.environ.get("JARVIS_NO_ONBOARDING") in ("1", "true", "yes"):
            return
        if not self._is_first_run():
            return

        print()
        print(f"{C_BRAND}─ first-run setup ─{C_RESET}  "
              f"{C_DIM}~60 seconds. Helps me be useful to you specifically.{C_RESET}")
        print(f"{C_DIM}  press Enter to skip a question; Ctrl-C any time to skip the rest{C_RESET}")
        print()

        # WHERE to keep files — ask BEFORE anything writes to disk, so a user
        # whose C: drive is full (or who just wants it elsewhere) can put the
        # workspace on another drive. Skip if already chosen (pointer exists).
        try:
            from hearth.tools import _WORKSPACE_POINTER, set_workspace_location, WORKSPACE as _CUR_WS
            if not os.path.isfile(_WORKSPACE_POINTER) and not os.environ.get("JARVIS_WORKSPACE"):
                loc = input(
                    f"  {C_TOOL}Where should I keep my files — memory, documents, models?{C_RESET}\n"
                    f"  {C_DIM}Enter for default ({_CUR_WS}), or a path on another drive "
                    f"like D:\\Hearth{C_RESET}\n  > "
                ).strip()
                if loc and os.path.abspath(os.path.expanduser(loc)) != os.path.abspath(_CUR_WS):
                    new = set_workspace_location(loc)
                    print(f"\n  {C_OK}Workspace set to {new}.{C_RESET}")
                    print(f"  {C_DIM}Close and reopen Hearth once to use it — then we'll "
                          f"finish setup there. (Move that folder anytime; just update "
                          f"this location to match.){C_RESET}\n")
                    raise SystemExit(0)
        except SystemExit:
            raise
        except (KeyboardInterrupt, EOFError):
            print()
        except Exception:
            pass

        # Learn the machine FIRST — hardware, installed models, drive map — so the
        # model walks in with real context (and never wastes 2 minutes disk-scanning
        # for things it could have just known). Same call backs the /learn command.
        print(f"  {C_DIM}getting to know your machine (hardware, models, drives)...{C_RESET}")
        try:
            from hearth.environment import learn_environment
            print(f"  {C_OK}{learn_environment(endpoint=LOCAL_API_BASE)}{C_RESET}")
        except Exception as e:
            print(f"  {C_DIM}(machine scan skipped: {e}){C_RESET}")
        print()

        def ask(prompt: str) -> str:
            return input(f"  {C_TOOL}{prompt}{C_RESET}\n  > ").strip()

        # Brain choice — parity with the GUI's local-vs-cloud onboarding step.
        # Reuses the /brain command so key storage + endpoint switch + context
        # re-detect all happen through one code path. Default stays local.
        try:
            cur = "cloud" if not _is_local_endpoint(LOCAL_API_BASE) else "local"
            print(f"  {C_TOOL}Which brain should I run on?{C_RESET}")
            print(f"  {C_DIM}    Enter = local ({self.current_model} via {LOCAL_API_BASE}, "
                  f"currently {cur}). Or pick a cloud model.{C_RESET}")
            brain = ask("Enter for local, or: grok / gemini / openai / openrouter").lower()
            if brain in ("grok", "gemini", "openai", "openrouter"):
                key = ask(f"Paste your {brain} API key (saved locally, asked once)")
                if key:
                    await self.handle_command(f"/brain {brain} {key}")
                else:
                    print(f"  {C_DIM}no key — staying local. Switch anytime with /brain {brain} <key>.{C_RESET}")
            print()
        except (KeyboardInterrupt, EOFError):
            print()

        answers: Dict[str, str] = {}
        try:
            answers["name"] = ask("What should I call you?")
            answers["agent_name"] = ask(
                "And what would you like to call ME? (Enter to stay JARVIS)"
            )
            answers["role"] = ask(
                "Briefly, what do you do? (e.g. 'student', 'web dev', 'streamer', 'designer')"
            )
            print()
            print(f"  {C_TOOL}How blunt should I be?{C_RESET}")
            print(f"  {C_DIM}    1 = polite & formal, 3 = friendly default, "
                  f"5 = brutally honest no filler{C_RESET}")
            answers["tone"] = ask("Pick 1–5 or describe in your own words")
            answers["avoid"] = ask(
                "Topics or language I should avoid? (or press Enter for none)"
            )
            answers["browser"] = ask(
                "Preferred browser for opening links? "
                "(chrome / brave / firefox / Enter for system default)"
            )
            if answers["browser"]:
                answers["profile"] = ask(
                    f"Which {answers['browser']} profile? "
                    f"(the display name in the profile picker, e.g. 'personal')"
                )
            else:
                answers["profile"] = ""
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C_DIM}  ok, skipping rest. "
                  f"Edit ~/Jarvis/rules.md or use memory_save anytime.{C_RESET}\n")
            self._persist_onboarding(answers)
            return

        self._persist_onboarding(answers)
        print()
        print(f"  {C_OK}saved.{C_RESET}  "
              f"{C_DIM}/rules to view; memory_save to add more; "
              f"~/Jarvis/rules.md to edit by hand.{C_RESET}")
        print()
        # Offer importing memory from another AI — most people already have
        # years of context in ChatGPT/Claude. /import-memory walks them through it.
        print(f"  {C_TOOL}Already use ChatGPT or Claude?{C_RESET}  "
              f"{C_DIM}Run {C_RESET}{C_TOOL}/import-memory{C_RESET}{C_DIM} anytime — it gives you a "
              f"prompt to\n  paste into that AI, then pulls your facts in here.{C_RESET}")
        print()

        # Migrate prompt — only shown when a prior agent install is detected.
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
                print(f"  {C_DIM}Import its memory into Hearth? "
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
        # Substring match — "y" picks "Yes", "first" picks "First option", etc.
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
        # Register the CLI as the interactive surface for ask_user — the tool
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
                print(f"{C_DIM}● MCP: spawning {r['servers']} bridge(s){C_RESET}")
        except Exception as e:
            print(f"{C_DIM}● MCP client bootstrap skipped: {e}{C_RESET}")
        # Sync subagents inherit the CLI's cancel signal so a Ctrl-C
        # during a respond() stream also aborts any active child loop.
        try:
            from hearth import subagents as _sa
            _sa.set_parent_cancel_check(self._respond_cancel.is_set)
        except Exception:
            pass
        # Reminder watcher — fire desktop notifications for due reminders,
        # including catch-up for any that came due while Hearth was closed.
        # The GUI starts this; the CLI never did, so reminders set in the CLI
        # (e.g. study reminders) were saved but NOTHING ever fired them. This is
        # why notifications never appeared in CLI-only use.
        try:
            from hearth import reminders as _rem
            _rem.start_watcher(_rem.desktop_notify)
        except Exception:
            pass
        await self._maybe_run_onboarding()

        last_interrupt = 0.0
        while True:
            if self._exit_requested:
                self.save_history()
                print(f"{C_DIM}● session ended.{C_RESET}")
                sys.exit(0)
            try:
                if self.listen_continuous:
                    user_input = (await self._read_input_or_listen()).strip()
                else:
                    user_input = (await self._read_input()).strip()
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
            if not user_input:
                continue
            # Strip lone surrogate chars before any string ops — Windows
            # terminals occasionally emit them for emojis and they crash
            # the UTF-8 encoder downstream.
            user_input = _sanitize(user_input)
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
            # Keeps Jarvis silent at the desk until you actually call him.
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
                        print(f"{C_DIM}(awake — what's up?){C_RESET}")
                        continue
                else:
                    print(f"{C_DIM}(sleeping — say '{w}' or /wake){C_RESET}")
                    continue
            # Drain background subagent completion notifications BEFORE
            # the user's new prompt so the model sees them in arrival
            # order. Each notification is a synthetic user-role message
            # with a <task-notification> block.
            try:
                from hearth import subagents as _sa
                for notif in _sa.drain_pending_notifications():
                    xml = _sa.format_notification_as_user_message(notif)
                    # role=system, NOT user — a background subagent reporting
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
            r = sync_client.chat.completions.create(
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
            print(f"{C_DIM}(no memory dir yet — first save creates it){C_RESET}")
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
            print(f"{C_DIM}(no memories yet — type something casual and JARVIS will start saving facts){C_RESET}")
            return
        # Render in canonical order — same as the GUI
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
        # Probe the standard GUI port first — if it answers, just open it.
        for port in (8765, 8766):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1):
                    url = f"http://127.0.0.1:{port}/?view=memory"
                    print(f"{C_OK}↳ opening memory map at {url}{C_RESET}")
                    webbrowser.open(url)
                    return
            except Exception:
                continue
        # Nothing running — spawn the web backend in a background thread
        print(f"{C_DIM}(no GUI server running — booting one on :8765 for you){C_RESET}")
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
            r = sync_client.chat.completions.create(
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
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = fresh_system_message(think_on=self.think_on, voice_on=self.voice_on)
        else:
            self.messages.insert(0, fresh_system_message(think_on=self.think_on, voice_on=self.voice_on))

        # The tool schemas (~6K tok for 46 tools) ride in EVERY prompt but are
        # NOT in self.messages — so all budgeting must reserve room for them.
        # Without this, a restored/long history "fits" the message budget while
        # the REAL prompt (messages + tool schemas + output) overflows the
        # model's context; LM Studio then drops the user turn and crashes with
        # "No user query found in messages." (the mid-session model-switch bug).
        try:
            tool_tokens = len(json.dumps(self.openai_tools)) // CHARS_PER_TOKEN
        except Exception:
            tool_tokens = 0
        effective_ctx = max(2048, self.context_tokens - tool_tokens)

        # 2) Auto-compact at any SAFE boundary — i.e. not in the middle of an
        # in-flight tool chain. Safe = last message is a user turn, OR an
        # assistant text reply with no tool_calls (chain finished). Unsafe = the
        # last message is a `tool` reply or an assistant message with pending
        # tool_calls — compacting then can break the tool_call_id pairing or
        # strip the user query, crashing LM Studio's Jinja template with
        # "No user query found in messages.".
        # Older logic only allowed `last_role == 'user'`, which meant a long
        # tool chain that *finished* with an assistant answer never got
        # compacted until the next user turn — and on the way there
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
            # tail still held 4 huge browse results — server wedged.
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
        # turn — fenced + authoritative — appended to the system message so
        # the model actually uses what it knows instead of ignoring the
        # passive index or re-asking/disk-scanning. Adds zero tokens when
        # nothing matches; bounded otherwise. See memory.recall_for_prompt.
        from hearth import memory as _mem
        last_user_text = next((m.get("content", "") for m in reversed(sent)
                               if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        if sent and sent[0].get("role") == "system":
            # Inject current local time so the model gets it for free —
            # "what should I do?" / "good morning" / "remind me tomorrow"
            # all work without a get_time call. Mirrors headless.py for
            # CLI/GUI parity. ~50 chars cost per turn.
            import datetime as _dt
            _now = _dt.datetime.now().astimezone()
            time_line = (f"\n\nCurrent local time: {_now.strftime('%Y-%m-%d %H:%M')} "
                         f"({_now.strftime('%A')}, tz {_now.tzname() or _now.strftime('%z')}).")
            sent[0] = {**sent[0], "content": sent[0]["content"] + time_line}
            if last_user_text:
                block = _mem.recall_for_prompt(last_user_text)
                if block:
                    sent[0] = {**sent[0], "content": sent[0]["content"] + "\n\n" + block}
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
        if self.think_on:
            return None
        base = (LOCAL_API_BASE or "").lower()
        if "openai.com" in base:
            return "minimal"
        return "none"

    async def _open_stream(self, create_kwargs: Dict):
        """Open the streaming completion. If a cloud model rejects
        `reasoning_effort` with a 400 (plain grok-4, grok-3, etc. don't expose
        it), drop the param, remember the model, and retry once so the turn
        still goes through instead of dying on an unsupported-field error."""
        try:
            return await self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            if "reasoning_effort" in create_kwargs and _is_reasoning_param_error(e):
                self._no_reasoning_effort.add((self.current_model or "").lower())
                create_kwargs.pop("reasoning_effort", None)
                return await self.client.chat.completions.create(**create_kwargs)
            raise

    async def respond(self, depth: int = 0):  # noqa: C901  # see _looks_like_yield helper at module level
        if depth > MAX_TURNS:
            # Generous safety ceiling (the loop guard handles real spirals long
            # before this); reaching it means something genuinely runaway.
            print(f"{C_DIM}● reached the turn limit — wrapping up{C_RESET}")
            return

        # Reset the anti-yield flag at the start of each new user turn so we
        # can nudge at most ONCE per user message. (Not at every recursive
        # tool-chain depth — that would loop.)
        if depth == 0:
            self._yielded_this_turn = False
            # Tool-loop guard: outcome-hash based, tiered (skip dup / warn /
            # stop). Fresh per user turn. See hearth/loop_guard.py for the full
            # rationale.
            self._loop_guard = ToolLoopGuard()
            self._force_answer = False
            # Cloud vision: image to attach as a user turn after this round of
            # tool calls (set by the view_image handler on cloud endpoints).
            self._pending_image_block = None
            # Fresh cancel signal — clear any stale state from the prior turn.
            self._respond_cancel.clear()

        send_messages = await asyncio.to_thread(self._prepare_context)

        # Qwen3 `/no_think` injection — `chat_template_kwargs.enable_thinking=False`
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
        # is actually allowed to reason; otherwise "Working" — because the
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
            }
            if getattr(self, "_force_answer", False):
                # Spiral guard tripped last turn: withhold tools so the model
                # must produce text. One-shot — clear so tools return next turn.
                self._force_answer = False
            else:
                # Rebuilt each turn so tools unlocked via `load_tools`
                # (tool-diet) show up on the next request.
                self.openai_tools = to_openai_tools()
                create_kwargs["tools"] = self.openai_tools
                create_kwargs["tool_choice"] = "auto"
            # Thinking gate:
            #   - chat_template_kwargs.enable_thinking is the PROPER way for
            #     Qwen3+ and other reasoning-aware models — flips the Jinja
            #     branch in the chat template so the model never enters the
            #     thinking phase at all.
            #   - stop=["<think>"] is a safety net for models that ignore
            #     the chat-template kwarg (older / non-reasoning models that
            #     happen to emit <think> tags via system prompt).
            # `chat_template_kwargs` is LM-Studio/llama.cpp-specific. Cloud
            # OpenAI-compat endpoints (Gemini, OpenAI, OpenRouter) reject
            # unknown fields with a 400 — only send it to local servers.
            _base = (LOCAL_API_BASE or "").lower()
            _is_local = any(h in _base for h in ("localhost", "127.0.0.1", "0.0.0.0",
                                                 "::1", "192.168.", "10.", "host.docker.internal"))
            if _is_local:
                create_kwargs["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": self.think_on}
                }
                # `stop` is local-only too — Grok rejects it outright, and
                # cloud models stream reasoning via reasoning_content anyway.
                if not self.think_on:
                    create_kwargs["stop"] = ["<think>", "<thinking>"]
            else:
                # Cloud: actually disable reasoning at the API when /think is
                # off — not just hide it. Without this the model (e.g. Grok)
                # still runs a full reasoning pass server-side, so you pay the
                # latency with zero visibility. `reasoning_effort` is the lever
                # that reasoning-capable cloud models expose.
                eff = self._cloud_reasoning_effort()
                if eff is not None:
                    create_kwargs["reasoning_effort"] = eff
            stream = await self._open_stream(create_kwargs)
        except Exception as e:
            spinner_task.cancel()
            info = classify_api_error(e, _is_local)
            # One clean line — not the raw HTML/stack. (run.txt showed a raw 500
            # HTML wall cascading; this turns it into "the model server errored
            # — try /compact or reload the model".)
            print(f"\n{C_ERR}● {info.hint}{C_RESET}")
            print(f"{C_DIM}({info.category}{' · retryable' if info.retryable else ''}) "
                  f"Your message is preserved — resend or type to retry.{C_RESET}")
            # Keep the last user message in self.messages so a follow-up
            # turn naturally retries the same context, instead of silently
            # losing what they typed.
            return

        tool_calls_dict: Dict[int, Dict] = {}
        content_captured = ""
        first = True
        in_think = False
        # Streaming voice state — we flush sentences to TTS as they
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
            force_tail at end-of-turn — felt like a non-streaming voice."""
            nonlocal speak_cursor
            if not (self.voice_on and voice.is_available()):
                return
            tail = speak_buffer[speak_cursor:]
            if not tail.strip():
                return
            # Find the FIRST sentence boundary in the tail. Earlier code
            # looked at the LAST boundary, which batched multi-sentence
            # chunks together — defeated streaming. Now: flush each
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
                return  # no boundary yet — wait for more chunks
            chunk = tail[:cut].strip()
            if not chunk:
                return
            # Strip markdown noise that sounds awful as audio
            chunk = re.sub(r"`{1,3}[^`]*`{1,3}", "", chunk)
            chunk = re.sub(r"[#*_`]+", "", chunk).strip()
            # Strip Windows file paths — Kokoro reads "C:\Users\you\file"
            # as "see colon users... ". Keep just the filename.
            chunk = re.sub(r"[A-Za-z]:[\\/](?:[^\s\\/]+[\\/])*([^\s\\/]+)", r"\1", chunk)
            # Long digit runs (timestamps like 20260521_233527) get pronounced
            # as "twenty trillion two hundred sixty billion..." — gibberish.
            chunk = re.sub(r"\b\d{8,}\b", "the file", chunk)
            # Collapse runs of newlines — `\n\n` in source text otherwise
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
        # Auto-collapse by default — show "▶ thought for X.Xs" instead of
        # the body. /think toggles inline display.
        reasoning_open = False
        reasoning_chars = 0
        reasoning_t0 = 0.0

        def _open_reasoning():
            nonlocal reasoning_open, reasoning_chars, reasoning_t0
            if not reasoning_open:
                reasoning_open = True
                reasoning_chars = 0
                reasoning_t0 = time.time()
                if self.think_on:
                    sys.stdout.write(
                        f"\n{C_FRAME}┌─ {C_WARN}thinking{C_FRAME} ─────{C_RESET}\n{C_DIM}"
                    )
                else:
                    # Spinner-style placeholder; replaced when reasoning ends
                    sys.stdout.write(f"\n{C_DIM}▶ thinking…{C_RESET}")
                sys.stdout.flush()

        def _close_reasoning():
            nonlocal reasoning_open, reasoning_chars, reasoning_t0
            if reasoning_open:
                dt = time.time() - reasoning_t0
                if self.think_on:
                    sys.stdout.write(
                        f"{C_RESET}\n{C_FRAME}└─ {dt:.1f}s, {reasoning_chars}c{C_RESET}\n{C_BOT}"
                    )
                else:
                    # Erase the "▶ thinking…" line, replace with summary
                    sys.stdout.write(
                        f"\r\033[K{C_DIM}▶ thought for {dt:.1f}s ({reasoning_chars}c) — /think to expand{C_RESET}\n{C_BOT}"
                    )
                sys.stdout.flush()
                reasoning_open = False

        def _stream_reasoning(text: str):
            nonlocal reasoning_chars
            reasoning_chars += len(text)
            if self.think_on:
                sys.stdout.write(C_DIM + text)
                sys.stdout.flush()
            # else: silently absorb — only the summary line shows

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
            if not chunk.choices:
                continue
            if first:
                # BUG-FIX: We must AWAIT the spinner task after cancelling
                # so its CancelledError handler (which writes "\r\033[K")
                # runs BEFORE we write our own content. Without this, the
                # spinner's cleanup runs on the next event-loop tick and
                # erases the first few characters of our streamed reply —
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

            # OpenAI-extension: dedicated reasoning channel. Some servers
            # (LM Studio with reasoning models) emit thinking here even when
            # we asked them not to via stop tokens or chat_template_kwargs.
            # Gate ENTIRELY on self.think_on so /think off truly hides it.
            reasoning = None
            if self.think_on:
                reasoning = getattr(delta, "reasoning_content", None) \
                    or getattr(delta, "reasoning", None)
            if reasoning:
                _open_reasoning()
                _stream_reasoning(reasoning)
                # don't add to content_captured — reasoning isn't part of
                # the assistant message we send back next turn

            if delta.content:
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
                        content_captured += pre
                        sys.stdout.write(C_BOT + pre)
                    if self.think_on:
                        _open_reasoning()
                    # Either way (display on/off), we're in a thinking block
                    text = after
                    in_think = True
                if "</think>" in text and in_think:
                    body, _, post = text.partition("</think>")
                    if body and self.think_on:
                        _stream_reasoning(body)
                    if self.think_on:
                        _close_reasoning()
                    in_think = False
                    text = post

                # Reasoning channel (no <think> tags) ends when content arrives
                if reasoning_open and not in_think:
                    _close_reasoning()

                if in_think:
                    # When display is on, stream into the thinking UI.
                    # When display is off, silently drop these bytes.
                    if text and self.think_on:
                        _stream_reasoning(text)
                else:
                    if text:
                        content_captured += text
                        sys.stdout.write(C_BOT + text)
                        sys.stdout.flush()
                        # Track code-block state so we don't speak code
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

        sys.stdout.write(C_RESET + "\n")

        if first:
            spinner_task.cancel()

        # Strip malformed tool-call markup that small models emit when confused
        # about the OpenAI tool format (or when we withhold tools and Hermes
        # still "wants" to call something). Three known shapes:
        #   - Gemma:  <|channel>call:NAME{...}<tool_call|>
        #   - Hermes/Llama XML:  <tool_call><function=NAME>...<parameter=..>...
        # The new hearth.tool_call_parser (used by run_once / headless) RECOVERS
        # the call into proper OpenAI tool_calls, so we no longer need to scrub
        # here. Leaving the old strip_tool_markup() + visible "(stripped
        # malformed tool-call markup)" line was just confusing residue.
        # If a pattern slips through the parser we'll see it as text and can
        # add a new family to tool_call_parser._PATTERNS rather than blindly
        # nuking it from history.

        if content_captured:
            entry: Dict = {"role": "assistant", "content": content_captured}
        else:
            # Empty string, NOT None: Gemini's OpenAI-compat layer rejects a
            # null assistant content with tool_calls (400 INVALID_ARGUMENT).
            # OpenAI/LM Studio accept "" just as happily.
            entry = {"role": "assistant", "content": ""}

        if tool_calls_dict:
            entry["tool_calls"] = list(tool_calls_dict.values())

        self.messages.append(entry)

        # Voice already streamed sentence-by-sentence during the delta
        # loop above — no end-of-turn speak needed.

        if not tool_calls_dict:
            # Anti-yield wrapper: if the model emitted an "I'll do X" style
            # announcement WITHOUT actually calling the tool, give it one
            # nudge to follow through. Limited to once per user turn.
            # Catches the most common Qwen/Gemma drift: "I'll search for
            # that..." with no search.
            if (
                not self._yielded_this_turn
                and content_captured
                and len(content_captured) < 500
                and depth < 6
                and _looks_like_yield(content_captured)
            ):
                self._yielded_this_turn = True
                print(f"{C_DIM}(model announced an action without taking it — nudging){C_RESET}")
                self.messages.append({
                    "role": "user",
                    "content": (
                        "You said you'd do that but you didn't actually call the tool. "
                        "Don't narrate the intent — just call the tool. Right now, this turn."
                    ),
                })
                await self.respond(depth + 1)
                return
            self.save_history()
            return

        for tc in entry["tool_calls"]:
            name = tc["function"]["name"]
            args_raw = tc["function"]["arguments"] or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}
            # Args preview. We dump the FULL argument set only when we're
            # about to ASK you to approve — that's when a hidden ">D:\evil.bat"
            # tail matters and you need to read it. When the call runs
            # unprompted (auto-approve, an [a]lways grant, or a write inside
            # the workspace), we show a tidy one-line preview cut with "…" —
            # no 600-char code blob (e.g. create_plugin) dumped to the screen.
            preview_full = json.dumps(args, ensure_ascii=False)
            is_risky = name in RISKY_TOOLS

            # Path-scoped auto-approval: for file-touching tools, a path arg
            # that resolves inside the workspace sandbox runs without a prompt
            # (prompting on every workspace edit is permission fatigue).
            _path_safe = False
            try:
                _path_arg = args.get("path") if isinstance(args, dict) else None
                if _path_arg and isinstance(_path_arg, str) and name in (
                    "write_file", "edit_file", "create_directory", "delete_path", "move_path",
                ):
                    from hearth.tools import WORKSPACE as _WS
                    try:
                        _abs = os.path.abspath(os.path.expanduser(_path_arg))
                        _path_safe = os.path.normpath(_abs).lower().startswith(
                            os.path.normpath(_WS).lower())
                    except Exception:
                        _path_safe = False
            except Exception:
                _path_safe = False

            # Will a [y/n/a/N] prompt actually fire? Only then do we need the
            # full args on screen.
            _will_prompt = (is_risky
                            and not self.auto_approve
                            and not _path_safe
                            and self.tool_perms.get(name) not in ("always", "never"))

            print(f"{C_FRAME}╭─ {C_TOOL}⚡ {name}{C_FRAME} ─{C_RESET}")
            if _will_prompt and len(preview_full) > 100:
                # Pretty-print + word-wrap so the full args read cleanly on
                # multiple lines before the [y/n/a/N] prompt.
                try:
                    pretty = json.dumps(args, ensure_ascii=False, indent=2)
                except Exception:
                    pretty = preview_full
                for ln in pretty.split("\n"):
                    print(f"{C_FRAME}│ {C_DIM}{ln}{C_RESET}")
            else:
                preview = preview_full
                if len(preview) > 100:
                    preview = preview[:100] + "…"
                print(f"{C_FRAME}│ {C_DIM}{preview}{C_RESET}")

            # Permission gate for risky tools — uses prompt_async if pt is
            # active so we don't deadlock with prompt_toolkit's stdin grab.
            denied = False
            decline_reason = ""

            if (name in RISKY_TOOLS
                    and not self.auto_approve
                    and not _path_safe
                    and self.tool_perms.get(name) != "always"):
                if self.tool_perms.get(name) == "never":
                    denied = True
                else:
                    print(f"{C_FRAME}│ {C_WARN}? allow this call?{C_RESET} "
                          f"{C_DIM}[y]es / [n]o / [a]lways / [N]ever / or type what to do instead{C_RESET}")
                    raw_choice = await self._read_choice(f"{C_FRAME}│ > {C_RESET}")
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
            # clean one-liner shown on screen — internal control directives must
            # NEVER leak to the user as raw text.
            display = None
            skipped = False
            if denied:
                if decline_reason:
                    result = (f"The user declined '{name}' and asked for this instead: "
                              f"\"{decline_reason}\". Follow that; don't retry the original call.")
                    display = f"declined -> {decline_reason[:60]}"
                else:
                    result = (f"The user declined '{name}'. Don't retry the same call — pick a "
                              f"different approach, ask what they'd prefer, or answer without it.")
                    display = "declined"
                dt = 0.0
            elif (skip := self._loop_guard.before(name, args)) is not None:
                # Identical MUTATING repeat — skip execution (no dup side effect).
                result = skip.note
                display = "skipped (duplicate call)"
                dt = 0.0
                skipped = True
            else:
                t0 = time.time()
                # The user has already approved this specific call via the
                # [y]es/[a]lways prompt above (or it's an always-allowed
                # tool). Mark _approved so the inner destructive-pattern
                # guard doesn't refuse it a SECOND time — the redundant
                # gate would make echo>file fail after user already said y.
                _approved_args = dict(args, _approved=True) if isinstance(args, dict) else args
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

            # Screen preview: clean dim label for synthetic results, else the
            # real result head (green ok / orange error).
            if display is not None:
                print(f"{C_FRAME}│ {C_DIM}↳ {display}{C_RESET}")
                print(f"{C_FRAME}╰─ {C_DIM}{dt:.0f}ms{C_RESET}")
            else:
                head = result.split("\n", 1)[0][:140]
                is_err = head.startswith("Error") or "could not" in head.lower()
                head_color = C_WARN if is_err else C_OK
                extra_lines = result.count("\n")
                size_label = f"{len(result)}c" + (f", +{extra_lines}L" if extra_lines else "")
                print(f"{C_FRAME}│ {head_color}↳ {head}{C_RESET}")
                print(f"{C_FRAME}╰─ {C_DIM}{size_label} · {dt:.0f}ms{C_RESET}")

            tool_msg: Dict = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": result,
            }

            # If view_image returned the __JARVIS_IMAGE__ marker, decide based
            # on whether the loaded model is vision-capable:
            #   - Vision model (id contains vl/vision/gemma-3/llava/moondream)
            #     → rewrite tool content as a multimodal block so the model
            #       actually sees the image
            #   - Text-only model → keep as text, but make the message tell
            #     the model EXPLICITLY that it can't describe images so it
            #     doesn't try (which used to stall for 80+ seconds or
            #     hallucinate generic "VS Code on left, terminals in middle")
            if (
                name == "view_image" and not denied
                and isinstance(result, str)
                and result.startswith("__JARVIS_IMAGE__")
            ):
                m = re.match(r"__JARVIS_IMAGE__\s+(.+?)\s+\(\d+\s+bytes", result)
                if m:
                    img_path = m.group(1).strip()
                    if os.path.isfile(img_path):
                        is_vision = self._is_vision_capable()
                        if is_vision:
                            try:
                                import base64, mimetypes
                                with open(img_path, "rb") as f:
                                    b64 = base64.b64encode(f.read()).decode("ascii")
                                mime = mimetypes.guess_type(img_path)[0] or "image/png"
                                image_block = {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                                }
                                if _is_local_endpoint(LOCAL_API_BASE):
                                    # LM Studio VLMs accept the image inline in
                                    # the tool result (tested path).
                                    tool_msg["content"] = [
                                        {"type": "text", "text": f"image loaded: {os.path.basename(img_path)} — look at it before describing"},
                                        image_block,
                                    ]
                                else:
                                    # Cloud OpenAI-compat (Gemini/OpenAI) reject
                                    # multimodal content in a TOOL message — it
                                    # has to ride a user turn. Ack here, attach
                                    # the image as a user message after the loop.
                                    tool_msg["content"] = (
                                        f"Screenshot captured ({os.path.basename(img_path)}). "
                                        f"The image is attached in the next message — "
                                        f"look at it and answer."
                                    )
                                    self._pending_image_block = image_block
                                self.last_image_path = img_path
                            except OSError:
                                pass
                        else:
                            # Text-only model — replace marker with a clear
                            # admission so model doesn't hallucinate or stall
                            tool_msg["content"] = (
                                f"Image saved at {img_path}. "
                                f"The currently-loaded model ({self.current_model}) "
                                f"is NOT vision-capable — you cannot actually see this image. "
                                f"Do NOT describe what's in it (any description would be "
                                f"made-up). Either: tell the user to switch to a vision "
                                f"model (Gemma 3 vision, Qwen-VL, Llava, MiniCPM-V) and "
                                f"re-ask, OR open the image for them with "
                                f"open_app('{img_path}') so they can view it themselves."
                            )
                            self.last_image_path = img_path

            self.messages.append(tool_msg)

            # generate_image / generate_video / check_video_task → pop the
            # file open in the OS default viewer + show a clickable file:// path
            # so the CLI user has the same "look, here's the image" moment
            # the GUI gets via inline render. Markers are __JARVIS_IMAGE__
            # <path> and __JARVIS_VIDEO__ <path>.
            if (
                name in ("generate_image", "generate_video", "check_video_task")
                and not denied
                and isinstance(result, str)
            ):
                _media_re = re.compile(r"__JARVIS_(?:IMAGE|VIDEO)__\s+([^\n(]+?)(?:\s+\(|\s*$)", re.MULTILINE)
                for _m in _media_re.finditer(result):
                    _media_path = _m.group(1).strip().rstrip(")").rstrip()
                    if _media_path and os.path.isfile(_media_path):
                        print(f"{C_FRAME}│ {C_OK}↳ saved:{C_RESET} {_media_path}")
                        # Open in the OS default viewer (image viewer / mp4 player).
                        # Non-blocking — we don't want to lock up the CLI.
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
        # pairing intact — we must NOT inject this mid-loop).
        if getattr(self, "_pending_image_block", None) is not None:
            self.messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here is the screenshot you just captured — look at it and answer my question."},
                    self._pending_image_block,
                ],
            })
            self._pending_image_block = None

        # Spiral guard tripped: append a hard "answer now" directive AFTER all
        # tool results (valid ordering). The next respond() call withholds
        # tools (see create_kwargs gate), forcing a plain-text answer.
        if getattr(self, "_force_answer", False):
            print(f"{C_DIM}● wrapping up — using what we have{C_RESET}")
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
        await self.respond(depth + 1)


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

    app = JarvisCLI()
    app.animate_intro()
    # Suppress the httpx/anyio cleanup spam that happens when the asyncio
    # loop closes before background openai clients finish their TLS
    # teardown. The spam looks like 30+ "Event loop is closed" tracebacks
    # at the bottom of every session. It's purely cosmetic — the loop is
    # closing because we asked it to — so hide that one ExceptionGroup
    # without hiding real errors.
    import logging as _logging
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)
    # The asyncio-logger silence above covers the loop's own exception handler,
    # but a background subagent runs in a thread via asyncio.run(); when its
    # httpx/openai client is garbage-collected AFTER that loop closes, the
    # transport's __del__ raises "Event loop is closed" — which Python reports
    # through sys.unraisablehook, NOT the asyncio logger. That's the ugly
    # traceback users saw every time a subagent finished. Swallow just that one.
    _orig_unraisable = sys.unraisablehook
    def _quiet_unraisable(args):  # noqa: ANN001
        exc = getattr(args, "exc_value", None)
        if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
            return
        _orig_unraisable(args)
    sys.unraisablehook = _quiet_unraisable
    # Top-level exit handling: Ctrl-C anywhere (even mid-tool-call) should
    # surface as a clean exit, not the full asyncio traceback the user was
    # seeing when run_command got stuck on a daemon batch file.
    try:
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
