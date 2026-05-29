"""Tool-loop guard — principled control of the agentic tool-calling loop.

Small local models (7-9B) spiral: they re-call the same tool, bash a failing
command, or read the same file expecting a different answer. A hard "stop after
N calls" cap is duct tape — it cuts legitimate multi-step work short AND lets
real no-progress loops through.

This module synthesizes how production agents actually bound the loop:

  - Claude Code: trust the model to stop (a turn ends when it emits no tool
    calls); a *generous* optional turn cap as safety net; tool errors fed back
    so the model self-corrects; detect *diminishing returns*, not raw counts.
  - Hermes Agent (`tool_guardrails.py`): signature = tool + canonical-args
    hash; classify idempotent vs mutating; separate detectors for exact-failure
    loops and idempotent no-progress; soft warnings injected into context vs
    hard blocks; everything configurable.
  - OpenClaw (`tool-loop-detection.ts`): hash the tool *outcome*, not just the
    args — same args + *different* result = progress (polling); same args +
    *same* result = stuck. Ping-pong detection (A->B->A->B, identical results).
    Tiered warn -> critical.

The principles we keep:
  1. Trust the model to stop; a generous depth cap is the only hard ceiling.
  2. Detect NO-PROGRESS and FAILURE, not mere repetition (varied successful
     calls are legitimate work and must never be clamped).
  3. Hash outcomes, not just calls.
  4. NUDGE first (inject a warning the model sees) — hard-stop is the rare
     backstop, so we don't fight the model into emitting malformed tool markup.
  5. Mutating dup-calls are SKIPPED (no duplicate side effects like 4 identical
     reminders); idempotent repeats are allowed but watched for no-progress.

Thresholds are tuned tighter than the big frameworks (90 turns) because 9B
models stall faster, but every value is env-overridable.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Malformed tool-call markup small models emit as RAW TEXT instead of proper
# structured tool_calls (or when tools are withheld and the model still "wants"
# to call something). Must be stripped from both screen and saved history —
# leaving it poisons the conversation (re-rendering it later has crashed
# LM Studio with a 500).
_MARKUP_PATTERNS = [
    re.compile(r"<\|channel>call:.*?<tool_call\|>", re.DOTALL),  # Gemma
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL),         # Hermes/Llama XML
    re.compile(r"<function=.*?</function>", re.DOTALL),           # bare function tag
]


def strip_tool_markup(text: str) -> Tuple[str, bool]:
    """Remove malformed tool-call markup from model output. Returns
    (cleaned_text, changed)."""
    if not text:
        return text, False
    cleaned = text
    for pat in _MARKUP_PATTERNS:
        cleaned = pat.sub("", cleaned)
    cleaned = cleaned.strip()
    return cleaned, cleaned != text


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Generous hard ceiling on model turns per user message. Almost never hit in
# normal use — it's the seatbelt, not the steering. (Claude Code leaves this
# optional; Hermes uses 90. We pick a middle value for small-model safety.)
MAX_TURNS = _env_int("HEARTH_MAX_TURNS", 25)

# No-progress: the SAME call returning the SAME outcome this many times.
NO_PROGRESS_WARN = _env_int("HEARTH_NOPROGRESS_WARN", 2)
NO_PROGRESS_STOP = _env_int("HEARTH_NOPROGRESS_STOP", 3)

# Repeated FAILURE of the same EXACT call (same tool + same args).
FAILURE_WARN = _env_int("HEARTH_FAILURE_WARN", 2)
FAILURE_STOP = _env_int("HEARTH_FAILURE_STOP", 4)

# Same TOOL failing across DIFFERENT args — "fumbling blind" (e.g. 5 different
# run_commands all erroring while hunting for a working python). The tictactoe
# spiral. Distinct from the exact-repeat detector above.
SAME_TOOL_FAIL_WARN = _env_int("HEARTH_SAMETOOL_FAIL_WARN", 3)
SAME_TOOL_FAIL_STOP = _env_int("HEARTH_SAMETOOL_FAIL_STOP", 6)

# Ping-pong: alternating between two calls that both keep returning the same
# outcome (a deadlock). Counted in full A->B->A cycles.
PINGPONG_STOP = _env_int("HEARTH_PINGPONG_STOP", 3)

# Tools whose RE-EXECUTION has side effects — an identical repeat is a bug, so
# we SKIP it (prevents "4 copies of the same reminder"). Everything else is
# treated as read-only/idempotent: a repeat is allowed but watched for
# no-progress (same result again = stuck).
MUTATING_TOOLS = frozenset({
    "write_file", "edit_file", "create_directory", "delete_path", "move_path",
    "run_command", "open_app", "open_url", "open_in_browser",
    "memory_save", "memory_forget", "set_reminder", "cancel_reminder",
    "extract_archive_file", "set_voice", "clipboard_write",
    "forge_generate", "forge_shutdown", "end_session",
    "create_plugin", "delete_plugin",
    "browse_click", "browse_type",
    "learn_environment",
})


def _is_failure(result: str) -> bool:
    """Heuristic: did this tool result represent a failure the model should
    react to? Conservative — a user *declining* is not a model failure."""
    if not isinstance(result, str):
        return False
    head = result.lstrip()[:60].lower()
    return head.startswith("error") or head.startswith("[error")


@dataclass
class GuardDecision:
    """What the harness should do about a tool call.

    action:
      "ok"   — proceed / nothing to add
      "skip" — do NOT execute (identical mutating dup); use `note` as the result
      "warn" — execute happened; append `note` to the result so the model sees it
      "stop" — pathological loop; the turn should wrap up. `note` is the directive.
    """
    action: str = "ok"
    note: str = ""


@dataclass
class _SigState:
    calls: int = 0
    failures: int = 0
    last_succeeded: bool = False


class ToolLoopGuard:
    """Per-user-turn loop guard. Construct once, call reset() at the start of
    each new user turn, then before()/after() around each tool execution."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._sigs: Dict[str, _SigState] = {}
        self._tool_fails: Dict[str, int] = {}  # per-tool failures across any args
        self._recent: List[str] = []   # recent signatures, for ping-pong
        self.stopped: bool = False     # latched once a hard-stop fires this turn

    @staticmethod
    def signature(name: str, args: dict) -> str:
        try:
            a = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            a = str(args)
        return f"{name}|{a}"

    def before(self, name: str, args: dict) -> Optional[GuardDecision]:
        """Call BEFORE executing. Returns a 'skip' decision when an identical
        MUTATING call already succeeded this turn (so we don't repeat the side
        effect). Returns None to proceed normally."""
        sig = self.signature(name, args)
        st = self._sigs.get(sig)
        if st and st.last_succeeded and name in MUTATING_TOOLS:
            return GuardDecision(
                action="skip",
                note=(f"Skipped: '{name}' was already run with these exact arguments "
                      f"this turn and succeeded. Don't repeat it — use the earlier "
                      f"result or answer the user now."),
            )
        return None

    def after(self, name: str, args: dict, result: str) -> GuardDecision:
        """Call AFTER executing. Records the call and returns ok/warn/stop."""
        sig = self.signature(name, args)
        st = self._sigs.setdefault(sig, _SigState())
        failed = _is_failure(result)

        st.calls += 1
        st.last_succeeded = not failed
        if failed:
            st.failures += 1
            self._tool_fails[name] = self._tool_fails.get(name, 0) + 1

        self._recent.append(sig)
        if len(self._recent) > 12:
            self._recent.pop(0)

        # 1) Repeated FAILURE of the exact same call.
        if failed and st.failures >= FAILURE_STOP:
            return self._stop(
                f"'{name}' has failed {st.failures} times with the same arguments. "
                f"Stop retrying it. Tell the user plainly what failed and what "
                f"you'd need to proceed."
            )
        if failed and st.failures >= FAILURE_WARN:
            return GuardDecision("warn",
                f"[loop guard: '{name}' failed {st.failures}x with identical args — "
                f"change the arguments/approach or stop and report the failure; do "
                f"not repeat the same call.]")

        # 1b) Same TOOL failing across DIFFERENT args — fumbling blind. (The
        # tictactoe spiral: run_command erroring with 5 different commands.)
        tf = self._tool_fails.get(name, 0)
        if failed and tf >= SAME_TOOL_FAIL_STOP:
            return self._stop(
                f"'{name}' has failed {tf} times this turn (different arguments each time). "
                f"You're guessing, not progressing. Stop — tell the user plainly what's wrong "
                f"and what you'd need to fix it."
            )
        if failed and tf >= SAME_TOOL_FAIL_WARN:
            return GuardDecision("warn",
                f"[loop guard: '{name}' has now failed {tf} times with different arguments — "
                f"stop guessing. Step back and rethink the approach, or stop and report the "
                f"problem to the user.]")

        # 2) NO-PROGRESS: the SAME tool + SAME args called over and over. Same
        # inputs => no new information, whatever the output wobble (e.g. fetching
        # the same URL repeatedly). (Identical MUTATING calls are skipped earlier
        # by before(); this catches read-only repeats.)
        if st.calls >= NO_PROGRESS_STOP:
            return self._stop(
                f"'{name}' has been called {st.calls} times with identical arguments — "
                f"you already have that result. Use it and answer the user now; do not "
                f"call it again."
            )
        if st.calls >= NO_PROGRESS_WARN:
            return GuardDecision("warn",
                f"[loop guard: you've called '{name}' {st.calls}x with the SAME arguments — "
                f"that gives no new info. Use the result you have, or change approach.]")

        # 3) PING-PONG: alternating A,B,A,B,... with no progress.
        if self._pingpong_cycles() >= PINGPONG_STOP:
            return self._stop(
                "You're alternating between the same two tool calls without making "
                "progress. Break the loop: answer the user with what you have."
            )

        return GuardDecision("ok")

    def _stop(self, note: str) -> GuardDecision:
        self.stopped = True
        return GuardDecision("stop", note)

    def _pingpong_cycles(self) -> int:
        """Count how many times the tail looks like ...A B A B (two distinct
        signatures strictly alternating)."""
        r = self._recent
        if len(r) < 4:
            return 0
        a, b = r[-1], r[-2]
        if a == b:
            return 0
        cycles = 0
        i = len(r) - 1
        expect = a
        while i >= 0 and r[i] == expect:
            cycles += 1 if expect == a else 0
            expect = b if expect == a else a
            i -= 1
        return cycles
