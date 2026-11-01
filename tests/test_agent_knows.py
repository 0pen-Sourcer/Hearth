"""The agent finds out what it can do and what's set up, and fixes what's missing.

The product grew past what the agent was told: it could not say what was installed,
a picture with nothing set up ended in "needs a key", and nothing it could call
installed an engine or a model. A turn runs here through the real loop against a
stand-in model that spoke only the OpenAI format, and the rest is checked on the
tools and the prompt every brain gets.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_agent_knows.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ---------------------------------------------------------------- a turn, for real

MODEL = "stand-in-model"
SEEN: list = []                # each request's tool names and its newest tool result
MOVES = [("load_tools", {"query": "select:generate_image"}),
         ("generate_image", {"prompt": "a red fox"})]


def _chunk(delta, finish=None):
    return ("data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "model": MODEL,
                                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


class _Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "not here"})
        msgs = req.get("messages") or []
        results = [m for m in msgs if m.get("role") == "tool"]
        SEEN.append({"tools": [t["function"]["name"] for t in req.get("tools") or []],
                     "system": next((m.get("content") for m in msgs if m.get("role") == "system"), ""),
                     "result": str(results[-1].get("content")) if results else ""})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        step = len(results)
        if step < len(MOVES):
            name, args = MOVES[step]
            self.wfile.write(_chunk({"role": "assistant", "tool_calls": [{
                "index": 0, "id": f"call_{step}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}))
            self.wfile.write(_chunk({}, "tool_calls"))
        else:
            self.wfile.write(_chunk({"role": "assistant", "content": "All done."}))
            self.wfile.write(_chunk({}, "stop"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
threading.Thread(target=srv.serve_forever, daemon=True).start()
env = dict(os.environ, LOCAL_API_BASE=f"http://127.0.0.1:{srv.server_address[1]}/v1", LOCAL_MODEL=MODEL,
           LOCAL_API_KEY="test", HEARTH_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
try:
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "hearth.headless", "--prompt", "make me an image",
                        "--format", "text"], capture_output=True, timeout=120, env=env, cwd=ROOT)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
except subprocess.TimeoutExpired as e:
    out = str(e.stdout)[-1500:]
srv.shutdown()

first = SEEN[0] if SEEN else {"tools": [], "system": "", "result": ""}
last = SEEN[-1] if SEEN else first
check("the tool that says what's set up goes out on every turn", "what_can_i_do" in first["tools"])
check("the prompt says when to use it", "what_can_i_do" in first["system"])
check("the prompt says a missing piece gets installed here", "set_up" in first["system"])
check("the prompt says a picture is one call away", "generate_image" in first["system"])
check("a picture with nothing set up says how to get it, not only 'needs a key'",
      "set_up" in last["result"] and "what_can_i_do" in last["result"])
check("and doesn't send them off to fetch anything", "website" not in last["result"] and "download" in last["result"])
check("set_up is in the model's tools right after that failure", "set_up" in last["tools"])
check("the turn ended cleanly", "Traceback" not in out and "All done." in out)

# ---------------------------------------------------------------- the tools

import hearth                                     # noqa: E402
from hearth import tools                          # noqa: E402

names = {t["name"] for t in hearth.TOOL_DEFINITIONS}
for n in ("what_can_i_do", "set_up", "undo"):
    check(f"{n} exists", n in names)

if "what_can_i_do" not in names:
    print("\nFAILED (no capability tools to test further)")
    sys.exit(1)

from hearth import capabilities, channel_core, claude_brain, external_agents, imagine, llmserver, persona, sdserver, subagents  # noqa: E402

check("what_can_i_do needs no yes", "what_can_i_do" not in tools._RISKY_TOOLS)
check("set_up and undo need a yes", {"set_up", "undo"} <= tools._RISKY_TOOLS)
check("load_tools names set_up and undo", {"set_up", "undo"} <= set(re.findall(
    r"\w+", tools._LOAD_TOOLS_SCHEMA["function"]["description"])))
check("load_tools with no match points at what_can_i_do", "what_can_i_do" in tools.execute_tool("load_tools", {"query": "zzqx"}))
check("send_to_phone takes a file with no message", "message" not in (next(
    t for t in hearth.TOOL_DEFINITIONS if t["name"] == "send_to_phone")["parameters"].get("required") or []))
check("spawn_subagent says when to use it", "parallel" in next(
    t for t in hearth.TOOL_DEFINITIONS if t["name"] == "spawn_subagent")["description"])

report = capabilities.report()
for key, title, _read in capabilities.AREAS:
    check(f"the report covers {key}", title + ":" in report)
check("every area reads without failing", "couldn't check" not in report)
known = names | {"load_tools"}
named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", re.sub(r"\S*[\\/]\S*", "", report))) - {"runs_on"}
check(f"every tool the report names is real: {sorted(named - known)}", named <= known)
check("an unknown area lists the areas", "Error" in capabilities.report("nope") and "images" in capabilities.report("nope"))

# ---------------------------------------------------------------- what's missing, fixed

tools.reset_unlocked_tools()
said = tools.execute_tool("generate_image", {"prompt": "x", "where": "local", "_approved": tools.APPROVAL_TOKEN})
check("asked for on this PC with no engine, the error names set_up", "set_up(what='images')" in said)
check("a video on this PC with no engine too", "set_up(what='images')" in tools.execute_tool(
    "generate_video", {"prompt": "x", "where": "local", "_approved": tools.APPROVAL_TOKEN}))
check("what_can_i_do brings set_up in", (tools.reset_unlocked_tools(), tools.execute_tool("what_can_i_do", {}),
                                         "set_up" in tools._unlocked_tools)[2])
check("no model file and no server names set_up", "set_up" in tools.execute_tool("list_models", {}))


def waited(key):
    end = time.time() + 15
    while time.time() < end and capabilities._JOBS[key]["status"] == "downloading":
        time.sleep(0.05)
    return capabilities._JOBS[key]["status"]


subagents.drain_pending_notifications()
fake = {"installed": [], "fetched": [], "free": 10**12}


def fake_install(on_progress=None, **_k):
    on_progress(5, 10)
    fake["installed"].append(sdserver.Path("sd"))
    return {"ok": True, "tag": "b1", "backend": "cuda"}


sdserver.install = fake_install
sdserver.installed = lambda: fake["installed"]
sdserver.start = lambda timeout=60: {"ok": True}
sdserver.api = lambda *a, **k: {}
RECIPE = {"id": "flux-schnell", "name": "FLUX Schnell", "defaults": {}}
sdserver.find_recipe = lambda w: (RECIPE, []) if "flux" in w else (None, ["sdxl"])
sdserver.plan_for_this_pc = lambda rid: {"ok": True, "parts": [
    {"repo": "o/r", "path": "sub/a.gguf", "category": "diffusion_models", "size": 100, "have": False},
    {"repo": "o/r", "path": "b.safetensors", "category": "vae", "size": 50, "have": True}]}
capabilities.shutil.disk_usage = lambda _p: type("U", (), {"free": fake["free"]})()


def fake_start_download(repo, path, dest_dir="", save_as="", vision_for=""):
    fake["fetched"].append((repo, path, dest_dir, save_as))


llmserver.start_model_download = fake_start_download
llmserver.model_download_state = lambda name: {"status": "done", "result": {"ok": True}}

said = tools.execute_tool("set_up", {"what": "images", "_approved": tools.APPROVAL_TOKEN})
check("set_up returns at once and says it reports back", "Started" in said and "report" in said)
check("the engine went in", waited("images") == "done" and bool(fake["installed"]))
report_back = subagents.drain_pending_notifications()
check("a report reaches the turn, and it says what to do next",
      report_back and "what_can_i_do" in str(report_back[-1].get("result_text") or report_back[-1]))
check("an engine already in isn't installed twice", "already installed" in capabilities.set_up("images"))

said = capabilities.set_up("images", "flux schnell")
check("a model after the engine runs too", waited("images-flux schnell") == "done")
check("only the part not here is fetched, into its own folder, under its bare name",
      len(fake["fetched"]) == 1 and fake["fetched"][0][1] == "sub/a.gguf"
      and fake["fetched"][0][2].endswith("diffusion_models") and fake["fetched"][0][3] == "a.gguf")
check("its report says it's ready", "generate_image" in str(subagents.drain_pending_notifications()))

capabilities.set_up("images", "nosuch")
check("a model the engine doesn't know fails with the close ones", waited("images-nosuch") == "error"
      and "sdxl" in str(subagents.drain_pending_notifications()))
fake["free"] = 10
capabilities.set_up("images", "flux")
check("too big for the disk says so before fetching", waited("images-flux") == "error"
      and "GB free" in str(subagents.drain_pending_notifications()))

llmserver.list_hf_files = lambda repo: [{"filename": "m-Q8.gguf", "size_gb": 9, "fit_tier": "tight"},
                                        {"filename": "m-Q4.gguf", "size_gb": 4, "recommended": True}]
llmserver.search_huggingface = lambda q, limit=12: [{"hf_repo": "acme/m-GGUF"}]
check("chat words find the file that fits", capabilities.pick_chat_file("m model") == {
    "repo": "acme/m-GGUF", "filename": "m-Q4.gguf", "size_gb": 4})
check("a repo and file is taken as it is", capabilities.pick_chat_file("acme/m/sub/x.gguf") == {
    "repo": "acme/m", "filename": "sub/x.gguf"})
check("a page link too", capabilities.pick_chat_file("https://huggingface.co/acme/m/blob/main/x.gguf")["filename"] == "x.gguf")
check("set_up refuses a kind it doesn't have", capabilities.set_up("printer").startswith("Error"))
external_agents.missing = lambda: [{"id": "crush", "label": "Crush", "get": "install", "site": ""},
                                   {"id": "pi", "label": "Pi", "get": "site", "site": "https://pi.dev"}]
external_agents.available = lambda: []
check("an agent only its maker offers is said to be that", "maker" in capabilities.set_up("agent", "pi"))
check("a wrong agent lists the ones it can install", "crush" in capabilities.set_up("agent", "zz"))

# ---------------------------------------------------------------- every brain, every surface

before = persona.system_prompt()
capabilities._JOBS["held"] = {"id": "held", "label": "Something", "status": "downloading", "done": 0, "total": 0}
fake["installed"].append(sdserver.Path("sd2"))
check("the prompt's bytes don't move while things are installed", persona.system_prompt() == before)
check("a running setup shows in the report", "Being set up now: Something" in capabilities.report("email"))
del capabilities._JOBS["held"]

for n in ("what_can_i_do", "set_up", "load_model", "connectors"):
    check(f"an agent brain runs {n} in the app, where its state lives", n in claude_brain.HEARTH_SIDE)
check("an agent brain is told what load_tools means for it", "that is find_tools" in claude_brain.persona_text(False))

from hearth import mcp_client                     # noqa: E402

mcp_client._BRIDGES["notes"] = {"raw_name": "notes", "state": "connected", "site": "", "kind": "",
                                "tools": [{"name": "add", "description": "Add a note", "input_schema": {}}]}
check("a connected MCP server is named in the prompt", "notes" in persona._mcp_line())
check("and in the report", "notes: connected, 1 tool" in capabilities.report("mcp"))
got = claude_brain.run_tool(claude_brain._TOKEN, "find_mcp_tools", {"query": "notes"})["result"]
check("an agent brain finds its tools", [t["name"] for t in got] == ["mcp_notes_add"])
mcp_client._BRIDGES.clear()
check("with none connected the prompt says nothing", persona._mcp_line() == "")


class _Telegram:
    label = "Telegram"


token = channel_core._HERE.set((_Telegram(), "42"))
check("on a phone turn the report says send_to_phone lands right here",
      "over Telegram" in capabilities.report("phone") and "send_to_phone" in capabilities.report("phone"))
channel_core._HERE.reset(token)

path = os.path.join(tools.WORKSPACE, "keep.txt")
tools.execute_tool("write_file", {"path": path, "content": "first", "_approved": tools.APPROVAL_TOKEN})
tools.execute_tool("edit_file", {"path": path, "edits": [{"old_text": "first", "new_text": "second"}],
                                 "_approved": tools.APPROVAL_TOKEN})
listed = capabilities.report("undo")
ids = re.findall(r"(\w+): ", listed.split("Latest:")[-1])
check("the undo area lists the change", "undo(id)" in listed and bool(ids))
said = tools.execute_tool("undo", {"id": ids[0], "_approved": tools.APPROVAL_TOKEN}) if ids else ""
with open(path, encoding="utf-8") as f:
    check("undo takes it back", f.read() == "first" and said.startswith("Undone"))

print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'PASSED'}")
sys.exit(1 if FAILS else 0)
