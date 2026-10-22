"""An agent program sent into a git repository works on its own copy.

Before, two coding helpers sent into one repository edited the same working
folder at the same time, and nothing they did could be held back or undone."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-wt-")
os.environ.update(JARVIS_WORKSPACE=os.path.join(work, "ws"), HEARTH_WORKSPACE=os.path.join(work, "ws"),
                  HEARTH_NO_ACTIVITY_LOG="1")
os.makedirs(os.path.join(work, "ws"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import external_agents as xa, helper_changes as hc      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


repo = os.path.join(work, "proj")
os.makedirs(repo)
git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo,
                                capture_output=True, text=True)
Path(repo, "app.py").write_text("print('hi')\n", encoding="utf-8")
Path(repo, "readme.txt").write_text("v1\n", encoding="utf-8")
git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "start")
Path(repo, "readme.txt").write_text("v2, not committed yet\n", encoding="utf-8")

# A coding agent that reads the readme, edits app.py and adds a file where it is started.
coder = ("import os, pathlib; print('saw: ' + pathlib.Path('readme.txt').read_text().strip()); "
         "pathlib.Path('app.py').write_text(pathlib.Path('app.py').read_text() + '# by ' + os.environ.get('WHO','?') + '\\n'); "
         "pathlib.Path('new.py').write_text('x = 1\\n'); print('done in ' + os.getcwd())")
xa.AGENTS["stand-in-coder"] = {"label": "Coder", "exe": ["stand-in"], "args": lambda allow: ["-c", coder],
                               "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)


def start(name):
    done, got = threading.Event(), {}
    os.environ["WHO"] = name
    r = xa.spawn("stand-in-coder", "edit app.py", name=name, cwd=repo,
                 on_done=lambda i, res: (got.update(res), done.set()))
    return r, done, got


(r1, d1, g1), (r2, d2, g2) = start("Noah"), start("Lin")
d1.wait(60); d2.wait(60)
a, b = r1["agent_id"], r2["agent_id"]
check(f"both ran, each in its own copy {g1.get('text', '')[-80:]!r}",
      g1.get("ok") and g2.get("ok") and "done in" in g1.get("text", "")
      and os.path.abspath(repo) not in g1.get("text", "").split("done in")[-1])
check("the copy started from the folder as it is, uncommitted changes included",
      "saw: v2, not committed yet" in g1.get("text", ""))
check("nothing reached the real folder yet",
      Path(repo, "app.py").read_text(encoding="utf-8") == "print('hi')\n" and not Path(repo, "new.py").exists())
s1 = hc.summary(a)
check(f"its changes wait, listed {s1}", s1["state"] == "waiting" and sorted(s1["files"]) == ["app.py", "new.py"])
check("JARVIS hears they are waiting", "separate copy" in g1.get("text", ""))
shown = hc.act(a, "show")
check("show gives the diff", "+# by Noah" in shown.get("diff", ""))

got = hc.act(a, "bring_in")
check(f"bringing in applies them to the folder {got}",
      got.get("ok") and "# by Noah" in Path(repo, "app.py").read_text(encoding="utf-8") and Path(repo, "new.py").exists()
      and Path(repo, "readme.txt").read_text(encoding="utf-8") == "v2, not committed yet\n")
clash = hc.act(b, "bring_in")
check(f"the second one's overlapping change is caught, not written over {clash}", not clash.get("ok"))
check(f"so it can be thrown away instead {hc.act(b, 'discard')}", hc.summary(b)["state"] == "discarded")
back = hc.act(a, "undo")
check(f"undo takes the brought-in changes back {back}",
      back.get("ok") and Path(repo, "app.py").read_text(encoding="utf-8") == "print('hi')\n" and not Path(repo, "new.py").exists())
left = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout.strip().splitlines()
check(f"no copies are left behind {left}", len(left) == 1)

# A race: the same task to two agents, each on its own copy, then one comparison.
from hearth import team, subagents   # noqa: E402
subagents.drain_pending_notifications()
# PowerShell runs a quoted program with &; bash runs it as is.
check_cmd = ("& " if sys.platform == "win32" else "") + \
    f'"{sys.executable}" -c "import os,sys; sys.exit(0 if os.path.exists(\'new.py\') else 1)"'
r = team.race("edit app.py", ["stand-in-coder", "stand-in-coder"], repo, check_cmd)
check(f"a race starts each agent on its own copy {r}", r.get("ok") and [x["name"] for x in r["racing"]] == ["Coder 1", "Coder 2"])
report = ""
for _ in range(120):
    got = [n for n in subagents.drain_pending_notifications() if n.get("persona") == "race"]
    if got:
        report = got[0]["result_text"]
        break
    import time as _time
    _time.sleep(0.5)
check(f"when both finish, the check runs in each copy and one comparison comes back {report!r}",
      report.count("check passed") == 2 and "Coder 1" in report and "Coder 2" in report and "helper_changes" in report)
check("a race outside a git repository is refused",
      "git repository" in team.race("x", ["stand-in-coder", "stand-in-coder"], os.path.join(work, "ws")).get("error", ""))
for x in r.get("racing", []):
    hc.act(x["agent_id"], "discard")

# Outside a repository it works in the folder, and hears who else is there.
plain = os.path.join(work, "plain")
os.makedirs(plain)
w1, note1 = hc.isolate("sub_9_aaaa", plain, "Ada", "tidy the notes")
w2, note2 = hc.isolate("sub_9_bbbb", plain, "Bo", "sum up the notes")
check(f"a shared plain folder says who else works there {note2!r}", w1 == plain and w2 == plain and "Ada" in note2)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all helper worktree checks passed")
