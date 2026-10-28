"""The thinking level reaches every agent brain that takes one, with that
agent's own levels, and isn't offered where the agent takes none.

ACP agents: a session option of category thought_level is the control, read
again after each model switch; an agent's verbose model list gives every
model's levels; a start flag's values come from its --help. Gemini CLI sends
its models as a separate list. A stand-in program that only says the real
CLI isn't installed is passed over for the real one."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-think-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import acp_brain as ab, claude_brain as cb, external_agents as xa, web      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ab._MODELS = os.path.join(work, "acp_models.json")
cb._HOME = work
cb._SESSIONS = os.path.join(work, "sessions.json")
cb.ensure_bridge = lambda: None

# A stand-in ACP agent. It offers an effort option only once the model is
# "deep", the way OpenCode does for models with variants; as "gem" it sends its
# models the way Gemini CLI does. Every message it gets is logged.
log = os.path.join(work, "log.jsonl")
agent = os.path.join(work, "agent.py")
Path(agent).write_text(r'''
import json, sys
mode, log = sys.argv[1], open(sys.argv[2], "a")
log.write(json.dumps({"argv": sys.argv[3:]}) + "\n"); log.flush()
if "--help" in sys.argv:
    print("  --reasoning-effort <level>\n      Set the effort [possible values: none, low, high]")
    sys.exit(0)
out = lambda o: (sys.stdout.write(json.dumps({"jsonrpc": "2.0", **o}) + "\n"), sys.stdout.flush())
note = lambda sid, u: out({"method": "session/update", "params": {"sessionId": sid, "update": u}})
model = "plain"
def opts():
    o = [{"id": "model", "category": "model", "type": "select", "currentValue": model,
          "options": [{"value": "plain", "name": "Plain"}, {"value": "deep", "name": "Deep"}]}]
    if model == "deep":
        o.append({"id": "effort", "category": "thought_level", "type": "select", "currentValue": "low",
                  "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High"},
                              {"value": "default", "name": "Default"}]})
    return o
for raw in sys.stdin:
    m = json.loads(raw)
    log.write(raw); log.flush()
    meth, p = m.get("method"), m.get("params") or {}
    if meth == "initialize":
        out({"id": m["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif meth == "session/new":
        if mode == "gem":
            out({"id": m["id"], "result": {"sessionId": "s1", "models": {"currentModelId": "g-pro",
                 "availableModels": [{"modelId": "g-pro", "name": "Pro"}, {"modelId": "g-flash", "name": "Flash"}]}}})
        elif mode == "flag":
            out({"id": m["id"], "result": {"sessionId": "s1", "configOptions": []}})
        else:
            out({"id": m["id"], "result": {"sessionId": "s1", "configOptions": opts()}})
    elif meth == "session/set_config_option":
        if p["configId"] == "model":
            model = p["value"]
        out({"id": m["id"], "result": {"configOptions": opts()}})
    elif meth == "session/set_model":
        out({"id": m["id"], "result": {}})
    elif meth == "session/prompt":
        note(p["sessionId"], {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}})
        out({"id": m["id"], "result": {"stopReason": "end_turn"}})
''', encoding="utf-8")

for pid, mode, extra in (("think-acp", "think", {}), ("gem-acp", "gem", {}),
                         ("flag-acp", "flag", {"effort_flag": "--reasoning-effort"})):
    cb.ACP[pid] = {"label": pid, "plan": pid, "site": "https://example.org", "agent": pid,
                   "args": [agent, mode, log], **extra}
    cb.BRAINS[pid] = pid
    cb.SITES[pid] = "https://example.org"
    xa.AGENTS[pid] = {"label": pid, "exe": [pid], "args": lambda allow: [], "stream": "text"}
# The flag one is started as its own program, so its --help is its own.
flag_cmd = os.path.join(work, "flag-acp.cmd")
Path(flag_cmd).write_text(f'@"{sys.executable}" "{agent}" flag "{log}" %*\r\n', encoding="utf-8")
cb.ACP["flag-acp"]["args"] = []
real_which = xa._which
xa._which = lambda names: (flag_cmd if names == ["flag-acp"] else sys.executable) if names and names[0].endswith("-acp") \
    else real_which(names)
xa._which_all = (lambda real: lambda names: [] if names and names[0].endswith("-acp") else real(names))(xa._which_all)


def settings(**kv):
    s = web._load_settings()
    s.update(kv)
    web._save_settings(s)


def sent():
    return [json.loads(l) for l in open(log, encoding="utf-8")]


def turn(provider):
    open(log, "w").close()
    cb.current = lambda: provider
    events = []
    asyncio.run(ab.run_turn("hi", emit=lambda t, **f: events.append((t, f)), history=[],
                            permission_check=lambda n, a: "allow"))
    return events


# 1. The effort option shows up after the model switch and is set to the level.
settings(onboarded=True, think_default=True, think_level="high", **{"think-acp_model": "deep"})
ev = turn("think-acp")
msgs = sent()
sets = [m["params"] for m in msgs if m.get("method") == "session/set_config_option"]
check(f"after switching to a model with levels, the level is set {sets}",
      {"configId": "effort", "value": "high"} in [{k: s[k] for k in ("configId", "value")} for s in sets])
check("the reply still comes back", any(t == "assistant" and f["content"] == "ok" for t, f in ev))
info = ab.models("think-acp")
deep = next((m for m in info if m["id"] == "deep"), {})
plain = next((m for m in info if m["id"] == "plain"), {})
check(f"the model menu has the levels the agent offered for that model {deep.get('efforts')}",
      deep.get("efforts") == ["low", "high"])
check(f"a model the agent offered no levels for has none, so no control {plain.get('efforts')}",
      plain.get("efforts") == [])

# 2. A level that model doesn't take sends the agent's own default, not a stale one.
settings(think_level="max")
turn("think-acp")
sets = [m["params"] for m in sent() if m.get("method") == "session/set_config_option" and m["params"]["configId"] == "effort"]
check(f"a level the model doesn't offer goes back to its default {sets}", [s["value"] for s in sets] == ["default"])

# 3. Gemini CLI style: models as their own list, set with session/set_model.
settings(**{"gem-acp_model": "g-flash"})
turn("gem-acp")
gm = [m for m in sent() if m.get("method") == "session/set_model"]
check(f"an agent that lists models outside its options gets the model set {gm}",
      gm and gm[0]["params"]["modelId"] == "g-flash")
gem = ab.models("gem-acp")
check(f"its models are in the menu, with no levels {[(m['id'], m['efforts']) for m in gem]}",
      [m["id"] for m in gem] == ["", "g-pro", "g-flash"] and not any(m["efforts"] for m in gem))

# 4. A start flag: its levels from --help, passed when the process starts.
settings(think_level="high")
turn("flag-acp")
starts = [m["argv"] for m in sent() if "argv" in m]
check(f"an agent that takes the level as a start flag gets it {starts}",
      any(a[-2:] == ["--reasoning-effort", "high"] for a in starts))
flag = ab.models("flag-acp")
check(f"its levels come from its own --help {flag}", flag and flag[0]["efforts"] == ["none", "low", "high"])
settings(think_level="xhigh")
turn("flag-acp")
starts = [m["argv"] for m in sent() if "argv" in m and "--help" not in m["argv"]]
check(f"a level it doesn't list isn't passed {starts}", starts and "--reasoning-effort" not in starts[-1])

# 5. A verbose model listing: id lines, each followed by its JSON details.
listing = ('opencode/big-pickle\n{\n  "id": "big-pickle",\n  "variants": {}\n}\n'
           'openrouter/openai/gpt-5.1\n{\n  "id": "gpt-5.1",\n  "name": "GPT {5.1}",\n'
           '  "variants": {"none": {}, "low": {"reasoningEffort": "low"}, "high": {}}\n}\n')
check(f"each model's levels are read from the verbose listing {ab.parse_variants(listing)}",
      ab.parse_variants(listing) == {"opencode/big-pickle": [], "openrouter/openai/gpt-5.1": ["none", "low", "high"]})
check("a flag's values read from help in either spelling",
      ab.flag_levels('--effort <e>  (choices: "low", "high")', "--effort") == ["low", "high"])

# 6. A program on PATH that only says the real CLI isn't there is passed over.
fake_dir, real_dir = os.path.join(work, "shim"), os.path.join(work, "real")
os.makedirs(fake_dir); os.makedirs(real_dir)
for d, text, code in ((fake_dir, "Copy CLI was not found. Run it to install.", 127), (real_dir, "Copy CLI 1.0", 0)):
    Path(d, "copy-cli.py").write_text(f"import sys; print({text!r}); sys.exit({code})", encoding="utf-8")
    Path(d, "copy-cli.cmd").write_text(f'@"{sys.executable}" "%~dp0copy-cli.py" %*\r\n', encoding="utf-8")
xa.AGENTS["copy-cli"] = {"label": "Copy", "exe": ["copy-cli"], "args": lambda allow: [], "stream": "text"}
os.environ["PATH"] = fake_dir + os.pathsep + real_dir + os.pathsep + os.environ.get("PATH", "")
xa._which_all = xa.__dict__["_which_all"]
import importlib                                                     # noqa: E402
fresh = importlib.reload(xa)
fresh.AGENTS["copy-cli"] = {"label": "Copy", "exe": ["copy-cli"], "args": lambda allow: [], "stream": "text"}
hit = fresh.find("copy-cli") or ""
check(f"the stand-in that says the CLI wasn't found is skipped for the real one {hit}",
      os.path.dirname(hit) == real_dir)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all ACP thinking level checks passed")
