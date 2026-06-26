"""Local plugin system — the gap-bridge (Hermes's self-improving hook, but
100% local + private, no cloud, no telemetry, no account).

Two layers:
  1. AUTO-LOAD: any `~/Jarvis/plugins/*.py` that follows the contract below is
     loaded as a first-class tool at startup, right alongside the built-ins.
  2. SELF-AUTHORING: the `create_plugin` tool lets the agent WRITE a new plugin
     when it hits a capability gap, validate it, and use it the same session.
     (Defined in tools.py; the validation lives here.)

Plugin contract — a plugin file defines exactly two module-level names:

    TOOL = {
        "name": "reverse_text",
        "description": "Reverse a string. Use when the user wants text reversed.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The text."}},
            "required": ["text"],
        },
    }

    def run(args: dict) -> str:
        return (args.get("text") or "")[::-1]

Safety: loading is fully sandboxed in try/except — a broken plugin is skipped
with a warning and can NEVER crash the core tool registry. A plugin may not
shadow a built-in tool name. Plugin `run()` is arbitrary local Python (same
trust level as run_command), so `create_plugin` is permission-gated upstream.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple


def plugins_dir(workspace: str) -> str:
    return os.path.join(workspace, "plugins")


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def validate_plugin_source(code: str) -> Tuple[Optional[dict], Optional[Callable], Optional[str]]:
    """Compile + exec plugin source in a fresh namespace and pull out (TOOL,
    run). Returns (tool_dict, run_callable, error). On success error is None."""
    try:
        compiled = compile(code, "<plugin>", "exec")
    except SyntaxError as e:
        return None, None, f"syntax error: {e}"
    ns: Dict[str, Any] = {}
    try:
        exec(compiled, ns)  # plugin code is trusted local code (like run_command)
    except Exception as e:
        return None, None, f"failed to import: {type(e).__name__}: {e}"

    tool = ns.get("TOOL")
    run = ns.get("run")
    if not isinstance(tool, dict):
        return None, None, "missing a module-level TOOL = {...} dict"
    if not callable(run):
        return None, None, "missing a module-level run(args) function"
    name = tool.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return None, None, "TOOL['name'] must be lower_snake_case (a-z, 0-9, _)"
    if not isinstance(tool.get("description"), str) or not tool["description"].strip():
        return None, None, "TOOL needs a non-empty 'description'"
    params = tool.get("parameters")
    if not isinstance(params, dict) or params.get("type") != "object":
        return None, None, "TOOL['parameters'] must be a JSON-schema object ({'type':'object',...})"
    return tool, run, None


def _register(tool: dict, run: Callable, tool_defs: list, handlers: dict,
              reserved: set) -> Optional[str]:
    """Add a validated plugin to the live registry. Returns an error reason if
    it can't (name collision), else None."""
    name = tool["name"]
    if name in reserved or name in handlers or any(t["name"] == name for t in tool_defs):
        return f"name '{name}' already exists (a plugin can't shadow a built-in tool)"
    tool_defs.append({
        "name": name,
        "description": tool["description"],
        "parameters": tool["parameters"],
        # Mark as a user plugin so the core stays lean: plugins are deferred by
        # default (rediscovered via load_tools) UNLESS they opt in with
        # TOOL["core"] = True. Keeps the prompt bounded no matter how many
        # plugins a user writes.
        "_plugin": True,
        "core": bool(tool.get("core")),
    })

    def _wrapped(args, _run=run):
        out = _run(args or {})
        return out if isinstance(out, str) else str(out)

    handlers[name] = _wrapped
    return None


def load_plugins(workspace: str, tool_defs: list, handlers: dict) -> List[str]:
    """Scan the plugins dir and register every valid plugin. Never raises —
    returns the list of loaded plugin tool names. `reserved` is the set of
    built-in names captured BEFORE loading, so plugins can't shadow them."""
    pdir = plugins_dir(workspace)
    loaded: List[str] = []
    if not os.path.isdir(pdir):
        return loaded
    reserved = {t["name"] for t in tool_defs} | set(handlers)
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(pdir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
            tool, run, err = validate_plugin_source(code)
            if err:
                _warn(f"plugin '{fn}' skipped — {err}")
                continue
            reg_err = _register(tool, run, tool_defs, handlers, reserved)
            if reg_err:
                _warn(f"plugin '{fn}' skipped — {reg_err}")
                continue
            loaded.append(tool["name"])
        except Exception as e:  # belt-and-suspenders: never break core tools
            _warn(f"plugin '{fn}' skipped — {type(e).__name__}: {e}")
    return loaded


def save_and_register(name: str, code: str, workspace: str,
                      tool_defs: list, handlers: dict) -> str:
    """Validate + write a new plugin to disk + register it live (used by the
    `create_plugin` tool). Returns a human result string."""
    if not _NAME_RE.match(name or ""):
        return "Error: plugin name must be lower_snake_case (a-z, 0-9, _), 2-41 chars."
    tool, run, err = validate_plugin_source(code)
    if err:
        return (
            f"Error: plugin code invalid — {err}. Nothing was written.\n"
            f"NEXT STEP: FIX exactly that error and call create_plugin AGAIN with "
            f"corrected `code`. Keep the plugin SMALL — one focused capability, a "
            f"few lines, plain stdlib. Do NOT fall back to write_file / run_command "
            f"/ edit_file, and do NOT try to run the plugin as a script — plugins are "
            f"loaded, not executed. Just re-call create_plugin with valid code."
        )
    if tool["name"] != name:
        return f"Error: TOOL['name'] is '{tool['name']}' but file name is '{name}' — make them match."
    reserved = {t["name"] for t in tool_defs} | set(handlers)
    # Allow OVERWRITING an existing plugin of the same name (re-authoring), but
    # never a built-in.
    if name in reserved and name not in _plugin_names(workspace):
        return f"Error: '{name}' is a built-in tool name — pick a different name."

    pdir = plugins_dir(workspace)
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, f"{name}.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code if code.endswith("\n") else code + "\n")
    except OSError as e:
        return f"Error: couldn't write plugin file: {e}"

    # Register live (replace any prior instance of this plugin).
    tool_defs[:] = [t for t in tool_defs if t["name"] != name]
    handlers.pop(name, None)
    reg_err = _register(tool, run, tool_defs, handlers, reserved - {name})
    if reg_err:
        return f"Wrote {path}, but couldn't register live: {reg_err}"
    return (f"Plugin '{name}' created + loaded ({path}). It's available as a tool "
            f"NOW — call it like any other tool. It persists across restarts.")


def _plugin_names(workspace: str) -> set:
    pdir = plugins_dir(workspace)
    if not os.path.isdir(pdir):
        return set()
    return {fn[:-3] for fn in os.listdir(pdir)
            if fn.endswith(".py") and not fn.startswith("_")}


def list_plugins(workspace: str) -> str:
    """Human + model readable list of installed plugins (name · ok/broken ·
    description). The curator's eyes — what tools has the agent grown?"""
    pdir = plugins_dir(workspace)
    if not os.path.isdir(pdir):
        return "No plugins installed yet. (Use create_plugin to author one.)"
    files = sorted(fn for fn in os.listdir(pdir)
                   if fn.endswith(".py") and not fn.startswith("_"))
    if not files:
        return "No plugins installed yet. (Use create_plugin to author one.)"
    lines = [f"{len(files)} plugin(s) in {pdir}:"]
    for fn in files:
        name = fn[:-3]
        try:
            with open(os.path.join(pdir, fn), "r", encoding="utf-8") as f:
                tool, _run, err = validate_plugin_source(f.read())
        except Exception as e:
            err = str(e); tool = None
        if err:
            lines.append(f"  - {name}  [BROKEN: {err[:60]}]")
        else:
            desc = (tool.get("description") or "").split(".")[0][:70]
            lines.append(f"  - {name}  — {desc}")
    return "\n".join(lines)


def delete_plugin(name: str, workspace: str, tool_defs: list, handlers: dict) -> str:
    """Remove a plugin's file + unregister it live. Only plugins (never a
    built-in). Returns a result string."""
    name = (name or "").strip()
    if name not in _plugin_names(workspace):
        return f"Error: no plugin named '{name}'. Use list_plugins to see installed ones."
    path = os.path.join(plugins_dir(workspace), f"{name}.py")
    try:
        os.remove(path)
    except OSError as e:
        return f"Error: couldn't delete {path}: {e}"
    # Unregister live (only if it was a plugin-registered tool).
    before = len(tool_defs)
    tool_defs[:] = [t for t in tool_defs if t["name"] != name]
    handlers.pop(name, None)
    freed = "removed from the live toolset" if len(tool_defs) < before else "file deleted (wasn't loaded)"
    return f"Plugin '{name}' deleted ({path}) — {freed}."


def _warn(msg: str) -> None:
    # A skipped/malformed plugin is NOT an error — but printing this on every
    # launch reads like a crash to users (it did, for them). Stay quiet on the
    # console unless HEARTH_DEBUG is set; always keep a copy in the workspace log
    # for anyone actually debugging why a plugin didn't load.
    line = f"[hearth.plugins] {msg}\n"
    try:
        if os.environ.get("HEARTH_DEBUG"):
            sys.stderr.write(line)
    except Exception:
        pass
    try:
        from .tools import WORKSPACE
        logp = os.path.join(WORKSPACE, "logs", "plugins.log")
        os.makedirs(os.path.dirname(logp), exist_ok=True)
        with open(logp, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
