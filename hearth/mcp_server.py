"""Hearth MCP server — exposes every Hearth tool to any MCP client.

LM Studio config snippet (paste into its mcp.json — swap the paths for
wherever you cloned the repo and which venv you're using):

{
  "mcpServers": {
    "Hearth": {
      "command": "<absolute path>/.venv/Scripts/python.exe",
      "args": ["<absolute path>/hearth/mcp_server.py"]
    }
  }
}

Once configured, every tool call shows up live in LM Studio's chat — title,
arguments, result — exactly the real-time display you want.

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

mcp = FastMCP("Jarvis")


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


def _make_tool(name: str, description: str, schema: Dict[str, Any]):
    props: Dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    # Build a function signature dynamically. We can't use real annotations
    # on a runtime-generated function without exec, so we go via exec.
    # Python syntax requires required params BEFORE optional ones, so sort
    # accordingly before emitting the signature (the tool schemas list
    # properties in declaration order which sometimes puts optionals first).
    req_parts: List[str] = []
    opt_parts: List[str] = []
    for pname, pschema in props.items():
        pytype = _TYPE_PY.get(pschema.get("type", "string"), "str")
        if pname in required:
            req_parts.append(f"{pname}: {pytype}")
        else:
            opt_parts.append(f"{pname}: Optional[{pytype}] = None")

    sig = ", ".join(req_parts + opt_parts)

    src = (
        f"def _tool({sig}):\n"
        f"    args = {{k: v for k, v in locals().items() if v is not None}}\n"
        f"    return execute_tool({name!r}, args)\n"
    )

    ns: Dict[str, Any] = {
        "execute_tool": execute_tool,
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
#   - forge_generate: needs a local Stable-Diffusion (Forge) install + env; it's
#     non-deferred (surfaces for weak local models when Forge is detected), so it
#     leaks onto the MCP surface where it's setup-specific noise. Keep it off.
_MCP_NEVER = {"ask_user", "spawn_subagent", "launch_team", "forge_generate"}
_mcp_exposed = 0
for td in TOOL_DEFINITIONS:
    if td["name"] in _SKIP_DYNAMIC:
        _mcp_exposed += 1
        continue  # registered manually above with proper return type
    if td["name"] in _MCP_NEVER:
        continue  # needs Hearth's own UI — useless / hangs on an external host
    if not _MCP_ALL_TOOLS and td["name"] in _DEFERRED_TOOLS:
        continue  # niche tool — kept off the host's list to save its context
    fn = _make_tool(td["name"], td["description"], td["parameters"])
    mcp.tool(name=td["name"], description=td["description"])(fn)
    _mcp_exposed += 1


# ----------------------------------------------------------------------------
# Bonus: a status resource so LM Studio can show "Jarvis is alive" on connect.
# ----------------------------------------------------------------------------

@mcp.resource("jarvis://status")
def _status() -> str:
    """Live status of the Jarvis brain."""
    return json.dumps({
        "name": "Jarvis",
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
    print(f"[Jarvis MCP] {_mcp_exposed} tools registered{_diet}", file=sys.stderr)
    print(f"[Jarvis MCP] workspace: {WORKSPACE}", file=sys.stderr)
    print(f"[Jarvis MCP] activity log: {ACTIVITY_LOG}", file=sys.stderr)
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
            _pn = psutil.Process(os.getppid()).name().lower()
            _client = _pn
            for _k, _label in (("claude", "Claude Desktop"), ("lmstudio", "LM Studio"),
                               ("lm studio", "LM Studio"), ("cursor", "Cursor"),
                               ("code", "VS Code"), ("ollama", "Ollama")):
                if _k in _pn:
                    _client = _label
                    break
        except Exception:
            pass
        _reg = os.path.join(_clients_dir, f"{os.getpid()}.json")
        with open(_reg, "w", encoding="utf-8") as _f:
            json.dump({"pid": os.getpid(), "client": _client,
                       "connected_at": datetime.now().isoformat(timespec="seconds")}, _f)
        import atexit as _ax
        _ax.register(lambda: os.path.exists(_reg) and os.remove(_reg))
    except Exception:
        pass
    # Hand the pristine stdout back for the JSON-RPC stdio transport.
    sys.stdout = _REAL_STDOUT
    mcp.run()
