"""Other coding agents as helpers: Claude Code, Codex, Gemini CLI, OpenCode and
the rest, run headless on the user's own install and subscription.

Each one runs in the background in a working folder and writes the same
transcript as Hearth's own helpers (start, route, tool calls, final text,
end), so it shows up in the Team view and its result reaches the main
assistant the same way. Claude Code, Codex and Cursor report each step as
they go, so their work shows while it happens; the others run in their
one-shot mode and their output is the result.

Claude Code note: its headless mode (`claude -p`) is billed to the Agent SDK
credit that comes with a Claude plan, not to the plan's interactive limits.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import env as _env

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_ANSI = __import__("re").compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _claude_cmd(allow: str) -> List[str]:
    mode = {"auto": "auto", "edits": "acceptEdits"}.get(allow, "plan")
    return ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--permission-mode", mode]


def _codex_cmd(allow: str) -> List[str]:
    """Headless Codex has nobody to ask, so it never stops to: it works inside
    its folder, with the network only when the user chose Auto."""
    return (["exec", "--json", "--skip-git-repo-check", "-c", 'approval_policy="never"', "-s", "workspace-write"]
            + (["-c", "sandbox_workspace_write.network_access=true"] if allow == "auto" else []) + ["-"])


# id -> how to run it. `args` builds the arguments (the prompt goes on stdin
# unless `prompt_arg` is set). `stream` names the output parser. `allow` is
# how much the user lets agents do on their own: "auto" or "edits". `maker`
# is the company behind it, shown next to its agents in the Team view.
AGENTS: Dict[str, Dict[str, Any]] = {
    # Claude Code reads messages on stdin while it works, so it can be told
    # things part-way; the others take one prompt and run to the end.
    "claude-code": {"label": "Claude Code", "maker": "Anthropic", "exe": ["claude"], "args": _claude_cmd,
                    "stream": "claude", "takes_messages": True,
                    "billing": "Agent SDK credit on your Claude plan",
                    # Its own installer puts it here, which a running Hearth's PATH doesn't have yet.
                    "also": [r"~\.local\bin\claude.exe", "~/.local/bin/claude"]},
    "codex": {"label": "Codex", "maker": "OpenAI", "exe": ["codex"], "args": _codex_cmd, "stream": "codex",
              # The Codex desktop app keeps its own copy of the CLI off PATH.
              "also": [r"%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe", r"~\.codex\.sandbox-bin\codex.exe"]},
    "antigravity": {"label": "Antigravity", "maker": "Google", "exe": ["agy"],
                    "args": lambda allow: (["--dangerously-skip-permissions"] if allow == "auto" else []) + ["-p"],
                    "stream": "text", "prompt_arg": True},
    # --skip-trust: headless, nobody is there to answer its "trust this folder?".
    "gemini-cli": {"label": "Gemini CLI", "maker": "Google", "exe": ["gemini"],
                   "args": lambda allow: ["--skip-trust", "--approval-mode=" + ("yolo" if allow == "auto" else "auto_edit"), "-p"],
                   "stream": "text", "prompt_arg": True},
    "grok": {"label": "Grok", "maker": "xAI", "exe": ["grok"],
             "args": lambda allow: ["--no-auto-update"] + (["--always-approve"] if allow == "auto" else []) + ["-p"],
             "stream": "text", "prompt_arg": True},
    # Headless Kimi approves every tool call by design, so it only runs when
    # the user has already chosen Auto.
    "kimi": {"label": "Kimi Code", "maker": "Moonshot AI", "exe": ["kimi"],
             "args": lambda allow: ["-p"], "stream": "text", "prompt_arg": True, "auto_only": True},
    # `app`: where its desktop app lives. The app has no command line of its
    # own, so it can't be handed a task; its CLI can, and shares its sign-in.
    "opencode": {"label": "OpenCode", "maker": "SST", "exe": ["opencode"], "args": lambda allow: ["run"],
                 "stream": "text", "prompt_arg": True,
                 "app": [r"%LOCALAPPDATA%\Programs\@opencode-aidesktop\OpenCode.exe"]},
    "qwen-code": {"label": "Qwen Code", "maker": "Alibaba", "exe": ["qwen"],
                  "args": lambda allow: (["--yolo"] if allow == "auto" else []) + ["-p"],
                  "stream": "text", "prompt_arg": True},
    "crush": {"label": "Crush", "maker": "Charm", "exe": ["crush"], "args": lambda allow: ["run"],
              "stream": "text", "prompt_arg": True},
    "cursor-agent": {"label": "Cursor Agent", "maker": "Cursor", "exe": ["cursor-agent"],
                     "args": lambda allow: ["-p", "--output-format", "stream-json", "--trust"]
                     + (["--force"] if allow == "auto" else []),
                     "stream": "cursor", "prompt_arg": True,
                     "also": [os.path.join("%LOCALAPPDATA%", "cursor-agent", "cursor-agent.cmd")]},
    "copilot": {"label": "Copilot CLI", "maker": "GitHub", "exe": ["copilot"],
                "args": lambda allow: (["--allow-all-tools"] if allow == "auto" else []) + ["-p"],
                "stream": "text", "prompt_arg": True},
    "goose": {"label": "Goose", "maker": "Block", "exe": ["goose"],
              "args": lambda allow: ["run", "--no-session", "-t"], "stream": "text", "prompt_arg": True},
    "aider": {"label": "Aider", "maker": "Aider", "exe": ["aider"],
              "args": lambda allow: (["--yes-always"] if allow in ("auto", "edits") else []) + ["--message"],
              "stream": "text", "prompt_arg": True},
    "droid": {"label": "Droid", "maker": "Factory", "exe": ["droid"],
              "args": lambda allow: ["exec", "--auto", "medium" if allow == "auto" else "low"],
              "stream": "text", "prompt_arg": True},
    "amp": {"label": "Amp", "maker": "Sourcegraph", "exe": ["amp"],
            "args": lambda allow: (["--dangerously-allow-all"] if allow == "auto" else []) + ["-x"],
            "stream": "text", "prompt_arg": True},
    "pi": {"label": "Pi", "maker": "pi.dev", "exe": ["pi"], "args": lambda allow: ["-p"],
           "stream": "text", "prompt_arg": True},
    "cline": {"label": "Cline", "maker": "Cline", "exe": ["cline"],
              "args": lambda allow: ["-y"] if allow == "auto" else [], "stream": "text", "prompt_arg": True},
    # Headless Continue leaves out any tool that would need an approval.
    "continue": {"label": "Continue", "maker": "Continue", "exe": ["cn"], "args": lambda allow: ["-p"],
                 "stream": "text", "prompt_arg": True},
    "amazon-q": {"label": "Amazon Q", "maker": "AWS", "exe": ["q"],
                 "args": lambda allow: ["chat", "--no-interactive"] + (["--trust-all-tools"] if allow == "auto" else []),
                 "stream": "text", "prompt_arg": True},
    "kiro": {"label": "Kiro", "maker": "AWS", "exe": ["kiro-cli"],
             "args": lambda allow: ["chat", "--no-interactive"] + (["--trust-all-tools"] if allow == "auto" else []),
             "stream": "text", "prompt_arg": True},
    "kilo": {"label": "Kilo Code", "maker": "Kilo", "exe": ["kilo"],
             "args": lambda allow: ["run"] + (["--auto"] if allow == "auto" else []), "stream": "text", "prompt_arg": True},
    # These two act without asking in their headless modes (OpenHands says so;
    # Augment doesn't say otherwise), so they only run on Auto.
    "augment": {"label": "Augment", "maker": "Augment Code", "exe": ["auggie"], "args": lambda allow: ["--print"],
                "stream": "text", "prompt_arg": True, "auto_only": True},
    "openhands": {"label": "OpenHands", "maker": "All Hands AI", "exe": ["openhands"],
                  "args": lambda allow: ["--headless", "-t"], "stream": "text", "prompt_arg": True, "auto_only": True},
}
# Where each one is got from, for the ones that aren't on the PC.
SITES = {"claude-code": "https://claude.com/claude-code", "codex": "https://chatgpt.com/codex",
         "antigravity": "https://antigravity.google", "gemini-cli": "https://github.com/google-gemini/gemini-cli",
         "grok": "https://x.ai/cli", "kimi": "https://github.com/MoonshotAI/kimi-cli", "opencode": "https://opencode.ai",
         "qwen-code": "https://github.com/QwenLM/qwen-code", "crush": "https://github.com/charmbracelet/crush",
         "cursor-agent": "https://cursor.com/cli", "copilot": "https://github.com/github/copilot-cli",
         "goose": "https://goose-docs.ai", "aider": "https://aider.chat", "droid": "https://factory.ai",
         "amp": "https://ampcode.com", "pi": "https://pi.dev", "cline": "https://cline.bot",
         "continue": "https://continue.dev", "amazon-q": "https://aws.amazon.com/q/developer/",
         "kiro": "https://kiro.dev", "kilo": "https://kilo.ai", "augment": "https://www.augmentcode.com",
         "openhands": "https://all-hands.dev"}
# Only these have been run end to end from Hearth; the rest follow their docs.
VERIFIED = {"claude-code"}


def _which(names: List[str]) -> Optional[str]:
    for n in names:
        for cand in (n + ".cmd", n + ".exe", n) if sys.platform == "win32" else (n,):
            p = shutil.which(cand)
            if p:
                return p
    return None


def find(aid: str) -> Optional[str]:
    """Where an agent's CLI is: on PATH, or where its desktop app keeps it."""
    spec = AGENTS.get(aid) or {}
    path = _which(spec.get("exe") or [])
    if path:
        return path
    for pat in spec.get("also") or []:
        hits = [p for p in glob.glob(os.path.expandvars(os.path.expanduser(pat))) if os.path.isfile(p)]
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


# An agent whose own account turned it away: which one, and what it said.
# Kept a day, since a usage limit resets and a sign-in gets fixed.
_REFUSED = os.path.join(os.path.expanduser("~"), ".hearth", "agents_refused.json")
_ACCOUNT_WORDS = ("no longer supported", "not eligible", "ineligible", "usage limit", "rate limit", "quota",
                  "not logged in", "not signed in", "log in", "login", "sign in", "unauthorized", "authenticat",
                  "subscription", "api key")


def why_failed(lines: List[str]) -> str:
    """The line of an agent's output that says why it stopped, without the
    stack trace under it."""
    said = [l.strip() for l in lines if l.strip() and not l.lstrip().startswith(("at ", "{", "}", "[", "]"))]
    for l in reversed(said):
        if any(w in l.lower() for w in ("error", "failed", "denied", "refus") + _ACCOUNT_WORDS):
            import re as _re
            return _re.sub(r"^.*?\b\w*Error:\s*", "", l).strip()[:300]     # "…occurred:SomeError: why" -> "why"
    return (said[-1] if said else "")[:300]


def refused() -> Dict[str, str]:
    """agent id -> what its account said, for agents turned away in the last day."""
    try:
        with open(_REFUSED, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return {}
    return {k: v.get("why", "") for k, v in data.items() if time.time() - v.get("at", 0) < 86400}


def _note_refused(aid: str, why: str) -> None:
    """Remember an account-level refusal, or forget one after a run that worked."""
    try:
        with open(_REFUSED, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    if why and any(w in why.lower() for w in _ACCOUNT_WORDS):
        data[aid] = {"why": why, "at": time.time()}
    elif aid in data:
        del data[aid]
    else:
        return
    try:
        os.makedirs(os.path.dirname(_REFUSED), exist_ok=True)
        with open(_REFUSED, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def available() -> List[Dict[str, Any]]:
    """The agent CLIs installed on this machine."""
    turned_away = refused()
    out = []
    for aid, spec in AGENTS.items():
        path = find(aid)
        # A batch file that isn't an npm shim is a launcher of something else:
        # VS Code puts a "copilot" on PATH that offers to install the real CLI
        # and would sit waiting for an answer. Not installed, so not listed.
        if path and spec.get("prompt_arg") and _direct_argv(path)[0].lower().endswith((".cmd", ".bat")):
            path = None
        if path:
            out.append({"id": aid, "label": spec["label"], "maker": spec.get("maker", ""), "path": path,
                        "tested": aid in VERIFIED, "billing": spec.get("billing", ""),
                        "refused": turned_away.get(aid, "")})
    return out


# ---------------------------------------------------------------- updates
# The npm package behind each CLI that is installed from npm, so Hearth can
# tell when a newer one is out and update it when asked. A CLI that came with
# a desktop app (Codex) or updates itself (Cursor) isn't listed.
NPM_PACKAGES = {"claude-code": "@anthropic-ai/claude-code", "codex": "@openai/codex",
                "gemini-cli": "@google/gemini-cli", "opencode": "opencode-ai",
                "crush": "@charmland/crush", "copilot": "@github/copilot"}
_UPDATES_CACHE = os.path.join(os.path.expanduser("~"), ".hearth", "agent_updates.json")


def _version_tuple(text: str) -> tuple:
    import re as _re
    m = _re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def installed_version(aid: str) -> str:
    exe = find(aid)
    if not exe:
        return ""
    try:
        r = subprocess.run(_direct_argv(exe) + ["--version"], capture_output=True, text=True,
                           timeout=30, creationflags=_NO_WINDOW)
    except Exception:
        return ""
    v = _version_tuple((r.stdout or "") + (r.stderr or ""))
    return ".".join(str(x) for x in v)


def latest_version(package: str) -> str:
    """The newest version on the npm registry. One small request, kept half a day."""
    try:
        with open(_UPDATES_CACHE, encoding="utf-8") as f:
            cache = json.load(f) or {}
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(package) or {}
    if hit.get("version") and time.time() - hit.get("at", 0) < 12 * 3600:
        return hit["version"]
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://registry.npmjs.org/{package}/latest", timeout=8) as r:
            version = str(json.load(r).get("version") or "")
    except Exception:
        return hit.get("version", "")
    if version:
        cache[package] = {"version": version, "at": time.time()}
        try:
            os.makedirs(os.path.dirname(_UPDATES_CACHE), exist_ok=True)
            with open(_UPDATES_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except OSError:
            pass
    return version


def _from_npm(aid: str) -> bool:
    """Installed from npm (a shim in npm's folder), not carried by an app."""
    exe = find(aid) or ""
    return bool(_which(AGENTS[aid]["exe"])) and "npm" in exe.lower()


def updates() -> List[Dict[str, str]]:
    """Installed agent CLIs that have a newer version out."""
    out = []
    for aid, package in NPM_PACKAGES.items():
        if aid not in AGENTS or not find(aid) or not _from_npm(aid):
            continue
        have, newest = installed_version(aid), latest_version(package)
        if have and newest and _version_tuple(newest) > _version_tuple(have):
            out.append({"id": aid, "label": AGENTS[aid]["label"], "installed": have, "latest": newest})
    return out


def update(aid: str) -> Dict[str, Any]:
    """Update one agent CLI through npm. Returns {ok, version} or {ok: False, error}."""
    package = NPM_PACKAGES.get(aid)
    if not package or aid not in AGENTS or not find(aid):
        return {"ok": False, "error": "that agent isn't one Hearth can update"}
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return {"ok": False, "error": "npm isn't on this PC, so the update can't run"}
    try:
        r = subprocess.run([npm, "install", "-g", package + "@latest"], capture_output=True, text=True,
                           timeout=600, creationflags=_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0:
        tail = [l for l in ((r.stderr or "") + (r.stdout or "")).splitlines() if l.strip()][-2:]
        return {"ok": False, "error": " ".join(tail)[:300] or f"npm exited with code {r.returncode}"}
    return {"ok": True, "version": installed_version(aid)}


# ---------------------------------------------------------------- setup
# Getting an agent onto the PC and signed in, with its maker's own installer
# and its own sign-in, so nobody has to open a terminal. `win` and `sh` are the
# one-line installers each maker publishes; `npm` is the fallback where there
# is none. `status` asks the CLI itself whether it is signed in.
SETUP: Dict[str, Dict[str, Any]] = {
    "claude-code": {"win": "irm https://claude.ai/install.ps1 | iex",
                    "sh": "curl -fsSL https://claude.ai/install.sh | bash",
                    "npm": "@anthropic-ai/claude-code", "login": ["auth", "login"], "status": ["auth", "status"],
                    "signed": lambda out: '"loggedin":true' in out.replace(" ", "")},
    "codex": {"npm": "@openai/codex", "login": ["login"], "status": ["login", "status"],
              "signed": lambda out: "logged in" in out and "not logged in" not in out,
              "needs": "Codex comes with the ChatGPT desktop app, or installs with Node.js. Get either one, then try again."},
    "cursor-agent": {"win": "irm 'https://cursor.com/install?win32=true' | iex",
                     "sh": "curl https://cursor.com/install -fsS | bash",
                     "login": ["login"], "status": ["status"],
                     "signed": lambda out: "logged in as" in out},
}
_SIGNED: Dict[str, tuple] = {}            # agent id -> (when asked, answer)


def signed_in(aid: str, fresh: bool = False) -> Optional[bool]:
    """Whether an installed agent is signed in, asked of the agent itself.
    None when it isn't installed or can't say. Kept two minutes."""
    how = SETUP.get(aid)
    exe = find(aid) if how else None
    if not exe:
        return None
    hit = _SIGNED.get(aid)
    if hit and not fresh and time.time() - hit[0] < 120:
        return hit[1]
    try:
        r = subprocess.run(_direct_argv(exe) + how["status"], capture_output=True, text=True, timeout=40,
                           encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                           cwd=os.path.expanduser("~"))
        answer = bool(how["signed"](((r.stdout or "") + (r.stderr or "")).lower()))
    except Exception:
        return None
    _SIGNED[aid] = (time.time(), answer)
    return answer


def setup_state(fresh: bool = False) -> List[Dict[str, Any]]:
    """Each agent Hearth can set up: here or not, signed in or not."""
    return [{"id": aid, "label": AGENTS[aid]["label"], "maker": AGENTS[aid].get("maker", ""),
             "installed": bool(find(aid)), "signed_in": signed_in(aid, fresh)} for aid in SETUP]


def how_to_get(aid: str) -> str:
    """What Hearth can do about an agent that isn't here: "setup" (install and
    sign in from the dialog), "install" (it comes from npm and Node is on this
    PC), or "site" (its maker's page)."""
    if aid in SETUP:
        return "setup"
    if aid in NPM_PACKAGES and (shutil.which("npm.cmd") or shutil.which("npm")):
        return "install"
    return "site"


def missing() -> List[Dict[str, Any]]:
    """The agent programs Hearth can run that aren't on this PC, each with how to get it."""
    here = {a["id"] for a in available()}
    def has_app(spec) -> bool:
        return any(glob.glob(os.path.expandvars(os.path.expanduser(pat))) for pat in spec.get("app") or [])

    return [{"id": aid, "label": spec["label"], "maker": spec.get("maker", ""), "site": SITES.get(aid, ""),
             "get": how_to_get(aid), "has_app": has_app(spec)}
            for aid, spec in AGENTS.items() if aid not in here and aid in SITES]


def install(aid: str) -> Dict[str, Any]:
    """Install an agent with its maker's installer. Asked for by the person,
    from the setup dialog; never on its own."""
    how = SETUP.get(aid) or ({"npm": NPM_PACKAGES[aid]} if aid in NPM_PACKAGES and aid in AGENTS else None)
    if not how:
        return {"ok": False, "error": "that isn't an agent Hearth can install"}
    if find(aid):
        return {"ok": True, "already": True}
    line = how.get("win" if sys.platform == "win32" else "sh")
    if line:
        argv = (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", line]
                if sys.platform == "win32" else ["bash", "-lc", line])
    else:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm or not how.get("npm"):
            return {"ok": False, "error": how.get("needs") or "there is no installer for it on this system"}
        argv = [npm, "install", "-g", how["npm"]]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not find(aid):
        said = why_failed(((r.stdout or "") + "\n" + (r.stderr or "")).splitlines())
        return {"ok": False, "error": said or f"the installer finished (code {r.returncode}) but {AGENTS[aid]['label']} "
                                              "isn't where it should be"}
    return {"ok": True}


def sign_in(aid: str) -> Dict[str, Any]:
    """Open the agent's own sign-in in a window of its own; it opens the
    browser from there. Hearth never sees the account or its password."""
    # One with no sign-in command of its own is simply opened: it asks on its first run.
    how = SETUP.get(aid) or {"login": []}
    exe = find(aid) if aid in AGENTS else None
    if not exe:
        return {"ok": False, "error": "install it first"}
    argv = _direct_argv(exe) + how["login"]
    try:
        if sys.platform == "win32":
            subprocess.Popen(argv, creationflags=0x00000010, cwd=os.path.expanduser("~"))     # CREATE_NEW_CONSOLE
        else:
            import shlex
            term = next((t for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm") if shutil.which(t)), None)
            if sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "{shlex.join(argv)}"'])
            elif term:
                subprocess.Popen([term, "-e", shlex.join(argv)])
            else:
                return {"ok": False, "error": "run `" + shlex.join([os.path.basename(argv[0])] + how["login"]) + "` in a terminal"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _SIGNED.pop(aid, None)
    return {"ok": True}


# Running agents that can be told something: agent id -> its process.
_OPEN: Dict[str, Any] = {}
_OPEN_LOCK = threading.Lock()


def _user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user",
                                                   "content": [{"type": "text", "text": text}]}}) + "\n"


def can_tell(agent_id: str) -> bool:
    with _OPEN_LOCK:
        return agent_id in _OPEN


def tell(agent_id: str, text: str) -> Optional[Dict[str, Any]]:
    """Give a running agent a message. None when it isn't one that can take it."""
    with _OPEN_LOCK:
        proc = _OPEN.get(agent_id)
        if proc is None:
            return None
        try:
            proc.stdin.write(_user_line(text))
            proc.stdin.flush()
        except (OSError, ValueError):
            return {"ok": False, "error": "it finished before the message reached it"}
    return {"ok": True, "note": "It will read this at its next step."}


def describe() -> str:
    """One line for tool descriptions: what can be delegated to here."""
    found = available()
    if not found:
        return "No other agent CLIs are installed here."
    return "Installed here: " + ", ".join(
        f"{a['label']} (agent='{a['id']}'" + (", not usable now: its account refused it" if a["refused"] else "") + ")"
        for a in found) + "."


def _direct_argv(exe: str) -> List[str]:
    """How to start an installed CLI without cmd.exe in between.

    npm installs a CLI on Windows as a .cmd file, and running a .cmd means
    cmd.exe parses the arguments: a prompt carrying & or | could run a second
    command. The shim only starts node on a script, so start that directly.
    Anything that can't be resolved that way is refused for argument prompts
    by the caller."""
    if sys.platform != "win32" or not exe.lower().endswith((".cmd", ".bat")):
        return [exe]
    try:
        text = open(exe, encoding="utf-8", errors="replace").read()
    except OSError:
        return [exe]
    import re as _re
    found = [t for t in _re.findall(r'"%(?:~?dp0%?)\\?([^"]+?\.(?:(?:c|m)?js|exe))"', text, _re.I)
             if os.path.basename(t).lower() != "node.exe"]
    if not found and ".ps1" in text.lower():
        # A launcher that hands over to PowerShell, which starts the newest
        # bundled version on its own node (Cursor's CLI).
        builds = [d for d in glob.glob(os.path.join(os.path.dirname(exe), "versions", "*"))
                  if os.path.isfile(os.path.join(d, "node.exe")) and os.path.isfile(os.path.join(d, "index.js"))]
        if builds:
            d = max(builds, key=os.path.getmtime)
            return [os.path.join(d, "node.exe"), os.path.join(d, "index.js")]
    if not found:
        return [exe]
    script = os.path.join(os.path.dirname(exe), found[0].lstrip("\\"))
    if script.lower().endswith(".exe"):           # newer shims wrap a native binary
        return [script] if os.path.isfile(script) else [exe]
    node = os.path.join(os.path.dirname(exe), "node.exe")
    node = node if os.path.isfile(node) else shutil.which("node")
    if node and os.path.isfile(script):
        return [node, script]
    return [exe]


def _long_path(p: str) -> str:
    """Expand Windows 8.3 short names (NAME~1 folders): agents treat them as
    suspicious and refuse to read inside them."""
    if sys.platform != "win32":
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, len(buf))
        return buf.value if 0 < n < len(buf) else p
    except Exception:
        return p


def _allow_level() -> str:
    """How much an external agent may do without asking, from the user's own
    standing choices: auto-approve lets it act freely, otherwise it may edit
    files in its folder, and never more than the user allowed Hearth."""
    if _env.get("AUTO_APPROVE") in ("1", "true", "yes"):
        return "auto"
    try:
        from .tools import approval_mode
        return "auto" if approval_mode() == "auto" else "edits"
    except Exception:
        return "edits"


def _parse_claude(line: str, emit: Callable[[Dict[str, Any]], None], state: Dict[str, Any]) -> None:
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        state["model"] = ev.get("model", "")
    elif t == "assistant":
        blocks = (ev.get("message") or {}).get("content") or []
        calls = [{"name": b.get("name", ""), "args": json.dumps(b.get("input") or {})[:400]}
                 for b in blocks if b.get("type") == "tool_use"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if calls:
            emit({"kind": "assistant_tool_call", "content": text, "calls": calls})
            state["pending"] = [c["name"] for c in calls]
        elif text:
            state["last_text"] = text
    elif t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                name = state["pending"].pop(0) if state.get("pending") else "tool"
                body = b.get("content")
                if isinstance(body, list):
                    body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                body = str(body or "")
                emit({"kind": "tool_result", "name": name, "chars": len(body), "preview": body[:400]})
                state["tools"] = state.get("tools", 0) + 1
    elif t == "result":
        state["result"] = ev.get("result") or state.get("last_text", "")
        state["ok"] = not ev.get("is_error") and ev.get("subtype") == "success"
        if not state["ok"]:
            state["error"] = ev.get("result") or ev.get("subtype") or "the agent stopped with an error"


def _step(emit, state: Dict[str, Any], key: str, name: str, args: Any) -> None:
    emit({"kind": "assistant_tool_call", "content": "", "calls": [{"name": name, "args": json.dumps(args or {})[:400]}]})
    state.setdefault("open", {})[key] = name


def _step_done(emit, state: Dict[str, Any], key: str, name: str, body: str) -> None:
    if key not in state.get("open", {}):
        _step(emit, state, key, name, {})
    state["open"].pop(key, None)
    emit({"kind": "tool_result", "name": name, "chars": len(body), "preview": body[:400]})
    state["tools"] = state.get("tools", 0) + 1


def _parse_codex(line: str, emit: Callable[[Dict[str, Any]], None], state: Dict[str, Any]) -> bool:
    """One line of `codex exec --json`. False when it isn't one of its events."""
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(ev, dict) or "type" not in ev:
        return False
    t, item = ev["type"], ev.get("item") or {}
    kind, key = item.get("type"), str(item.get("id") or "")
    shown = {"command_execution": ("run_command", {"command": item.get("command", "")}),
             "mcp_tool_call": (item.get("tool", "tool"), item.get("arguments") or {}),
             "web_search": ("web_search", {"query": item.get("query", "")}),
             "file_change": ("edit_file", {"path": next((str(c.get("path") or "") for c in item.get("changes") or []
                                                         if isinstance(c, dict)), "")})}.get(kind)
    if t == "item.started" and shown:
        _step(emit, state, key, *shown)
    elif t == "item.completed" and shown:
        if kind == "command_execution":
            body = str(item.get("aggregated_output") or "")
        elif kind == "mcp_tool_call":
            body = "\n".join(str(c.get("text") or "") for c in (item.get("result") or {}).get("content") or []
                             if isinstance(c, dict))
        elif kind == "file_change":
            body = "\n".join(f"{c.get('kind', 'update')}: {c.get('path', '')}" for c in item.get("changes") or []
                             if isinstance(c, dict))
        else:
            body = str(item.get("query") or "")
        if key not in state.get("open", {}):
            _step(emit, state, key, *shown)
        _step_done(emit, state, key, shown[0], body)
    elif t == "item.completed" and kind == "agent_message" and item.get("text"):
        state["last_text"] = (state.get("last_text", "") + "\n\n" + item["text"]).strip()
    elif t == "turn.completed":
        state["ok"], state["result"] = True, state.get("last_text", "")
    elif t in ("turn.failed", "error"):
        state["ok"] = False
        state["error"] = str((ev.get("error") or {}).get("message") or ev.get("message") or "the turn failed")
    return True


def _parse_cursor(line: str, emit: Callable[[Dict[str, Any]], None], state: Dict[str, Any]) -> bool:
    """One line of `cursor-agent -p --output-format stream-json`."""
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(ev, dict) or "type" not in ev:
        return False
    t = ev["type"]
    if t == "tool_call":
        from .cursor_brain import hearth_view, _result_text
        name, shown, body = hearth_view(ev.get("tool_call") or {})
        key = str(ev.get("call_id") or "")
        if name and ev.get("subtype") == "started":
            _step(emit, state, key, name, shown)
        elif name and ev.get("subtype") == "completed":
            if key not in state.get("open", {}):
                _step(emit, state, key, name, shown)
            _step_done(emit, state, key, name, _result_text(body))
    elif t == "result":
        state["ok"] = not ev.get("is_error")
        state["result"] = str(ev.get("result") or "")
        if not state["ok"]:
            state["error"] = state["result"] or str(ev.get("subtype") or "the agent stopped with an error")
    return True


def _parse_claude_line(line: str, emit, state) -> bool:
    _parse_claude(line, emit, state)
    return line.lstrip().startswith("{")


# Agents that report each step: the name of their output format -> its reader.
_READERS = {"claude": _parse_claude_line, "codex": _parse_codex, "cursor": _parse_cursor}


def spawn(agent: str, prompt: str, *, name: str = "", cwd: str = "",
          on_done: Optional[Callable[[str, Dict[str, Any]], None]] = None,
          task: str = "", by: str = "") -> Dict[str, Any]:
    """Start an external agent in the background. Returns {ok, agent_id} or {ok: False, error}.
    `task` is what was asked in the asker's own words, when `prompt` wraps it
    in a brief; `by` is who asked."""
    from . import subagents as _sa
    spec = AGENTS.get(agent)
    if not spec:
        return {"ok": False, "error": f"unknown agent '{agent}'. Known: {', '.join(AGENTS)}"}
    exe = find(agent)
    if not exe:
        return {"ok": False, "error": f"{spec['label']} is not installed on this machine"}
    work = _long_path(os.path.abspath(cwd or _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")))
    if not os.path.isdir(work):
        return {"ok": False, "error": f"folder not found: {work}"}
    allow = _allow_level()
    if spec.get("auto_only") and allow != "auto":
        return {"ok": False, "error": f"{spec['label']} approves everything it does when run this way, so "
                                      "it only runs with the approval switch on Auto"}
    args = spec["args"](allow)
    head = _direct_argv(exe)
    if spec.get("prompt_arg") and head[0].lower().endswith((".cmd", ".bat")):
        return {"ok": False, "error": f"{spec['label']} is installed as a script Hearth can't start safely "
                                      "with a prompt; reinstall it from npm or its own installer"}
    argv = head + args + ([prompt] if spec.get("prompt_arg") else [])
    agent_id = f"sub_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    tpath = _sa._transcript_path(agent_id)
    name = _sa.helper_name(name, spec["label"], agent)
    _sa._append_transcript(tpath, {"kind": "start", "persona": spec["label"], "name": name,
                                   "prompt": task or prompt, "runtime": agent, "cwd": work, "by": by})
    _sa._append_transcript(tpath, {"kind": "route", "runtime": agent, "exe": exe, "allow": allow,
                                   "billing": spec.get("billing", "")})
    with _sa._BG_RESULTS_LOCK:
        _sa._BG_RUNNING.add(agent_id)

    def run() -> None:
        state: Dict[str, Any] = {"pending": [], "tools": 0}
        out_lines: List[str] = []
        emit = lambda rec: _sa._append_transcript(tpath, rec)
        try:
            proc = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    creationflags=_NO_WINDOW,
                                    # A helper does its task; it doesn't send helpers of its own.
                                    env=dict(os.environ, HEARTH_HELPER="1"))
            listening = bool(spec.get("takes_messages"))
            try:
                if listening:
                    proc.stdin.write(_user_line(prompt))
                    proc.stdin.flush()
                    with _OPEN_LOCK:
                        _OPEN[agent_id] = proc
                else:
                    if not spec.get("prompt_arg"):
                        proc.stdin.write(prompt)
                    proc.stdin.close()
            except OSError:
                pass
            read = _READERS.get(spec["stream"])
            for line in proc.stdout:
                line = line.rstrip("\n")
                if read and read(line, emit, state):
                    if listening and "result" in state:
                        # It has answered. Closing stdin is what lets it exit.
                        listening = False
                        with _OPEN_LOCK:
                            _OPEN.pop(agent_id, None)
                            try:
                                proc.stdin.close()
                            except OSError:
                                pass
                else:
                    # Colour codes are for a terminal; here they would land in the result as noise.
                    line = _ANSI.sub("", line)
                    if line.strip():
                        out_lines.append(line)
            code = proc.wait()
            if "ok" in state:
                ok = bool(state.get("ok")) and code == 0
                text = state.get("result") or state.get("last_text", "")
                err = "" if ok else (state.get("error") or why_failed(out_lines) or f"exited with code {code}")
            elif spec["stream"] == "claude":
                ok, text = False, state.get("last_text", "")
                err = why_failed(out_lines) or f"exited with code {code}"
            else:
                text = "\n".join(out_lines[-200:])
                ok = code == 0
                err = "" if ok else (why_failed(out_lines) or f"exited with code {code}")
            _note_refused(agent, "" if ok else err)
        except Exception as e:
            ok, text, err = False, "", f"{type(e).__name__}: {e}"
        with _OPEN_LOCK:
            _OPEN.pop(agent_id, None)
        if text:
            emit({"kind": "assistant_final", "text": text[:4000]})
        result = {"ok": ok, "text": text, "error": err, "persona": spec["label"], "name": name or spec["label"],
                  "turns": state.get("tools", 0), "used_tools": [], "transcript_path": str(tpath)}
        emit({"kind": "end", **{k: v for k, v in result.items() if k != "text"}})
        with _sa._BG_RESULTS_LOCK:
            _sa._BG_RUNNING.discard(agent_id)
            _sa._BG_RESULTS[agent_id] = result
        cb = on_done or _sa._BG_COMPLETION_CB
        if cb:
            try:
                cb(agent_id, result)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name=f"external-{agent}").start()
    return {"ok": True, "agent_id": agent_id, "agent": agent, "label": spec["label"], "maker": spec.get("maker", ""),
            "tested": agent in VERIFIED, "billing": spec.get("billing", "")}
