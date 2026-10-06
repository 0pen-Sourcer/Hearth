"""run_command and background jobs: output and exit status come back true, and
bash is there for bash syntax.

Windows PowerShell 5.1 wrote redirected output in the console code page, so a
file named in Hindi came back as "???? ???.txt" and an accented one as
"caf\\ufffd.txt"; a child Python crashed printing non-ASCII into the pipe; a
command that failed with an ordinary error still exited 0; and bash syntax,
which models write most, had nowhere to run.

Runs real commands in a throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_run_command.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

WS = tempfile.mkdtemp(prefix="hearth-runcmd-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T, shells, jobs                          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


if sys.platform != "win32":
    print("the shells checked here are Windows ones; nothing to do")
    sys.exit(0)

sub = os.path.join(WS, "sub")
os.makedirs(sub)
NAMES = ["मेरा नोट.txt", "café.txt", "我的狗.txt"]
for n in NAMES:
    with open(os.path.join(sub, n), "w", encoding="utf-8") as f:
        f.write("x")


def run(cmd, **kw):
    return T.execute_tool("run_command", {"command": cmd, "cwd": WS, **kw})


def code(out):
    m = re.search(r"\[exit (-?\d+)\]", out)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------ text comes back
out = run("Get-ChildItem sub | Select-Object -ExpandProperty Name")
check(f"file names in any script come back as they are: {out.splitlines()[2:5]}",
      all(n in out for n in NAMES))
out = run("python -c \"print('héllo नमस्ते 你好')\"")
check("a Python child prints non-ASCII instead of crashing",
      code(out) == 0 and "héllo नमस्ते 你好" in out)
out = run("echo héllo", shell="cmd")
check("cmd output is UTF-8 too", "héllo" in out)

# -------------------------------------------------------- failure is failure
out = run("Get-Item .\\no_such_file.txt")
check(f"a cmdlet that fails exits non-zero (exit {code(out)})", code(out) not in (0, None))
out = run("export FOO=bar; echo still-ran")
check(f"an error earlier in the line is not hidden by what ran after it (exit {code(out)})",
      code(out) not in (0, None) and "still-ran" in out)
out = run("cmd /c exit 3")
check("a program's own exit code is passed through", code(out) == 3)
out = run("Write-Output ok")
check("and success is still 0", code(out) == 0 and "ok" in out)

# ------------------------------------------------------------------- bash
if shells.bash_path():
    out = run("echo one && echo two | tr a-z A-Z && export X=5 && echo $X", shell="bash")
    check("bash runs bash syntax", code(out) == 0 and "one" in out and "TWO" in out
          and out.rstrip().endswith("5"))
    out = run("ls sub", shell="bash")
    check("bash lists names in any script too", all(n in out for n in NAMES))
    out = run("false", shell="bash")
    check("and a failing bash command exits non-zero", code(out) == 1)
    desc = next(t for t in T.TOOL_DEFINITIONS if t["name"] == "run_command")["description"]
    check("the tool tells the model bash is here", "shell='bash'" in desc)
else:
    print("skip  bash is not installed on this machine")

# `python` is pointed at Hearth's own interpreter; that rewrite used
# PowerShell's call operator in every shell, a syntax error in cmd and bash.
out = run("python -c \"print(6*7)\"", shell="cmd")
check(f"python runs under cmd (exit {code(out)})", code(out) == 0 and "42" in out)
if shells.bash_path():
    out = run("python -c 'print(6*7)'", shell="bash")
    check(f"and under bash (exit {code(out)})", code(out) == 0 and "42" in out)

out = run("echo hi", shell="fish")
check(f"an unknown shell is refused with the ones there are: {out[:90]!r}",
      "unknown shell" in out and "powershell" in out)

# -------------------------------------------------------------- background
r = jobs.start_job("Get-ChildItem sub | Select-Object -ExpandProperty Name", cwd=WS)
job = r.get("job_id")
st = {}
for _ in range(100):
    st = jobs.get_job(job, tail_lines=10)
    if st.get("status") in ("completed", "failed"):
        break
    time.sleep(0.1)
tail = str(st.get("tail") or st.get("output") or st)
check(f"a background job's output keeps the names too ({st.get('status')})",
      st.get("status") == "completed" and all(n in tail for n in NAMES))
r = jobs.start_job("Get-Item .\\missing_again.txt", cwd=WS)
for _ in range(100):
    st = jobs.get_job(r["job_id"])
    if st.get("status") in ("completed", "failed"):
        break
    time.sleep(0.1)
check("and a job that fails is marked failed", st.get("status") == "failed")

# Every sleep was refused, so a script that starts a server and waits three
# seconds before asking it something could not run at all.
out = run("Start-Sleep -Milliseconds 300; Write-Output after-the-wait", shell="powershell")
check(f"a short wait inside a command runs {out[:80]!r}", "after-the-wait" in out and "refused" not in out)
check("a wait of more than a minute is refused, with what to do instead",
      "refused" in run("Start-Sleep 120") and "set_reminder" in run("Start-Sleep 120"))
check("waiting for a keypress or a typed answer is refused", "refused" in run("pause") and "refused" in run("Read-Host name"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all run_command checks passed")
