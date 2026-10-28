"""How a turn looks in the terminal.

The engine (headless.run_once) emits what happens; this draws it. Finished
lines go to the scrollback. What is still coming in (the line being written,
the working line) lives just above the input, which stays at the bottom so
typing during a reply steers it.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional

ESC = "\x1b["
RESET = ESC + "0m"
DIM = ESC + "2m"
ITAL = ESC + "3m"
BOLD = ESC + "1m"
ACCENT = ESC + "38;5;214m"
OK = ESC + "38;5;114m"
ERR = ESC + "38;5;203m"
WARN = ESC + "38;5;179m"
CODE = ESC + "38;5;151m"
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def plain(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s or "")


# ---------------------------------------------------------------- words
_VERB = {
    "run_command": ("Ran", "command"), "start_job": ("Started", "command"),
    "read_file": ("Read", "path"), "write_file": ("Wrote", "path"), "edit_file": ("Edited", "path"),
    "list_dir": ("Listed", "path"), "delete_file": ("Deleted", "path"), "move_file": ("Moved", "src"),
    "find_files": ("Searched for", "pattern"), "grep": ("Searched for", "pattern"),
    "web_search": ("Searched the web for", "query"), "fetch_url": ("Opened", "url"), "browse": ("Opened", "url"),
    "web_fetch": ("Read", "url"), "open_url": ("Opened", "url"), "search_chats": ("Searched past chats for", "query"),
    "read_chat": ("Read an earlier chat", ""), "load_model": ("Loaded", "model"), "list_models": ("Listed the models", ""),
    "memory_save": ("Remembered", "name"), "memory_recall": ("Looked up", "query"), "memory_forget": ("Forgot", "name"),
    "set_reminder": ("Set a reminder:", "what"), "open_app": ("Opened", "name"), "screenshot": ("Took a screenshot", ""),
    "view_image": ("Looked at", "path"), "spawn_subagent": ("Started a helper:", "persona"),
    "generate_image": ("Made an image:", "prompt"), "disk_usage": ("Measured", "path"),
}
_ARG_KEYS = ("command", "path", "query", "url", "name", "pattern", "prompt", "what", "text", "app")


def tool_words(name: str, args: Optional[Dict[str, Any]]) -> str:
    """A tool call said plainly: "Ran echo hi", "Read notes.txt"."""
    args = args if isinstance(args, dict) else {}
    verb, key = _VERB.get(name, (name.replace("_", " ").capitalize(), ""))
    val = args.get(key) if key else None
    if val is None and name not in _VERB:
        val = next((args[k] for k in _ARG_KEYS if args.get(k)), None)
    val = " ".join(str(val).split()) if val else ""
    if key == "path" and val:
        val = val.replace(os.path.expanduser("~"), "~")
    if key == "url" and val:
        val = re.sub(r"^https?://(www\.)?", "", val).rstrip("/")
    return f"{verb} {val[:90]}".strip() if val else verb


# ---------------------------------------------------------------- markdown
def md_line(line: str, in_code: bool) -> str:
    """One finished line of a reply, lightly formatted."""
    if in_code:
        return "  " + CODE + line + RESET
    m = re.match(r"^(#{1,6})\s+(.*)$", line)
    if m:
        return BOLD + m.group(2) + RESET
    m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
    if m:
        line = m.group(1) + "• " + m.group(2)
    line = re.sub(r"\*\*(.+?)\*\*", BOLD + r"\1" + RESET, line)
    line = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", ITAL + r"\1" + RESET, line)
    line = re.sub(r"`([^`]+)`", CODE + r"\1" + RESET, line)
    return line


class Turn:
    """One turn on screen. `emit` is handed to the engine; `live()` is what
    sits above the input while the turn runs."""

    def __init__(self, out=print, show_thinking: bool = False, model: str = ""):
        self.out = out
        self.show_thinking = show_thinking
        self.model = model
        self.t0 = time.time()
        self.text: List[str] = []           # the whole reply, for history
        self.partial = ""                   # reply line still being written
        self.in_code = False
        self.thinking = ""                  # this turn's reasoning, for /thinking
        self.think_partial = ""
        self.think_t0: Optional[float] = None
        self.think_s = 0.0
        self.status = "Working"
        self.running = True
        self.stats: Dict[str, Any] = {}
        self.usage: Dict[str, Any] = {}
        self.context: Dict[str, Any] = {}       # the engine's last context count
        self.pending_tools: Dict[str, str] = {}
        self._said_hint = False
        self.ended = False             # the model said goodbye (end_session)
        self.table: List[str] = []     # table lines of the reply, drawn when it ends

    # ---- what sits above the input
    def live(self) -> str:
        if not self.running:
            return ""
        width = max(20, shutil.get_terminal_size().columns - 2)
        lines = []
        if self.partial:
            lines.append(md_line(self.partial, self.in_code)[: width * 3])
        dt = time.time() - self.t0
        spin = ACCENT + SPIN[int(dt * 10) % len(SPIN)] + RESET
        if self.think_t0 is not None:
            what = f"Thinking {time.time() - self.think_t0:.0f}s"
            if self.show_thinking and self.think_partial:
                lines.append(DIM + ITAL + self.think_partial[-width:] + RESET)
        else:
            what = f"{self.status} {dt:.0f}s"
        lines.append(f"{spin} {DIM}{what} · esc to stop{RESET}")
        return "\n".join(lines) + "\n"

    # ---- engine events
    def emit(self, kind: str, **f: Any) -> None:
        h = getattr(self, "_on_" + kind, None)
        if h is not None:
            try:
                h(**f)
            except Exception as e:                     # a drawing slip never breaks the turn
                self.out(f"{DIM}(display: {e}){RESET}")

    def _end_thinking(self) -> None:
        if self.think_t0 is None:
            return
        took = time.time() - self.think_t0
        self.think_s += took
        self.think_t0 = None
        if self.show_thinking:
            if self.think_partial.strip():
                self.out(DIM + ITAL + self.think_partial + RESET)
        elif took >= 1:
            # A moment's thought before a step isn't worth a line.
            hint = "" if self._said_hint else f"  {DIM}(ctrl+t shows thinking){RESET}"
            self._said_hint = True
            self.out(f"{DIM}✻ Thought for {took:.0f}s{RESET}{hint}")
        self.think_partial = ""

    def _on_thinking_chunk(self, content: str = "", **_):
        if not content:
            return
        if self.think_t0 is None:
            self.think_t0 = time.time()
        self.thinking += content
        if self.show_thinking:
            buf = self.think_partial + content
            *done, self.think_partial = buf.split("\n")
            for line in done:
                self.out(DIM + ITAL + line + RESET)
        else:
            self.think_partial = (self.think_partial + content)[-400:]

    def _on_assistant_chunk(self, content: str = "", **_):
        if not content:
            return
        self._end_thinking()
        self.status = "Writing"
        self.text.append(content)
        buf = self.partial + content
        *done, self.partial = buf.split("\n")
        for line in done:
            self._line(line)

    def _line(self, line: str) -> None:
        st = line.strip()
        if not self.in_code and st.startswith("|") and st.endswith("|") and len(st) > 1:
            self.table.append(st)            # drawn whole once the table ends
            return
        self._flush_table()
        if line.strip().startswith("```"):
            self.in_code = not self.in_code
            self.out(DIM + ("─" * 3) + RESET)
            return
        self.out(md_line(line, self.in_code))

    def _flush_table(self) -> None:
        """A markdown table as aligned columns: header bold, a rule, then rows."""
        rows = [[c.strip() for c in r.strip("|").split("|")] for r in self.table]
        self.table = []
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
        if not rows:
            return
        n = max(len(r) for r in rows)
        rows = [r + [""] * (n - len(r)) for r in rows]
        width = max(20, shutil.get_terminal_size().columns - 4)
        cols = [min(40, max(len(plain(md_line(r[i], False))) for r in rows)) for i in range(n)]
        while sum(cols) + 2 * (n - 1) > width and max(cols) > 8:
            cols[cols.index(max(cols))] -= 1

        def cell(text, w, bold=False):
            shown = plain(md_line(text, False))
            shown = shown if len(shown) <= w else shown[: w - 1] + "…"
            return (BOLD + shown + RESET if bold else shown) + " " * (w - len(shown))
        self.out("  " + "  ".join(cell(c, cols[i], True) for i, c in enumerate(rows[0])))
        self.out("  " + DIM + "  ".join("─" * w for w in cols) + RESET)
        for r in rows[1:]:
            self.out("  " + "  ".join(cell(c, cols[i]) for i, c in enumerate(r)))

    def _flush_text(self) -> None:
        if self.partial:
            self._line(self.partial)
            self.partial = ""
        self._flush_table()
        self.in_code = False

    def _on_tool_call_partial(self, name: str = "", **_):
        self._end_thinking()
        self.status = "Writing " + tool_words(name, {}).lower()

    def _on_tool_call(self, name: str = "", args: Any = None, id: str = "", **_):
        self._flush_text()
        self._end_thinking()
        if name == "end_session":           # the goodbye itself is the reply; the terminal closes after
            self.ended = True
            return
        self.status = tool_words(name, args if isinstance(args, dict) else {})
        self.out(f"{ACCENT}●{RESET} {self.status}")
        if id:
            self.pending_tools[id] = name

    def _on_tool_result(self, name: str = "", content: Any = "", ok: Any = None, error: Any = None, **f):
        if name == "end_session":
            return
        text = str(content if content is not None else f.get("result") or "")
        bad = ok is False or bool(error) or text.lstrip().lower().startswith(("error", "denied", "refused"))
        first = " ".join(text.split())[:100] or ("failed" if bad else "done")
        self.out(f"  {(ERR + '✗ ') if bad else DIM + '⎿ '}{first}{RESET}")
        self.status = "Working"

    def _on_steered(self, text: str = "", content: str = "", **_):
        self._flush_text()
        self.out(f"{DIM}↳ you steered: {(text or content)[:100]}{RESET}")

    def _on_retry(self, attempt: int = 0, max_attempts: int = 0, wait_s: float = 0, message: str = "", **_):
        self.out(f"{WARN}↻ {message or 'retrying'} ({attempt}/{max_attempts}){RESET}")

    def _on_compacting(self, **_):
        self.status = "Summing up older turns"

    def _on_context_state(self, **f):
        self.context = f

    def _on_compacted(self, **f):
        self.out(f"{DIM}↳ older turns summed up to make room{RESET}")

    def _on_context_warning(self, message: str = "", **_):
        self.out(f"{WARN}{message}{RESET}")

    def _on_heads_up(self, message: str = "", **_):
        self.out(f"{WARN}{message}{RESET}")

    def _on_quiet(self, message: str = "", **_):
        self.status = message

    def _on_slow_tool(self, name: str = "", **_):
        self.status = f"{tool_words(name, {})} (taking a while)"

    def _on_subagent_done(self, **f):
        who = f.get("name") or f.get("persona") or "a helper"
        self.out(f"{ACCENT}◆{RESET} {who} finished{DIM}: {str(f.get('summary') or '')[:100]}{RESET}")

    def _on_context_used(self, threads: Any = None, **_):
        # What it had in mind from memory, one quiet line, as the window's icon.
        # The list comes again whole as it grows; only what is new is said.
        seen = self.__dict__.setdefault("_mind_seen", set())
        names = [str(t.get("topic") or "") for t in (threads or []) if isinstance(t, dict) and t.get("topic")]
        names = [n for n in names if n not in seen]
        if names:
            seen.update(names)
            self.out(f"{DIM}◦ had in mind: {', '.join(names[:5])}{RESET}")

    def _on_facts_saved(self, titles: Any = None, **f):
        names = titles or f.get("saved") or []
        if names:
            self.out(f"{DIM}↳ remembered: {', '.join(map(str, names))[:120]}{RESET}")

    def _on_cancelled(self, **_):
        self._flush_text()
        self._end_thinking()
        self.out(f"{DIM}■ stopped{RESET}")

    def _on_error(self, message: str = "", **_):
        self._flush_text()
        self._end_thinking()
        self.out(f"{ERR}{message}{RESET}")

    def _on_stats_live(self, stats: Any = None, **_):
        if isinstance(stats, dict):
            self.stats.update(stats)

    def _on_turn_messages(self, messages: Any = None, **_):
        self.turn_messages = list(messages or [])

    def _on_done(self, stats: Any = None, **f):
        self._flush_text()
        self._end_thinking()
        if isinstance(stats, dict):
            self.stats.update(stats)
        self.running = False
        bits = [self.stats.get("brain") or self.stats.get("model") or self.model]
        tps = self.stats.get("decode_tps") or self.stats.get("tps")
        if tps:
            bits.append(f"{float(tps):.0f} tok/s")
        # The whole turn when the engine counted it, else the last call.
        tin = self.stats.get("tokens_in") or self.stats.get("prompt_tokens")
        tout = self.stats.get("tokens_out") or self.stats.get("gen_tokens")
        if tin:
            bits.append(f"↑{_k(tin)} ↓{_k(tout or 0)}")
        if self.stats.get("cost_usd") is not None:
            bits.append(f"${float(self.stats['cost_usd']):.3f}" + (" on your plan" if self.stats.get("plan") else ""))
        bits.append(f"{time.time() - self.t0:.1f}s")
        self.out(DIM + "  " + " · ".join(str(b) for b in bits if b) + RESET)

    def reply(self) -> str:
        return "".join(self.text).strip()


def _k(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
