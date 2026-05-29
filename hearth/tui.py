"""Hearth TUI — a Textual terminal app over the agent loop.

Thin frontend over `hearth.headless.run_once` (which runs the full agent loop:
tool calls, loop-guard, error classification, streaming). The TUI pumps
run_once's `emit` events into a styled transcript + keeps multi-turn history.
No duplicated agent logic.

Run:  python -m hearth.tui

Visual language: Hearth's violet/charcoal palette (NOT a generic gold TUI).
Tool calls render as compact rows (⚡ name · detail · ms); the assistant gets
an accent label; a status bar shows model / local-vs-cloud / live state.

v0.6 preview — the CLI (`hearth_cli.py`) is still the daily driver.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, RichLog, Static

from . import headless


class HearthTUI(App):
    CSS = """
    Screen { background: #14121c; }

    #status {
        dock: top;
        height: 1;
        background: #1e1a2e;
        color: #b8a9e0;
        padding: 0 1;
    }

    #log {
        background: #14121c;
        color: #e8e3f5;
        border: round #4a3f6b;
        padding: 0 1;
        scrollbar-color: #7c5cff;
    }

    #prompt {
        dock: bottom;
        border: round #7c5cff;
        background: #1a1726;
        color: #e8e3f5;
    }
    #prompt:focus { border: round #a98bff; }

    Footer { background: #1e1a2e; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._history: List[Dict[str, Any]] = []
        self._last_assistant = ""
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield RichLog(id="log", wrap=True, markup=True, highlight=False)
        yield Input(placeholder="Talk to Hearth…   (Ctrl+L clear · Ctrl+C quit)", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Hearth"
        self._set_status("idle")
        log = self.query_one("#log", RichLog)
        log.write("[bold #a98bff]✦ Hearth[/]  [dim #8a7fb0]— local-first personal AI · v0.6 preview[/]")
        log.write("[dim #8a7fb0]Type below. It runs the full agent — tools, memory, the works.[/]\n")
        self.query_one("#prompt", Input).focus()

    # ---- status bar ----
    def _set_status(self, state: str) -> None:
        model = os.getenv("LOCAL_MODEL") or "local model"
        base = headless.LOCAL_API_BASE
        loc = "cloud" if not headless._is_local_endpoint(base) else "local"
        dot = {"idle": "[#6ee7b7]●[/]", "working": "[#ffb86b]●[/]"}.get(state, "[#6ee7b7]●[/]")
        label = "working…" if state == "working" else "ready"
        try:
            self.query_one("#status", Static).update(
                f"{dot} [bold #b8a9e0]JARVIS[/] [dim]·[/] {model} [dim]·[/] "
                f"[#a98bff]{loc}[/] [dim]·[/] {label}"
            )
        except Exception:
            pass

    # ---- event pump from run_once ----
    def _emit(self, kind: str, **fields: Any) -> None:
        log = self.query_one("#log", RichLog)
        if kind == "tool_call":
            args = fields.get("args") or {}
            # show the most informative single arg value, compact
            detail = ""
            for k in ("command", "path", "query", "name", "url", "what"):
                if args.get(k):
                    detail = str(args[k]); break
            if not detail and args:
                detail = str(args)
            if len(detail) > 64:
                detail = detail[:64] + "…"
            log.write(f"[#7c5cff]⚡[/] [bold #c9bdf0]{fields.get('name')}[/]"
                      + (f"  [dim #8a7fb0]{detail}[/]" if detail else ""))
        elif kind == "tool_result":
            content = fields.get("content") or ""
            head = content.split("\n", 1)[0][:110]
            ms = fields.get("ms")
            err = head.lower().startswith(("error", "skipped"))
            arrow = "[#ff8c6b]↳[/]" if err else "[#6ee7b7]↳[/]"
            tail = f"  [dim #6a6088]{ms}ms[/]" if isinstance(ms, int) else ""
            log.write(f"  {arrow} [dim #8a7fb0]{head}[/]{tail}")
        elif kind == "assistant":
            content = fields.get("content") or ""
            if content.strip():
                self._last_assistant = content
                log.write(f"\n[bold #a98bff]JARVIS[/]  {content}\n")
        elif kind == "nudge":
            log.write(f"  [dim #6a6088]· {fields.get('reason','')}[/]")
        elif kind == "error":
            log.write(f"[#ff6b6b]● {fields.get('message','error')}[/]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._busy:
            return
        inp = self.query_one("#prompt", Input)
        inp.value = ""
        if prompt.lower() in ("/quit", "/exit", "exit", "quit"):
            self.exit()
            return
        self.query_one("#log", RichLog).write(f"[bold #7c5cff]You[/]  {prompt}")
        self._busy = True
        inp.disabled = True
        self._set_status("working")
        self.run_worker(self._run_turn(prompt), exclusive=True)

    async def _run_turn(self, prompt: str) -> None:
        self._last_assistant = ""
        try:
            await headless.run_once(
                prompt,
                emit=self._emit,
                history=list(self._history),
                permission_check=lambda name, args: "allow",  # v1: auto-approve
            )
            self._history.append({"role": "user", "content": prompt})
            if self._last_assistant:
                self._history.append({"role": "assistant", "content": self._last_assistant})
        finally:
            self._busy = False
            self._set_status("idle")
            inp = self.query_one("#prompt", Input)
            inp.disabled = False
            inp.focus()

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()


def main() -> None:
    HearthTUI().run()


if __name__ == "__main__":
    main()
