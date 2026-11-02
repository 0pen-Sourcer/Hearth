"""Saved secrets (provider keys, bot tokens, the licence key, the mail password),
kept sealed instead of as plain text.

On Windows a value is sealed with DPAPI (CryptProtectData, bound to this
Windows account) and stays in the file it belongs to as "sealed:dpapi:...", so
a program that reads the file gets nothing it can use elsewhere. Elsewhere the
value moves to a 0600 file outside the workspace (~/.config/inglen) and the
file keeps "sealed:file:<name>".

Readers call `reveal`/`read_json`; writers call `write_json`, which seals the
secret fields before anything reaches disk. A file still holding plain values
(an install from before) is sealed the first time it is read.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PREFIX = "sealed:"
_ENTROPY = b"Inglen saved keys"
_LOCK = threading.RLock()

# The secret fields of each file that holds any, by file name.
SETTINGS_FIELDS = ("llm_key", "provider_keys")
BRIDGE_FIELDS = ("bot_token",)
FIELDS: Dict[str, Tuple[str, ...]] = {
    "settings.json": SETTINGS_FIELDS,
    "phone_bridge.json": BRIDGE_FIELDS,
    "discord_bridge.json": BRIDGE_FIELDS,
    "whatsapp_bridge.json": BRIDGE_FIELDS,
    "plus.json": ("key",),
    "email.json": ("password",),
    "relay.json": ("pc_key",),
}


def backend() -> str:
    forced = os.environ.get("HEARTH_KEYSTORE", "").strip().lower()
    if forced in ("dpapi", "file"):
        return forced
    return "dpapi" if sys.platform == "win32" else "file"


# ---------------------------------------------------------------- DPAPI
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(data: bytes) -> Tuple["_Blob", Any]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _dpapi(data: bytes, protect: bool) -> bytes:
        src, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        if protect:
            ok = fn(ctypes.byref(src), "Inglen", ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
        else:
            ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
        if not ok:
            raise OSError(ctypes.get_last_error(), "DPAPI failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)
else:
    def _dpapi(data: bytes, protect: bool) -> bytes:
        raise OSError("DPAPI is Windows only")


# ---------------------------------------------------------------- the 0600 file
def store_path() -> str:
    """Where sealed values live when DPAPI isn't there: outside the workspace."""
    base = (os.environ.get("HEARTH_SECRETS_DIR")
            or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                            "inglen"))
    return os.path.join(base, "secrets.json")


def _store_read() -> Dict[str, Any]:
    try:
        with open(store_path(), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _store_write(d: Dict[str, Any]) -> None:
    p = store_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        os.chmod(os.path.dirname(p), 0o700)
    except OSError:
        pass
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- one value
def is_sealed(v: Any) -> bool:
    return isinstance(v, str) and v.startswith(PREFIX)


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == {} or v == []


def seal(value: Any, name: str) -> Any:
    """`value` sealed; `name` says where it belongs (the file store's slot)."""
    if _empty(value) or is_sealed(value):
        return value
    raw = json.dumps(value).encode("utf-8")
    if backend() == "dpapi":
        return PREFIX + "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    with _LOCK:
        d = _store_read()
        d[name] = value
        _store_write(d)
    return PREFIX + "file:" + name


def unseal(value: Any) -> Any:
    """The plain value; a plain one comes back as it is. None when it can't be opened."""
    if not is_sealed(value):
        return value
    rest = value[len(PREFIX):]
    try:
        if rest.startswith("dpapi:"):
            return json.loads(_dpapi(base64.b64decode(rest[6:]), False).decode("utf-8"))
        if rest.startswith("file:"):
            return _store_read().get(rest[5:])
    except (OSError, ValueError):
        return None
    return None


def _slot(path: str, field: str) -> str:
    h = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:12]
    return f"{os.path.basename(path)}:{h}:{field}"


def fields_for(path: str) -> Tuple[str, ...]:
    return FIELDS.get(os.path.basename(str(path or "")).lower(), ())


# ---------------------------------------------------------------- a file's fields
# Fields that hold one key per provider. Every reader calls .get on them.
_MAPS = ("provider_keys",)


def _note(msg: str) -> None:
    try:
        print(f"[hearth.keystore] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _as_map(field: str, v: Any) -> Dict[str, Any]:
    """A per-provider field as a dict, whatever the file held: a dict saved as
    JSON text is read back, anything else (a value still sealed, a stray
    string) counts as no keys, with a line in the log. Never the values."""
    if isinstance(v, dict):
        return v
    if _empty(v):
        return {}
    if isinstance(v, str) and not is_sealed(v):
        try:
            j = json.loads(v)
        except ValueError:
            j = None
        if isinstance(j, dict):
            return j
    what = "a sealed value that could not be opened" if is_sealed(v) else f"a {type(v).__name__}"
    _note(f"{field} holds {what}, not a set of keys; read as none")
    return {}


def reveal(d: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    out = dict(d or {})
    for k in fields:
        if k not in out:
            continue
        if is_sealed(out[k]):
            v = unseal(out[k])
            if v is None and k not in _MAPS:
                _note(f"{k} is sealed and could not be opened on this account; read as empty")
            out[k] = v if v is not None else ("" if k not in _MAPS else out[k])
        if k in _MAPS:
            out[k] = _as_map(k, out[k])
    return out


def conceal(d: Dict[str, Any], fields: Iterable[str], path: str) -> Dict[str, Any]:
    out = dict(d or {})
    for k in fields:
        if k in out and not _empty(out[k]) and not is_sealed(out[k]):
            out[k] = seal(out[k], _slot(path, k))
    return out


def has_plain(d: Dict[str, Any], fields: Iterable[str]) -> bool:
    return any(k in (d or {}) and not _empty(d[k]) and not is_sealed(d[k]) for k in fields)


def _read_raw(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_raw(path: str, d: Dict[str, Any], indent: int = 2) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=indent)
    for _ in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            import time
            time.sleep(0.05)
    else:
        os.replace(tmp, path)
    if os.name == "posix" and os.path.basename(path) != "settings.json":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _under(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.realpath(path))
    r = os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


_APP_MARK = "HEARTH_KEYS_SEALED"


def app_start() -> List[str]:
    """Called once by Inglen's own start (the tray, the desktop window, the
    terminal): from here on this process and the programs it starts seal saved
    keys, and files an older version left plain are sealed now. Nothing else
    turns sealing on, so code run by a test can never seal the real files."""
    if not os.environ.get("HEARTH_TEST_HOME"):
        os.environ[_APP_MARK] = "1"
    return migrate()


def may_seal(path: str) -> bool:
    """Whether a save may seal this file. A file in the temp folder (a test's own)
    always may. Any other only in Inglen itself (app_start ran in this process or
    the one that started it), never during a test run (HEARTH_TEST_HOME, or a temp
    workspace): sealing the real install's files under a test breaks the copy
    that is installed."""
    import tempfile
    tmp = tempfile.gettempdir()
    if _under(path, tmp):
        return True
    if os.environ.get("HEARTH_TEST_HOME"):
        return False
    if os.environ.get(_APP_MARK) != "1":
        # Already sealed by Inglen: keep it so, rather than write a key back as plain text.
        d = _read_raw(path)
        return any(is_sealed(v) for v in d.values()) or any(
            isinstance(v, dict) and any(is_sealed(x) for x in v.values()) for v in d.values())
    try:
        return not _under(_workspace(), tmp)
    except Exception:
        return False


def read_json(path: str, fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """A JSON file with its secret fields opened. Reading never writes: `migrate` seals old files."""
    fields = tuple(fields) if fields is not None else fields_for(path)
    return reveal(_read_raw(path), fields)


def write_json(path: str, d: Dict[str, Any], fields: Optional[Iterable[str]] = None, indent: int = 2) -> None:
    fields = tuple(fields) if fields is not None else fields_for(path)
    with _LOCK:
        _write_raw(path, conceal(d, fields, path) if may_seal(path) else dict(d), indent=indent)


def seal_file(path: str) -> bool:
    """Seal any plain secret in this file. True when it changed."""
    fields = fields_for(path)
    if not fields or not os.path.isfile(path) or not may_seal(path):
        return False
    with _LOCK:
        d = _read_raw(path)
        if not has_plain(d, fields):
            return False
        _write_raw(path, conceal(d, fields, path))
        return True


# ---------------------------------------------------------------- the whole set
def _workspace() -> str:
    from . import env as _envmod
    return _envmod.get("WORKSPACE") or __import__("hearth").default_workspace()


def _hearth_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".hearth")


def secret_files() -> List[str]:
    """Every file that holds a saved secret."""
    ws = _workspace()
    out = [os.path.join(ws, "settings.json"), os.path.join(ws, "brain_keys.json")]
    out += [os.path.join(_hearth_dir(), n) for n in FIELDS if n != "settings.json"]
    return out


def settings_path() -> str:
    return os.path.join(_workspace(), "settings.json")


def read_settings(path: Optional[str] = None) -> Dict[str, Any]:
    return read_json(path or settings_path(), SETTINGS_FIELDS)


def write_settings(d: Dict[str, Any], path: Optional[str] = None) -> None:
    write_json(path or settings_path(), d, SETTINGS_FIELDS)


def update_settings(path: Optional[str] = None, **changes: Any) -> Dict[str, Any]:
    """Change some settings in place, keys sealed. Returns the settings, opened."""
    path = path or settings_path()
    with _LOCK:
        s = read_settings(path)
        s.update(changes)
        write_settings(s, path)
    return s


def brain_keys(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """The terminal's per-provider entries ({url, key, model}) with keys opened."""
    path = path or os.path.join(_workspace(), "brain_keys.json")
    return {p: reveal(v, ("key",)) for p, v in _read_raw(path).items() if isinstance(v, dict)}


def save_brain_keys(d: Dict[str, Dict[str, Any]], path: Optional[str] = None) -> None:
    path = path or os.path.join(_workspace(), "brain_keys.json")
    seal_it = may_seal(path)
    with _LOCK:
        _write_raw(path, {p: conceal(v, ("key",), path + ":" + p) if isinstance(v, dict) and seal_it else v
                          for p, v in (d or {}).items()})


def migrate() -> List[str]:
    """Seal every plain secret Inglen has saved. Returns the files changed."""
    changed = []
    for p in secret_files():
        if not may_seal(p):
            continue
        try:
            if os.path.basename(p) == "brain_keys.json":
                raw = _read_raw(p)
                if any(isinstance(v, dict) and has_plain(v, ("key",)) for v in raw.values()):
                    save_brain_keys(brain_keys(p), p)
                    changed.append(p)
            elif seal_file(p):
                changed.append(p)
        except OSError:
            pass
    return changed


_KNOWN: Dict[str, Any] = {"sig": None, "values": set()}
# Environment variables Inglen puts a key in, or reads one from.
_ENV_SECRETS = ("LOCAL_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HEARTH_EMAIL_PASSWORD",
                "JARVIS_EMAIL_PASSWORD")
# What Inglen itself puts in the environment that an agent program has no use for.
_AGENT_STRIP = ("LOCAL_API_KEY", "HEARTH_EMAIL_PASSWORD", "JARVIS_EMAIL_PASSWORD")


def agent_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for an agent program: this one without the keys Inglen put
    in it (the brain's key, the mail password), so its shell can't print them."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in _AGENT_STRIP}
    env.update(extra or {})
    return env


def _flatten(v: Any, out: Set[str]) -> None:
    if isinstance(v, dict):
        for x in v.values():
            _flatten(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _flatten(x, out)
    elif isinstance(v, str):
        s = v.strip()
        if len(s) >= 8 and s.lower() not in ("hearth-builtin", "jarvis-local"):
            out.add(s)


def known_values() -> Set[str]:
    """Every saved secret value, opened, for redaction. Cached until a file changes."""
    files = secret_files()
    sig = []
    for p in files:
        try:
            st = os.stat(p)
            sig.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((p, 0, 0))
    env = tuple(os.environ.get(k) or "" for k in _ENV_SECRETS)
    sig_t = (tuple(sig), env)
    if _KNOWN["sig"] == sig_t:
        return _KNOWN["values"]
    vals: Set[str] = set()
    for p in files:
        name = os.path.basename(p)
        try:
            if name == "brain_keys.json":
                for v in brain_keys(p).values():
                    _flatten(v.get("key"), vals)
                continue
            d = reveal(_read_raw(p), fields_for(p))
            for k in fields_for(p):
                _flatten(d.get(k), vals)
        except Exception:
            continue
    for v in env:
        _flatten(v, vals)
    _KNOWN.update(sig=sig_t, values=vals)
    return vals
