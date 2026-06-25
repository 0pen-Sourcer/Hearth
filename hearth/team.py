"""Watch a team of agents build — spawn several Hearth subagents and watch them
work live, side by side, in terminal panes.

Opt-in: nothing here runs unless you call it (the `launch_team` tool, the CLI
`/team` command, or launch_team() directly). The agents run IN-PROCESS via
subagents.spawn_subagent(mode='background'), each writing a live JSONL
transcript; each pane just live-TAILS one agent's transcript and pretty-prints
it. So you see every agent think, call tools, and finish in real time — the
Hearth-native version of the Claude Code tmux multi-agent setup.

Panes: Windows Terminal split-panes (wt.exe) on Windows; tmux on Linux/macOS.
If neither is available, the agents still run — you just read their transcripts
instead of watching panes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_C = {
    "dim": "\033[2m", "reset": "\033[0m", "violet": "\033[38;5;141m",
    "lav": "\033[38;5;183m", "ok": "\033[38;5;42m", "tool": "\033[38;5;180m",
    "warn": "\033[38;5;203m", "bold": "\033[1m",
}


# --------------------------------------------------------------- pane viewer
def _render_entry(e: Dict[str, Any]) -> Optional[str]:
    """Turn one transcript JSONL entry into a pretty colored line (or None)."""
    C = _C
    k = e.get("kind")
    if k == "start":
        return (f"{C['violet']}{C['bold']}▶ {e.get('persona', 'agent')}{C['reset']}  "
                f"{C['dim']}{(e.get('prompt') or '')[:240]}{C['reset']}")
    if k == "route":
        tools = ", ".join(e.get("tools_exposed") or [])
        return f"{C['dim']}· {e.get('model', '?')} — tools: {tools[:120]}{C['reset']}"
    if k == "assistant_tool_call":
        lines: List[str] = []
        txt = (e.get("content") or "").strip()
        if txt:
            lines.append(f"{C['lav']}{txt}{C['reset']}")
        for c in (e.get("calls") or []):
            args = c.get("args")
            args_s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
            lines.append(f"{C['tool']}  🔧 {c.get('name')}{C['reset']} {C['dim']}{(args_s or '')[:160]}{C['reset']}")
        return "\n".join(lines) if lines else None
    if k in ("tool_result", "result"):
        r = str(e.get("result") or e.get("content") or "").replace("\n", " ")
        return f"{C['dim']}    ↳ {r[:160]}{C['reset']}"
    if k in ("assistant_final", "assistant", "assistant_text", "final"):
        t = (e.get("text") or e.get("content") or "").strip()
        return f"\n{C['reset']}{t}\n" if t else None
    if k == "end":
        st = str(e.get("status", "done"))
        col = C["ok"] if st in ("completed", "ok", "done", "success") else C["warn"]
        return f"{col}{C['bold']}■ {st}{C['reset']}"
    return None


def watch(path: str, title: str = "") -> int:
    """Live-tail a subagent transcript JSONL and pretty-print until it ends."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    hdr = title or os.path.basename(path)
    print(f"{_C['violet']}{_C['bold']}╭─ {hdr} ─╮{_C['reset']}\n", flush=True)
    pos = 0
    ended = False
    idle = 0.0
    while not ended:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    idle = 0.0
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        out = _render_entry(e)
                        if out:
                            print(out, flush=True)
                        if e.get("kind") == "end":
                            ended = True
                else:
                    idle += 0.3
            else:
                idle += 0.3
                if idle > 30:
                    print(f"{_C['warn']}(waiting for {hdr} to start…){_C['reset']}", flush=True)
                    idle = 0.0
            time.sleep(0.3)
        except KeyboardInterrupt:
            break
    print(f"\n{_C['dim']}— {hdr} finished. Press Enter to close this pane.{_C['reset']}", flush=True)
    try:
        input()
    except Exception:
        pass
    return 0


# ------------------------------------------------------------------- launch
def _py_cmd() -> List[str]:
    """Command prefix to run `-m hearth.team` — handles the frozen bundle
    (sys.executable is Hearth.exe, which re-execs python via a sentinel)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hearth-run-python"]
    return [sys.executable]


def _watch_cmd(member: Dict[str, Any]) -> List[str]:
    return _py_cmd() + ["-m", "hearth.team", "--watch", member["transcript"],
                        "--title", member["name"]]


def _find_wt() -> Optional[str]:
    """Locate wt.exe — PATH, the bundled copy (source + packaged), or the
    Store install. Hearth ships Windows Terminal in the dist, so a packaged
    user has it even without a system install."""
    c = shutil.which("wt.exe") or shutil.which("wt")
    if c:
        return c
    cands = [os.path.join(_REPO_ROOT, "Windows Terminal", "wt.exe"),
             os.path.join(_REPO_ROOT, "_internal", "Windows Terminal", "wt.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "wt.exe")]
    if getattr(sys, "frozen", False):
        cands.insert(0, os.path.join(os.path.dirname(sys.executable), "_internal", "Windows Terminal", "wt.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _open_wt(spawned: List[Dict[str, Any]]) -> bool:
    """Windows Terminal: one split-pane per agent in a fresh 'Team' tab."""
    wt = _find_wt()
    if not wt:
        return False
    args = [wt, "new-tab", "--title", "Hearth Team", "-d", _REPO_ROOT, "--"] + _watch_cmd(spawned[0])
    for m in spawned[1:]:
        args += [";", "split-pane", "-d", _REPO_ROOT, "--"] + _watch_cmd(m)
    # Even out the layout after the splits.
    args += [";", "move-focus", "first"]
    try:
        subprocess.Popen(args, creationflags=_NO_WINDOW)
        return True
    except Exception:
        return False


def _open_consoles(spawned: List[Dict[str, Any]]) -> bool:
    """Fallback when Windows Terminal isn't available: open each agent's watcher
    in its own separate console window (not as nice as panes, but still live)."""
    ok = False
    for m in spawned:
        try:
            subprocess.Popen(_watch_cmd(m), cwd=_REPO_ROOT,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            ok = True
        except Exception:
            pass
    return ok


def _open_tmux(spawned: List[Dict[str, Any]]) -> bool:
    """tmux: a detached session with one pane per agent, opened in a terminal."""
    tmux = shutil.which("tmux")
    if not tmux:
        return False
    sess = f"hearth_team_{int(time.time())}"
    try:
        def q(c: List[str]) -> str:
            return " ".join(f"'{x}'" if " " in x else x for x in c)
        subprocess.run([tmux, "new-session", "-d", "-s", sess, "-c", _REPO_ROOT,
                        q(_watch_cmd(spawned[0]))], check=True)
        for m in spawned[1:]:
            subprocess.run([tmux, "split-window", "-t", sess, "-c", _REPO_ROOT,
                            q(_watch_cmd(m))], check=True)
        subprocess.run([tmux, "select-layout", "-t", sess, "tiled"], check=False)
        # Open a terminal emulator attached to the session so the user sees it.
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                flag = "--" if term in ("gnome-terminal", "x-terminal-emulator") else "-e"
                subprocess.Popen([term, flag, tmux, "attach", "-t", sess])
                return True
        # No GUI terminal — session still exists; user can `tmux attach -t <sess>`.
        return True
    except Exception:
        return False


def launch_team(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Spawn a team of background subagents and open a live pane per agent.

    members: [{name, persona, prompt}]. persona defaults to 'coder'.
    """
    from . import subagents
    if not members:
        return {"ok": False, "error": "no team members given"}
    # Cap team size: each member is a full background LLM loop hammering the
    # model server + its own pane. A model that returns 20 members would self-DoS
    # an 8GB local box (depth guard only stops NESTED forks, not siblings). Cap is
    # endpoint-aware: a cloud endpoint can handle more parallelism (it costs $ but
    # doesn't serialize), a local single-GPU server can't.
    _base = os.environ.get("LOCAL_API_BASE", "").lower()
    _local = any(h in _base for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                                      "192.168.", "10.", "host.docker.internal"))
    _MAX_TEAM = 4 if _local else 8
    if len(members) > _MAX_TEAM:
        members = members[:_MAX_TEAM]
    spawned: List[Dict[str, Any]] = []
    for m in members:
        prompt = (m.get("prompt") or "").strip()
        if not prompt:
            continue
        persona = (m.get("persona") or "coder").strip()
        name = (m.get("name") or persona).strip()
        r = subagents.spawn_subagent(persona=persona, prompt=prompt,
                                     mode="background", name=name)
        if r.get("ok"):
            spawned.append({"name": name, "persona": persona,
                            "agent_id": r.get("agent_id"),
                            "transcript": r.get("transcript_path")})
    if not spawned:
        return {"ok": False, "error": "no agents spawned — each member needs a prompt"}
    if sys.platform == "win32":
        opened = _open_wt(spawned) or _open_consoles(spawned)
    else:
        opened = _open_tmux(spawned)
    return {
        "ok": True,
        "spawned": [{"name": s["name"], "persona": s["persona"], "agent_id": s["agent_id"]} for s in spawned],
        "watching": opened,
        "note": (f"Spawned {len(spawned)} agents and opened a live pane for each — "
                 f"watch them work side by side." if opened else
                 f"Spawned {len(spawned)} agents. Couldn't open watch panes "
                 f"(no Windows Terminal / tmux found) — they're running in the "
                 f"background; their results will arrive as notifications."),
    }


def _main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hearth.team")
    p.add_argument("--watch", metavar="TRANSCRIPT", help="Live-tail a subagent transcript (pane mode).")
    p.add_argument("--title", default="", help="Pane title.")
    args = p.parse_args(argv)
    if args.watch:
        return watch(args.watch, args.title)
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
