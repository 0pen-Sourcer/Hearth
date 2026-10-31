"""Places that hold keys, and the calls that would read them.

Reading one asks in every approval mode, and Auto's reviewer can't let it
through (tools.sends_out). The places: Inglen's own sealed store and key files
in ~/.hearth, .env files, ~/.ssh, ~/.aws, ~/.config/gcloud, ~/.docker/config.json,
.netrc, a browser's saved logins and cookies, Windows' saved credentials, and a
settings.json in the workspace while it still holds keys as plain text. Paths
are matched after ~, $env:, %VAR% and relative paths are resolved, inside
shell commands as well as tool arguments.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, Dict, Iterable, List, Optional

# Tool arguments that name a file or folder to read.
_PATH_ARGS = ("path", "file", "file_path", "filePath", "source", "archive", "paths", "files", "folder",
              "directory", "dir", "target", "image", "attachment", "attachments")
_SHELL = {"run_command", "start_job"}
_SHELL_ARGS = ("command", "cmd", "script", "code")

_ENV_FILE = re.compile(r"^\.env(\..+)?$", re.I)
_ENV_SAFE = re.compile(r"^\.env\.(example|sample|template|dist|defaults)$", re.I)
# Folders that hold keys wherever they are (another account's home, a copy).
_SECRET_DIRS = {".ssh": "SSH keys", ".aws": "AWS credentials", ".hearth": "Inglen's key files",
                ".gnupg": "GPG keys", ".azure": "Azure credentials"}
_BROWSER_FILES = {"login data", "login data for account", "cookies", "cookies.sqlite", "logins.json",
                  "key4.db", "web data"}

# Commands that pull out saved passwords or keys without naming a file.
_DUMPERS = [
    (re.compile(r"\b(mimikatz|lazagne|sekurlsa|sharpdpapi|sharpchrome|hackbrowserdata|"
                r"credentialfileview|webbrowserpassview|netpass)\b", re.I), "saved passwords (a tool that extracts them)"),
    (re.compile(r"ProtectedData\]?::Unprotect|CryptUnprotectData|Unprotect-CmsMessage|dpapi::", re.I),
     "sealed keys (it would unseal them)"),
    (re.compile(r"\b(Get-StoredCredential|CredRead|CredEnumerate|vaultcmd\b[^\n;|&]*/list(creds)?)", re.I),
     "Windows' saved credentials"),
    (re.compile(r"\breg(\.exe)?\s+(save|export)\s+\"?hklm\\(sam|security|system)\b", re.I),
     "Windows' password database"),
    (re.compile(r"\bminidump\b|\bprocdump\b[^\n;|&]*lsass", re.I), "Windows' signed-in passwords (a memory dump)"),
    (re.compile(r"\bsecurity\s+(find|dump)-(generic|internet)-password|\bsecurity\s+dump-keychain\b|"
                r"\bsecret-tool\s+lookup\b", re.I), "the keychain's saved passwords"),
    (re.compile(r"\bgh\s+auth\s+token\b|\bgit\s+credential(-manager)?\s+(fill|get)\b|"
                r"\bgcloud\s+auth\s+(print-access-token|print-identity-token|application-default\s+print)|"
                r"\baws\s+configure\s+(get|export-credentials)\b|\bdocker-credential-\w+\s+get\b", re.I),
     "a saved login token"),
]
# An environment variable that holds a key, or the whole environment.
_KEY_VAR = r"[A-Za-z0-9_]*(?:API_?KEY|_TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*"
_ENV_READS = [
    re.compile(r"\$env:(" + _KEY_VAR + r")\b", re.I),
    re.compile(r"%(" + _KEY_VAR + r")%", re.I),
    re.compile(r"\$\{?(" + _KEY_VAR + r")\b"),
    re.compile(r"\b(?:printenv|getenv|environ(?:\.get)?|GetEnvironmentVariable)\b\W{0,4}['\"]?(" + _KEY_VAR + r")",
               re.I),
]
_ENV_DUMP = re.compile(r"(?:^|[;&|]\s*)(?:(?:Get-ChildItem|gci|dir|ls)\s+env:\S*\s*(?:$|[;&|])|printenv\s*(?:$|[;&|])|"
                       r"env\s*(?:$|[;&|])|set\s*(?:$|[;&|])|\[(?:System\.)?Environment\]::GetEnvironmentVariables)",
                       re.I | re.M)
# Searching a whole home or drive for key-shaped text.
_KEY_HUNT = re.compile(r"\b(grep|rg|findstr|Select-String|sls|ag|ack)\b[^\n;|&]*"
                       r"(sk-|api[_-]?key|secret|token|password|passwd|AIza|xai-|gsk_|PRIVATE KEY|bearer)", re.I)


def _home() -> str:
    return os.path.expanduser("~")


def _workspace() -> str:
    from . import env as _envmod
    return _envmod.get("WORKSPACE") or __import__("hearth").default_workspace()


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).replace("\\", "/")


def expand(text: str) -> str:
    """~, $env:NAME, ${env:NAME}, %NAME%, $HOME and ${NAME} replaced by their values."""
    s = str(text or "")

    def env(name: str, whole: str) -> str:
        if name.upper() in ("HOME", "USERPROFILE"):
            return _home()
        v = os.environ.get(name)
        return v if v is not None else whole

    s = re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: env(m.group(1), m.group(0)), s, flags=re.I)
    s = re.sub(r"\$env:([A-Za-z_][A-Za-z0-9_]*)", lambda m: env(m.group(1), m.group(0)), s, flags=re.I)
    s = re.sub(r"%([A-Za-z_][A-Za-z0-9_()]*)%", lambda m: env(m.group(1), m.group(0)), s)
    s = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: env(m.group(1), m.group(0)), s)
    s = re.sub(r"\$(HOME|USERPROFILE|APPDATA|LOCALAPPDATA)\b", lambda m: env(m.group(1), m.group(0)), s, flags=re.I)
    s = re.sub(r"(^|[\s'\"=(,;|&])~(?=[\\/]|$|[\s'\";|&)])", lambda m: m.group(1) + _home(), s)
    return s


def _plain_settings(path: str) -> bool:
    try:
        from . import keystore
        return keystore.has_plain(keystore._read_raw(path), keystore.SETTINGS_FIELDS)
    except Exception:
        return False


def _plain_brain_keys(path: str) -> bool:
    try:
        from . import keystore
        return any(isinstance(v, dict) and keystore.has_plain(v, ("key",)) for v in keystore._read_raw(path).values())
    except Exception:
        return False


def _places() -> List[tuple]:
    """(folder or file, what it is, is_folder) for the fixed places."""
    h = _home()
    out = [
        (os.path.join(h, ".hearth"), "one of Inglen's own key files (bot tokens, the licence key, the PC's key)", True),
        (os.path.join(h, ".ssh"), "your SSH keys", True),
        (os.path.join(h, ".aws"), "your AWS credentials", True),
        (os.path.join(h, ".config", "gcloud"), "your Google Cloud credentials", True),
        (os.path.join(h, ".azure"), "your Azure credentials", True),
        (os.path.join(h, ".docker", "config.json"), "Docker's saved registry logins", False),
        (os.path.join(h, ".netrc"), "saved logins (.netrc)", False),
        (os.path.join(h, "_netrc"), "saved logins (_netrc)", False),
        (os.path.join(h, ".git-credentials"), "Git's saved logins", False),
        (os.path.join(h, ".npmrc"), "npm's saved token", False),
        (os.path.join(h, ".pypirc"), "PyPI's saved token", False),
    ]
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if base:
            for sub in ("Credentials", "Protect", "Vault"):
                out.append((os.path.join(base, "Microsoft", sub), "Windows' saved credentials", True))
    try:
        from . import keystore
        out.append((os.path.dirname(keystore.store_path()), "Inglen's key store", True))
    except Exception:
        pass
    return out


def what_path(path: str, cwd: str = "") -> str:
    """What secret this path is or holds, in plain words; '' when it isn't one."""
    raw = expand(str(path or "").strip().strip("'\""))
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = os.path.join(cwd or _workspace(), raw)
    try:
        full = os.path.realpath(raw) if not any(c in raw for c in "*?[") else os.path.normpath(raw)
    except (OSError, ValueError):
        full = os.path.normpath(raw)
    n = _norm(full)
    base = os.path.basename(n)
    glob = any(c in base for c in "*?[")
    if _ENV_FILE.match(base) and not _ENV_SAFE.match(base):
        return f"{full}, an .env file, where projects keep keys and passwords"
    for place, what, folder in _places():
        p = _norm(os.path.realpath(place))
        if n == p or (folder and n.startswith(p.rstrip("/") + "/")):
            return f"{full}: {what}"
        if glob and base.startswith(".") and fnmatch.fnmatch(p, n):     # ~/.s*, ~/.*: aimed at dot folders
            return f"{place}: {what}"
    segs = n.split("/")
    for i, seg in enumerate(segs[:-1] if not glob else segs):
        if seg in _SECRET_DIRS:
            return f"{full}: {_SECRET_DIRS[seg]}"
        if seg == ".config" and i + 1 < len(segs) and segs[i + 1] == "gcloud":
            return f"{full}: Google Cloud credentials"
    if segs[-2:] == [".docker", "config.json"]:
        return f"{full}: Docker's saved registry logins"
    if base in _BROWSER_FILES or (base == "local state" and "/user data/" in n):
        return f"{full}: the browser's saved passwords and sign-in cookies"
    ws = _norm(os.path.realpath(_workspace()))
    if n.startswith(ws.rstrip("/") + "/") or n == ws:
        for name, plain in (("settings.json", _plain_settings), ("brain_keys.json", _plain_brain_keys)):
            target = os.path.join(_workspace(), name)
            hit = base == name or (glob and fnmatch.fnmatch(name, base))
            if hit and n.rsplit("/", 1)[0] == ws and plain(target):
                return f"{target}, which still holds your API keys as plain text"
    return ""


def _strings(v: Any) -> Iterable[str]:
    if isinstance(v, str):
        yield v
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for k in ("path", "file", "file_path", "filePath", "source"):
            if isinstance(v.get(k), str):
                yield v[k]


_SPLIT = re.compile(r"[\s'\"`;|&<>(),\[\]{}]+")
_QUOTED = re.compile(r"\"([^\"]+)\"|'([^']+)'")


def _command_paths(cmd: str) -> List[str]:
    s = expand(cmd)
    out = [a or b for a, b in _QUOTED.findall(s)]
    for tok in _SPLIT.split(s):
        if not tok:
            continue
        out.append(tok)
        for sep in ("=", ":"):          # --file=x, -Path:x
            if sep in tok[1:] and not re.match(r"^[A-Za-z]:[\\/]", tok):
                out.append(tok.split(sep, 1)[1])
    return out


def command_reason(cmd: str, cwd: str = "") -> str:
    if not cmd:
        return ""
    for pat, what in _DUMPERS:
        if pat.search(cmd):
            return f"This would read {what}."
    for pat in _ENV_READS:
        m = pat.search(cmd)
        if m:
            return f"This would read the environment variable {m.group(1)}, which holds a key or password."
    if _ENV_DUMP.search(cmd):
        return "This would list every environment variable, and some of them hold keys."
    for tok in _command_paths(cmd):
        w = what_path(tok, cwd)
        if w:
            return f"This would read {w}."
    if _KEY_HUNT.search(cmd):
        h = _norm(os.path.realpath(_home()))
        roots = [_norm(os.path.realpath(expand(t))) for t in _SPLIT.split(cmd) if t and not t.startswith("-")]
        if any(r == h or re.fullmatch(r"[a-z]:/?", r) or r == "/" for r in roots):
            return "This would search your whole home folder or drive for keys and passwords."
    return ""


def reason(name: str, args: Any = None) -> str:
    """Why this call asks in every mode because it would read a place that holds
    keys; '' when it doesn't."""
    try:
        from .outbound import tool_name
        name = tool_name(name)
    except Exception:
        name = str(name or "")
    a = args if isinstance(args, dict) else {}
    cwd = str(a.get("cwd") or a.get("workdir") or "")
    if cwd:
        cwd = expand(cwd)
        w = what_path(cwd)
        if w and name in _SHELL:
            return f"This would run a command inside {w}."
    if name in _SHELL or name.lower() in ("bash", "powershell", "shell", "exec", "execute"):
        for k in _SHELL_ARGS:
            v = a.get(k)
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            r = command_reason(str(v or ""), cwd)
            if r:
                return r + " It always asks, whatever the approval mode."
        return ""
    for k in _PATH_ARGS:
        for s in _strings(a.get(k)):
            w = what_path(s, cwd)
            if w:
                return f"This would read {w}. It always asks, whatever the approval mode."
    if name in ("grep_search", "find_file", "search_files") and _KEY_HUNT.search("grep " + str(a.get("pattern") or "")):
        root = _norm(os.path.realpath(expand(str(a.get("path") or "")) or _workspace()))
        if root == _norm(os.path.realpath(_home())) or re.fullmatch(r"[a-z]:/?", root) or root == "/":
            return "This would search your whole home folder or drive for keys and passwords. It always asks."
    return ""


def _fixed_globs() -> List[str]:
    """The places as globs, ~ for the home folder, / between parts."""
    h = _home().replace("\\", "/")
    out = []
    for place, _what, folder in _places():
        p = place.replace("\\", "/")
        p = "~" + p[len(h):] if p.lower().startswith(h.lower()) else p
        out.append(p + "/**" if folder else p)
    return out


def claude_rules() -> List[str]:
    """Claude Code permission rules: a read of these comes to Inglen's check first."""
    out = []
    for g in _fixed_globs() + ["**/.env", "**/.env.*", "**/Login Data", "**/Cookies", "**/logins.json"]:
        if re.match(r"^[A-Za-z]:/", g):
            g = "//" + g[0].lower() + g[2:]            # Claude Code's spelling of an absolute path
        out.append(f"Read({g})")
    return out


def opencode_read() -> Dict[str, str]:
    """OpenCode's read permission: reads go ahead, except a key place, which asks
    (and so comes to Inglen). Later patterns win."""
    rules: Dict[str, str] = {"*": "allow"}
    for g in _fixed_globs():
        tail = g[1:] if g.startswith("~") else g
        tail = tail.rstrip("*").rstrip("/")
        for sep in ("/", "\\"):
            t = tail.replace("/", sep)
            rules[f"*{t}" + (f"{sep}*" if g.endswith("**") else "")] = "ask"
    for pat in ("*.env", "*.env.*", "*Login Data", "*Cookies", "*logins.json"):
        rules[pat] = "ask"
    rules["*.env.example"] = rules["*.env.sample"] = rules["*.env.template"] = "allow"
    return rules
