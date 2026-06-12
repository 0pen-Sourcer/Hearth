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


for td in TOOL_DEFINITIONS:
    if td["name"] in _SKIP_DYNAMIC:
        continue  # registered manually above with proper return type
    fn = _make_tool(td["name"], td["description"], td["parameters"])
    mcp.tool(name=td["name"], description=td["description"])(fn)


# ----------------------------------------------------------------------------
# Bonus: a status resource so LM Studio can show "Jarvis is alive" on connect.
# ----------------------------------------------------------------------------

@mcp.resource("jarvis://status")
def _status() -> str:
    """Live status of the Jarvis brain."""
    return json.dumps({
        "name": "Jarvis",
        "workspace": WORKSPACE,
        "tools": len(TOOL_DEFINITIONS),
        "started": datetime.now().isoformat(timespec="seconds"),
        "activity_log": ACTIVITY_LOG,
    }, indent=2)


# ----------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[Jarvis MCP] {len(TOOL_DEFINITIONS)} tools registered", file=sys.stderr)
    print(f"[Jarvis MCP] workspace: {WORKSPACE}", file=sys.stderr)
    print(f"[Jarvis MCP] activity log: {ACTIVITY_LOG}", file=sys.stderr)
    _log_activity("server_start", tools=len(TOOL_DEFINITIONS), workspace=WORKSPACE)
    mcp.run()
