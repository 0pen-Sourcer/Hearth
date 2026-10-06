"""The terminal, run as a real process.

It has its own agent loop rather than the one the window and the phone bridges
share, so a fix made there does not reach it, and the only honest way to check
it is to start it.

End of input is how Ctrl+Z and Ctrl+D ask to leave, and it is also what a
closed console or a finished pipe looks like. The loop had an exit written for
it that nothing could reach: the input race caught every Exception, EOFError
is one, and it came back as an empty line. The loop re-prompted, met the same
end of input at once, and spun. Measured at twenty thousand prompts in
twenty-five seconds, never exiting, until it was killed.

And there was no argument handling at all, so --help and --version started the
whole session and sat at a prompt.

    .\\.venv\\Scripts\\python.exe tests\\test_cli.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "hearth_cli.py")

FAILS: list = []


def hearth_strip(text: str) -> str:
    """The output without its colour codes."""
    import re as _re
    return _re.sub(r"\x1b\[[0-9;]*m", "", text)


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def run(args, stdin=subprocess.DEVNULL, timeout=120, home=None, extra_env=None):
    # A scratch workspace and no server, so this touches nothing real and does
    # not depend on a model being loaded.
    env = dict(os.environ)
    if home:
        env["USERPROFILE"] = env["HOME"] = home
    env.pop("JARVIS_LOCKDOWN", None)
    env["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-cli-test-")
    env["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"
    env["JARVIS_NO_ONBOARDING"] = "1"
    env["HEARTH_NO_UPDATE_CHECK"] = "1"
    env.update(extra_env or {})
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        feed = stdin if isinstance(stdin, bytes) else None
        p = subprocess.run([sys.executable, "-X", "utf8", CLI] + args,
                           input=feed, stdin=None if feed is not None else stdin,
                           capture_output=True, timeout=timeout,
                           env=env, cwd=ROOT)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace"), time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace")
        return None, out, time.time() - t0


# ------------------------------------------------------------------- flags
code, out, secs = run(["--help"], timeout=60)
check("--help exits cleanly", code == 0)
check("and prints usage rather than starting a session",
      "usage:" in out and "❯" not in out)
check(f"and does it without loading everything first ({secs:.1f}s)", secs < 20)

code, out, _ = run(["-h"], timeout=60)
check("-h does the same", code == 0 and "usage:" in out)

code, out, _ = run(["--version"], timeout=60)
check("--version exits cleanly", code == 0)
check(f"and names the version: {out.strip()[:40]!r}",
      out.strip().startswith("hearth ") and "unknown" not in out)

# ------------------------------------------------------------- end of input
code, out, secs = run([], stdin=subprocess.DEVNULL, timeout=150)
prompts = out.count("❯")
check(f"end of input ends the session instead of spinning ({secs:.0f}s)",
      code == 0)
check(f"it asked once, not over and over ({prompts} prompt(s))", prompts <= 3)
check("and says goodbye on the way out", "bye" in out)

# ---------------------------------------------------------------- lockdown
# /lockdown flipped a flag the read check never looked at: it printed
# "reads confined" and every read still reached the whole disk.
home = tempfile.mkdtemp(prefix="hearth-cli-home-")
code, out, _ = run([], stdin=b"/lockdown on\n", timeout=150, home=home)
check("/lockdown on reports it is on", "lockdown ON" in out)
probe = subprocess.run(
    [sys.executable, "-X", "utf8", "-c",
     "from hearth import tools as t; print(t.read_locked()); "
     "import os; t._resolve_read(os.path.expanduser('~'))"],
    capture_output=True, cwd=ROOT,
    env={**{k: v for k, v in os.environ.items() if k != "JARVIS_LOCKDOWN"},
         "USERPROFILE": home, "HOME": home,
         "JARVIS_WORKSPACE": tempfile.mkdtemp(prefix="hearth-cli-ws-")})
said = (probe.stdout + probe.stderr).decode("utf-8", "replace")
check(f"and reads outside the workspace are refused afterwards ({said.splitlines()[:1]})",
      said.startswith("True") and "PermissionError" in said)
code, out, _ = run([], stdin=b"/lockdown status\n", timeout=150, home=home)
check("a new session still has it on", "lockdown ON" in out)
code, out, _ = run([], stdin=b"/lockdown off\n", timeout=150, home=home)
check("/lockdown off turns it off", "lockdown OFF" in out)

# -------------------------------------------------------------- local brains
# /brain lmstudio and /brain builtin were refused for want of an API key, and
# every local choice pinned the model to qwen2.5-7b-instruct whatever was loaded.
import http.server                                                  # noqa: E402
import json as _json                                                # noqa: E402
import threading                                                    # noqa: E402


class _Models(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        body = _json.dumps({"object": "list", "data": [{"id": "standin-9b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Models)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
code, out, _ = run([], stdin=b"/brain builtin\n", timeout=150, home=home,
                   extra_env={"JARVIS_BUILTIN_PORT": str(_srv.server_address[1])})
check("/brain builtin switches without asking for a key", "brain → builtin" in out
      and "no saved key" not in out)
check("and reports the model the server has loaded, not a pinned one",
      "standin-9b" in out and "qwen2.5-7b-instruct" not in out)
code, out, _ = run([], stdin=b"/brain lmstudio\n", timeout=150, home=home)
check("/brain lmstudio switches without asking for a key",
      "brain → lmstudio" in out and "no saved key" not in out)
_srv.shutdown()

# A key saved in the window is kept in settings.json. The terminal only looked
# in its own file, so the same provider asked for the key a second time.
_ws = tempfile.mkdtemp(prefix="hearth-cli-keys-")
with open(os.path.join(_ws, "settings.json"), "w", encoding="utf-8") as f:
    _json.dump({"llm_provider": "local", "provider_keys": {"anthropic": "saved-in-the-window"}}, f)
code, out, _ = run([], stdin=b"/brain anthropic\n", timeout=150, home=home, extra_env={"JARVIS_WORKSPACE": _ws})
check("/brain uses a key that was saved in the window", "brain → anthropic" in out and "no saved key" not in out)
with open(os.path.join(_ws, "settings.json"), encoding="utf-8") as f:
    _after = _json.load(f)
check("and switches to that provider with it",
      _after.get("llm_provider") == "anthropic" and _after.get("llm_key") == "saved-in-the-window"
      and "api.anthropic.com" in _after.get("llm_url", ""))

# /brain <provider> <model> saved the model name as the provider's API key, and
# the context size was always Anthropic's whatever the provider.
_ws2 = tempfile.mkdtemp(prefix="hearth-cli-brain-")
_or_key, _g_key = "sk-or-v1-" + "a" * 64, "AIza" + "B" * 35
with open(os.path.join(_ws2, "brain_keys.json"), "w", encoding="utf-8") as f:
    _json.dump({"openrouter": {"url": "https://openrouter.ai/api/v1", "key": _or_key, "model": ""},
                "gemini": {"url": "", "key": _g_key, "model": ""}}, f)
code, out, _ = run([], stdin=b"/brain openrouter qwen/qwen3.8-27b:free\n/brain cusror\n/brian gemini\n/brain gemini\n",
                   timeout=200, home=home, extra_env={"JARVIS_WORKSPACE": _ws2})
with open(os.path.join(_ws2, "brain_keys.json"), encoding="utf-8") as f:
    _keys = _json.load(f)
check("/brain openrouter <model> picks the model and keeps the saved key",
      "brain → openrouter" in out and "qwen/qwen3.8-27b:free" in out and _keys["openrouter"]["key"] == _or_key
      and "key saved" not in out)
check("a mistyped provider and a mistyped command get a suggestion",
      "Did you mean /brain cursor?" in hearth_strip(out) and "Did you mean /brain?" in hearth_strip(out))
check("Gemini's context is Gemini's, not Anthropic's", "context auto-set to 1,000,000" in out)

# /status: what is thinking, this chat, plan limits, the PC. Agent limits come
# from what the agent reported, so a stand-in file is written for one.
_ws3 = tempfile.mkdtemp(prefix="hearth-cli-status-")
_home3 = tempfile.mkdtemp(prefix="hearth-cli-status-home-")
os.makedirs(os.path.join(_home3, ".hearth"), exist_ok=True)
with open(os.path.join(_home3, ".hearth", "plan_limits.json"), "w", encoding="utf-8") as f:
    _json.dump({"claude-code": {"at": time.time() - 120, "windows": {"5 hours": {"used": 89, "resets_at": time.time() + 3600}}}}, f)
code, out, _ = run([], stdin=b"/status\n", timeout=150, home=_home3, extra_env={"JARVIS_WORKSPACE": _ws3})
plain = hearth_strip(out)
check("/status says what is thinking, how full the chat is and each plan's limits",
      "Thinking with" in plain and "This chat" in plain and "Claude Code" in plain and "89% used" in plain
      and "resets in" in plain)
check("/stauts is caught and the right one suggested", "Did you mean /status?" in hearth_strip(
      run([], stdin=b"/stauts\n", timeout=150, home=_home3, extra_env={"JARVIS_WORKSPACE": _ws3})[1]))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal checks passed")
