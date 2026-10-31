"""Where Node.js is, found without trusting PATH alone.

npm-based agents, their updates and MCP servers started with npx all need
node. On Windows they go through cmd.exe, which reads PATH itself: one entry
with a stray quote (`C:\\Program Files\\nodejs"`) makes cmd skip everything
after it, and node "is not recognized" though it is installed. So node is
looked up by full path (PATH, the installer's folders, the registry, nvm,
scoop, volta), and the PATH children get is cleaned and starts with node's
folder and npm's global folder.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from typing import Dict, List, Optional

_WIN = sys.platform == "win32"
_NODE = "node.exe" if _WIN else "node"
_found: Dict[str, Optional[str]] = {}


def clean_path(path: str) -> List[str]:
    """PATH's entries without quotes, blanks or repeats, in order."""
    out: List[str] = []
    seen = set()
    for d in (path or "").split(os.pathsep):
        d = d.strip().replace('"', "").strip()
        if not d:
            continue
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _registry_dirs() -> List[str]:
    if not _WIN:
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (r"SOFTWARE\Node.js", r"SOFTWARE\WOW6432Node\Node.js"):
            try:
                with winreg.OpenKey(hive, key) as k:
                    out.append(str(winreg.QueryValueEx(k, "InstallPath")[0]))
            except OSError:
                continue
    return out


def _nvm_dirs() -> List[str]:
    out = []
    link = os.environ.get("NVM_SYMLINK")
    if link:
        out.append(link)
    homes = [os.environ.get("NVM_HOME") or "", os.path.expandvars(r"%APPDATA%\nvm") if _WIN else ""]
    for home in filter(None, homes):
        versions = [d for d in glob.glob(os.path.join(home, "v*")) if os.path.isfile(os.path.join(d, _NODE))]
        out += sorted(versions, key=os.path.getmtime, reverse=True)
    nvm_unix = os.path.expanduser("~/.nvm/versions/node")
    out += sorted(glob.glob(os.path.join(nvm_unix, "*", "bin")), key=os.path.getmtime, reverse=True)
    return out


def candidate_dirs() -> List[str]:
    """Folders node is commonly installed in, likeliest first."""
    env = os.environ.get
    if _WIN:
        dirs = [os.path.join(env("ProgramFiles") or r"C:\Program Files", "nodejs"),
                os.path.join(env("ProgramFiles(x86)") or r"C:\Program Files (x86)", "nodejs"),
                os.path.join(env("LOCALAPPDATA") or "", "Programs", "nodejs")]
        dirs += _registry_dirs() + _nvm_dirs()
        dirs += [os.path.expanduser(r"~\scoop\apps\nodejs\current"),
                 os.path.expanduser(r"~\scoop\apps\nodejs-lts\current"),
                 os.path.join(env("LOCALAPPDATA") or "", "Volta", "bin"),
                 os.path.join(env("ProgramFiles") or r"C:\Program Files", "Volta")]
    else:
        dirs = ["/usr/local/bin", "/usr/bin", "/opt/homebrew/bin"] + _nvm_dirs()
        dirs += [os.path.expanduser("~/.volta/bin")]
    return [d for d in dirs if d]


def node() -> Optional[str]:
    """node's full path, or None when it isn't on this PC."""
    if "node" in _found:
        return _found["node"]
    hit = None
    for d in clean_path(os.environ.get("PATH", "")) + candidate_dirs():
        p = os.path.join(d, _NODE)
        if os.path.isfile(p):
            hit = p
            break
    _found["node"] = hit
    return hit


def node_dir() -> Optional[str]:
    n = node()
    return os.path.dirname(n) if n else None


def npm_prefix_dir() -> Optional[str]:
    """Where `npm install -g` puts its command shims."""
    if _WIN:
        d = os.path.expandvars(r"%APPDATA%\npm")
        return d if os.path.isdir(d) else None
    return None


def tool(name: str) -> Optional[str]:
    """npm or npx by full path: beside node first, then PATH."""
    nd = node_dir()
    names = (name + ".cmd", name + ".exe", name) if _WIN else (name,)
    if nd:
        for n in names:
            p = os.path.join(nd, n)
            if os.path.isfile(p):
                return p
    search = os.pathsep.join(path_entries())
    for n in names:
        p = shutil.which(n, path=search)
        if p:
            return p
    return None


def npm() -> Optional[str]:
    return tool("npm")


def npx() -> Optional[str]:
    return tool("npx")


def path_entries(path: Optional[str] = None) -> List[str]:
    """A clean PATH with node's folder and npm's global folder first."""
    front = [d for d in (node_dir(), npm_prefix_dir()) if d]
    rest = clean_path(os.environ.get("PATH", "") if path is None else path)
    keys = {os.path.normcase(os.path.normpath(d)) for d in front}
    return front + [d for d in rest if os.path.normcase(os.path.normpath(d)) not in keys]


def child_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """`env` (or this process's environment) with that PATH."""
    out = dict(os.environ if env is None else env)
    key = next((k for k in out if k.upper() == "PATH"), "PATH")
    out[key] = os.pathsep.join(path_entries(out.get(key, "")))
    return out


def fix_process_path() -> None:
    """Give this process that PATH, so everything it starts inherits it."""
    key = next((k for k in os.environ if k.upper() == "PATH"), "PATH")
    os.environ[key] = os.pathsep.join(path_entries())


def resolve(command: str) -> str:
    """A bare node, npm or npx as its full path; anything else as given."""
    base = os.path.basename(command or "").lower()
    for ext in (".cmd", ".exe"):
        if base.endswith(ext):
            base = base[:-len(ext)]
    if os.path.dirname(command or "") or base not in ("node", "npm", "npx"):
        return command
    found = node() if base == "node" else tool(base)
    return found or command


def forget() -> None:
    _found.clear()
