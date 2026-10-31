"""An MCP server that can't start says why in one plain sentence, keeps the raw
detail for a click, and prints nothing of its own into the terminal. Nothing
shipped writes an example server that would fail.

    .\\.venv\\Scripts\\python.exe tests\\test_mcp_plain_error.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import mcp_client  # noqa: E402

# ---------------------------------------------------------------- the sentence
grp = BaseExceptionGroup("unhandled errors in a TaskGroup", [FileNotFoundError(2, "No such file", "npx")])
err, detail = mcp_client.plain_failure(grp, "", "npx")
check(f"a missing program inside a TaskGroup error reads plainly ({err!r})", err == "Couldn't start: npx can't be found")
check("the raw error stays in the detail", "ExceptionGroup" in detail and "FileNotFoundError" in detail)

closed = BaseExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("Connection closed")])
err, detail = mcp_client.plain_failure(closed, '"node" is not recognized as an internal or external command,\n'
                                               'operable program or batch file.\n', "npx")
check(f"Windows' 'not recognized' names the missing program ({err!r})", err == "Couldn't start: node can't be found")
check("and the detail keeps what the server printed", "is not recognized" in detail)
err, _ = mcp_client.plain_failure(closed, "/usr/bin/env: 'node': No such file or directory\n", "npx")
check(f"so does env's 'No such file' ({err!r})", err == "Couldn't start: node can't be found")
err, _ = mcp_client.plain_failure(closed, "", "server")
check(f"a server that just exits says so ({err!r})", err == "Couldn't start: it stopped right away")
check("no sentence carries an exception class name",
      all("Error" not in mcp_client.plain_failure(e, "", "x")[0] and "Group" not in mcp_client.plain_failure(e, "", "x")[0]
          for e in (grp, closed, BaseExceptionGroup("g", [TimeoutError()]), BaseExceptionGroup("g", [ValueError("bad")]))))

# ---------------------------------------------------------------- a real spawn
# A server whose launcher calls a program that isn't there, as npx does when
# node is missing from PATH. Run in a child so its stderr can be inspected.
work = tempfile.mkdtemp(prefix="hearth-mcperr-")
if os.name == "nt":
    launcher = os.path.join(work, "fake-npx.cmd")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write("@echo off\r\nhearth-missing-node-xyz %*\r\n")
else:
    launcher = os.path.join(work, "fake-npx")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexec hearth-missing-node-xyz \"$@\"\n")
    os.chmod(launcher, 0o755)
child = (
    "import asyncio, json, sys; sys.path.insert(0, %r)\n"
    "from hearth import mcp_client as m\n"
    "async def go():\n"
    "    await asyncio.wait_for(m._connect_one('example', {'command': %r, 'args': []}), 40)\n"
    "asyncio.run(go())\n"
    "print(json.dumps(m.list_bridges()))\n"
) % (ROOT, launcher)
p = subprocess.run([sys.executable, "-X", "utf8", "-c", child], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=90)
try:
    bridges = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    bridges = []
b = bridges[0] if bridges else {}
check(f"the failed server is listed as an error ({b.get('state')})", b.get("state") == "error")
check(f"its reason names the missing program ({b.get('error')!r})",
      b.get("error") == "Couldn't start: hearth-missing-node-xyz can't be found")
check("the raw detail is kept", "hearth-missing-node-xyz" in (b.get("detail") or ""))
check(f"nothing the server printed reached the terminal ({p.stderr.strip()[:120]!r})",
      "hearth-missing-node-xyz" not in p.stderr and "not recognized" not in p.stderr)

# ---------------------------------------------------------------- shipped defaults
shipped = [os.path.join(ROOT, "hearth", "ui.html"), os.path.join(ROOT, "hearth_cli.py")]
shipped += [p for p in glob.glob(os.path.join(ROOT, "hearth", "**", "*.py"), recursive=True)]
hits = [os.path.relpath(f, ROOT) for f in shipped if "example-filesystem" in open(f, encoding="utf-8", errors="replace").read()]
check(f"no example server is shipped {hits}", not hits)
for cfg in glob.glob(os.path.join(ROOT, "**", "mcp.json"), recursive=True):
    if any(s in cfg for s in (".venv", "node_modules", ".git")):
        continue
    try:
        servers = (json.load(open(cfg, encoding="utf-8")) or {}).get("mcpServers") or {}
    except Exception:
        servers = {}
    check(f"{os.path.relpath(cfg, ROOT)} ships no servers", not servers)

# The terminal's /mcp edit writes an empty config when there is none.
import hearth_cli  # noqa: E402
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = work
import hearth.tools as _tools  # noqa: E402
_tools.WORKSPACE = work
os.environ["EDITOR"] = sys.executable + "-not-an-editor"
cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
try:
    asyncio.run(hearth_cli.JarvisCLI.handle_command(cli, "/mcp edit"))
except Exception as e:
    print("  /mcp edit raised", type(e).__name__, e)
path = os.path.join(work, "mcp.json")
written = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else None
check(f"/mcp edit starts from an empty config ({written})", written == {"mcpServers": {}})

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all MCP error checks passed")
