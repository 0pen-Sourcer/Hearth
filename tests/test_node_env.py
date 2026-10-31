"""Node is found by full path, and children get a PATH cmd.exe can read.

One PATH entry with a stray quote hides every entry after it from cmd.exe, so
npm agents, their updates and npx MCP servers said node "is not recognized"
on a PC that has it. Here PATH holds such an entry and node sits behind it.

    .\\.venv\\Scripts\\python.exe tests\\test_node_env.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import node_env, external_agents as xa      # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


tmp = tempfile.mkdtemp(prefix="hearth-node-")
fake = os.path.join(tmp, "nodejs")
os.makedirs(fake)
for n in ("node.exe" if sys.platform == "win32" else "node", "npm.cmd", "npx.cmd"):
    open(os.path.join(fake, n), "w").close()

real_path = os.environ.get("PATH", "")
real_dirs = node_env.candidate_dirs
try:
    # The broken entry is the node folder itself, quoted on one side only.
    os.environ["PATH"] = os.pathsep.join([r"C:\Windows\system32", fake + '"', r"C:\Windows", ""])
    node_env.candidate_dirs = lambda: []
    node_env.forget()

    check("clean_path drops quotes and blanks",
          node_env.clean_path(os.environ["PATH"]) == [r"C:\Windows\system32", fake, r"C:\Windows"])
    check("node is found behind a quoted entry",
          os.path.normcase(node_env.node() or "") == os.path.normcase(os.path.join(fake, "node.exe")))
    check("npm is found beside node", (node_env.npm() or "").lower().endswith("npm.cmd"))
    check("an npm agent can be installed", xa.how_to_get("gemini-cli") == "install")

    env = node_env.child_env()
    first = env["PATH"].split(os.pathsep)[0]
    check("children's PATH starts with node's folder", os.path.normcase(first) == os.path.normcase(fake))
    check("children's PATH has no quotes", '"' not in env["PATH"])
    check("a bare npx in mcp.json runs by full path",
          os.path.normcase(node_env.resolve("npx")) == os.path.normcase(os.path.join(fake, "npx.cmd")))
    check("other commands are left alone", node_env.resolve("uvx") == "uvx")
    check("a command with a folder is left alone", node_env.resolve(r"C:\x\npx.cmd") == r"C:\x\npx.cmd")

    # Nothing on PATH: the usual install folders are looked in.
    os.environ["PATH"] = r"C:\Windows\system32"
    node_env.candidate_dirs = lambda: [os.path.join(tmp, "missing"), fake]
    node_env.forget()
    check("node is found in an install folder off PATH",
          os.path.normcase(node_env.node() or "") == os.path.normcase(os.path.join(fake, "node.exe")))
finally:
    os.environ["PATH"] = real_path
    node_env.candidate_dirs = real_dirs
    node_env.forget()

# The real thing: cmd.exe with a stray quote ahead of node's folder.
real_node = shutil.which("node")
if sys.platform == "win32" and real_node:
    nd = os.path.dirname(real_node)
    broken = os.pathsep.join([r"C:\Windows\system32", r"C:\Nowhere" + '"', nd])
    old = subprocess.run(["cmd", "/c", "node --version"], capture_output=True, text=True,
                         env=dict(os.environ, PATH=broken))
    check("cmd can't find node with the broken PATH (the failure)", old.returncode != 0)
    node_env.forget()
    new = subprocess.run(["cmd", "/c", "node --version"], capture_output=True, text=True,
                         env=node_env.child_env(dict(os.environ, PATH=broken)))
    check("cmd finds node with the child PATH", new.returncode == 0 and new.stdout.strip().startswith("v"))
else:
    print("skip  no node here to run through cmd")

shutil.rmtree(tmp, ignore_errors=True)
print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
