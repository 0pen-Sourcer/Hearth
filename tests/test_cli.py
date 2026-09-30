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

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal checks passed")
