"""MCP client runtime — spawn the servers configured in ~/Jarvis/mcp.json,
list their tools, register each as a Hearth tool with name
'mcp_<server>_<tool>'. Calls route through the persistent stdio session.

Lifecycle:
  - bootstrap() at Hearth start (web.py / hearth_cli.py call it).
  - Each server runs as a subprocess; a background asyncio thread owns
    the loop that talks to it via mcp.client.stdio.
  - shutdown() at process exit closes sessions cleanly.

Failures are non-fatal: if one server crashes or its `command` is missing,
that server's tools just don't register and a warning surfaces in the
bridges-status box. Other servers stay up.

Config shape: standard mcp.json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
      "env": {"OPTIONAL_VAR": "value"}
    }
  }
}
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Per-server live state. Each entry:
#   {
#     "name": "filesystem",
#     "state": "starting"|"connected"|"error",
#     "tools": [tool dict, ...],
#     "error": "msg" (optional),
#     "session": ClientSession (when connected),
#     "spawn_started_ts": float,
#   }
_BRIDGES: Dict[str, Dict[str, Any]] = {}
_BRIDGES_LOCK = threading.Lock()

# Single dedicated event loop owns ALL MCP client sessions. Spawned once
# in a daemon thread on bootstrap(). Tool calls from Hearth's sync world
# bounce in via asyncio.run_coroutine_threadsafe.
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_THREAD: Optional[threading.Thread] = None
_LOOP_READY = threading.Event()

_WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE") or (Path.home() / "Jarvis"))
_MCP_JSON = _WORKSPACE / "mcp.json"

# Tool-call timeout when forwarding to an MCP server. The model's chat
# request usually has its own ceiling; 60s is enough for filesystem /
# fetch / etc. without leaving Hearth hung on a slow remote.
_TOOL_TIMEOUT_S = 60.0


def _read_config() -> Dict[str, Dict[str, Any]]:
    if not _MCP_JSON.is_file():
        return {}
    try:
        data = json.loads(_MCP_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _sanitize(name: str) -> str:
    """MCP server / tool names can have characters that don't fit OpenAI's
    tool-name regex. Strip down to [A-Za-z0-9_-] and prefix-collapse runs."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "mcp"


# ---------------------------------------------------------------------------
# Asyncio side — runs on the dedicated _LOOP thread
# ---------------------------------------------------------------------------

async def _connect_one(name: str, spec: Dict[str, Any]) -> None:
    """Spawn one MCP server subprocess, open a stdio session, list its
    tools, stash everything in _BRIDGES. Keeps the session alive for the
    duration of the loop (i.e. until shutdown)."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    safe = _sanitize(name)
    with _BRIDGES_LOCK:
        _BRIDGES[safe] = {
            "name": safe,
            "raw_name": name,
            "state": "starting",
            "tools": [],
            "spawn_started_ts": time.time(),
        }

    command = (spec.get("command") or "").strip()
    args = list(spec.get("args") or [])
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
    if not command:
        with _BRIDGES_LOCK:
            _BRIDGES[safe].update(state="error",
                                  error="no 'command' in mcp.json")
        return

    params = StdioServerParameters(command=command, args=args, env=env)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30.0)
                tool_list = await asyncio.wait_for(
                    session.list_tools(), timeout=15.0)
                tools = []
                for t in tool_list.tools:
                    tools.append({
                        "name": t.name,
                        "description": getattr(t, "description", "") or "",
                        "input_schema": getattr(t, "inputSchema", None)
                                      or {"type": "object", "properties": {}},
                    })
                with _BRIDGES_LOCK:
                    _BRIDGES[safe].update(
                        state="connected",
                        tools=tools,
                        session=session,
                    )
                # Keep the session alive until the loop is cancelled. The
                # CancelledError that fires on shutdown propagates through
                # the async-with blocks and closes the subprocess cleanly.
                try:
                    while True:
                        await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    pass
    except Exception as e:
        with _BRIDGES_LOCK:
            _BRIDGES[safe].update(state="error",
                                  error=f"{type(e).__name__}: {e}")


async def _bootstrap_async(servers: Dict[str, Dict[str, Any]]) -> None:
    """Fire connect tasks for each configured server. They run forever
    (until the loop is cancelled) so the sessions stay open for tool calls."""
    for name, spec in servers.items():
        asyncio.create_task(_connect_one(name, spec))


async def _call_tool_async(server: str, tool: str,
                            args: Dict[str, Any]) -> Dict[str, Any]:
    """Forward a tool call to the named MCP server, return its result."""
    with _BRIDGES_LOCK:
        bridge = _BRIDGES.get(server)
    if not bridge or bridge.get("state") != "connected":
        return {"ok": False, "error": f"server '{server}' not connected "
                                       f"(state: {bridge.get('state') if bridge else 'unknown'})"}
    session = bridge.get("session")
    if not session:
        return {"ok": False, "error": f"server '{server}' has no session"}
    try:
        result = await asyncio.wait_for(
            session.call_tool(tool, args), timeout=_TOOL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"MCP tool {server}.{tool} timed out "
                                       f"after {_TOOL_TIMEOUT_S}s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    # CallToolResult has .content (list of TextContent / ImageContent)
    out_parts: List[str] = []
    for c in (getattr(result, "content", None) or []):
        text = getattr(c, "text", None)
        if text is not None:
            out_parts.append(str(text))
        else:
            # ImageContent / other — fall back to a repr the model can read
            out_parts.append(repr(c))
    is_error = bool(getattr(result, "isError", False))
    return {
        "ok": not is_error,
        "output": "\n".join(out_parts) if out_parts else "(empty)",
    }


# ---------------------------------------------------------------------------
# Sync facade — what tools.py + web.py call
# ---------------------------------------------------------------------------

def _loop_target() -> None:
    """Run the dedicated asyncio loop forever in its own thread."""
    global _LOOP
    _LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_LOOP)
    _LOOP_READY.set()
    try:
        _LOOP.run_forever()
    finally:
        try:
            _LOOP.close()
        except Exception:
            pass


def _ensure_loop() -> Optional[asyncio.AbstractEventLoop]:
    global _LOOP_THREAD
    if _LOOP_THREAD is not None and _LOOP_THREAD.is_alive():
        return _LOOP
    _LOOP_READY.clear()
    _LOOP_THREAD = threading.Thread(target=_loop_target,
                                    name="hearth-mcp-client", daemon=True)
    _LOOP_THREAD.start()
    if not _LOOP_READY.wait(timeout=5.0):
        return None
    return _LOOP


def bootstrap() -> Dict[str, Any]:
    """Spin up the loop, spawn each configured MCP server, return status.

    Safe to call multiple times — the function early-returns if servers
    are already known. To restart (after editing mcp.json), call
    shutdown() then bootstrap() again.
    """
    servers = _read_config()
    if not servers:
        return {"ok": True, "servers": 0, "note": "no servers configured"}
    loop = _ensure_loop()
    if loop is None:
        return {"ok": False, "error": "could not start MCP client loop"}
    fut = asyncio.run_coroutine_threadsafe(_bootstrap_async(servers), loop)
    try:
        fut.result(timeout=2.0)
    except Exception as e:
        return {"ok": False, "error": f"bootstrap dispatch: {e}"}
    # Connections happen async — return current state immediately so the
    # bridges-status box can show "starting…" rows. They'll flip to
    # "connected" or "error" over the next few seconds.
    return {"ok": True, "servers": len(servers),
            "bridges": list_bridges()}


def shutdown() -> None:
    """Cancel all sessions + stop the loop. Used by tray exit."""
    global _LOOP, _LOOP_THREAD
    if _LOOP is None:
        return
    try:
        # Schedule loop.stop on the loop itself so pending tasks get a
        # chance to clean up their async-with contexts (closes subprocesses).
        _LOOP.call_soon_threadsafe(_LOOP.stop)
    except Exception:
        pass
    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        _LOOP_THREAD.join(timeout=3.0)
    _LOOP = None
    _LOOP_THREAD = None
    with _BRIDGES_LOCK:
        _BRIDGES.clear()


def list_bridges() -> List[Dict[str, Any]]:
    """Snapshot for the GUI status box. Excludes the live session object
    (not JSON-serializable). Each tool list shrunk to {name, description}."""
    out = []
    with _BRIDGES_LOCK:
        for safe, b in _BRIDGES.items():
            out.append({
                "name": safe,
                "raw_name": b.get("raw_name", safe),
                "state": b.get("state", "unknown"),
                "transport": "stdio",
                "tools": [{"name": t["name"], "description": t["description"]}
                          for t in b.get("tools", [])],
                "error": b.get("error", ""),
                "uptime_s": round(time.time() - b.get("spawn_started_ts", time.time()), 1),
            })
    return out


def list_remote_tools() -> List[Dict[str, Any]]:
    """Return Hearth-shaped tool definitions for every connected MCP tool.
    Names are namespaced 'mcp_<server>_<tool>' so they don't collide with
    Hearth's built-in tools. tools.py loads this on each tool refresh."""
    out: List[Dict[str, Any]] = []
    with _BRIDGES_LOCK:
        for safe, b in _BRIDGES.items():
            if b.get("state") != "connected":
                continue
            for t in b.get("tools", []):
                full = f"mcp_{safe}_{_sanitize(t['name'])}"
                schema = t.get("input_schema") or {"type": "object",
                                                    "properties": {}}
                # Ensure top-level type=object so OpenAI accepts it
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    schema = {"type": "object", "properties": {}}
                out.append({
                    "name": full,
                    "description": f"[MCP via {b.get('raw_name', safe)}] {t['description']}",
                    "parameters": schema,
                    "_mcp_server": safe,
                    "_mcp_tool": t["name"],
                })
    return out


def call_tool(server: str, tool: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Sync entrypoint for tool dispatch in tools.py. Blocks the caller
    thread, runs the coro on the MCP loop, returns the formatted result.
    """
    loop = _ensure_loop()
    if loop is None:
        return {"ok": False, "error": "MCP client loop not running"}
    fut = asyncio.run_coroutine_threadsafe(
        _call_tool_async(server, tool, args or {}), loop)
    try:
        return fut.result(timeout=_TOOL_TIMEOUT_S + 10.0)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
