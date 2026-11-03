"""Hearth MCP server — exposes every Hearth tool to any MCP client.

LM Studio config snippet (paste into its mcp.json — swap the paths for
wherever you cloned the repo and which venv you're using):

{
  "mcpServers": {
    "Hearth": {
      "command": "<absolute path>/.venv/Scripts/python.exe",
      "args": ["<absolute path>/hearth/mcp_server.py"],
      "env": { "HEARTH_AUTO_APPROVE": "1" }
    }
  }
}

Once configured, every tool call shows up live in LM Studio's chat — title,
arguments, result — exactly the real-time display you want.

The env block matters here: an MCP client can't surface Hearth's approval
prompt, so a write or a guarded (destructive) command would come back refused
with no way to confirm. HEARTH_AUTO_APPROVE=1 trusts that session to run them,
and to edit files outside the default workspace. Drop the env line (or set it
to "0") to keep those blocked. To allow edits in specific folders only, without
full trust, set HEARTH_EXTRA_WORKSPACES instead (semicolon- or comma-separated
paths, e.g. "E:\\Projects;D:\\Work"). Note this is the main-process API only —
a background sub-agent still needs its own opt-in (HEARTH_SUBAGENT_AUTO_APPROVE),
auto-approve doesn't silently extend to it.

Install: pip install mcp
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

# Make the package importable when run directly via the LM Studio config.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# MCP over stdio uses STDOUT for the JSON-RPC protocol — it must contain
# nothing but protocol messages. ANY stray print to stdout (from this module
# OR anything it imports: a banner, a warning, a debug line) corrupts the
# stream and the client (Claude Desktop / LM Studio) silently fails to handshake.
# So route stdout to stderr for ALL of import + setup, and restore the real
# stdout only right before mcp.run() takes over the transport.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

try:
    from mcp.server.fastmcp import FastMCP, Image  # type: ignore
except ImportError:
    print("Install mcp: pip install mcp", file=sys.stderr)
    sys.exit(1)

from hearth.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    WORKSPACE,
    LOGS_DIR,
    ACTIVITY_LOG,
    _log_activity,
    _DEFERRED_TOOLS,
)

mcp = FastMCP("Inglen")


# ----------------------------------------------------------------------------
# view_image gets a hand-written wrapper instead of going through the dynamic
# factory below — it must return an `Image` content block (not text) so that
# LM Studio's chat actually shows the image to the model and vision kicks in.
# ----------------------------------------------------------------------------

@mcp.tool(
    name="view_image",
    description=(
        "Load an image file from disk so you can SEE it. Use this when the "
        "user gives you a path to an image (e.g. 'see this image C:\\path.png') "
        "or asks about a screenshot you just took. The image is rendered into "
        "the conversation and you'll see it on the next turn. Supports .png, "
        ".jpg, .jpeg, .gif, .webp, .bmp."
    ),
)
def view_image(path: str):
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        return "Error: missing path"
    abs_path = os.path.expanduser(raw)
    if not os.path.isabs(abs_path):
        abs_path = os.path.abspath(abs_path)
    if not os.path.isfile(abs_path):
        return f"Error: not a file: {abs_path}"
    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return f"Error: not a recognized image extension ({ext})"
    if os.path.getsize(abs_path) > 20 * 1024 * 1024:
        return "Error: image too large (max 20MB)"
    try:
        return Image(path=abs_path)
    except Exception as e:
        return f"Error: could not load image: {e}"


# Hide the dynamically-built version below from being registered as a duplicate.
_SKIP_DYNAMIC = {"view_image"}


# ----------------------------------------------------------------------------
# Register every Hearth tool as an MCP tool.
#
# FastMCP expects real Python functions with type hints + a docstring. We
# generate them dynamically from TOOL_DEFINITIONS so we never get out of sync
# with the brain.
# ----------------------------------------------------------------------------

_TYPE_PY = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _sanitize_param(name: str) -> str:
    """Turn an arbitrary JSON property name into a valid Python identifier."""
    import keyword as _kw
    import re as _re
    s = _re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not s or s[0].isdigit():
        s = "_" + s
    if _kw.iskeyword(s):
        s = s + "_"
    # Ensure uniqueness is handled by caller if needed; for now the sanitized
    # form is deterministic. Colliding originals (e.g. "a-b" and "a_b") will
    # share the same sanitized name — last wins, which is acceptable for MCP
    # tool schemas that should not have such collisions.
    return s


# Started by Hearth as Claude Code's tool server: Claude Code asks the
# `approve` tool before acting, `approve` asks the Hearth that started it, and
# a call the user approved carries Hearth's approval token, so a command they
# confirmed isn't refused by the destructive-command guard on the way in.
_BRAIN_URL = os.environ.get("HEARTH_BRAIN_URL", "").rstrip("/")
_BRAIN_TOKEN = os.environ.get("HEARTH_BRAIN_TOKEN", "")
# (tool name, when): an approval is used by the next call of that tool, within
# a minute. Matching the arguments exactly failed on how they arrive here.
_APPROVED: List[tuple] = []
# A brain with no permission hook of its own (Codex): the server asks Hearth
# itself before running anything risky.
_BRAIN_GATE = os.environ.get("HEARTH_BRAIN_GATE", "") == "1"


def _post_hearth(route: str, tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(
        _BRAIN_URL + route,
        data=json.dumps({"token": _BRAIN_TOKEN, "tool": tool, "input": inp}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def _ask_hearth(tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _post_hearth("/api/brain/permission", tool, inp)
    except Exception as e:
        return {"behavior": "deny", "message": f"Couldn't reach Inglen to ask: {e}"}


# Helpers live in the Hearth that started this server, so their tools run there.
# A helper started by Hearth gets none of them: it does its task and reports.
from hearth.claude_brain import HEARTH_SIDE
_IN_HEARTH = HEARTH_SIDE if (_BRAIN_URL and _BRAIN_TOKEN and not os.environ.get("HEARTH_HELPER")) else ()


def _run(name: str, args: Dict[str, Any]):
    import time as _t
    from hearth.tools import APPROVAL_TOKEN, _RISKY_TOOLS, mode_allows, reads_secret
    from hearth.outbound import URL_ARGS
    now = _t.time()
    hit = next((a for a in _APPROVED if a[0] == name and now - a[1] < 60), None)
    secret = reads_secret(name, args)
    if hit:
        _APPROVED.remove(hit)
        args = dict(args, _approved=APPROVAL_TOKEN)
    elif secret and not (_BRAIN_GATE and _BRAIN_URL):
        return f"Not done. {secret} Nobody here can say yes to it."
    elif _BRAIN_GATE and _BRAIN_URL and (name in _RISKY_TOOLS or name in URL_ARGS or secret or name.startswith("mcp_")):
        # Whether a request would carry data out depends on what the turn has
        # read from outside, which the Inglen that started this server keeps.
        if name in URL_ARGS or not mode_allows(name, args):
            verdict = _ask_hearth("mcp__hearth__" + name, args)
            if verdict.get("behavior") != "allow":
                return "Not done. " + str(verdict.get("message") or "The user declined this.")
        # Allowed by the switch or by the person: marked approved, as the window's own turns mark it,
        # so run_command's guard doesn't refuse again what Auto let through (deleting what the app made).
        args = dict(args, _approved=APPROVAL_TOKEN)
    if name in _IN_HEARTH or (_IN_HEARTH and name.startswith("mcp_")):
        # Helpers, and the MCP servers Inglen connects to, live in the Hearth that started this server.
        try:
            return str(_post_hearth("/api/brain/tool", name, args).get("result") or "")
        except Exception as e:
            return f"Error: couldn't reach Inglen: {e}"
    return execute_tool(name, args)


def _make_tool(name: str, description: str, schema: Dict[str, Any]):
    props: Dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    # Build a function signature dynamically. We can't use real annotations
    # on a runtime-generated function without exec, so we go via exec.
    # Python syntax requires required params BEFORE optional ones, so sort
    # accordingly before emitting the signature (the tool schemas list
    # properties in declaration order which sometimes puts optionals first).
    # Property names like "foo-bar" or "123abc" are not valid Python identifiers,
    # so sanitize them and map back to the original key when calling execute_tool.
    param_map: List[tuple] = []  # (original, sanitized)
    seen: set = set()
    for pname in props:
        san = _sanitize_param(pname)
        # De-dupe sanitized collisions: "a-b" and "a_b" both -> "a_b"
        base, n = san, 1
        while san in seen:
            n += 1
            san = f"{base}_{n}"
        seen.add(san)
        param_map.append((pname, san))

    req_parts: List[str] = []
    opt_parts: List[str] = []
    for (pname, san), (_, pschema) in zip(param_map, props.items()):
        pytype = _TYPE_PY.get(pschema.get("type", "string"), "str")
        if pname in required:
            req_parts.append(f"{san}: {pytype}")
        else:
            opt_parts.append(f"{san}: Optional[{pytype}] = None")

    sig = ", ".join(req_parts + opt_parts)
    # Build args dict mapping original names -> sanitized variable values
    if param_map:
        pairs = ", ".join(f"{orig!r}: {san}" for orig, san in param_map)
        args_line = f"    _raw = {{{pairs}}}\n    args = {{k: v for k, v in _raw.items() if v is not None}}\n"
    else:
        args_line = "    args = {}\n"

    src = (
        f"def _tool({sig}):\n"
        + args_line
        + f"    return _run({name!r}, args)\n"
    )

    ns: Dict[str, Any] = {
        "_run": _run,
        "Optional": Optional,
    }
    exec(src, ns)
    fn = ns["_tool"]
    fn.__name__ = name
    fn.__doc__ = description
    return fn


# Tool diet on the OUTBOUND MCP surface. Unlike Hearth's own prompt, an MCP host
# (Claude Desktop / Cursor / LM Studio) has no `load_tools` escape hatch — every
# tool we register lands in ITS context permanently. Dumping all ~100 floods the
# host, so by default we expose only the core (non-deferred) set — the same diet
# that keeps Hearth's own prompt lean. HEARTH_MCP_ALL_TOOLS=1 exposes everything.
_MCP_ALL_TOOLS = os.environ.get("HEARTH_MCP_ALL_TOOLS", "") in ("1", "true", "yes")
# Interactive tools that depend on Hearth's OWN UI to render options / a decision
# prompt. Over MCP the host (LM Studio, Claude Desktop, Cursor) has no way to show
# those choices, so the tool just hangs or dead-ends. Never expose them outward —
# not even under HEARTH_MCP_ALL_TOOLS.
#   - ask_user: renders numbered options in Hearth's UI; a host can't show them.
#   - spawn_subagent / launch_team: results come back through Hearth's OWN
#     turn-notification loop, which an external host never sees — it just hangs.
#   - set_up: a download that reports back the same way, and it installs programs.
_MCP_NEVER = {"ask_user", "spawn_subagent", "launch_team", "set_up"}
# Low-level computer-use PRIMITIVES: redundant on MCP with smart_click (fused
# vision + accessibility) and desktop_snapshot/click/type + screenshot, which are
# the reliable interface. Hidden by default to keep the host's tool list clean;
# HEARTH_MCP_ALL_TOOLS=1 restores them for a host that wants raw pixel control.
_MCP_DIET_EXTRA = {"computer_move", "computer_drag", "computer_scroll", "computer_screen"}
_mcp_exposed = 0
for td in TOOL_DEFINITIONS:
    if td["name"] in _SKIP_DYNAMIC:
        _mcp_exposed += 1
        continue  # registered manually above with proper return type
    if td["name"] in _IN_HEARTH:
        pass      # Hearth's own brain: its helpers run in Hearth and report back to it
    elif td["name"] in _MCP_NEVER:
        continue  # needs Hearth's own UI — useless / hangs on an external host
    elif not _MCP_ALL_TOOLS and (td["name"] in _DEFERRED_TOOLS or td["name"] in _MCP_DIET_EXTRA):
        continue  # niche / redundant tool — kept off the host's list to save its context
    fn = _make_tool(td["name"], td["description"], td["parameters"])
    mcp.tool(name=td["name"], description=td["description"])(fn)
    _mcp_exposed += 1


# Hearth's own brain sees only the everyday tools; these two reach the rest,
# the way load_tools does in Hearth itself.
if _BRAIN_URL and _BRAIN_TOKEN and not _MCP_ALL_TOOLS:
    def _reachable(name: str) -> bool:
        from hearth.tools import TOOL_DEFINITIONS, sync_plugins
        if name in _IN_HEARTH or (_IN_HEARTH and name.startswith("mcp_")) or (
                name in _DEFERRED_TOOLS and name not in _MCP_NEVER):
            return True
        # A plugin made since this server started (here or in another process): listed nowhere else.
        plugin = lambda: any(t["name"] == name and t.get("_plugin") for t in TOOL_DEFINITIONS)  # noqa: E731
        return plugin() or (name in sync_plugins() and plugin())

    from hearth.tools import deferred_menu

    @mcp.tool(name="find_tools", structured_output=False,
              description="Find more of Inglen's tools (they aren't all listed, to save room). Check here "
                          "before saying you can't do something. Give a tool's name or a word for what you "
                          "need, or 'all', then run it with use_tool. They are: " + deferred_menu() + ".")
    def find_tools(query: str) -> str:
        from hearth.tools import TOOL_DEFINITIONS, sync_plugins, unlock_tools
        sync_plugins()
        found = [td for td in unlock_tools(query, touch=False) if _reachable(td["name"])]
        q = (query or "").strip().lower()
        # A plugin made in this server is live but not held back, so the search above can't see it.
        found += [td for td in TOOL_DEFINITIONS if td.get("_plugin") and td not in found
                  and q and (q in td["name"] or q in td["description"].lower())]
        if _IN_HEARTH:
            try:
                found += _post_hearth("/api/brain/tool", "find_mcp_tools", {"query": query}).get("result") or []
            except Exception:
                pass
        if not found:
            return "Nothing more matched that. Try another word, or 'all'."
        return "\n\n".join(f"{td['name']}: {td.get('description', '')}\nparameters: "
                             f"{json.dumps(td.get('parameters') or {}, ensure_ascii=False)}" for td in found)
    _mcp_exposed += 1

    @mcp.tool(name="use_tool", structured_output=False,
              description="Run one of the tools find_tools returned: its name and its arguments.")
    def use_tool(name: str, arguments: Optional[dict] = None) -> str:
        if not _reachable(name):
            return f"Error: {name} isn't one of the tools find_tools offers."
        return str(_run(name, dict(arguments or {})))
    _mcp_exposed += 1


if _BRAIN_URL and _BRAIN_TOKEN and not _BRAIN_GATE:
    @mcp.tool(name="approve", description="Inglen's permission check. Not for direct use.",
              structured_output=False)
    def approve(tool_name: str, input: dict, tool_use_id: str = "") -> str:
        verdict = _ask_hearth(tool_name, input)
        if verdict.get("behavior") == "allow" and tool_name.startswith("mcp__hearth__"):
            import time as _t
            from hearth.claude_brain import ran_by_use_tool
            inner = ran_by_use_tool(tool_name, input)
            _APPROVED.append((inner[0] if inner else tool_name[len("mcp__hearth__"):], _t.time()))
        return json.dumps(verdict)


# ----------------------------------------------------------------------------
# Bonus: a status resource so LM Studio can show "Hearth is alive" on connect.
# ----------------------------------------------------------------------------

@mcp.resource("jarvis://status")
def _status() -> str:
    """Live status of the Hearth brain."""
    return json.dumps({
        "name": "Inglen",
        "workspace": WORKSPACE,
        "tools": _mcp_exposed,
        "started": datetime.now().isoformat(timespec="seconds"),
        "activity_log": ACTIVITY_LOG,
    }, indent=2)


# ----------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    _diet = "" if _MCP_ALL_TOOLS else f" (core diet; {len(TOOL_DEFINITIONS)} available — set HEARTH_MCP_ALL_TOOLS=1 for all)"
    print(f"[Inglen MCP] {_mcp_exposed} tools registered{_diet}", file=sys.stderr)
    print(f"[Inglen MCP] workspace: {WORKSPACE}", file=sys.stderr)
    print(f"[Inglen MCP] activity log: {ACTIVITY_LOG}", file=sys.stderr)
    _log_activity("server_start", tools=_mcp_exposed, workspace=WORKSPACE)
    # Register this inbound connection so the Hearth GUI's MCP tab can show WHO'S
    # connected. stdio spawns one server process per client, so our pid + the
    # parent process name (claude.exe / lmstudio / cursor …) identify the client.
    # The file is removed on clean exit; the GUI prunes dead pids for hard kills.
    try:
        _clients_dir = os.path.join(os.path.expanduser("~/.hearth"), "mcp_clients")
        os.makedirs(_clients_dir, exist_ok=True)
        _client = "unknown"
        try:
            import psutil  # type: ignore
            # The app that started us, past any Python launcher (a venv's
            # python.exe starts the real interpreter, so the parent is often
            # just another python.exe) and Hearth's own runner.
            _pr = psutil.Process(os.getppid())
            for _ in range(4):
                if not _pr.name().lower().startswith(("python", "py.exe", "pythonw", "uv", "cmd.exe")):
                    break
                _pr = _pr.parent() or _pr
            _pn = _pr.name().lower()
            _client = _pn
            for _k, _label in (("claude", "Claude Desktop"), ("lmstudio", "LM Studio"),
                               ("lm studio", "LM Studio"), ("cursor", "Cursor"), ("codex", "Codex"),
                               ("opencode", "OpenCode"), ("hearth", "Inglen"),
                               ("code", "VS Code"), ("ollama", "Ollama")):
                if _k in _pn:
                    _client = _label
                    break
        except Exception:
            pass
        _reg = os.path.join(_clients_dir, f"{os.getpid()}.json")
        with open(_reg, "w", encoding="utf-8") as _f:
            # Record this process's start time as well. Windows recycles pids,
            # so a pid alone let a finished session show as still connected once
            # something unrelated inherited the number.
            _started = None
            try:
                import psutil  # type: ignore
                _started = psutil.Process(os.getpid()).create_time()
            except Exception:
                pass
            json.dump({"pid": os.getpid(), "client": _client,
                       "started_at": _started,
                       "connected_at": datetime.now().isoformat(timespec="seconds")}, _f)
        import atexit as _ax
        _ax.register(lambda: os.path.exists(_reg) and os.remove(_reg))
    except Exception:
        pass
    # Hand the pristine stdout back for the JSON-RPC stdio transport.
    sys.stdout = _REAL_STDOUT
    mcp.run()
