"""Optional built-in LLM server so Hearth works WITHOUT LM Studio or Ollama.

If `llama-cpp-python` is installed, this module spawns its OpenAI-compatible
server as a subprocess at the same address (`localhost:1234/v1`) that the rest
of Hearth already talks to. Drop-in replacement - no other code changes needed.

Strictly OPTIONAL. With LM Studio / Ollama / a remote endpoint, this module is
inert. To enable:

    pip install llama-cpp-python
    # GPU build on Windows + CUDA 12 + Python 3.11:
    pip install llama-cpp-python \
      --extra-index-url=https://abetlen.github.io/llama-cpp-python/whl/cu124

Workflow from a brand-new install:
    1. Hearth starts, sees no external server, sees llama-cpp-python installed.
    2. The GUI welcome card offers "use built-in server". User picks a model.
    3. download_from_hf_repo() pulls the GGUF from Hugging Face into ~/Jarvis/models/.
    4. start_builtin(path) spawns the server on 1234. Hearth talks to it normally.
    5. stop_builtin() shuts it down at exit (and on /api/llmserver/stop).
"""

from __future__ import annotations

import json
from . import env as _env
import math
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from .local_engine import local_base as _local_base

WORKSPACE = Path(_env.get("WORKSPACE") or __import__("hearth").default_workspace())

# Models can live on a different drive than the workspace, so a user whose C: is
# full can keep GGUFs on D:/E:/X: without moving their chats + memory too.
# Resolution: HEARTH_MODELS_DIR env, then the pointer ~/.hearth/models_dir.txt
# (written by Settings), then the default <workspace>/models. The pointer lives
# outside the workspace, same reasoning as workspace.txt.
_MODELS_POINTER = Path(os.path.expanduser("~")) / ".hearth" / "models_dir.txt"


def _resolve_models_dir() -> Path:
    env = os.environ.get("HEARTH_MODELS_DIR")
    if env and env.strip():
        return Path(env.strip()).expanduser()
    try:
        if _MODELS_POINTER.is_file():
            p = _MODELS_POINTER.read_text(encoding="utf-8").strip()
            if p:
                return Path(p).expanduser()
    except OSError:
        pass
    return WORKSPACE / "models"


def set_models_dir(path: str) -> Dict[str, Any]:
    """Point Hearth at a models folder (e.g. on a drive with space). Writes the
    pointer + creates the dir. Takes effect on the next launch (MODELS_DIR is read
    at import); existing models can be moved there or re-scanned in place."""
    global MODELS_DIR
    try:
        p = Path(os.path.abspath(os.path.expanduser((path or "").strip())))
        if not str(p) or p == p.parent and not p.drive:
            return {"ok": False, "error": "invalid path"}
        p.mkdir(parents=True, exist_ok=True)
        _MODELS_POINTER.parent.mkdir(parents=True, exist_ok=True)
        _MODELS_POINTER.write_text(str(p), encoding="utf-8")
        MODELS_DIR = p
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def get_models_dir() -> str:
    return str(MODELS_DIR)


MODELS_DIR = _resolve_models_dir()
try:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # A pointed-at drive that isn't mounted right now — fall back so import
    # still succeeds; the user fixes the path in Settings.
    MODELS_DIR = WORKSPACE / "models"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

FULL_DOWNLOAD_URL = "https://github.com/0pen-sourcer/hearth/releases"


def _is_lite_edition() -> bool:
    """True on a Lite install that still has no engine for local models. Once
    one has been added, Lite does everything Full does, so it is treated as
    Full wherever that matters."""
    try:
        from . import edition
        return edition.is_lite() and not find_native_llama_server()
    except Exception:
        return False

# Per-model load-config persistence. Keyed by normalized absolute path so the
# user only configures GPU offload / ctx / KV cache once per .gguf file and we
# auto-restore on every "Use this" click after.
MODEL_CONFIGS_PATH = WORKSPACE / "model_configs.json"


def _normalize_model_key(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return path


def load_model_configs() -> Dict[str, Dict[str, Any]]:
    """Return the saved per-model config dict, or {} on first run / corruption."""
    try:
        if MODEL_CONFIGS_PATH.exists():
            with open(MODEL_CONFIGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def get_model_config(model_path: str) -> Dict[str, Any]:
    """Return the saved load-config for a given .gguf path (empty dict if none)."""
    return load_model_configs().get(_normalize_model_key(model_path), {})


def save_model_config(model_path: str, config: Dict[str, Any]) -> None:
    """Persist this model's load-config. Idempotent — safe to call on every start."""
    data = load_model_configs()
    key = _normalize_model_key(model_path)
    # Drop None / empty values so they don't shadow future defaults
    clean = {k: v for k, v in (config or {}).items() if v not in (None, "")}
    if clean:
        data[key] = clean
    else:
        data.pop(key, None)
    try:
        MODEL_CONFIGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_CONFIGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

from .local_engine import builtin_port as _builtin_port, helper_port as _helper_port
BUILTIN_PORT = _builtin_port()     # its own port, not LM Studio's (local_engine)
BUILTIN_HOST = "127.0.0.1"
# Key the builtin server is spawned with; every local caller must send it.
BUILTIN_API_KEY = "hearth-builtin"
# Smallest context Hearth's own server will boot at. Persona (~11K) + tool
# schemas (~8K) are ~20K tokens, so below ~24K the prompt overflows with no room
# for a reply.
MIN_USABLE_CTX = int(_env.get("MIN_CTX", "24576"))  # 24K

# Concurrent request slots on the built-in server. llama.cpp splits --ctx-size
# EVENLY across slots, so N slots that each hold a usable prompt need N *
# per-slot ctx of KV cache. At 1 slot every request queues, which is why a team
# of sub-agents on a local model runs one at a time. Raising this is what lets
# them actually overlap, paid for in KV-cache VRAM.
MAX_PARALLEL_SLOTS = 4


# Cross-surface handoff. `_proc` only exists in the process that spawned the
# server, so a GUI polling a server the CLI booted (or vice versa) sees its own
# empty _proc and classifies Hearth's own server as a foreign endpoint — which
# is how it ends up talking about LM Studio on a machine with no LM Studio. The
# run-file lets any surface recognise the built-in and read its real config.
_RUNFILE_PATH = Path(os.path.expanduser("~/.hearth/builtin_server.json"))


def _write_runfile(info: Dict[str, Any]) -> None:
    try:
        _RUNFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(info)
        payload["pid"] = os.getpid()
        with open(_RUNFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except (OSError, TypeError, ValueError):
        pass


def _clear_runfile() -> None:
    try:
        _RUNFILE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def read_runfile(verify: bool = True) -> Dict[str, Any]:
    """Config of a built-in server started by ANOTHER Hearth surface, or {} if
    there isn't one. Verified live before being trusted, since a hard kill
    leaves the file behind. Pass verify=False when the caller has already
    confirmed something is answering, to skip a duplicate probe."""
    try:
        with open(_RUNFILE_PATH, encoding="utf-8") as f:
            info = json.load(f) or {}
    except (OSError, ValueError):
        return {}
    if not info.get("url"):
        return {}
    # Only ours answers to the built-in key, so this doubles as ownership proof.
    if verify and not external_server_running(info["url"], timeout=0.5,
                                              api_key=BUILTIN_API_KEY):
        _clear_runfile()
        return {}
    return info


def _port_of(url: str) -> Optional[int]:
    from urllib.parse import urlparse
    try:
        return urlparse(url).port
    except ValueError:
        return None


def vram_for_a_new_model() -> Optional[float]:
    """Graphics memory a model would have once loaded. Loading one stops
    Hearth's own server first, so what its current model holds counts as free;
    another program's model stays where it is."""
    free = detect_gpu_vram_free_gb()
    total = detect_gpu_vram_gb()
    if free is None:
        return total
    ours = (_proc is not None and _proc.poll() is None) or bool(read_runfile())
    if ours and total:
        return max(free, total - 1.2)
    return free


def ours_at(url: str) -> Dict[str, Any]:
    """The built-in server answering at this address, when it is Hearth's own,
    started by this process or by another Hearth window or terminal. {} when
    what answers there is some other program."""
    port = _port_of(url)
    if port is None:
        return {}
    if _proc is not None and _proc.poll() is None and _port_of((_proc_info or {}).get("url") or "") == port:
        return dict(_proc_info or {})
    other = read_runfile()
    return other if other and _port_of(other.get("url") or "") == port else {}


def current_slots() -> int:
    """Concurrent slots the RUNNING built-in server actually has. 1 means every
    request queues, so a team of sub-agents finishes one at a time."""
    try:
        return max(1, int((_proc_info or {}).get("slots") or 1))
    except (TypeError, ValueError):
        return 1


def _auto_slots_for_vram() -> int:
    """Default concurrent slots picked from the card's total VRAM, so a big GPU
    gets real sub-agent parallelism instead of everyone queuing at 1 like an 8GB
    box. start_builtin() steps this back down at load time if the chosen model +
    slots won't actually fit, so being generous here is safe."""
    try:
        vram = detect_gpu_vram_gb() or 0.0
    except Exception:
        vram = 0.0
    if vram >= 40:
        return MAX_PARALLEL_SLOTS     # 48GB+ pro cards
    if vram >= 22:
        return min(3, MAX_PARALLEL_SLOTS)   # 24GB (3090/4090)
    if vram >= 14:
        return min(2, MAX_PARALLEL_SLOTS)   # 16GB
    return 1                          # 8-12GB: one at a time


def get_parallel_slots() -> int:
    """Slots the built-in server should boot with. An explicit choice wins (env
    then the shared settings.json the GUI writes); otherwise auto-scale by VRAM."""
    raw = (os.environ.get("HEARTH_PARALLEL_SLOTS") or "").strip()
    if not raw:
        try:
            import json as _j
            with open(WORKSPACE / "settings.json", encoding="utf-8") as f:
                s = _j.load(f) or {}
            # Only a number the person picked. Every settings file has a 1 in it from
            # when that was the default, and it kept 16 GB cards at one helper at a time.
            if s.get("server_parallel_slots_chosen"):
                raw = str(s.get("server_parallel_slots") or "")
        except (OSError, ValueError):
            raw = ""
    if raw and raw != "0":
        try:
            return max(1, min(int(raw), MAX_PARALLEL_SLOTS))
        except ValueError:
            pass
    return _auto_slots_for_vram()

# Hide the cmd-flash for any subprocess we spawn on Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Path-level lock — guards against two concurrent start_builtin() calls
# racing into a double-load of the SAME model (auto-boot daemon thread vs
# manual GUI "Use this" click, or a user double-clicking the button). The
# Job Object catches orphan children; this lock catches duplicate live ones.
# The lock pairs with `_starting_paths` so we can also reject the case where
# a second caller asks for a DIFFERENT model while the first is mid-load.
import threading as _threading
_start_lock = _threading.Lock()
_starting_paths: set = set()

# Windows Job Object — guarantees the llama_cpp.server child dies with us NO
# MATTER HOW the parent process exits (Ctrl-C, taskbar close, force-kill,
# tray exit, unhandled crash). atexit() alone doesn't cover SIGKILL or hard
# crashes; the OS does, via this job. Without this, the child llama.cpp keeps
# hogging port 1234 + several GB of RAM/VRAM until the user reboots or kills
# it manually. **THE launch blocker fix.**
_WIN_JOB = None
if os.name == "nt":
    try:
        import win32job, win32api, win32con  # type: ignore
        _WIN_JOB = win32job.CreateJobObject(None, "")
        _ext = win32job.QueryInformationJobObject(
            _WIN_JOB, win32job.JobObjectExtendedLimitInformation
        )
        _ext['BasicLimitInformation']['LimitFlags'] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            _WIN_JOB, win32job.JobObjectExtendedLimitInformation, _ext
        )
    except Exception:
        # pywin32 not installed — fall back to atexit-only (best-effort).
        # Won't survive force-kill, but covers clean exits.
        _WIN_JOB = None


def _assign_to_job(proc: subprocess.Popen) -> None:
    """Bind the spawned llama_cpp.server to our Windows Job Object so it dies
    with us. No-op on non-Windows or when pywin32 isn't available."""
    if _WIN_JOB is None or os.name != "nt":
        return
    try:
        import win32api, win32con, win32job  # type: ignore
        hp = win32api.OpenProcess(
            win32con.PROCESS_TERMINATE | win32con.PROCESS_SET_QUOTA,
            False, proc.pid,
        )
        win32job.AssignProcessToJobObject(_WIN_JOB, hp)
    except Exception:
        pass

def detect_gpu_vram_gb() -> Optional[float]:
    """Total memory in GB on the main graphics card, any vendor, or None when
    there is none. Used to pick the right model size for this rig (4-6 GB ->
    small, 6-8 GB -> default, 8+ -> can run the smart-but-heavy options)."""
    try:
        from . import gpu as _gpu
        v = _gpu.vram_total_gb()
        if v:
            return v
    except Exception:
        pass
    try:
        import subprocess
        no_window = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=no_window,
        )
        line = (out.stdout or "").strip().splitlines()[0].strip()
        if line:
            return round(float(line) / 1024.0, 1)
    except Exception:
        pass
    return None


_blackwell_cache: List[Optional[bool]] = []


def _gpu_is_blackwell() -> bool:
    """True for an RTX 50-series (Blackwell, sm_120) card.

    The bundled llama-cpp-python wheel has no kernels for these, so it loads and
    then returns empty replies once the prompt grows. Detected by name, since
    that works without CUDA installed. Cached; unknown means False so no other
    GPU is ever blocked by a bad guess.
    """
    if _blackwell_cache:
        return bool(_blackwell_cache[0])
    ok = False
    try:
        from . import gpu as _gpu
        out = " ".join(a["name"] for a in _gpu.adapters()).lower()
        # RTX 50-series consumer parts, plus the Blackwell workstation cards.
        ok = bool(re.search(r"rtx\s*50\d0", out)) or "blackwell" in out
    except Exception:
        ok = False
    _blackwell_cache.append(ok)
    return ok


def detect_gpu_vram_free_gb() -> Optional[float]:
    """Return FREE VRAM in GB right now. Different from detect_gpu_vram_gb()
    (which returns total). Used by start_builtin pre-flight to catch the
    'LM Studio is already using 5 GB of your 8 GB' scenario before we boot
    another 5+ GB model on top and freeze the whole PC."""
    try:
        from . import gpu as _gpu
        v = _gpu.vram_free_gb()
        if v is not None:
            return v
    except Exception:
        pass
    try:
        import subprocess
        no_window = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=no_window,
        )
        line = (out.stdout or "").strip().splitlines()[0].strip()
        if line:
            return round(float(line) / 1024.0, 1)
    except Exception:
        pass
    return None


_compute_cap_cache: List[Optional[float]] = []


def detect_gpu_compute_cap() -> Optional[float]:
    """NVIDIA compute capability (e.g. 8.6 Ampere, 8.9 Ada, 12.0 Blackwell) as a
    float. Cached. Used to work around arch-specific llama.cpp kernel bugs — on
    Blackwell (>=12.0) this build's flash-attention returns empty on long
    context. Returns None if not detectable."""
    if _compute_cap_cache:
        return _compute_cap_cache[0]
    val: Optional[float] = None
    try:
        import subprocess
        no_window = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4, creationflags=no_window,
        )
        line = (out.stdout or "").strip().splitlines()[0].strip()
        if line:
            val = float(line)
    except Exception:
        val = None
    _compute_cap_cache.append(val)
    return val


def detect_ram_gb() -> Optional[float]:
    """Total system RAM in GB. Fallback when there's no GPU - llama.cpp will
    run on CPU + RAM, so we pick a tinier model in that case."""
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return None


def llama_cpp_available() -> bool:
    """Is llama-cpp-python importable in this Python?"""
    try:
        import importlib.util
        return importlib.util.find_spec("llama_cpp") is not None
    except Exception:
        return False


def external_server_running(api_base: str, timeout: float = 1.5,
                            api_key: Optional[str] = None) -> bool:
    """Is anything (LM Studio / Ollama / our own builtin) listening at <base>/models?

    For our own llama-cpp-python builtin (booted with `--api_key hearth-builtin`),
    pass `api_key` so the request actually authenticates. We ONLY treat 401/403
    as "alive" when we supplied a key — otherwise a random unauthenticated
    listener on port 1234 (an old service, a dead webhook, anything) would
    falsely register as "LM Studio is running" in the Models pill.
    """
    url = api_base.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        # 401 = we KNOW it's an auth-gated LLM server but our key was wrong.
        # Only count it as "alive" if we ARE the one with the right key (i.e.
        # we supplied one). Without a key supplied, treat 401 as "not the server
        # we expected to see" rather than a generic 'something is up'.
        return bool(api_key) and e.code in (401, 403)
    except Exception:
        return False


# Ports + endpoints to probe when the configured LOCAL_API_BASE isn't reachable.
# Order matters: LM Studio first (most common), Ollama second, llama.cpp server
# third. Each entry is (label, api_base_url).
KNOWN_LOCAL_SERVERS = [
    ("LM Studio", "http://localhost:1234/v1"),
    ("Ollama",    "http://localhost:11434/v1"),
    ("llama.cpp", "http://localhost:8080/v1"),
]


def detect_running_server(default_api_base: str = "") -> Optional[str]:
    """Probe the configured endpoint + well-known local LLM ports. Return the
    first one that answers, or None if nothing is running.

    Lets Hearth transparently use whatever the user already has up - LM Studio,
    Ollama, llama.cpp server - without forcing them to set LOCAL_API_BASE.
    """
    default_api_base = default_api_base or _local_base()
    if external_server_running(default_api_base):
        return default_api_base
    for _label, url in KNOWN_LOCAL_SERVERS:
        if url == default_api_base:
            continue  # already tried as default
        if external_server_running(url):
            return url
    return None


_SHAPE_CACHE: Dict[Tuple[str, float, int], Dict[str, bool]] = {}


def gguf_shape(path) -> Dict[str, bool]:
    """What a GGUF file is, from its header: `chat` when it carries a tokenizer
    (every language model does; an image or video model's doesn't), and
    `complete` when the file is as long as its own tensor table says. Both
    True when the header can't be read, so nothing is hidden on a guess."""
    import struct
    try:
        st = os.stat(path)
        key = (os.path.normcase(str(path)), st.st_mtime, st.st_size)
    except OSError:
        return {"chat": True, "complete": True}
    if key in _SHAPE_CACHE:
        return _SHAPE_CACHE[key]
    out = {"chat": True, "complete": True}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                raise ValueError("not gguf")
            f.read(4)
            n_tensors = struct.unpack("<Q", f.read(8))[0]
            n_kv = struct.unpack("<Q", f.read(8))[0]
            tokenizer, align = False, 32

            def val(vt):
                if vt in _GGUF_FIXED_SZ:
                    return f.read(_GGUF_FIXED_SZ[vt])
                if vt == 8:
                    return f.read(struct.unpack("<Q", f.read(8))[0])
                if vt == 9:
                    et = struct.unpack("<I", f.read(4))[0]
                    n = struct.unpack("<Q", f.read(8))[0]
                    if et in _GGUF_FIXED_SZ:
                        f.read(_GGUF_FIXED_SZ[et] * n)
                    else:
                        for _ in range(n):
                            val(et)
                    return None
                raise ValueError(f"unknown gguf type {vt}")

            for _ in range(n_kv):
                k = f.read(struct.unpack("<Q", f.read(8))[0]).decode("utf-8", "replace")
                vt = struct.unpack("<I", f.read(4))[0]
                v = val(vt)
                if k.startswith("tokenizer.ggml."):
                    tokenizer = True
                elif k == "general.alignment" and v and vt == 4:
                    align = struct.unpack("<I", v)[0] or 32
            last = 0
            for _ in range(n_tensors):
                f.read(struct.unpack("<Q", f.read(8))[0])
                f.read(8 * struct.unpack("<I", f.read(4))[0])
                f.read(4)
                last = max(last, struct.unpack("<Q", f.read(8))[0])
            data_start = (f.tell() + align - 1) // align * align
        out = {"chat": tokenizer, "complete": st.st_size > data_start + last}
    except Exception:
        pass
    _SHAPE_CACHE[key] = out
    return out


def _model_entry(p, source: str, size_gb: Optional[float] = None) -> Dict[str, Any]:
    """One row describing a GGUF on disk.

    The ONLY place this dict is built. It used to be assembled in both scan
    functions, so a field added to one was silently missing from the other.
    """
    if size_gb is None:
        size_gb = round(p.stat().st_size / (1024 ** 3), 2)
    return {
        "filename": p.name,
        "path": str(p),
        "size_gb": size_gb,
        "source": source,
        # Repo it was downloaded from, when Hearth fetched it, so the UI can link
        # the real model page instead of guessing from the filename.
        "hf_repo": model_source(p.name),
        # An image/video model, or a file cut short, is listed but can't be loaded as a chat model.
        **({"not_chat": True} if not (_shape := gguf_shape(p))["chat"] else {}),
        **({"incomplete": True} if not _shape["complete"] else {}),
    }


def list_local_models() -> List[Dict[str, Any]]:
    """List GGUF files under ~/Jarvis/models. Surfaced in the GUI Models panel.

    Recurses into subfolders so an LM-Studio-style layout
    (models/publisher/Model-GGUF/model.gguf) — e.g. a model you moved in with its
    folder — is found, not just files dropped flat at the top. This is bounded to
    the models dir (small, fast), NOT a disk scan, so it doesn't touch boot time.
    mmproj vision-projector sidecars are skipped — they aren't chat models."""
    out = []
    if not MODELS_DIR.is_dir():
        return out
    for p in sorted(MODELS_DIR.rglob("*.gguf")):
        if "mmproj" in p.name.lower():
            continue
        try:
            out.append(_model_entry(p, "Inglen"))
        except OSError:
            continue
    return out


def _scan_paths_for_gguf() -> List[Path]:
    """Return the well-known directories where LM Studio, Ollama, HF, and
    GPT4All keep models. Used by scan_disk_for_models() so users don't have
    to copy files into ~/Jarvis/models manually."""
    home = Path.home()
    candidates = [
        # LM Studio (Windows + macOS + Linux)
        home / ".lmstudio" / "models",
        home / ".cache" / "lm-studio" / "models",
        home / "AppData" / "Local" / "LM Studio" / "models",
        # Ollama (note: Ollama stores GGUF blobs without .gguf extension; skipped)
        # GPT4All
        home / "AppData" / "Local" / "nomic.ai" / "GPT4All",
        home / "Library" / "Application Support" / "nomic.ai" / "GPT4All",
        home / ".local" / "share" / "nomic.ai" / "GPT4All",
        # Hugging Face cache (where transformers / huggingface_hub stash)
        home / ".cache" / "huggingface" / "hub",
        # Common dev locations
        home / "Downloads",
        home / "Documents" / "models",
    ]
    return [p for p in candidates if p.is_dir()]


def _source_label(p: Path) -> str:
    s = str(p).lower()
    if "lmstudio" in s or "lm studio" in s: return "LM Studio"
    if "nomic.ai" in s or "gpt4all" in s:    return "GPT4All"
    if "huggingface" in s or "/hub" in s:    return "HuggingFace cache"
    if "downloads" in s:                     return "Downloads"
    if "/jarvis/" in s.replace("\\", "/"):   return "Inglen"
    return "Disk"


def _iter_gguf(root: Path, max_depth: int = 5):
    """Yield *.gguf paths under `root`, but PRUNE heavy/irrelevant subtrees and
    cap depth. A plain rglob over a broad dir like ~/Downloads (which can hold a
    whole project + .venv + cloned repos) walks tens of thousands of files and
    made GUI boot hang ~10-15s. This skips venv/node_modules/.git/site-packages/
    build dirs + anything hidden, and stops at max_depth."""
    _SKIP = {"node_modules", "site-packages", "__pycache__", "venv",
             "dist", "build", "$recycle.bin", "windows", "program files",
             "program files (x86)"}
    base = str(root).rstrip("\\/").count(os.sep)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base >= max_depth:
                dirnames[:] = []
            # prune in place: hidden dirs (.git/.venv/.cache subtrees) + heavies
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d.lower() not in _SKIP]
            for f in filenames:
                if f.endswith(".gguf"):
                    yield Path(dirpath) / f
    except (OSError, PermissionError):
        return


def scan_disk_for_models(max_per_dir: int = 50) -> List[Dict[str, Any]]:
    """Search well-known locations for *.gguf files so the user can pick from
    everything they already have — no manual copy into ~/Jarvis/models needed.
    Returns the combined list, including ~/Jarvis/models, deduped by path."""
    seen = set()
    out = []
    # Always start with ~/Jarvis/models (canonical destination)
    for m in list_local_models():
        seen.add(m["path"])
        out.append(m)
    # Then scan everything else
    for root in _scan_paths_for_gguf():
        try:
            # Broad PERSONAL dirs (Downloads/Documents) are scanned TOP-LEVEL
            # ONLY — on a shipped app we must never crawl a stranger's whole
            # Downloads tree (privacy + speed). Model-specific dirs (LM Studio /
            # GPT4All / HF cache) get the depth-bounded pruned walk, since they
            # legitimately shard into <publisher>/<model>/file.gguf.
            count = 0
            _broad = root.name.lower() in ("downloads", "documents")
            _gen = root.glob("*.gguf") if _broad else _iter_gguf(root)
            for p in _gen:
                if count >= max_per_dir:
                    break
                sp = str(p)
                if sp in seen:
                    continue
                try:
                    size_gb = round(p.stat().st_size / (1024 ** 3), 2)
                except OSError:
                    continue
                # Skip suspiciously small files (likely partial downloads)
                if size_gb < 0.05:
                    continue
                seen.add(sp)
                out.append(_model_entry(p, _source_label(p), size_gb))
                count += 1
        except (OSError, PermissionError):
            continue
    # Bigger first - that's usually the more capable model
    out.sort(key=lambda m: (-m.get("size_gb", 0), m.get("filename", "")))
    # Tag each with its real transformer layer count (GGUF header, cached) so the
    # load-config UI can cap the GPU-offload slider at the true max instead of a
    # blind 99, and size a VRAM estimate.
    for m in out:
        if "layers" not in m:
            try:
                m["layers"] = gguf_layer_count(m.get("path", ""))
            except Exception:
                m["layers"] = None
        if "ctx_max" not in m:
            try:
                m["ctx_max"] = gguf_context_length(m.get("path", ""))
            except Exception:
                m["ctx_max"] = None
    return out


def _hf_resolve_url(repo: str, filename: str) -> str:
    """Where a file in a Hugging Face repo is fetched from."""
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


class DownloadCancelled(Exception):
    """Raised out of a download loop when the caller asks to stop. The .part
    file is deliberately left behind so a later attempt resumes."""


# One writer per part file. Two downloads of the same file appending to one part
# at once interleave their bytes, and a model file built that way is corrupt in
# a way no size check catches, because each writer only counts its own. That is
# exactly what Pause then Resume produced: the paused transfer never stopped,
# the resumed one started beside it, and the result was 262 KB longer than the
# file on the server. A new download for a file already being written asks the
# old one to stop and waits for it to let go before it reads where to resume.
_part_locks: Dict[str, "_threading.Lock"] = {}
_part_writers: Dict[str, "_threading.Event"] = {}
_part_registry = _threading.Lock()


def _claim_part(tmp) -> Tuple[str, "_threading.Lock", "_threading.Event"]:
    key = os.path.normcase(os.path.abspath(str(tmp)))
    with _part_registry:
        lock = _part_locks.setdefault(key, _threading.Lock())
        previous = _part_writers.get(key)
    if previous is not None:
        previous.set()
    lock.acquire()
    mine = _threading.Event()
    with _part_registry:
        _part_writers[key] = mine
    return key, lock, mine


def _release_part(key: str, lock: "_threading.Lock", mine: "_threading.Event") -> None:
    with _part_registry:
        if _part_writers.get(key) is mine:
            del _part_writers[key]
    lock.release()


def _download_with_resume(url, dest, tmp,
                          on_progress: Optional[Callable[[int, int], None]] = None,
                          ua: str = "Inglen/0.7.6 (HF-download)",
                          cancel: Optional[Callable[[], bool]] = None) -> int:
    """Stream `url` into `tmp`, RESUMING from an existing .part via an HTTP Range
    request so a closed/interrupted download continues instead of restarting
    from zero (matters a lot on slow/metered links). Atomic rename to `dest` on
    completion. The caller LEAVES the .part on failure so the next attempt
    resumes. Returns total bytes written."""
    key, lock, superseded = _claim_part(tmp)
    try:
        return _download_claimed(url, dest, tmp, on_progress, ua, cancel, superseded)
    finally:
        _release_part(key, lock, superseded)


def _download_claimed(url, dest, tmp, on_progress, ua, cancel, superseded) -> int:
    # Read after the claim, so a writer that was still appending has stopped
    # and the offset is where the file really ends.
    resume_from = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": ua}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        conn = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        # 416 means the part on disk is at or past the end of the file: a part
        # left by an earlier attempt at a different build, or one that finished
        # and failed to rename. Every retry asks for the same impossible range,
        # so without clearing it the download is wedged for good with no way out
        # from the interface. Drop it and start clean.
        if e.code != 416 or not resume_from:
            raise
        try:
            tmp.unlink()
        except OSError:
            pass
        resume_from = 0
        conn = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": ua}), timeout=30)
    with conn as r:
        status = getattr(r, "status", 200) or 200
        clen = int(r.headers.get("Content-Length") or 0)
        if resume_from and status == 206:          # server honored the range
            total, mode, done = resume_from + clen, "ab", resume_from
        else:                                       # 200 / no range -> clean restart
            total, mode, done = clen, "wb", 0
        with open(tmp, mode) as f:
            while True:
                # Checked per chunk so a cancel lands in well under a second.
                if (cancel and cancel()) or superseded.is_set():
                    raise DownloadCancelled()
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass
    # A dropped connection on a flaky link ends the loop with an empty read too,
    # not only a finished download. Promoting a short .part to the final .gguf
    # yields a valid-header-but-truncated file that loads and then emits garbage
    # (the '????' / degenerate-token failure). Verify byte-completeness before
    # the rename; leave the .part in place so the next attempt RESUMES.
    if total and done < total:
        raise IOError(f"incomplete download: {done}/{total} bytes — connection "
                      f"closed early; kept .part for resume")
    # Longer than the server said is never a good file. Only a short read was
    # checked, and a part that two writers had appended to came out long.
    if total and done > total:
        raise IOError(f"download is {done - total} bytes longer than the server "
                      f"sent; the partial file is not trustworthy")
    # os.replace overwrites an existing dest atomically (Path.rename raises on
    # Windows when dest already exists), and we retry because antivirus / the
    # search indexer briefly locks the just-closed .part — that surfaces as
    # WinError 32 ("used by another process") on the rename.
    for _attempt in range(6):
        try:
            os.replace(tmp, dest)
            break
        except PermissionError:
            if _attempt == 5:
                raise
            time.sleep(0.5)
    return done


_proc: Optional[subprocess.Popen] = None
_proc_info: Dict[str, Any] = {}
# Kept so the log can be truncated in place while the child still holds it
# open (Windows won't let another handle rename/delete a locked file).
_log_fh = None

# Live load-progress snapshot. Written by start_builtin while a model loads,
# read by /api/llmserver/progress every ~500ms so the GUI can show a real
# bar instead of an indeterminate spinner. Reset to "idle" once ready.
#   phase   : starting | loading_weights | initializing_backend | reserving_kv |
#             starting_http | ready | error | idle
#   percent : 0..100 (rough but monotonically increasing within a load)
#   marker  : last interesting llama.cpp log line we noticed
#   started_at, finished_at : epoch seconds
_load_progress: Dict[str, Any] = {
    "phase": "idle",
    "percent": 0,
    "marker": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
}


# The stretch of the bar the measured weight read owns. Below it the process
# has barely started; above it the engine is doing work that is not file reading
# and has its own log markers.
_WEIGHT_BAND_BOTTOM = 8
_WEIGHT_BAND_TOP = 88


def _phase_for_line(line: str) -> Optional[Tuple[str, int]]:
    """Map an engine log line to (phase, percent), or None if it says nothing.

    These are boundaries, not a spread. Almost everything the engine prints
    about the model, the metadata dump, the architecture summary, the CUDA
    device list, is printed from the header before a single tensor is read, so
    a percentage assigned by where a line sits in the log lands far ahead of
    the truth and then the bar stops for the whole real wait. Those lines are
    pinned to the bottom and the measured read fills the middle.

    Only the lines that genuinely come after the weights are resident carry a
    high number: the threadpool, the context and KV allocation, the HTTP start.
    """
    l = line
    # --- before or during the read: the bottom of the bar -------------------
    if ("load_model: loading model" in l
            or "llama_model_load:" in l
            or "llama_model_loader:" in l
            or "Model metadata:" in l
            or "print_info:" in l
            or "llm_load_print_meta:" in l
            or "ggml_cuda_init:" in l
            or "CUDA : " in l):
        return ("loading_weights", 8)
    if "load_tensors:" in l:
        # Printed as the read BEGINS, so it is a floor, not a milestone.
        return ("loading_weights", 10)
    # --- after the weights are resident ------------------------------------
    if "llama threadpool init" in l:
        return ("initializing_backend", 90)
    if ("load_model: initializing" in l or "n_ctx_slot" in l
            or "llama_kv_cache" in l or "kv self size" in l
            or "sched_reserve:" in l or "graph_reserve:" in l):
        return ("reserving_kv", 93)
    if ("llama_server: model loaded" in l
            or "Started server process" in l
            or "Waiting for application startup" in l
            or "Application startup complete" in l):
        return ("starting_http", 97)
    if ("llama_server: listening on" in l
            or "main: server is listening" in l
            or "Uvicorn running on" in l):
        return ("ready", 100)
    return None


def _model_total_bytes(model_path: str) -> int:
    """Size of the weights the engine is about to read, shards included.

    A split GGUF names itself `...-00001-of-00003.gguf` and the engine opens
    every part, so sizing off the first file alone reports three times the real
    progress and the bar finishes two thirds of the way through the load.
    """
    try:
        total = os.path.getsize(model_path)
    except OSError:
        return 0
    m = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", model_path, re.IGNORECASE)
    if not m:
        return total
    try:
        count = int(m.group(2))
        stem = model_path[:m.start()]
        total = 0
        for i in range(1, count + 1):
            part = f"{stem}-{i:05d}-of-{m.group(2)}.gguf"
            total += os.path.getsize(part)
        return total
    except OSError:
        return total


def _weight_load_fraction(pid: int, total_bytes: int) -> Optional[float]:
    """How far through reading the weights the engine is, 0..1, or None.

    Measured off the process rather than its log. Between "loading model" and
    "model loaded" the engine prints nothing for what is most of the wait on a
    multi-gigabyte file, so a bar driven by log lines sits on one number for
    half a minute and reads as a hang.

    Resident memory is the signal that survives the differences. The weights
    are mapped, not read, so IO counters stay near zero on Windows, but the
    pages fault in as the engine walks the file and resident size tracks the
    file size closely enough to drive a bar. It holds for a GPU offload too,
    since every tensor passes through host memory on its way to the card.

    Resident size also covers buffers that are not weights, so this is capped
    by its caller rather than trusted to land exactly on 100.
    """
    if total_bytes <= 0:
        return None
    try:
        import psutil
        rss = psutil.Process(pid).memory_info().rss
    except Exception:
        return None
    return max(0.0, min(1.0, rss / float(total_bytes)))


def _set_progress(**kwargs) -> None:
    _load_progress.update(kwargs)


def get_load_progress() -> Dict[str, Any]:
    """Snapshot the current load progress for the /api/llmserver/progress
    endpoint. Adds elapsed_s if a load is in flight."""
    snap = dict(_load_progress)
    if snap.get("started_at") and not snap.get("finished_at"):
        snap["elapsed_s"] = round(time.time() - snap["started_at"], 1)
    elif snap.get("started_at") and snap.get("finished_at"):
        snap["elapsed_s"] = round(snap["finished_at"] - snap["started_at"], 1)
    return snap


def server_extras_missing() -> Optional[str]:
    """Return the name of the FIRST missing llama_cpp.server runtime extra, or
    None if all are present. The prebuilt llama-cpp-python CUDA/CPU wheels DON'T
    ship fastapi/uvicorn — so `python -m llama_cpp.server` silently exits code 1
    on a fresh install. This preflight catches that before we spawn."""
    import importlib.util
    for name in ("fastapi", "uvicorn", "sse_starlette", "pydantic_settings", "starlette_context"):
        if importlib.util.find_spec(name) is None:
            return name
    return None


_native_server_cache: List[Optional[Dict[str, Any]]] = []


# The server's file name, and how its libraries are named, on this system.
SERVER_EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"
# Executable headers: Windows (MZ), Linux (ELF), macOS (Mach-O 64/32, universal).
_EXEC_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def _lib_name(stem: str) -> str:
    """ggml-cuda -> ggml-cuda.dll / libggml-cuda.so / libggml-cuda.dylib."""
    if sys.platform == "win32":
        return stem + ".dll"
    return "lib" + stem + (".dylib" if sys.platform == "darwin" else ".so")


from .release_files import classify as parse_asset, for_this_system as _assets_here  # noqa: E402


def _system_name() -> str:
    from .release_files import this_os
    return {"win": "Windows", "mac": "macOS", "linux": "Linux"}[this_os()]


def _with_libs(env: Dict[str, str], dirs: List[str]) -> Dict[str, str]:
    """Let the engine find the libraries beside it."""
    if not dirs:
        return env
    join = os.pathsep.join(dirs)
    env["PATH"] = join + os.pathsep + env.get("PATH", "")
    if sys.platform.startswith("linux"):
        env["LD_LIBRARY_PATH"] = join + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    elif sys.platform == "darwin":
        env["DYLD_FALLBACK_LIBRARY_PATH"] = join + os.pathsep + env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    return env


# Size below which an exe carries no code of its own. Current upstream builds
# ship every tool as a ~9 KB launcher next to a heavy <name>-impl.dll, so size
# alone rejects them; older builds (and LM Studio's) put the code in the exe.
_MIN_SERVER_EXE_BYTES = 15_000


def _usable_server_exe(exe: Path) -> bool:
    """True for a llama-server that can actually run. Requires an executable
    header (an AV-truncated file loses it), then accepts either a self-contained
    exe or a thin launcher whose -impl library sits beside it."""
    try:
        if not exe.is_file():
            return False
        with open(exe, "rb") as f:
            if not f.read(4).startswith(_EXEC_MAGIC):
                return False
        if exe.stat().st_size >= _MIN_SERVER_EXE_BYTES:
            return True
        impl = exe.with_name(_lib_name(exe.stem.replace(".exe", "") + "-impl"))
        return impl.is_file() and impl.stat().st_size >= _MIN_SERVER_EXE_BYTES
    except OSError:
        return False


def _engine_emits_token(api_base: str, timeout: float = 90.0) -> bool:
    """Ask the running server for one token. True only if it actually returns
    text — the single check that catches an engine which loads fine and then
    generates nothing."""
    import urllib.request
    body = json.dumps({"model": "local", "max_tokens": 24, "temperature": 0,
                       "messages": [{"role": "user", "content": "Hi"}]}).encode()
    req = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {BUILTIN_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        # A reasoning model on current llama.cpp puts its thinking in
        # reasoning_content and leaves content empty until it stops thinking.
        # Either one proves the engine generates; checking only content marks
        # a perfectly good engine dead.
        text = (msg.get("content") or "") + (msg.get("reasoning_content") or "")
        return bool(text.strip())
    except Exception:
        return False   # unreachable/erroring counts as not proven


_ENGINE_HEALTH_PATH = Path(os.path.expanduser("~/.hearth/engine_health.json"))


def _engine_health() -> Dict[str, Any]:
    try:
        with open(_ENGINE_HEALTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def set_engine_health(exe: str, ok: bool, reason: str = "") -> None:
    """Record whether an engine actually produced output. A binary can pass every
    static check, boot, report ready, and still emit nothing (seen: a llama.cpp
    build with no working kernels for the installed GPU), so health is decided by
    behaviour and remembered across runs."""
    data = _engine_health()
    data[str(exe)] = {"ok": bool(ok), "reason": reason, "at": int(time.time())}
    try:
        _ENGINE_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_ENGINE_HEALTH_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def engine_is_known_bad(exe: str) -> bool:
    rec = _engine_health().get(str(exe))
    return bool(rec) and rec.get("ok") is False


def clear_engine_health(exe: Optional[str] = None) -> None:
    """Forget one engine's verdict, or all of them (lets a user retry after a
    driver/GPU change)."""
    if exe is None:
        try:
            _ENGINE_HEALTH_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    data = _engine_health()
    data.pop(str(exe), None)
    try:
        with open(_ENGINE_HEALTH_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


_ENGINE_PREF_PATH = Path(os.path.expanduser("~/.hearth/engine_pref.txt"))


def _valid_pref(v: str) -> bool:
    # "hearth:<tag>" pins one installed build so a rollback actually sticks;
    # the picker otherwise always sorts to the newest tag.
    return v in ("hearth", "lmstudio", "wheel") or v.startswith("hearth:")


def get_engine_preference() -> str:
    """'' (auto), 'hearth', 'hearth:<tag>', 'lmstudio', or 'wheel'. Lets a user
    override the picker when one engine misbehaves on their hardware."""
    env = (os.environ.get("HEARTH_ENGINE") or "").strip().lower()
    if env == "auto":
        return ""
    if env and _valid_pref(env):
        return env
    try:
        v = _ENGINE_PREF_PATH.read_text(encoding="utf-8").strip().lower()
        return v if _valid_pref(v) else ""
    except OSError:
        return ""


def set_engine_preference(pref: str) -> Dict[str, Any]:
    pref = (pref or "").strip().lower()
    if pref in ("", "auto"):
        try:
            _ENGINE_PREF_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        reset_native_cache()
        return {"ok": True, "preference": ""}
    if not _valid_pref(pref):
        return {"ok": False, "error": f"unknown engine '{pref}'"}
    try:
        _ENGINE_PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENGINE_PREF_PATH.write_text(pref, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    reset_native_cache()
    return {"ok": True, "preference": pref}


def _engine_source_of(exe: Path) -> str:
    """Where an engine came from: the user's downloaded runtime, LM Studio's, or
    the copy shipped inside the app (Full)."""
    s = str(exe)
    if ".hearth" in s:
        return "hearth"
    if ".lmstudio" in s:
        return "lmstudio"
    # Shipped inside the packaged app: <app>/llamacpp/<build>/llama-server.exe
    if os.sep + "llamacpp" + os.sep in s or "/llamacpp/" in s:
        return "bundled"
    return "other"


def build_rank(tag: str) -> tuple:
    """Sort key for a llama.cpp build tag, newest last.

    Tags are `b<number>` and the number crossed four digits into five, so a
    plain string sort ranks b9999 above b10488. That picks a stale engine on
    cards whose kernels only exist in recent builds, and the prune below keeps
    the two highest, which under a string sort means deleting the current
    engine and keeping the ancient one. Compare the number as a number, and
    fall back to the name for anything not shaped like a tag.
    """
    m = re.match(r"^[bB](\d+)$", (tag or "").strip())
    return (1, int(m.group(1)), "") if m else (0, 0, (tag or "").lower())


def engine_build_of(exe) -> str:
    """What an engine was built for, told by the libraries that sit beside it:
    "cuda" (NVIDIA only), "vulkan" (any card), "metal" (a Mac) or "cpu"."""
    d = Path(exe).parent
    try:
        if ((d / _lib_name("ggml-cuda")).exists() or any(d.glob("cudart64_*.dll"))
                or any(d.glob("libcudart.so*")) or "cuda" in d.name.lower()):
            return "cuda"
        if (d / _lib_name("ggml-vulkan")).exists():
            return "vulkan"
        if (d / _lib_name("ggml-metal")).exists():
            return "metal"
    except OSError:
        pass
    return "cpu"


def engine_fits(exe) -> bool:
    """A CUDA build only runs on an NVIDIA card, a Metal one only on a Mac.
    The others run anywhere."""
    built = engine_build_of(exe)
    return built not in ("cuda", "metal") or engine_backend() == built


def unfit_engine() -> Optional[str]:
    """The build of an engine that is on this PC but can't use its card (an
    NVIDIA build after the card was changed, or shipped to a PC without one)."""
    roots = [Path(os.path.expanduser("~/.hearth/llamacpp"))]
    try:
        app = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent.parent
        roots.append(app / "llamacpp")
    except Exception:
        pass
    for root in roots:
        if root.is_dir():
            for exe in root.glob(f"*/{SERVER_EXE}"):
                if _usable_server_exe(exe) and not engine_fits(exe):
                    return engine_build_of(exe)
    return None


def find_native_llama_server() -> Optional[Dict[str, Any]]:
    """Locate a standalone llama.cpp `llama-server.exe` — the official OpenAI-
    compatible server. It's newer than our pinned llama-cpp-python wheel and,
    crucially, has working Blackwell (sm_120 / RTX 50-series) CUDA kernels the
    wheel lacks (the wheel returns empty on long context there). Prefers a
    Hearth-managed copy, then the newest CUDA-12 build LM Studio already pulled.
    Returns {exe, dll_dirs, label} or None. Cached; call reset_native_cache()
    after a runtime download."""
    if _native_server_cache:
        return _native_server_cache[0]
    found: Optional[Dict[str, Any]] = None
    candidates: List[Path] = []
    # 1) Hearth-managed runtime (download target — newest version dir wins)
    hearth_rt = Path(os.path.expanduser("~/.hearth/llamacpp"))
    if hearth_rt.is_dir():
        candidates += sorted(hearth_rt.glob(f"*/{SERVER_EXE}"),
                             key=lambda p: build_rank(p.parent.name), reverse=True)
    # 2) Engine shipped INSIDE the app (Full edition). A packaged build carries a
    # current standalone llama-server so a fresh install has a working engine with
    # no download, including on cards the llama-cpp-python wheel has no kernels
    # for. Ranked after a downloaded runtime, so an update the user pulled still
    # wins over the one baked into the installer.
    try:
        _app = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent.parent
        _bundled = _app / "llamacpp"
        if _bundled.is_dir():
            candidates += sorted(_bundled.glob(f"*/{SERVER_EXE}"),
                                 key=lambda p: build_rank(p.parent.name), reverse=True)
    except Exception:
        pass
    # 3) Reuse LM Studio's CUDA-12 backend if the user has it (zero download)
    lms = Path(os.path.expanduser("~/.lmstudio/extensions/backends"))
    if lms.is_dir():
        candidates += sorted(lms.glob(f"*nvidia-cuda12*/{SERVER_EXE}"),
                             key=lambda p: build_rank(p.parent.name), reverse=True)
    # One built for another maker's card is not an engine on this PC, and an
    # added build only runs the models the official one can't read.
    candidates = [c for c in candidates if engine_fits(c) and not is_added_build(c.parent.name)]
    # An explicit choice beats the ranking — the escape hatch when one engine
    # misbehaves on hardware we can't test.
    pref = get_engine_preference()
    if pref == "wheel":
        # "Built-in" means the engine that SHIPS WITH HEARTH. That used to be the
        # llama-cpp-python wheel; a packaged build now carries a real llama.cpp
        # server, so honour the preference with the bundled one when it is there.
        # Without this, choosing Built-in on a packaged build pinned users to the
        # wheel, which has no kernels for newer GPUs.
        # Also check ~/.hearth/llamacpp/ (the user-downloaded runtimes) as a
        # fallback — when running from source there is no bundled copy, but the
        # user may have installed one via the Engine tab.
        for source in ("bundled", "hearth"):
            _filtered = [c for c in candidates if _engine_source_of(c) == source]
            for exe in _filtered:
                if _usable_server_exe(exe) and not engine_is_known_bad(str(exe)):
                    found = {"exe": str(exe), "label": exe.parent.name,
                             "dll_dirs": [str(exe.parent)]}
                    _native_server_cache.append(found)
                    return found
        _native_server_cache.append(None)   # nothing bundled or hearth -> the wheel
        return None
    if pref.startswith("hearth:"):          # pinned to one build
        want = pref.split(":", 1)[1]
        pinned = [c for c in candidates
                  if _engine_source_of(c) == "hearth" and c.parent.name.lower() == want]
        # A pin to a build that's since been pruned must not drop the user to
        # the slow wheel; ignore the stale pin and rank normally instead.
        if pinned:
            candidates = pinned
    elif pref in ("hearth", "lmstudio"):
        candidates = [c for c in candidates if _engine_source_of(c) == pref]
    for exe in candidates:
        # Skip a missing exe or an AV-quarantined stub (AVs flag the unsigned
        # official llama-server.exe as IDP.generic and truncate it). Current
        # upstream ships a ~9 KB launcher + -impl.dll, so this must not be a
        # bare size test or every official build gets rejected.
        if not _usable_server_exe(exe):
            continue
        # Proven not to generate on this machine — skip so the next candidate
        # (or the wheel) takes over instead of silently returning empty replies.
        if engine_is_known_bad(str(exe)):
            continue
        dll_dirs = [str(exe.parent)]
        # CUDA runtime DLLs live in a sibling vendor dir for LM Studio builds.
        vendor = exe.parent.parent / "vendor" / "win-llama-cuda12-vendor-v2"
        if vendor.is_dir():
            dll_dirs.append(str(vendor))
        found = {"exe": str(exe), "dll_dirs": dll_dirs, "label": exe.parent.name}
        break
    _native_server_cache.append(found)
    return found


def reset_native_cache() -> None:
    _native_server_cache.clear()


_LLAMA_CPP_REPO = "ggml-org/llama.cpp"
# Other builds of llama.cpp the person added, for models the official build
# can't read (a fork with its own packing or model family). Downloaded ones sit
# beside the official builds as <owner>@<tag>; one in a folder of its own is
# listed in ~/.hearth/engines.json. Nothing here names any build: Hearth tries
# them when the official one refuses a file, and remembers which one read it.
_UNREADABLE = re.compile(r"invalid ggml type|unknown model architecture|unsupported model architecture"
                         r"|unknown architecture", re.I)


def is_added_build(name: str) -> bool:
    return "@" in (name or "")


def _hearth_file(name: str) -> Path:
    return Path(os.path.expanduser("~/.hearth")) / name


def _read_json(path: Path, empty):
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, type(empty)) else empty
    except (OSError, ValueError):
        return empty


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding="utf-8")


def added_engines() -> List[Dict[str, Any]]:
    """The added builds that can run here: {id, exe, dll_dirs, label, source}."""
    out: List[Dict[str, Any]] = []
    root = Path(os.path.expanduser("~/.hearth/llamacpp"))
    if root.is_dir():
        for exe in sorted(root.glob(f"*@*/{SERVER_EXE}"), key=lambda p: p.stat().st_mtime, reverse=True):
            owner, _, tag = exe.parent.name.partition("@")
            out.append({"id": exe.parent.name, "exe": str(exe), "dll_dirs": [str(exe.parent)],
                        "label": f"{owner} {tag}", "source": "github"})
    for e in _read_json(_hearth_file("engines.json"), []):
        exe = Path(str(e.get("exe") or ""))
        out.append({"id": str(exe), "exe": str(exe), "dll_dirs": [str(exe.parent)],
                    "label": e.get("name") or exe.parent.name, "source": "folder"})
    return [e for e in out if _usable_server_exe(Path(e["exe"])) and engine_fits(e["exe"])
            and not engine_is_known_bad(e["exe"])]


def _model_key(model_path: str) -> str:
    return os.path.normcase(os.path.abspath(model_path))


def engine_for(model_path: str) -> Optional[Dict[str, Any]]:
    """The added build that last read this model, while it is still here."""
    exe = _read_json(_hearth_file("engine_for_model.json"), {}).get(_model_key(model_path))
    return next((e for e in added_engines() if e["exe"] == exe), None) if exe else None


def _remember_engine(model_path: str, exe: Optional[str]) -> None:
    memo = _read_json(_hearth_file("engine_for_model.json"), {})
    if exe:
        memo[_model_key(model_path)] = exe
    elif memo.pop(_model_key(model_path), None) is None:
        return
    try:
        _write_json(_hearth_file("engine_for_model.json"), memo)
    except OSError:
        pass


def add_engine_folder(path: str) -> Dict[str, Any]:
    """Add a build of llama.cpp that is already on disk (its folder or its exe)."""
    p = Path(os.path.expanduser((path or "").strip().strip('"')))
    if p.is_file() and p.name.lower() == SERVER_EXE:
        exe = p
    elif p.is_dir():
        exe = next(iter(sorted(p.glob(SERVER_EXE)) + sorted(p.glob(f"*/{SERVER_EXE}"))), None)
    else:
        return {"ok": False, "error": f"there is no folder {p}"}
    if not exe or not _usable_server_exe(exe):
        return {"ok": False, "error": f"no working {SERVER_EXE} in {p}"}
    if not engine_fits(exe):
        return {"ok": False, "error": "that build is for an NVIDIA card, and this PC doesn't have one"}
    name = (p if p.is_dir() else exe.parent).name
    items = [e for e in _read_json(_hearth_file("engines.json"), []) if e.get("exe") != str(exe)]
    items.append({"name": name, "exe": str(exe)})
    _write_json(_hearth_file("engines.json"), items)
    return {"ok": True, "name": name, "exe": str(exe)}


def remove_added_engine(engine_id: str) -> Dict[str, Any]:
    """Forget an added build. One Hearth downloaded is deleted; a folder of the
    person's own is only taken off the list."""
    items = _read_json(_hearth_file("engines.json"), [])
    keep = [e for e in items if e.get("exe") != engine_id]
    if len(keep) != len(items):
        _write_json(_hearth_file("engines.json"), keep)
        return {"ok": True}
    if is_added_build(engine_id):
        return remove_llama_build(engine_id)
    return {"ok": False, "error": f"no added engine '{engine_id}'"}


def _repo_of(text: str) -> str:
    """owner/name from a GitHub link or an owner/name pair; "" otherwise."""
    m = re.search(r"(?:github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#].*)?$", (text or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def llama_runtime_info() -> Dict[str, Any]:
    """What runtime is Hearth using for the builtin server, and where from.
    Powers the CLI/GUI 'update' UI."""
    n = find_native_llama_server()
    exe = (n or {}).get("exe") or ""
    managed = Path(os.path.expanduser("~/.hearth/llamacpp"))
    # Same size floor as the picker — an AV-truncated stub on disk is not a
    # usable install, and reporting it as one offers "update" instead of a redo.
    have_managed = managed.is_dir() and any(
        _usable_server_exe(p) for p in managed.glob(f"*/{SERVER_EXE}"))
    stub_managed = (managed.is_dir()
                    and any(managed.glob(f"*/{SERVER_EXE}")) and not have_managed)
    if not n:
        source = "wheel"      # falling back to the bundled llama-cpp-python
    elif ".hearth" in exe:
        source = "hearth"     # Hearth's own downloaded runtime (self-sufficient)
    elif _engine_source_of(Path(exe)) == "bundled":
        source = "bundled"    # the real llama.cpp server shipped inside the app
    elif ".lmstudio" in exe:
        source = "lmstudio"   # reusing LM Studio's signed backend
    else:
        source = "other"
    return {
        "engine": "native" if n else "bundled-wheel",
        "source": source,
        "version": (n or {}).get("label"),
        "exe": exe or None,
        "managed": have_managed,   # True once Hearth downloaded its own copy
        "stub": stub_managed,      # downloaded but truncated (antivirus)
        "added": [{"id": e["id"], "label": e["label"], "source": e["source"]} for e in added_engines()],
        # The build serving the model loaded now, when it isn't the one above
        # (an added build reads a model the official one can't).
        "running": _running_added(),
    }


def _running_added() -> Optional[Dict[str, Any]]:
    try:
        exe = (_proc_info or {}).get("exe") or ""
        if not exe or _proc is None or _proc.poll() is not None:
            return None
    except Exception:
        return None
    hit = next((e for e in added_engines() if os.path.normcase(e["exe"]) == os.path.normcase(exe)), None)
    return {"label": hit["label"], "model": os.path.basename((_proc_info or {}).get("model_path") or "")} if hit else None


# Highest arch the bundled llama-cpp-python wheel compiles kernels for
# (CUDA : ARCHS = 700,750,800,860,890,900). Above this the driver PTX-JITs and
# long-context prompt processing degrades hard — currently means Blackwell.
_WHEEL_MAX_ARCH = 9.0


def runtime_advice() -> Dict[str, Any]:
    """Environment check for the builtin runtime. Advisory only — never
    downloads. Flags the one combination that silently costs performance:
    a GPU newer than the wheel's kernels falling back to that wheel, which
    both PTX-JITs every op and forces the f16 KV + flash-off workaround in
    start_builtin, so it pays in speed and in VRAM."""
    info = llama_runtime_info()
    none = {"nudge": False, "level": "none", "short": None,
            "reason": None, "action": None}
    if info.get("engine") == "native":
        return none
    if unfit_engine() == "cuda":
        return {"nudge": True, "level": "recommended",
                "short": "The model engine on this PC is built for NVIDIA cards, and this PC doesn't have one. "
                         "Get the engine for this card and local models will run.",
                "reason": "The engine that is installed only runs on NVIDIA graphics cards. This PC has a "
                          "different card (or none), so it can't start. The right build is a one-time download.",
                "action": "install_runtime"}
    cap = detect_gpu_compute_cap()
    if cap is None or cap <= _WHEEL_MAX_ARCH:
        # No NVIDIA GPU (a CUDA build buys nothing), or the wheel has real
        # kernels for this card.
        return none
    return {
        "nudge": True,
        "level": "recommended",
        "short": (f"Your GPU (compute {cap:g}) is newer than the bundled "
                  "engine's kernels, so past ~30K context it runs slower than "
                  "CPU. Installing Inglen's own llama.cpp fixes that."),
        "reason": (f"The bundled engine has no kernels for your GPU (compute "
                   f"{cap:g}), so every op goes through driver JIT, measured "
                   "slower than plain CPU past ~30K context. Flash attention is "
                   "also forced off to stay correct, which costs extra VRAM. "
                   "Installing Inglen's own llama.cpp fixes both."),
        "action": "install_runtime",
    }


_llama_dl_cancel = _threading.Event()


def cancel_llama_download() -> Dict[str, Any]:
    """Stop an in-flight runtime download. The partial .part survives, so
    starting again resumes rather than restarting. This is the PAUSE."""
    _llama_dl_cancel.set()
    return {"ok": True}


def discard_llama_download() -> Dict[str, Any]:
    """Abandon a runtime download: stop it AND delete the partial from the cache
    so it does NOT resume next time. The engine mirror of discard_partial (the
    CANCEL, as opposed to cancel_llama_download's pause)."""
    _llama_dl_cancel.set()
    cache = Path(os.path.expanduser("~/.hearth/llamacpp/.cache"))
    freed = 0
    try:
        for p in list(cache.glob("*.part")) + list(cache.glob("*.zip")):
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return {"ok": True, "freed_gb": round(freed / (1024 ** 3), 2)}


_LLAMA_LATEST_CACHE = Path(os.path.expanduser("~/.hearth/llamacpp_latest.json"))


def _latest_build_release(timeout: float = 12, repo: str = _LLAMA_CPP_REPO) -> Optional[Dict[str, Any]]:
    """The newest numbered build (bNNNNN) that ships Windows files.

    The repository's "latest" release is not always a build: it has pointed at
    a v0.5.0 tag carrying one file and nothing for Windows, which made every
    engine install fail with "no cuda Windows assets"."""
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=15",
        headers={"User-Agent": "Inglen", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rels = json.load(r)
    shape = r"^b\d+$" if repo == _LLAMA_CPP_REPO else r"."
    for rel in rels if isinstance(rels, list) else []:
        tag = (rel.get("tag_name") or "").strip()
        if re.match(shape, tag) and _assets_here(rel.get("assets")):
            return rel
    return None


def engine_backend() -> str:
    """Which llama.cpp build fits this machine's graphics card: CUDA for
    NVIDIA, Vulkan for AMD and Intel cards with their own memory (it runs on
    any vendor's driver), and the plain CPU build otherwise. A Mac's build always
    uses Metal."""
    if sys.platform == "darwin":
        return "metal"
    try:
        from . import gpu as _gpu
        p = _gpu.primary()
    except Exception:
        p = None
    if not p:
        return "cpu"
    if p.get("vendor") == "NVIDIA":
        return "cuda"
    return "vulkan" if (p.get("mem_total_mb") or 0) >= 1024 else "cpu"


def latest_llama_tag(max_age: int = 21600, force: bool = False) -> Optional[str]:
    """Newest llama.cpp release tag, cached 6h. None if GitHub is unreachable."""
    if not force:
        try:
            rec = json.loads(_LLAMA_LATEST_CACHE.read_text(encoding="utf-8"))
            if (time.time() - rec.get("at", 0) < max_age
                    and re.match(r"^b\d+$", str(rec.get("tag") or ""))):
                return rec["tag"]
        except Exception:
            pass
    try:
        rel = _latest_build_release(timeout=8)
        tag = ((rel or {}).get("tag_name") or "").strip()
    except Exception:
        return None
    if tag:
        try:
            _LLAMA_LATEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _LLAMA_LATEST_CACHE.write_text(json.dumps({"tag": tag, "at": int(time.time())}),
                                           encoding="utf-8")
        except OSError:
            pass
    return tag or None


def installed_llama_tags() -> List[str]:
    """Tags of usable Hearth-managed runtimes on disk, newest first."""
    root = Path(os.path.expanduser("~/.hearth/llamacpp"))
    if not root.is_dir():
        return []
    out = [p.parent.name for p in root.glob(f"*/{SERVER_EXE}")
           if _usable_server_exe(p) and not is_added_build(p.parent.name)]
    return sorted(out, key=build_rank, reverse=True)


def engine_build_dir(tag: str) -> Optional[str]:
    """Absolute path to a managed engine build's folder (for 'show in folder').
    None if the tag isn't a real installed build."""
    tag = (tag or "").strip().strip("/").strip("\\")
    if not tag or "/" in tag or "\\" in tag or ".." in tag:
        return None
    d = Path(os.path.expanduser(f"~/.hearth/llamacpp/{tag}"))
    return str(d) if d.is_dir() else None


def remove_llama_build(tag: str) -> Dict[str, Any]:
    """Delete a Hearth-managed llama.cpp runtime by tag. Refuses to delete the one
    currently powering the running server (Windows would lock it anyway)."""
    tag = (tag or "").strip().strip("/").strip("\\")
    if not tag or "/" in tag or "\\" in tag or ".." in tag:
        return {"ok": False, "error": "bad tag"}
    target = Path(os.path.expanduser(f"~/.hearth/llamacpp/{tag}"))
    if not target.is_dir():
        return {"ok": False, "error": f"no engine build '{tag}' on disk"}
    try:
        running = (_proc_info or {}).get("exe") or ""
        if running and _proc is not None and _proc.poll() is None:
            if os.path.normcase(str(target)) in os.path.normcase(str(running)):
                return {"ok": False, "error": "That engine is running — eject the "
                        "model first (Models tab), then delete it."}
    except Exception:
        pass
    try:
        import shutil as _sh
        _sh.rmtree(target)
    except Exception as e:
        return {"ok": False, "error": f"Couldn't delete it ({type(e).__name__}): {e}. "
                "If a model is loaded on this engine, eject it first."}
    reset_native_cache()
    _status_cache.pop("disk_models_cache", None)
    _status_cache.pop("disk_scan_ts", None)
    return {"ok": True, "removed": tag, "remaining": installed_llama_tags()}


def invalidate_disk_cache() -> None:
    """Force next status() to rescan disk. Call after download/delete."""
    _status_cache.pop("disk_models_cache", None)
    _status_cache.pop("disk_scan_ts", None)


def check_llama_update(max_age: int = 21600) -> Dict[str, Any]:
    """Is a newer llama.cpp runtime available than the one installed?
    Drives the update dot — nobody goes looking for this on their own."""
    installed = installed_llama_tags()
    latest = latest_llama_tag(max_age=max_age)
    if not latest:
        return {"ok": False, "installed": installed, "latest": None, "available": False}
    return {"ok": True, "installed": installed, "latest": latest,
            # Only meaningful once they have their own copy; otherwise the
            # install nudge is the right prompt, not an update dot.
            "available": bool(installed) and latest not in installed}


def _release_summary(body: Optional[str], max_len: int = 140) -> str:
    """One plain-text line describing what a llama.cpp build changed.

    The release body is a short changelog line followed by an auto-generated
    table of per-platform download links. Rendering it raw leaks markdown and
    HTML into the UI and tells a Hearth user nothing, so take the first line
    that actually reads like a change and strip the markup off it."""
    import re as _re
    if not body:
        return ""
    text = _re.sub(r"<[^>]+>", " ", body)               # drop HTML (<details> …)
    text = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    for raw in text.splitlines():
        line = raw.strip().lstrip("#-*>| ").strip()
        line = line.replace("**", "").replace("`", "").strip()
        if len(line) < 12:
            continue
        low = line.lower()
        # Skip the download-table scaffolding and bare URLs.
        if low.startswith(("http", "www.", "website", "macos", "windows", "linux",
                           "ios", "android", "download", "|", "---")):
            continue
        if "http" in low and len(line) - len(low.split("http")[0]) > 20:
            continue
        return line[:max_len].rstrip() + ("…" if len(line) > max_len else "")
    return ""


def list_llama_builds(limit: int = 4) -> Dict[str, Any]:
    """Recent llama.cpp releases and the Windows CUDA variants each one ships,
    so a build that regresses on some GPU can be rolled back to a known-good
    tag instead of leaving the user stuck on latest."""
    import urllib.request
    import re
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_LLAMA_CPP_REPO}/releases?per_page={max(1, min(limit, 30))}",
            headers={"User-Agent": "Inglen", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            rels = json.load(r)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "builds": []}
    installed = set(installed_llama_tags())
    health = _engine_health()
    bad = {Path(k).parent.name for k, v in health.items() if v.get("ok") is False}
    builds = []
    for rel in rels if isinstance(rels, list) else []:
        tag = (rel.get("tag_name") or "").strip()
        if not tag:
            continue
        cudas, bins_size, cudart_size = [], 0, 0
        want = engine_backend()
        for a, p in _assets_here(rel.get("assets")):
            if p["backend"] != want:
                continue
            if p["cudart"]:
                cudart_size = max(cudart_size, int(a.get("size") or 0))
            else:
                if p["cuda"]:
                    cudas.append(p["cuda"])
                bins_size = max(bins_size, int(a.get("size") or 0))
        if not bins_size or (want == "cuda" and not cudas):
            continue
        # The install pulls BOTH the binaries AND the CUDA runtime (cudart), so the
        # honest download size is their sum — counting only the bins made the
        # section show ~200 MB while the download bar (bins+cudart) showed ~0.64 GB.
        size = bins_size + cudart_size
        builds.append({"tag": tag, "cuda": sorted(set(cudas)),
                       "size_mb": round(size / 1e6),
                       "published": (rel.get("published_at") or "")[:10],
                       "summary": _release_summary(rel.get("body")),
                       # One honest way out to the full notes. We deliberately
                       # don't render the body: it's mostly a per-platform
                       # download table, and a rendered link to a macOS tarball
                       # sitting next to Hearth's Install button is a trap.
                       "url": rel.get("html_url") or "",
                       "prerelease": bool(rel.get("prerelease")),
                       "installed": tag in installed,
                       # Verified by actually generating on THIS machine, not
                       # inferred from the build name.
                       "verified": tag in {Path(k).parent.name
                                           for k, v in health.items() if v.get("ok")},
                       "known_bad": tag in bad})
    # llama.cpp ships most days, so a build the user actually installed falls
    # off the recent-N window within a week and vanishes from this list — they
    # end up unable to see (or roll back to) their own engine. Pin any installed
    # build that the window missed, using what's known locally.
    listed = {b["tag"] for b in builds}
    verified_tags = {Path(k).parent.name for k, v in health.items() if v.get("ok")}
    for tag in installed:
        if tag in listed:
            continue
        size = 0
        try:
            d = Path(os.path.expanduser(f"~/.hearth/llamacpp/{tag}"))
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            pass
        builds.append({"tag": tag, "cuda": [], "size_mb": round(size / 1e6),
                       "published": "", "summary": "", "prerelease": False,
                       "url": f"https://github.com/{_LLAMA_CPP_REPO}/releases/tag/{tag}",
                       "installed": True, "verified": tag in verified_tags,
                       "known_bad": tag in bad})
    # No honest way to label a build with the GPU generations it supports: the
    # asset name only carries the CUDA toolkit version, and the real kernel list
    # is only printed once the server runs. Report the card instead and let the
    # health verdict speak for compatibility.
    return {"ok": True, "builds": builds,
            "gpu_compute_cap": detect_gpu_compute_cap()}


def _archive_ok(path: Path) -> bool:
    import tarfile
    import zipfile
    try:
        return zipfile.is_zipfile(path) if path.name.endswith(".zip") else tarfile.is_tarfile(path)
    except OSError:
        return False


def _extract_archive(archive: Path, dest: Path) -> None:
    """Unpack a release file into dest. The Linux and macOS ones keep everything
    in one llama-<tag>/ folder; it is dropped so the server sits in dest itself,
    beside its libraries (some of them links to others)."""
    import zipfile
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    import tarfile
    with tarfile.open(archive) as t:
        members = t.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members}
        strip = len(tops) == 1 and any("/" in m.name for m in members)
        keep = []
        for m in members:
            if strip:
                if "/" not in m.name:
                    continue
                m.name = m.name.split("/", 1)[1]
            keep.append(m)
        try:
            t.extractall(dest, members=keep, filter="data")
        except TypeError:                     # Python without extraction filters
            t.extractall(dest, members=keep)


def download_llama_runtime(cuda: str = "12.4", tag: Optional[str] = None,
                           on_progress: Optional[Callable[[int, int], None]] = None,
                           force: bool = False, backend: Optional[str] = None,
                           repo: str = "") -> Dict[str, Any]:
    """Download the official ggml-org llama.cpp build for this system and card
    (llama-server + ggml libraries, and the CUDA runtime for NVIDIA) into
    ~/.hearth/llamacpp/<tag>/. This
    is what lets Hearth's builtin run on any GPU without bundling a giant wheel
    and without depending on LM Studio — the 'updatable runtime'. Also serves as
    the Lite->Full upgrade (pull the runtime, not the whole app).
    `repo` (a GitHub link or owner/name) fetches another build of llama.cpp the
    same way, kept apart as <owner>@<tag>.
    Returns {ok, dir, tag} or {ok: False, error}."""
    import urllib.request
    import shutil
    # A previous cancel must not kill this run — the flag is sticky otherwise
    # and every later install aborts instantly.
    _llama_dl_cancel.clear()
    backend = (backend or engine_backend()).lower()
    if repo and not _repo_of(repo):
        return {"ok": False, "error": f"'{repo}' isn't a GitHub page"}
    repo = _repo_of(repo) if repo else _LLAMA_CPP_REPO
    added = repo.lower() != _LLAMA_CPP_REPO.lower()
    try:
        if tag:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
                headers={"User-Agent": "Inglen", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                rel = json.load(r)
        else:
            rel = _latest_build_release(timeout=20, repo=repo)
            if not rel:
                return {"ok": False, "error": f"no build for {_system_name()} among the recent releases of {repo}"}
    except Exception as e:
        return {"ok": False, "error": f"couldn't reach GitHub releases: {e}"}
    tagname = rel.get("tag_name") or tag or "latest"
    assets = rel.get("assets") or []
    dirname = f"{repo.split('/')[0]}@{tagname}" if added else tagname

    # Already have this exact build — re-pulling 640 MB to land on the same
    # files is pure waste. `force` is the explicit repair path.
    _have = Path(os.path.expanduser(f"~/.hearth/llamacpp/{dirname}"))
    _same_build = _have.is_dir() and engine_build_of(_have / SERVER_EXE) == backend
    if not force and _same_build and _usable_server_exe(_have / SERVER_EXE):
        reset_native_cache()
        return {"ok": True, "dir": str(_have), "tag": tagname, "already": True}
    if _have.is_dir() and not _same_build:
        # The same release, built for another kind of card: its libraries
        # must not be left mixed in with the new ones.
        shutil.rmtree(_have, ignore_errors=True)

    from .release_files import pick as _pick
    bins, runtime, _picked = _pick(assets, backend, cuda)
    if backend == "cuda":
        cuda = _picked or cuda
        if not bins or (not runtime and not added):
            return {"ok": False, "error": f"no CUDA {cuda} build for {_system_name()} in release {tagname}"}
    else:
        # Vulkan, CPU and Metal builds are one archive each, with no separate runtime.
        if not bins:
            return {"ok": False, "error": f"no {backend} build for {_system_name()} in release {tagname}"}

    dest = Path(os.path.expanduser(f"~/.hearth/llamacpp/{dirname}"))
    dest.mkdir(parents=True, exist_ok=True)
    total = int(bins.get("size", 0)) + int((runtime or {}).get("size", 0))
    done = [0]

    # Zips stage here (not tempfile) so an interrupted pull leaves a .part that
    # the next attempt RESUMES from, same as model downloads.
    cache = Path(os.path.expanduser("~/.hearth/llamacpp/.cache"))
    cache.mkdir(parents=True, exist_ok=True)
    # A cancelled pull of an OLDER build would keep its .part forever once the
    # tag moves on, so drop anything not part of this fetch (hundreds of MB).
    _wanted = {bins.get("name", ""), (runtime or {}).get("name", "")}
    for _f in cache.iterdir():
        _base = _f.name[:-5] if _f.name.endswith(".part") else _f.name
        if _base not in _wanted:
            try:
                _f.unlink()
            except OSError:
                pass

    def _fetch(asset) -> None:
        name = asset.get("name") or "asset.zip"
        zip_path, part = cache / name, cache / (name + ".part")
        base = done[0]   # bytes from assets already finished

        def _prog(d: int, _t: int) -> None:
            if on_progress:
                try:
                    on_progress(base + d, total)
                except Exception:
                    pass

        # A stale/corrupt archive in cache (a truncated promotion, an AV-gutted
        # file) would otherwise skip the download and then fail to extract.
        # Trust one only if it opens; else drop it (and any stale .part).
        if zip_path.exists() and not _archive_ok(zip_path):
            zip_path.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        if not zip_path.exists():
            _download_with_resume(asset["browser_download_url"], zip_path, part,
                                  _prog, ua="Inglen",
                                  cancel=_llama_dl_cancel.is_set)
        done[0] = base + int(asset.get("size", 0))
        try:
            _extract_archive(zip_path, dest)   # exe + ggml libraries + cudart all land together
        except Exception:
            # Corrupt archive slipped through — nuke it + any .part so the NEXT
            # attempt starts clean instead of re-failing on the same bad bytes.
            zip_path.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            raise
        zip_path.unlink(missing_ok=True)

    try:
        _fetch(bins)
        if runtime:
            _fetch(runtime)
    except DownloadCancelled:
        # .part files stay on purpose — starting again resumes.
        return {"ok": False, "cancelled": True, "tag": tagname,
                "error": "download cancelled (progress kept, it will resume)"}
    except Exception as e:
        return {"ok": False, "error": f"download/extract failed: {type(e).__name__}: {e}"}
    # Each runtime is ~555 MB on disk; keep the newest two so a bad new build
    # can still fall back, and drop the rest instead of stacking up forever.
    # An added build keeps only its newest copy from the same place.
    try:
        _root = Path(os.path.expanduser("~/.hearth/llamacpp"))
        _owner = dirname.split("@")[0]
        _dirs = [d for d in _root.iterdir()
                 if d.is_dir() and not d.name.startswith(".") and is_added_build(d.name) == added
                 and (not added or d.name.split("@")[0] == _owner)]
        _vers = sorted(_dirs, key=(lambda p: p.stat().st_mtime) if added else (lambda p: build_rank(p.name)),
                       reverse=True)
        for _old in _vers[1 if added else 2:]:
            shutil.rmtree(_old, ignore_errors=True)
    except OSError:
        pass
    reset_native_cache()
    exe = next(iter(dest.rglob(SERVER_EXE)), None)
    if exe and sys.platform != "win32":
        try:
            os.chmod(exe, os.stat(exe).st_mode | 0o755)
        except OSError:
            pass
    if not exe:
        return {"ok": False, "error": f"{SERVER_EXE} not found after extract"}
    if not _usable_server_exe(exe):
        # Extracted but truncated: AVs flag the unsigned binary as IDP.generic
        # and gut it. Reporting ok here leaves a stub that silently loses to the
        # fallback engine, so the install looks like it worked and never did.
        try:
            size = exe.stat().st_size
        except OSError:
            size = 0
        return {"ok": False, "stub": True, "dir": str(dest), "tag": tagname,
                "error": (f"{SERVER_EXE} is unusable after extract ({size} "
                          f"bytes, no {_lib_name('llama-server-impl')} beside it). Antivirus "
                          f"quarantine is the usual cause - allow {dest.parent} "
                          f"and install again.")}
    return {"ok": True, "dir": str(dest), "tag": tagname, "backend": backend,
            **({"repo": repo, "id": dirname} if added else {})}


_MANAGED_SERVER_FLAGS = {
    "--model", "--host", "--port", "--ctx-size", "--parallel", "--n-gpu-layers",
    "--api-key", "--batch-size", "--ubatch-size", "--threads", "--flash-attn",
    "--cache-type-k", "--cache-type-v", "--mmproj",
}


def _find_mmproj(model_path: str) -> Optional[str]:
    """Find a vision projector (mmproj) sidecar next to the model so llama-server
    can actually SEE images. Convention is an `*mmproj*.gguf` in the same folder;
    when several exist, prefer the one whose name shares the model's stem. Returns
    the path or None (text-only model / single-file GGUF with no projector)."""
    try:
        import glob
        d = os.path.dirname(os.path.abspath(model_path))
        cands = [p for p in glob.glob(os.path.join(d, "*.gguf"))
                 if "mmproj" in os.path.basename(p).lower()]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        stem = os.path.splitext(os.path.basename(model_path))[0].lower()
        stem = re.sub(r"[-_.]?(q\d.*|f16|bf16|iq\d.*|mtp)$", "", stem)
        scored = sorted(cands, key=lambda p: -_stem_overlap(stem, os.path.basename(p).lower()))
        return scored[0]
    except Exception:
        return None


def _stem_overlap(a: str, b: str) -> int:
    """Count shared alphanumeric tokens between two filenames — a cheap 'do these
    belong together' score for picking the matching mmproj."""
    ta = set(re.findall(r"[a-z0-9]+", a))
    tb = set(re.findall(r"[a-z0-9]+", b))
    return len(ta & tb)


def _is_moe_model(model_path: str) -> bool:
    """Heuristic: is this a Mixture-of-Experts GGUF (Qwen3-A3B, Mixtral, etc.)?
    MoE wants a DIFFERENT offload than a dense model: attention/shared layers on
    the GPU, the big expert tensors in system RAM (via --n-cpu-moe + --no-mmap),
    not the dense partial-layer split. The 'A3B'/'A22B' active-param tag or a
    'moe'/'mixtral' name is a reliable signal in practice."""
    n = os.path.basename(model_path).lower()
    if re.search(r"[-_.]a\d+b(\b|[-_.])", n):   # ...-A3B, -A22B active-expert tag
        return True
    return any(t in n for t in ("moe", "mixtral", "deepseek-v2", "deepseek-v3", "grok-1"))


def _parse_extra_server_args(extra: Optional[str]) -> List[str]:
    """Split a user's advanced-flags string into argv tokens for the native
    llama-server, dropping any flag Hearth already sets so the two can't conflict.
    shlex handles quoting; a stray token just reaches llama-server, which ignores
    or errors on its own."""
    if not extra or not str(extra).strip():
        return []
    try:
        import shlex
        toks = shlex.split(str(extra))
    except Exception:
        return []
    out: List[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _MANAGED_SERVER_FLAGS:
            i += 2 if (i + 1 < len(toks) and not toks[i + 1].startswith("-")) else 1
            continue
        out.append(t)
        i += 1
    return out


_FLAGS: Dict[tuple, frozenset] = {}


def engine_flags(exe: str) -> frozenset:
    """The options this llama-server build lists in its --help. Builds differ
    (--kv-unified arrived in 2025), and an option a build doesn't know stops it
    from starting, so an extra one is passed only when the build lists it."""
    try:
        key = (exe, os.path.getmtime(exe))
    except OSError:
        return frozenset()
    if key not in _FLAGS:
        try:
            out = subprocess.run([exe, "--help"], capture_output=True, timeout=30,
                                 creationflags=_NO_WINDOW).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            out = ""
        _FLAGS[key] = frozenset(re.findall(r"(?<![\w-])(--?[a-zA-Z][\w-]*)", out))
    return _FLAGS[key]


def _shared_kv_args(exe: str, slots: int, extra: List[str]) -> List[str]:
    """With more than one slot, one KV buffer for all of them (--kv-unified):
    a slot that sits idle is saved to the RAM prompt cache and cleared when
    another starts work (the build's --cache-idle-slots, on by default), so its
    room goes to whoever is working instead of staying held in VRAM. Each
    request still fits in ctx/slots, as before, so the total never runs out.
    Left alone when the person set it either way in the Advanced flags."""
    if slots < 2 or any(a in ("-kvu", "--kv-unified", "-no-kvu", "--no-kv-unified") for a in extra):
        return []
    return ["--kv-unified"] if "--kv-unified" in engine_flags(exe) else []


def same_file(a: str, b: str) -> bool:
    try:
        return bool(a and b) and os.path.samefile(a, b)
    except OSError:
        return _normalize_model_key(a) == _normalize_model_key(b)


def start_builtin(model_path: str, *args, **kwargs) -> Dict[str, Any]:
    """Start the built-in server on a model (see _start_builtin for the options).

    Whatever goes wrong inside, the model is not left marked as loading and the
    progress bar is told it failed. A crash used to leave both behind, so the
    window waited on "getting ready" forever and every retry was refused as a
    duplicate."""
    try:
        r = _start_builtin(model_path, *args, **kwargs)
        if r.get("ok") or r.get("stopped") or not _UNREADABLE.search(r.get("log_tail") or ""):
            return r
        # The engine couldn't read this file. Try the other builds added here,
        # and remember the one that reads it so the next load goes straight there.
        tried = {(engine_for(model_path) or {}).get("exe")}
        for eng in added_engines():
            if eng["exe"] in tried:
                continue
            tried.add(eng["exe"])
            again = _start_builtin(model_path, *args, engine=eng, **kwargs)
            if again.get("ok"):
                _remember_engine(model_path, eng["exe"])
                return dict(again, engine=eng["label"])
            if again.get("stopped"):
                return again
        _remember_engine(model_path, None)
        return r
    except Exception as e:
        with _start_lock:
            _starting_paths.discard(os.path.normcase(os.path.abspath(model_path)))
        _set_progress(phase="error", error=f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"The model could not start ({type(e).__name__}: {e})"}


def load_failure_hint(tail: str) -> str:
    """Why the engine stopped while loading a model, in words, from the end of its log."""
    hint = ""
    if "ModuleNotFoundError" in tail:
        hint = (". The server's extras are missing; run: pip install fastapi 'uvicorn[standard]' "
                "sse-starlette pydantic-settings starlette-context")
    elif "out of memory" in tail.lower() or "CUDA error" in tail:
        hint = ". The graphics card ran out of memory; try a smaller model or a shorter context"
    elif ("unknown model architecture" in tail.lower()
            or "unsupported model architecture" in tail.lower()
            or "unknown architecture" in tail.lower()):
        # A brand-new model family the installed llama.cpp predates.
        # Checked BEFORE the generic load failure because llama.cpp
        # prints "error loading model" alongside this one. Reactive by
        # design: no curated model-to-engine table to go stale, and it
        # fires exactly when the engine is genuinely the problem.
        hint = (". This model is newer than your engine, so it can't read it. "
                "Update the engine in Models > Engine, then load it again.")
    elif "invalid ggml type" in tail.lower():
        hint = (". This file was made for a different build of llama.cpp than yours. If its page says "
                "which one, add that build in Models, Engine (Add an engine) and load it again")
    elif "could not load model" in tail.lower():
        hint = ". The model file couldn't be read; it may be damaged or not a model"
    elif "could not find module" in tail.lower():
        hint = ". The engine's CUDA files are missing; repair Inglen in Settings, About, or re-run install.ps1 -BuiltinLLM cuda"
    return hint


def _start_builtin(model_path: str, port: Optional[int] = None,
                  ctx: int = 24576, n_gpu_layers: int = -1,
                  n_threads: Optional[int] = None,
                  cache_type_k: Optional[str] = None,
                  cache_type_v: Optional[str] = None,
                  flash_attn: bool = True,
                  force: bool = False,
                  parallel_slots: Optional[int] = None,
                  extra_args: Optional[str] = None,
                  _smoke_checked: bool = False,
                  engine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:  # noqa: ARG001 — kept for caller compat; load no longer refuses on VRAM
    """Spawn llama-cpp-python's OpenAI-compatible server.

    Args:
      model_path:      absolute path to .gguf file
      port:            HTTP port (default: BUILTIN_PORT)
      ctx:             context length (n_ctx)
      n_gpu_layers:    -1 = offload all to GPU, 0 = CPU-only, N = first N layers
      n_threads:       CPU threads for non-offloaded work. None = llama.cpp default
      cache_type_k:    KV cache K-quant: 'f16' (default), 'q8_0', 'q4_0', 'q4_1'
                       q8_0 halves KV VRAM with negligible quality loss
      cache_type_v:    same for V cache
      flash_attn:      enable flash attention (faster on RTX 30+ series)

    Returns {ok, url, pid} or {ok: False, error}.
    """
    global _proc, _proc_info

    # Floor the context Hearth boots its OWN server at: the persona + tool
    # schemas are ~20K tokens, so below ~24K the prompt overflows immediately
    # with no room for conversation. Better to use a bit more KV-cache VRAM than
    # boot a server that can't hold a single turn.
    if ctx and ctx < MIN_USABLE_CTX:
        try:
            print(f"  [hearth.llmserver] raising ctx {ctx} -> {MIN_USABLE_CTX} "
                  f"(Inglen's prompt needs ~20K; below ~24K overflows instantly)",
                  flush=True)
        except Exception:
            pass
        ctx = MIN_USABLE_CTX

    # Path-level guard against concurrent double-spawn. The audit caught a
    # race where the auto-boot daemon and the GUI's "Use this" both reached
    # start_builtin() before _proc was assigned — both spawned llama_cpp.server,
    # both ended up holding the same GGUF in VRAM. _start_lock + _starting_paths
    # close that window. Holds for the whole 10-30s spawn-and-wait, so we use
    # a non-blocking acquire to fail-fast on the duplicate call.
    normalized_path = os.path.normcase(os.path.abspath(model_path))
    with _start_lock:
        if normalized_path in _starting_paths:
            return {"ok": False, "error": "this model is already being loaded; ignoring duplicate request"}
        # If a server is already running, behavior depends on whether the user
        # asked for the SAME model or a different one. Same → return 'already'.
        # Different → stop the old one and start the new (the user picked a
        # different model from the dropdown).
        if _proc is not None and _proc.poll() is None:
            existing_path = _proc_info.get("model_path") or ""
            _same_model = os.path.normcase(os.path.abspath(existing_path)) == normalized_path
            # Same model + same context → nothing to do. But if the user changed
            # the context window in the load panel and hit "Save & use", the path
            # matches yet ctx differs — restart with the new ctx instead of
            # silently returning "already" (the "it said ready but kept the old
            # context" bug). ctx is stored as-requested; n_gpu_layers / KV are
            # stored post-resolution, so they aren't safe to compare here.
            if _same_model and _proc_info.get("ctx") == ctx:
                return {"ok": True, "url": _proc_info.get("url"), "pid": _proc.pid, "already": True}
            # Different model, OR same model with a changed context → clean restart.
            try:
                stop_builtin()
            except Exception:
                pass
        _starting_paths.add(normalized_path)

    if not Path(model_path).is_file():
        with _start_lock: _starting_paths.discard(normalized_path)
        return {"ok": False, "error": f"model file not found: {model_path}"}

    # VRAM pre-flight: WARN when the math says we'll likely OOM, but never
    # block — the user knows their machine better than our estimate (KV math
    # is approximate, llama.cpp can spill to CPU mid-stream, the user may
    # have just freed VRAM, etc.). Surface the warning in the returned dict
    # so the GUI can show a non-blocking notice. CPU-only loads (n_gpu_layers=0)
    # skip this entirely.
    # Match LM Studio on consumer GPUs: quantize the KV cache (q8_0) when
    # offloading so a long context fits in VRAM instead of spilling to RAM/CPU
    # (~half the KV memory, negligible quality loss). q8_0 V-cache needs flash
    # attention, which is on by default. Caller can override either type.
    # Blackwell (compute cap >=12.0, e.g. RTX 50-series): this llama.cpp build's
    # flash-attention kernels return EMPTY output on long context. q8_0 KV
    # *requires* flash attention, so on these cards the safe default is f16 KV +
    # flash off (verified: full-context tool calls work with it, hang/empty with
    # flash on). Older cards keep the q8_0+flash VRAM saver. Only defaults are
    # touched — an explicit load-config choice is left alone (the GUI warns).
    _blackwell = (detect_gpu_compute_cap() or 0.0) >= 12.0
    _native = engine or engine_for(model_path) or find_native_llama_server()
    _use_native = _native is not None

    # The bundled llama-cpp-python wheel has no Blackwell (RTX 50-series, sm_120)
    # kernels, so on those cards it loads and then returns empty replies once the
    # context grows, which reads as "the model is broken". Refuse it there and
    # point at the one-click engine install rather than starting something known
    # not to work. Other GPUs keep the wheel as a working fallback.
    if not _use_native and n_gpu_layers != 0 and _gpu_is_blackwell():
        # Release the in-progress marker before bailing, or this path stays
        # flagged as loading and every later attempt is refused as a duplicate.
        with _start_lock:
            _starting_paths.discard(normalized_path)
        return {
            "ok": False,
            "error": ("This GPU (RTX 50-series) needs the standalone llama.cpp "
                      "engine. The bundled runtime has no kernels for it and would "
                      "return empty replies on longer prompts. Install the engine "
                      "in Models, Engine (one click), or run on CPU with GPU "
                      "layers set to 0."),
            "needs_engine": True,
        }

    # Resolve concurrent slots. Only the standalone llama-server has them; the
    # llama-cpp-python wheel wraps a single Llama instance and serialises no
    # matter what, so asking for slots there would silently do nothing.
    slots = (get_parallel_slots() if parallel_slots is None
             else max(1, min(int(parallel_slots), MAX_PARALLEL_SLOTS)))
    if slots > 1 and not _use_native:
        slots = 1
    # Slots multiply KV cache, so an over-ambitious setting has to degrade
    # rather than OOM at load. Step down until the weights + KV fit free VRAM.
    if slots > 1 and n_gpu_layers != 0:
        try:
            _free = detect_gpu_vram_free_gb()
            _msize = os.path.getsize(model_path) / (1024 ** 3)
            while slots > 1 and _free is not None:
                if _msize + estimate_kv_cache_gb(_msize, ctx * slots,
                                                 cache_type_k) <= _free - 0.4:
                    break
                slots -= 1
        except OSError:
            pass
    # ctx is PER SLOT from the caller's point of view, because that's what has
    # to hold one conversation. llama.cpp wants the total, so multiply.
    total_ctx = ctx * slots
    if slots > 1:
        try:
            print(f"  [hearth.llmserver] {slots} slots x {ctx} ctx "
                  f"= {total_ctx} total (sub-agents can overlap)", flush=True)
        except Exception:
            pass
    if n_gpu_layers != 0:
        # Blackwell (RTX 50-series): the bundled llama-cpp-python wheel has no
        # sm_120 kernels, so its flash path returns empty on long context. A
        # current standalone llama-server does have them and runs this card with
        # flash on and a quantised KV cache, which is what keeps a 9B inside 8GB.
        # Forcing the workaround on the native engine too would double the KV
        # cache and push a long context into system RAM for no gain, so it stays
        # scoped to the wheel. HEARTH_FORCE_SAFE_KV=1 applies it either way, for
        # a driver that still misbehaves.
        _force_safe = os.environ.get("HEARTH_FORCE_SAFE_KV", "").strip() in ("1", "true", "yes")
        _needs_bw_workaround = _blackwell and (not _use_native or _force_safe)
        _kv_default = "f16" if _needs_bw_workaround else "q8_0"
        _was_default = cache_type_k is None
        if cache_type_k is None:
            cache_type_k = _kv_default
        if cache_type_v is None:
            cache_type_v = _kv_default
        if _needs_bw_workaround and _was_default:
            flash_attn = False

    vram_warning: Optional[str] = None
    if n_gpu_layers != 0:
        try:
            sz = Path(model_path).stat().st_size / (1024 ** 3)
            needed = sz + estimate_kv_cache_gb(sz, ctx, cache_type_k)
            layers = gguf_layer_count(model_path)
            free_vram = detect_gpu_vram_free_gb()

            # n_gpu_layers == -1 means "Auto" — WE pick a count that fits current
            # free VRAM. If we let -1 ride when it won't fit, the NVIDIA driver
            # spills the overflow into shared system RAM, which is slower than CPU
            # AND returns empty output on this class of card. So downgrade to a
            # controlled partial offload (some layers explicitly on CPU).
            # Native llama-server spills gracefully → run a small reserve and
            # keep most layers on the GPU (fast). The wheel needs a big reserve
            # (its spill returns empty on Blackwell).
            # VRAM headroom kept free for the CUDA context + n_batch compute
            # buffer. The native server spills gracefully, so keep this LEAN to
            # pack as many layers as possible onto the GPU (the rest land on CPU
            # RAM, which is fine when you have the RAM). Tunable via
            # HEARTH_VRAM_RESERVE_GB — lower = more GPU layers, but too low can
            # OOM at load. The old llama-cpp-python wheel still needs its big 2.3.
            if _use_native:
                try:
                    _reserve = float(_env.get("VRAM_RESERVE_GB", "0.6"))
                except (TypeError, ValueError):
                    _reserve = 0.6
            else:
                _reserve = 2.3
            if n_gpu_layers == -1 and free_vram is not None:
                est = estimate_safe_gpu_layers(sz, free_vram, ctx,
                                               layer_count=layers, cache_type=cache_type_k,
                                               reserve_gb=_reserve,
                                               kv_may_spill=bool(_use_native))
                if est == 0:
                    n_gpu_layers = 0
                    vram_warning = (
                        f"Very low free VRAM (~{free_vram:.1f} GB) — running this "
                        f"model on CPU. Close other GPU apps and reload for GPU speed."
                    )
                elif est != -1:
                    n_gpu_layers = est
                    _tot = f"/{layers}" if layers else ""
                    print(f"  [llmserver] auto-offload {est}{_tot} layers "
                          f"(weights {sz:.1f}GB, KV {needed-sz:.1f}GB, free {free_vram:.1f}GB)",
                          flush=True)
                    vram_warning = (
                        f"Auto-fit: kept {est}{_tot} layers on your GPU and put the rest on "
                        f"CPU so it wouldn't overflow VRAM into slow shared RAM (~{free_vram:.1f} "
                        f"GB free). Loads fine, just a little slower. Want all {layers or est} on "
                        f"the GPU? Set the layers yourself in the model's config (it's respected "
                        f"as-is), or close other GPU apps and reload."
                    )
                # est == -1 → fits fully, leave n_gpu_layers as -1 (offload all)
            elif n_gpu_layers > 0 and free_vram is not None:
                # Explicit user pick (a specific layer count, even one that
                # over-fills). Never override it — the user's machine, the user's
                # call — but warn if the numbers say it'll spill so they know why
                # it might crawl or come back empty.
                fits = estimate_safe_gpu_layers(sz, free_vram, ctx,
                                                layer_count=layers, cache_type=cache_type_k,
                                                reserve_gb=_reserve)
                over = layers and n_gpu_layers > fits > 0
                if fits == 0 or over:
                    # Context is usually the bigger lever than layer count: the KV
                    # cache grows with it, and a context that overflows VRAM makes
                    # prompt processing an order of magnitude slower. Work out the
                    # largest power-of-two context that would fit as-is and name it.
                    _better_ctx = 0
                    try:
                        _c = ctx // 2
                        while _c >= 8192:
                            if estimate_safe_gpu_layers(sz, free_vram, _c,
                                                        layer_count=layers,
                                                        cache_type=cache_type_k,
                                                        reserve_gb=_reserve) >= n_gpu_layers:
                                _better_ctx = _c
                                break
                            _c //= 2
                    except Exception:
                        _better_ctx = 0
                    _ctx_hint = (
                        f" Lowering the context to {_better_ctx} would fit all "
                        f"{n_gpu_layers} layers and is usually the bigger win, since "
                        f"an oversized context is what pushes it into shared RAM."
                        if _better_ctx else ""
                    )
                    vram_warning = (
                        f"Heads up: {n_gpu_layers} GPU layers at {ctx} context likely "
                        f"won't fit ~{free_vram:.1f} GB free VRAM — the driver spills to "
                        f"shared RAM, which is slow and can return empty output on some "
                        f"GPUs.{_ctx_hint} Auto (or ~{max(fits,0)} layers) also works."
                    )
        except (OSError, AttributeError):
            pass  # if we can't stat or VRAM probe fails, just proceed silently

    port = port or BUILTIN_PORT

    # Pre-flight: is something ELSE already on this port? If yes, refuse with a
    # clear, first-timer-friendly error instead of letting llama.cpp.server
    # silently fail to bind. The most common case: LM Studio is already running
    # at localhost:1234. Two paths Hearth can offer the user:
    #   1) "Use what's already there" → /brain local (LM Studio answers chat)
    #   2) "Stop the other one + boot ours" → user closes LM Studio, retries
    # We don't auto-kill LM Studio — that's their data/their choice.
    other_api = f"http://127.0.0.1:{port}/v1"
    # external_server_running accepts our builtin's api_key too, so a fresh
    # check WITHOUT the builtin key tells us "is something not-ours there?".
    # We pass api_key=None so a 401 from a foreign server with auth still
    # registers as "something's there" (unlike auth-blind probes).
    foreign_running = False
    try:
        # short probe to keep startup snappy — we just need yes/no
        foreign_running = external_server_running(other_api, timeout=0.5, api_key=None)
    except Exception:
        foreign_running = False
    # But only treat as conflict if it's not OUR builtin (re-checked inside
    # the locked region above; this catches the case where LM Studio / Ollama
    # / a stray process is already squatting the port we want).
    if foreign_running and (_proc is None or _proc.poll() is not None):
        # Hearth's own server, started by another window or the terminal: the
        # same model is used as it is, another model takes its place.
        mine = ours_at(other_api)
        if mine:
            if os.path.normcase(os.path.abspath(mine.get("model_path") or "")) == os.path.normcase(os.path.abspath(model_path)):
                with _start_lock:
                    _starting_paths.discard(normalized_path)
                return {"ok": True, "url": mine.get("url"), "pid": mine.get("pid"), "already": True,
                        "adopted": True, "info": mine}
            _stop_other_surface()
            for _ in range(40):
                if not external_server_running(other_api, timeout=0.3, api_key=BUILTIN_API_KEY):
                    break
                time.sleep(0.25)
            foreign_running = external_server_running(other_api, timeout=0.5, api_key=None)
    if foreign_running and (_proc is None or _proc.poll() is not None):
        with _start_lock:
            _starting_paths.discard(normalized_path)
        return {
            "ok": False,
            "error": (
                f"Another program is already serving models on port {port} (LM Studio, "
                f"Ollama or similar). Close it and load again, or keep using what it serves "
                f"by pointing Inglen at http://localhost:{port}/v1 in Settings > Chat brain."
            ),
            "conflict_port": port,
        }

    # Auto-pick CPU thread counts when caller didn't specify. LM Studio uses
    # all logical cores by default for batch + worker counts; previously we
    # only passed --n_threads when explicitly set, leaving llama.cpp to fall
    # back to a conservative min(4, hw_concurrency). On a many-core CPU that is
    # a large prefill slowdown versus a server that uses every core.
    effective_threads = n_threads if (n_threads is not None and n_threads > 0) else (os.cpu_count() or 4)
    # In a PyInstaller build sys.executable is Hearth.exe (entrypoint = tray),
    # NOT a python interpreter — so `-m llama_cpp.server` gets handed to the
    # tray's argparse and dies with "unrecognized arguments". Re-invoke the
    # bundle with a sentinel the entrypoint catches and routes to
    # llama_cpp.server. From source, sys.executable IS python, so use -m.
    # Quantized V-cache (q8_0/q4) requires flash attention in llama.cpp.
    _kv_quant = (str(cache_type_k or "f16").lower() not in ("", "f16", "f32")
                 or str(cache_type_v or "f16").lower() not in ("", "f16", "f32"))
    _flash = bool(flash_attn or _kv_quant)

    # MoE offload is driven by the model's saved preset (Advanced flags), matching
    # what works in LM Studio for these models: all layers to GPU (n_gpu_layers
    # 999) + all experts to system RAM via --n-cpu-moe, mmap left ON. We don't
    # force --no-mmap here — LM Studio's proven 35 tk/s config keeps mmap on, and
    # the page cache handles the expert reads once the model is warm. A user who
    # prefers weights locked in RAM adds --no-mmap --mlock in the Advanced flags.
    _moe_flags: List[str] = []

    # No --chat_format / --jinja tool wiring on either engine: Hearth hand-injects
    # the <tools> spec and recovers <tool_call> via hearth.tool_call_parser
    # (headless._use_manual_tools) — the model's native template handles chat +
    # reasoning, Hearth handles tools. Same technique LM Studio uses.
    if _use_native:
        # Standalone llama.cpp `llama-server.exe`. Args differ from the wheel:
        # cache types are STRINGS (q8_0), flash is on/off, ctx is --ctx-size,
        # layers --n-gpu-layers. This is the path that actually works on Blackwell.
        cmd = [
            _native["exe"],
            "--model", model_path,
            "--host", BUILTIN_HOST,
            "--port", str(port),
            "--ctx-size", str(total_ctx),
            "--parallel", str(slots),
            "--n-gpu-layers", str(999 if n_gpu_layers == -1 else n_gpu_layers),
            "--api-key", BUILTIN_API_KEY,
            "--batch-size", "2048",
            "--ubatch-size", "512",
            "--threads", str(effective_threads),
            "--flash-attn", ("on" if _flash else "off"),
        ]
        if cache_type_k:
            cmd += ["--cache-type-k", str(cache_type_k)]
        if cache_type_v:
            cmd += ["--cache-type-v", str(cache_type_v)]
        cmd += _moe_flags
        _mmproj = _find_mmproj(model_path)
        if _mmproj:
            # Sidecar vision projector present → load it so the model can see
            # images (view_image / screenshots). llama-server offloads it to GPU
            # by default; harmless for a text-only chat.
            cmd += ["--mmproj", _mmproj]
        _extra = _parse_extra_server_args(extra_args)
        cmd += _shared_kv_args(_native["exe"], slots, _extra)
        cmd += _extra
    else:
        # PyInstaller: sys.executable is Hearth.exe (tray), so route via the
        # --hearth-run-llama-server sentinel; from source it's python → -m.
        # Only reach this path when no native server was found, so validate
        # the Python wheel is actually importable before trying to spawn it.
        if not llama_cpp_available():
            with _start_lock:
                _starting_paths.discard(normalized_path)
            return {"ok": False, "error":
                    "llama-cpp-python is not installed. Run: pip install llama-cpp-python "
                    "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"}
        missing = server_extras_missing()
        if missing:
            with _start_lock:
                _starting_paths.discard(normalized_path)
            return {"ok": False, "error":
                    f"llama_cpp.server can't start — '{missing}' is missing. "
                    "Run: pip install fastapi 'uvicorn[standard]' sse-starlette pydantic-settings starlette-context"}
        _launcher = ([sys.executable, "--hearth-run-llama-server"]
                     if getattr(sys, "frozen", False)
                     else [sys.executable, "-m", "llama_cpp.server"])
        cmd = [
            *_launcher,
            "--model", model_path,
            "--host", BUILTIN_HOST,
            "--port", str(port),
            "--n_ctx", str(ctx),
            "--n_gpu_layers", str(n_gpu_layers),
            "--api_key", BUILTIN_API_KEY,
            "--n_batch", "2048",
            "--n_ubatch", "512",
            "--n_threads", str(effective_threads),
            "--n_threads_batch", str(effective_threads),
        ]
        # This wheel wants --type_k/--type_v as the ggml type INT, not "q8_0".
        _GGML_TYPE = {"f32": 0, "f16": 1, "q4_0": 2, "q4_1": 3, "q5_0": 6, "q5_1": 7, "q8_0": 8}
        for _flag, _ct in (("--type_k", cache_type_k), ("--type_v", cache_type_v)):
            if _ct and _GGML_TYPE.get(str(_ct).lower()) is not None:
                cmd += [_flag, str(_GGML_TYPE[str(_ct).lower()])]
        if _flash:
            cmd += ["--flash_attn", "true"]
        _mmproj = _find_mmproj(model_path)
        if _mmproj:
            # llama_cpp.server exposes the projector as --clip_model_path.
            cmd += ["--clip_model_path", _mmproj]
        cmd += ["--verbose", "false"]

    # Pipe stdout+stderr to a real log file so the user (and the GUI failure
    # toast) can see WHY a start failed. Without this the user just sees "did
    # not respond within 120s" and has no clue if it OOM'd, missed CUDA, or
    # crashed on model header. The file is truncated on each start so it
    # represents the most recent attempt only.
    logs_dir = WORKSPACE / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "llamaserver.log"
    try:
        log_path.write_text(
            f"[hearth.llmserver] starting {model_path}\n"
            f"[hearth.llmserver] cmd: {' '.join(cmd)}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        # Pass parent env so the CUDA DLL dirs we prepended in hearth/__init__.py
        # ride along into the subprocess. stdout+stderr go to the log file so
        # the GUI failure toast can read them.
        global _log_fh
        log_fh = open(log_path, "ab")
        _log_fh = log_fh
        child_env = dict(os.environ)
        if _use_native and _native.get("dll_dirs"):
            # ggml-cuda + the CUDA runtime (cudart/cublas) live next to the
            # native exe / in its vendor dir: make them findable so it loads.
            child_env = _with_libs(child_env, _native["dll_dirs"])
        spawned = subprocess.Popen(
            cmd, stdout=log_fh, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW, env=child_env,
        )
        # Hand the child off to the Windows Job Object so it dies with us
        # even on Ctrl-C, force-kill, or unhandled crash. This is the actual
        # fix for "port 1234 still taken after Hearth closes".
        _assign_to_job(spawned)
        _proc = spawned
    except Exception as e:
        with _start_lock: _starting_paths.discard(normalized_path)
        return {"ok": False, "error": f"could not spawn server: {type(e).__name__}: {e}"}

    api_base = f"http://{BUILTIN_HOST}:{port}/v1"

    # Wait for the server to come up. llama.cpp loading a 7B Q4 takes 10-30s
    # on GPU, longer on CPU. Cap at 120s.
    # IMPORTANT: hold a LOCAL ref to the process we just spawned. If the user
    # presses "Use this" on a SECOND model while this loop is still waiting,
    # the new call's swap-on-start path will reset the module-global _proc to
    # None, and a .poll() against the global would AttributeError. The local
    # ref keeps us honest about who we're watching.
    def _log_tail(max_bytes: int = 4000) -> str:
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                return f.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    # Tight probe cadence: start at 200ms, back off to 1.5s. With the old flat
    # `time.sleep(1)` we'd miss the just-became-ready window by up to a full
    # second on every load — felt like a stall. Now we catch it within ~300ms
    # on average without spamming TCP connects during the cold phase.
    t_spawn = time.time()
    deadline = t_spawn + 120
    poll = 0.2
    # Initialize the load-progress snapshot so /api/llmserver/progress reports
    # "5% — starting" the instant the GUI starts polling.
    _set_progress(phase="starting", percent=5, marker="spawned llama_cpp.server",
                  started_at=t_spawn, finished_at=None, error=None,
                  model=os.path.basename(model_path))
    # Phase-tracking state for the parser: don't ever regress percent.
    last_phase_percent = 5
    last_phase_name = "starting"
    last_marker = "spawned"
    shown_percent = 5
    log_pos = 0  # byte offset we've already scanned for phase markers
    # Denominator for the measured part of the bar. Zero disables it and the
    # log markers drive alone, which is what happens when the path is unreadable.
    _weight_bytes = _model_total_bytes(model_path)
    while time.time() < deadline:
        # If a concurrent call replaced the global _proc with a different
        # subprocess, our work is moot — abandon ship cleanly.
        if _proc is None and time.time() - _stop_asked[0] < 60:
            # Stopped on purpose while it was still loading: not a failure.
            try: spawned.terminate()
            except Exception: pass
            with _start_lock: _starting_paths.discard(normalized_path)
            _set_progress(phase="idle", percent=0, marker="", error=None, finished_at=time.time())
            return {"ok": False, "stopped": True, "error": "Stopped before it finished loading."}
        if _proc is None or _proc is not spawned:
            try: spawned.terminate()
            except Exception: pass
            with _start_lock: _starting_paths.discard(normalized_path)
            _set_progress(phase="error", error="superseded by a newer model load",
                          finished_at=time.time())
            return {"ok": False, "error": "superseded by a newer model load",
                    "log_path": str(log_path)}
        if spawned.poll() is not None and time.time() - _stop_asked[0] < 60:
            # Stopped on purpose while it was still loading: not a failure.
            with _start_lock: _starting_paths.discard(normalized_path)
            _set_progress(phase="idle", percent=0, marker="", error=None, finished_at=time.time())
            return {"ok": False, "stopped": True, "error": "Stopped before it finished loading."}
        if spawned.poll() is not None:
            tail = _log_tail()
            hint = load_failure_hint(tail)
            with _start_lock: _starting_paths.discard(normalized_path)
            _set_progress(phase="error",
                          error=f"server exited (code {spawned.returncode}){hint}",
                          finished_at=time.time())
            return {"ok": False,
                    "error": f"server exited (code {spawned.returncode}) before responding{hint}",
                    "log_path": str(log_path),
                    "log_tail": tail[-2000:]}
        # Read any new bytes appended to the log since last poll, scan for
        # phase markers, advance the progress snapshot. Cheap — typically
        # < 1KB per poll. We only LOOK FORWARD from log_pos so a long log
        # doesn't get re-scanned every iteration.
        try:
            with open(log_path, "rb") as _lf:
                _lf.seek(log_pos)
                chunk = _lf.read().decode("utf-8", errors="replace")
                log_pos += len(chunk.encode("utf-8", errors="replace"))
            for ln in chunk.splitlines():
                phase_pct = _phase_for_line(ln)
                if phase_pct is not None:
                    ph, pct = phase_pct
                    if pct > last_phase_percent:
                        last_phase_percent = pct
                        last_phase_name = ph
                        last_marker = ln.strip()[:120]
        except OSError:
            pass
        # The log gives the floor, the process gives the movement between
        # markers. Capped below the point where the engine announces the model
        # is loaded, so a measurement that overshoots cannot claim the load is
        # finished; only the log says that.
        _cand = last_phase_percent
        if last_phase_percent < _WEIGHT_BAND_TOP:
            _frac = _weight_load_fraction(spawned.pid, _weight_bytes)
            if _frac is not None:
                _measured = _WEIGHT_BAND_BOTTOM + int(
                    _frac * (_WEIGHT_BAND_TOP - _WEIGHT_BAND_BOTTOM))
                if _measured > _cand:
                    _cand = _measured
                    if last_phase_percent < 50:
                        last_phase_name = "loading_weights"
        if _cand > shown_percent:
            shown_percent = _cand
            _set_progress(phase=last_phase_name, percent=shown_percent,
                          marker=last_marker)
        # Probe with the API key — builtin 401s unauthenticated requests, which
        # would make external_server_running return False even though the server
        # is fully up. external_server_running handles 401 as "alive" too.
        # Lowered timeout to 0.5s: when the server isn't ready yet, the TCP
        # connect either succeeds quickly (port bound, uvicorn not accepting
        # yet — slow socket close) or fails fast. 0.5s is long enough for the
        # response when the server IS ready and short enough to keep cadence.
        if external_server_running(api_base, timeout=0.5, api_key=BUILTIN_API_KEY):
            if os.environ.get("HEARTH_QUIET_ENGINE") != "1":      # the terminal draws its own line
                try:
                    print(f"  [hearth.llmserver] ready in {time.time() - t_spawn:.1f}s", flush=True)
                except Exception:
                    pass
            _set_progress(phase="ready", percent=100,
                          marker=f"ready in {time.time() - t_spawn:.1f}s",
                          finished_at=time.time())
            break
        time.sleep(poll)
        # Back off from 200ms → 1.5s so we're cheap during the cold phase
        # but don't spin the CPU.
        poll = min(1.5, poll * 1.5)
    else:
        tail = _log_tail()
        with _start_lock: _starting_paths.discard(normalized_path)
        _set_progress(phase="error", error="timed out after 120s",
                      finished_at=time.time())
        return {"ok": False,
                "error": "server did not respond within 120s — see log tail below",
                "log_path": str(log_path),
                "log_tail": tail[-2000:]}

    # Resolve GPU-vs-CPU split for display. n_gpu_layers is now concrete:
    # -1 = all layers on GPU, 0 = CPU-only, N = first N on GPU.
    _total_layers = gguf_layer_count(model_path)
    if n_gpu_layers == -1:
        _gpu_on = _total_layers
    elif n_gpu_layers == 0:
        _gpu_on = 0
    else:
        _gpu_on = n_gpu_layers
    _proc_info = {"url": api_base, "model_path": model_path, "port": port, "ctx": ctx,
                  "gpu_layers_on": _gpu_on, "total_layers": _total_layers,
                  "slots": slots, "total_ctx": total_ctx,
                  "exe": (_native or {}).get("exe") if _use_native else None,
                  "engine": "native" if _use_native else "wheel",
                  "kv_unified": "--kv-unified" in cmd}
    _write_runfile(_proc_info)
    # A helper server on this same file is now a second copy of it: helpers use
    # this one instead (subagents route to the main server when none runs).
    try:
        if helper_status().get("running") and same_file(_helper["info"].get("model_path", ""), model_path):
            stop_helper()
    except Exception:
        pass
    # Remember the user's tuned config so the next "Use this" on the same
    # .gguf restores GPU offload / ctx / KV cache without them setting it again.
    save_model_config(model_path, {
        "ctx": ctx,
        "n_gpu_layers": n_gpu_layers,
        "n_threads": n_threads,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "flash_attn": flash_attn,
        "extra_args": extra_args,
    })
    # First real use of a native engine: prove it emits a token before trusting
    # it. Booting and reporting ready is not proof — a build with no kernels for
    # the installed GPU does both and then returns empty for every request.
    # Verified once per exe, then remembered.
    if _use_native and _native and not _smoke_checked:
        _exe = (_native or {}).get("exe") or ""
        if _engine_health().get(_exe) is None:
            if _engine_emits_token(api_base):
                set_engine_health(_exe, True)
            else:
                set_engine_health(_exe, False, "booted but generated no tokens")
                try:
                    print(f"  [hearth.llmserver] engine {Path(_exe).parent.name} "
                          f"produced no output — falling back", flush=True)
                except Exception:
                    pass
                stop_builtin()
                reset_native_cache()
                with _start_lock: _starting_paths.discard(normalized_path)
                return start_builtin(model_path, port=port, ctx=ctx,
                                     n_gpu_layers=n_gpu_layers, n_threads=n_threads,
                                     cache_type_k=cache_type_k, cache_type_v=cache_type_v,
                                     flash_attn=flash_attn, force=force,
                                     _smoke_checked=True)

    # Release the path-level lock — this load is fully complete.
    with _start_lock: _starting_paths.discard(normalized_path)
    result = {"ok": True, "url": api_base, "pid": _proc.pid, "info": _proc_info}
    if vram_warning:
        result["warning"] = vram_warning
    return result


_stop_asked = [0.0]           # when a stop was last asked for, so a load that dies then isn't called a failure


def _stop_other_surface() -> bool:
    """Stop a built-in server another Hearth window or terminal started."""
    pid = read_runfile().get("pid")
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=0x08000000)
            else:
                os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        _status_cache["data"] = None
    _clear_runfile()
    return bool(pid)


def stop_builtin() -> Dict[str, Any]:
    """Terminate the running builtin server, if any. A model that is still
    loading is stopped the same way."""
    global _proc, _proc_info
    _stop_asked[0] = time.time()
    if _proc is None or _proc.poll() is not None:
        _proc = None
        _proc_info = {}
        # Not our subprocess, but the other surface may still be running one.
        # Now that status() reports it as builtin, Eject has to actually eject
        # it rather than silently no-op on the surface that didn't start it.
        if _stop_other_surface():
            return {"ok": True, "was_running": True, "adopted": True}
        return {"ok": True, "was_running": False}
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait(timeout=3)
    except Exception:
        pass
    _proc = None
    _proc_info = {}
    _clear_runfile()
    # Invalidate the status cache so the next GUI poll reflects the stop
    # instantly instead of waiting for the 8s TTL.
    invalidate_status_cache()
    return {"ok": True, "was_running": True}


def invalidate_status_cache() -> None:
    """Force the next status() call to rebuild from scratch.

    The status cache keys on (api_base, builtin_running), so a builtin
    start/stop already busts it implicitly. But state changes that DON'T
    flip those — an LM Studio REST eject, a cloud↔local brain switch where
    the new api_base happens to match a previously-cached key — would let a
    stale snapshot survive up to _STATUS_TTL_SECONDS. Callers in web.py
    (eject, brain-switch, start) call this so the very next /api/llmserver/
    status and /api/state reflect reality immediately, not up to 8s late."""
    _status_cache["data"] = None
    _status_cache["ts"] = 0
    _status_cache["key"] = None


def search_huggingface(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Search HF for GGUF models matching the query, without leaving Hearth.
    Hearth names no model of its own to download. Filters to GGUF repos so the
    list is restricted to things llama.cpp can actually load. Public API,
    no auth needed."""
    # A pasted page link searches for its repo. filter=gguf already keeps the
    # list to GGUF repos; adding "gguf" to the words lost owner/name searches.
    query = re.sub(r"^https?://(www\.)?huggingface\.co/", "", (query or "").strip()).strip("/")
    if not query:
        return []
    url = ("https://huggingface.co/api/models?"
           f"search={urllib.parse.quote(query)}"
           f"&filter=gguf&sort=downloads&direction=-1&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=8) as r:
            models = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]
    out = []
    for m in models[:limit]:
        repo = m.get("modelId") or m.get("id") or ""
        if not repo:
            continue
        out.append({
            "hf_repo": repo,
            "name": repo.split("/")[-1],
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "tags": (m.get("tags") or [])[:6],
            "kind": model_kind(m),
        })
    return out


def model_kind(m: Dict[str, Any]) -> str:
    """chat, image or video, from what Hugging Face says the repo does (its
    pipeline tag, else a tag of the same form)."""
    tags = [str(m.get("pipeline_tag") or "")] + [str(t) for t in m.get("tags") or []]
    for t in tags:
        if t.endswith("-to-video"):
            return "video"
        if t.endswith("-to-image"):
            return "image"
    return "chat"


def _estimate_gguf_size_gb(filename: str) -> Optional[float]:
    """Best-effort size estimate from filename quant tag + parameter count.

    HF's `/api/models/<repo>` siblings list often returns `size: null` for
    LFS-tracked files; we'd render "Loading files…" forever or show no size.
    This estimate is rough but lets the user see VRAM-fit at a glance even
    when HF won't tell us the real bytes.

    Heuristic: extract params (3B / 7B / 9B / 13B / 70B) from the filename,
    extract quant tag (Q2_K / Q4_K_M / Q5_K_M / Q6_K / Q8_0 / F16), apply
    standard bytes-per-weight, return GB."""
    if not filename:
        return None
    fn = filename.lower()
    # Param count
    import re as _re
    m = _re.search(r"(\d+(?:\.\d+)?)\s*b\b", fn)
    if not m:
        return None
    try:
        params_b = float(m.group(1))
    except ValueError:
        return None
    # Quant-tag → bytes-per-weight (empirical, llama.cpp standard quants)
    quant_bytes = {
        "q2_k":   0.31, "q3_k_s": 0.35, "q3_k_m": 0.40, "q3_k_l": 0.43,
        "q4_0":   0.50, "q4_k_s": 0.50, "q4_k_m": 0.56, "q4_1": 0.56,
        "q5_0":   0.62, "q5_k_s": 0.62, "q5_k_m": 0.69, "q5_1": 0.69,
        "q6_k":   0.81, "q8_0":   1.06, "f16":    2.0,  "fp16": 2.0,
    }
    bytes_per = None
    for tag, bp in quant_bytes.items():
        if tag in fn:
            bytes_per = bp
            break
    if bytes_per is None:
        bytes_per = 0.56  # Q4_K_M is the modal pick if no tag matches
    return round(params_b * bytes_per, 2)


# Driver/OS overhead reserve on a fresh Windows desktop with a discrete
# GPU. NVIDIA driver + Windows compositor + WebView2 each take a slice;
# Headroom the fit LABEL leaves on the table. Was 2.29 GB — far more pessimistic
# than the actual loader (which reserves ~0.6 GB), so a model that loads fine got
# labeled as spilling. Since load no longer refuses on VRAM and partial offload is
# framed as normal, keep this close to the loader's real reserve so the label
# matches what actually happens. Tweakable.
_VRAM_DRIVER_RESERVE_GB = 1.0


_GGUF_FIXED_SZ = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_layer_count_cache: Dict[str, Optional[int]] = {}
def gguf_layer_count(model_path: str) -> Optional[int]:
    """Read <arch>.block_count (the transformer layer count) straight from the
    GGUF header — no llama.cpp load, no deps. Lets the offload math size against
    the model's REAL layer count instead of a 40-layer proxy. Returns None if
    the header can't be parsed."""
    if model_path in _layer_count_cache:
        return _layer_count_cache[model_path]
    import struct
    result: Optional[int] = None
    try:
        with open(model_path, "rb") as f:
            if f.read(4) != b"GGUF":
                _layer_count_cache[model_path] = None
                return None
            f.read(4)                                    # version
            f.read(8)                                    # tensor_count
            kv_count = struct.unpack("<Q", f.read(8))[0]

            def _skip(vt: int) -> None:
                if vt in _GGUF_FIXED_SZ:
                    f.read(_GGUF_FIXED_SZ[vt])
                elif vt == 8:                            # STRING
                    f.read(struct.unpack("<Q", f.read(8))[0])
                elif vt == 9:                            # ARRAY
                    et = struct.unpack("<I", f.read(4))[0]
                    for _ in range(struct.unpack("<Q", f.read(8))[0]):
                        _skip(et)
                else:
                    raise ValueError(f"unknown gguf type {vt}")

            for _ in range(kv_count):
                klen = struct.unpack("<Q", f.read(8))[0]
                key = f.read(klen).decode("utf-8", "replace")
                vt = struct.unpack("<I", f.read(4))[0]
                if key.endswith(".block_count"):
                    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I",
                           5: "<i", 10: "<Q", 11: "<q"}.get(vt, "<I")
                    result = int(struct.unpack(fmt, f.read(_GGUF_FIXED_SZ.get(vt, 4)))[0])
                    break
                _skip(vt)
    except Exception:
        result = None
    _layer_count_cache[model_path] = result
    return result


_ctx_length_cache: Dict[str, Optional[int]] = {}


def gguf_context_length(model_path: str) -> Optional[int]:
    """Read <arch>.context_length (the model's REAL trained max context) from the
    GGUF header, exactly how gguf_layer_count reads block_count. This is the true
    ceiling (e.g. 1048576 for a 1M-context model), so we stop guessing it from
    the filename and capping capable models way too low. None if unparseable."""
    if model_path in _ctx_length_cache:
        return _ctx_length_cache[model_path]
    import struct
    result: Optional[int] = None
    try:
        with open(model_path, "rb") as f:
            if f.read(4) != b"GGUF":
                _ctx_length_cache[model_path] = None
                return None
            f.read(4)                                    # version
            f.read(8)                                    # tensor_count
            kv_count = struct.unpack("<Q", f.read(8))[0]

            def _skip(vt: int) -> None:
                if vt in _GGUF_FIXED_SZ:
                    f.read(_GGUF_FIXED_SZ[vt])
                elif vt == 8:                            # STRING
                    f.read(struct.unpack("<Q", f.read(8))[0])
                elif vt == 9:                            # ARRAY
                    et = struct.unpack("<I", f.read(4))[0]
                    for _ in range(struct.unpack("<Q", f.read(8))[0]):
                        _skip(et)
                else:
                    raise ValueError(f"unknown gguf type {vt}")

            for _ in range(kv_count):
                klen = struct.unpack("<Q", f.read(8))[0]
                key = f.read(klen).decode("utf-8", "replace")
                vt = struct.unpack("<I", f.read(4))[0]
                if key.endswith(".context_length"):
                    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I",
                           5: "<i", 10: "<Q", 11: "<q"}.get(vt, "<I")
                    result = int(struct.unpack(fmt, f.read(_GGUF_FIXED_SZ.get(vt, 4)))[0])
                    break
                _skip(vt)
    except Exception:
        result = None
    _ctx_length_cache[model_path] = result
    return result


def estimate_safe_gpu_layers(model_size_gb: Optional[float],
                             free_vram_gb: Optional[float],
                             ctx: int = 24576,
                             layer_count: Optional[int] = None,
                             cache_type: Optional[str] = None,
                             reserve_gb: float = 2.3,
                             kv_may_spill: bool = False) -> int:
    """How many transformer layers to put on the GPU. Returns -1 (offload all),
    0 (CPU-only, no VRAM budget), or a positive count (partial offload, the rest
    run on CPU).

    With `kv_may_spill` the weights are what must fit and the KV cache is
    allowed to page into shared memory; otherwise both must fit inside free
    VRAM.

    `reserve_gb` is headroom kept free for the CUDA context + n_batch compute
    buffer. The old llama-cpp-python wheel needed a big reserve (2.3) because a
    spill there returned empty/garbage — but the native llama-server spills
    gracefully (slow, not broken), so it can run a smaller reserve and keep far
    more layers on the GPU (much faster on a tight 8 GB)."""
    if not model_size_gb or not free_vram_gb or model_size_gb <= 0:
        return 0
    kv_gb = estimate_kv_cache_gb(model_size_gb, ctx, cache_type)
    L = layer_count or 40  # real count when known, proxy otherwise
    budget = free_vram_gb - reserve_gb
    if budget <= 0:
        return 0
    # `kv_may_spill` is the standalone server, which pages the KV cache into
    # shared memory rather than failing. Deciding on the weights alone there is
    # what LM Studio does, and it matters: requiring the whole KV to fit held
    # layers back on a card the weights fit on comfortably, paying a permanent
    # per-token cost to avoid a spill that only slows the part that spilled.
    # The old wheel returns garbage on a spill, so it still budgets for both.
    footprint = model_size_gb if kv_may_spill else model_size_gb + kv_gb
    if footprint <= budget:
        return -1  # offload all
    # Round up rather than truncate: a layer on the GPU beats one on the CPU,
    # and the reserve above already carries the safety margin.
    return max(1, min(L, math.ceil((budget / footprint) * L)))


def _kv_quant_factor(cache_type: Optional[str]) -> float:
    """KV-cache memory multiplier vs f16, by quant. q8_0 ~halves it, q4 ~quarter.
    Matches what LM Studio does by default to fit long context on consumer GPUs."""
    return {"f16": 1.0, "q8_0": 0.53, "q4_0": 0.27, "q4_1": 0.29}.get(
        (cache_type or "f16").lower(), 1.0)


def estimate_kv_cache_gb(model_size_gb: Optional[float], ctx: int = 8192,
                         cache_type: Optional[str] = None) -> float:
    """Approximate KV cache footprint (f16) at the given context window. Linear
    in ctx. Baseline tuned for modern GQA models (grouped-query attention keeps
    the KV small — the old 10%-of-weights baseline over-estimated ~2x and made
    models that actually fit look like they'd spill). `cache_type` applies the
    quant multiplier when the KV cache is quantized."""
    if not model_size_gb or model_size_gb <= 0:
        return 0.0
    return model_size_gb * 0.06 * (ctx / 4096.0) * _kv_quant_factor(cache_type)


def _total_ram_gb() -> Optional[float]:
    """Total system RAM in GB, or None if unreadable. Lets vram_fit_class tell a
    model that merely partial-offloads to CPU (fits VRAM + RAM) apart from one
    that genuinely won't load at all (bigger than VRAM + RAM combined)."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    try:  # Windows fallback, no psutil needed
        import ctypes

        class _MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = _MEMSTAT()
        ms.dwLength = ctypes.sizeof(_MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.ullTotalPhys / (1024 ** 3)
    except Exception:
        return None


def vram_fit_class(model_size_gb: Optional[float],
                   free_vram_gb: Optional[float] = None,
                   ctx: int = 8192) -> Dict[str, str]:
    """Classify how a model fits the current GPU. Returns
    {'tier': 'good'|'partial'|'overflow'|'unknown', 'label': '...'}.

    Math:
      required = model_size + estimate_kv_cache_gb(model_size, ctx)
      usable   = max(0, free_vram - driver_reserve GB)

      good     — required <= usable                       (full GPU offload)
      partial  — required <= total_vram                   (some CPU spill)
      overflow — required > total_vram                    (CPU-mostly)
      unknown  — we couldn't probe size or VRAM

    More conservative than a flat 75%-of-VRAM rule because the driver
    reserve is excluded from the budget instead of being silently absorbed
    by it — keeps a 5-6 GB model from looking "comfortable" on an 8 GB
    card when it would actually spill KV to CPU mid-stream."""
    if model_size_gb is None:
        return {"tier": "unknown", "label": ""}
    if free_vram_gb is None:
        free_vram_gb = detect_gpu_vram_free_gb()
        if free_vram_gb is None:
            free_vram_gb = detect_gpu_vram_gb()
    if free_vram_gb is None:
        return {"tier": "unknown", "label": "no GPU detected"}
    required = model_size_gb + estimate_kv_cache_gb(model_size_gb, ctx)
    usable = max(0.0, free_vram_gb - _VRAM_DRIVER_RESERVE_GB)
    if required <= usable:
        return {"tier": "good",     "label": f"fits on GPU ({required:.1f} of {usable:.1f} GB)"}
    if required <= free_vram_gb:
        return {"tier": "partial",  "label": f"partial offload — a few layers on CPU ({required:.1f} GB vs {usable:.1f} GB usable), still runs fine"}
    # Genuinely "too big" ONLY when it won't fit VRAM + system RAM combined — then
    # it really can't load (or would page off disk unusably). Short of that,
    # llama.cpp runs it mostly on the CPU: slower, but it works. That distinction
    # is the whole point — don't red-flag a usable model, DO flag an impossible one.
    ram = _total_ram_gb()
    if ram is not None and required > free_vram_gb + max(0.0, ram - 2.0):
        return {"tier": "toobig",   "label": f"too big to load — needs ~{required:.1f} GB, over VRAM + system RAM combined"}
    # How much of it lands on the card decides what to call it: a model that
    # just misses runs nearly all on the GPU, which isn't "mostly on CPU".
    on_gpu = usable / required if required else 0.0
    if on_gpu >= 0.5:
        return {"tier": "partial",  "label": f"about {round((1 - on_gpu) * 100)}% on CPU ({required:.1f} GB vs {usable:.1f} GB usable), slower but runs fine"}
    return {"tier": "overflow",     "label": f"mostly on CPU — needs ~{required:.1f} GB, {free_vram_gb:.1f} GB free; still runs, just slower"}


def quant_tier(filename: str) -> str:
    """Return the bucket label (Q2..Q8/F16) for a GGUF filename. The picker
    uses this to badge each file and to mark the modal Q4_K_M as recommended.
    Returns empty string when no quant tag is detectable."""
    fn = (filename or "").lower()
    if re.search(r"(?:^|[-_.])b?f16(?:[-_.]|$)", fn) or any(t in fn for t in ("fp16", "float16")):
        return "F16"
    if re.search(r"(?:^|[-_.])f32(?:[-_.]|$)", fn):
        return "F32"
    # Q4_K_M, IQ4_NL, IQ3_XXS, TQ1_0 and the like: the digit is the tier.
    m = re.search(r"(?:^|[-_.])(?:i|t|ud-)?q(\d)(?:[-_.]|$)", fn)
    return f"Q{m.group(1)}" if m else ""


def _is_recommended_quant(filename: str) -> bool:
    """Q4_K_M is the de-facto sweet spot — same quality as Q5 in practice, ~20%
    smaller. We surface a Recommended chip on it so first-time users don't
    have to learn what "K_M" means before they can pick something sane."""
    return "q4_k_m" in (filename or "").lower()


def list_hf_files(repo: str) -> List[Dict[str, Any]]:
    """List GGUF files inside an HF repo so the user can pick the right quant
    level. Returns size estimates (from filename when HF doesn't expose the
    real bytes), a VRAM-fit tag, a quant tier, and a "recommended" marker on
    the modal Q4_K_M pick — so the GUI can show fit badges + a one-glance
    starter recommendation."""
    if not repo:
        return []
    try:
        url = f"https://huggingface.co/api/models/{urllib.parse.quote(repo, safe='/')}"
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"could not reach HuggingFace: {type(e).__name__}: {e}"}]
    free_vram = vram_for_a_new_model()
    out = []
    has_recommended = False
    addon = vision_addon(info.get("siblings") or [])
    for sib in info.get("siblings", []) or []:
        fn = sib.get("rfilename") or ""
        # A vision projector isn't a model to run; it comes along with one.
        if not fn.lower().endswith(".gguf") or "mmproj" in fn.lower():
            continue
        raw_size = sib.get("size")
        actual = round(raw_size / (1024 ** 3), 2) if raw_size else None
        estimated = _estimate_gguf_size_gb(fn) if actual is None else None
        size_gb = actual if actual is not None else estimated
        fit = vram_fit_class(size_gb, free_vram)
        # Only badge a recommended pick if it actually FITS — pointing a new
        # user at a Q4_K_M that'll spill to CPU is worse than just letting
        # them sort by size.
        rec = (not has_recommended
               and _is_recommended_quant(fn)
               and fit["tier"] in ("good", "partial"))
        if rec:
            has_recommended = True
        out.append({
            "filename": fn,
            "size_gb": size_gb,
            "size_estimated": actual is None and estimated is not None,
            "fit_tier": fit["tier"],
            "fit_label": fit["label"],
            "quant_tier": quant_tier(fn),
            "recommended": rec,
            "vision": bool(addon),
        })
    # Sort: recommended first, then by VRAM fit (good→partial→overflow), then
    # by size ascending within tier. Keeps the safest pick on top regardless
    # of how the repo orders its siblings.
    tier_order = {"good": 0, "partial": 1, "unknown": 2, "overflow": 3}
    out.sort(key=lambda f: (
        0 if f.get("recommended") else 1,
        tier_order.get(f.get("fit_tier", "unknown"), 9),
        f.get("size_gb") or 999,
    ))
    return out


_SUGGEST: Dict[str, Any] = {}
_NOT_A_START = re.compile(r"uncensor|abliterat|heretic|obliterat|nsfw|erotic|roleplay|rp-", re.I)


def _from_its_maker(m: Dict[str, Any]) -> bool:
    """A GGUF the model's own makers published: its base model is theirs, or,
    with no base named, their name is in the model's."""
    repo = str(m.get("id") or m.get("modelId") or "")
    owner = repo.split("/")[0].lower()
    bases = [t.split(":")[-1] for t in (m.get("tags") or []) if str(t).startswith("base_model:")]
    if bases:
        return all(b.split("/")[0].lower() == owner for b in bases)
    stem = re.sub(r"[-_.]?(ai|ml|labs?|org|inc|team|research)$", "", owner)
    return len(stem) >= 3 and stem.replace("-", "") in repo.split("/")[-1].lower().replace("-", "")


_POPULAR: Dict[str, Any] = {}


def popular_chat_models(limit: int = 12) -> List[Dict[str, Any]]:
    """The most downloaded chat GGUFs this month, the ones their own makers
    publish first, for the Models page before anything is searched. Nothing is
    hidden; the order only says who made what."""
    if _POPULAR.get("at", 0) > time.time() - 3600:
        return _POPULAR["list"]
    url = ("https://huggingface.co/api/models?filter=gguf&pipeline_tag=text-generation"
           "&sort=downloads&direction=-1&limit=40&expand[]=gguf&expand[]=downloads&expand[]=likes&expand[]=tags")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=10) as r:
            models = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]
    out = []
    for m in models:
        repo = str(m.get("id") or "")
        if not repo:
            continue
        out.append({"hf_repo": repo, "name": repo.split("/")[-1], "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0), "tags": [t for t in (m.get("tags") or []) if not str(t).startswith("base_model:")][:6],
                    "params_b": round(((m.get("gguf") or {}).get("total") or 0) / 1e9, 1) or None,
                    "makers": _from_its_maker(m)})
    out.sort(key=lambda x: (not x["makers"], -(x["downloads"] or 0)))
    _POPULAR.update(at=time.time(), list=out[:limit])
    return _POPULAR["list"]


def suggest_model(vram_gb: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """A first model for this PC, chosen the way people are choosing now: the
    most downloaded chat GGUF this month that its makers published and that
    fits the graphics card. Hearth keeps no list of models; Hugging Face is
    asked each time (once a day at most)."""
    vram = detect_gpu_vram_gb() if vram_gb is None else vram_gb
    budget = (vram * 0.85) if vram else 3.5        # no card: a small model on the processor
    key = round(budget, 1)
    hit = _SUGGEST.get(key)
    if hit and time.time() - hit["at"] < 86400:
        return hit["pick"]
    url = ("https://huggingface.co/api/models?filter=gguf&pipeline_tag=text-generation"
           "&sort=downloads&direction=-1&limit=60&expand[]=gguf&expand[]=downloads&expand[]=tags")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=10) as r:
            models = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    pick = None
    for m in models:
        repo = str(m.get("id") or "")
        params = ((m.get("gguf") or {}).get("total") or 0) / 1e9
        if not repo or params < 1 or _NOT_A_START.search(repo) or not _from_its_maker(m):
            continue
        if params * 0.6 + 0.5 > budget:              # a Q4_K_M of it, roughly, with room to run
            continue
        files = [f for f in list_hf_files(repo) if not f.get("error")]
        good = [f for f in files if f.get("recommended")] or \
               [f for f in files if f.get("fit_tier") == "good" and (f.get("quant_tier") or 0) >= 4]
        if not good:
            continue
        f = good[0]
        pick = {"repo": repo, "filename": f["filename"], "size_gb": f.get("size_gb"),
                "params_b": round(params, 1), "downloads": m.get("downloads") or 0}
        break
    _SUGGEST[key] = {"at": time.time(), "pick": pick}
    return pick


def vision_addon(siblings: List[Dict[str, Any]]) -> str:
    """The repo's vision projector to fetch with a model from it, if it has one:
    F16 before BF16 before F32 (same quality, the smaller file)."""
    names = [str(x.get("rfilename") or "") for x in siblings]
    names = [n for n in names if n.lower().endswith(".gguf") and "mmproj" in n.lower()]
    def rank(n: str) -> int:
        n = n.lower()
        return 1 if "bf16" in n else 0 if "f16" in n else 2 if "f32" in n else 3
    return sorted(names, key=lambda n: (rank(n), len(n)))[0] if names else ""


_REPO_SIBLINGS: Dict[str, List[Dict[str, Any]]] = {}


def _repo_siblings(repo: str) -> List[Dict[str, Any]]:
    if repo not in _REPO_SIBLINGS:
        url = f"https://huggingface.co/api/models/{urllib.parse.quote(repo, safe='/')}?blobs=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=8) as r:
            _REPO_SIBLINGS[repo] = json.loads(r.read().decode("utf-8")).get("siblings") or []
    return _REPO_SIBLINGS[repo]


def repo_for_file(path: str) -> str:
    """The Hugging Face repo a model file came from: remembered when Hearth
    downloaded it, else found as a GGUF repo that holds a file of that exact
    name (and remembered from then on)."""
    name = os.path.basename(path)
    repo = model_source(name)
    if repo:
        return repo
    stem = re.sub(r"[-_.](?:ud-)?(?:i|t)?q\d[\w]*$|[-_.]b?f(?:16|32)$", "", Path(name).stem, flags=re.I)
    for hit in search_huggingface(stem, limit=6):
        r = hit.get("hf_repo") or ""
        try:
            if r and any((x.get("rfilename") or "").split("/")[-1] == name for x in _repo_siblings(r)):
                record_model_source(path, r)
                return r
        except Exception:
            continue
    return ""


def vision_for_file(path: str, look_up: bool = True) -> Dict[str, Any]:
    """Whether a model file can see. "sees": its projector is beside it.
    "needs_file": its repo publishes one that isn't here yet (it can see once
    that's fetched). "text": no projector anywhere we know of. "unknown": the
    repo couldn't be found or reached."""
    if _find_mmproj(path):
        return {"state": "sees"}
    if not look_up:
        return {"state": "unknown"}
    try:
        repo = repo_for_file(path)
        if not repo:
            return {"state": "unknown"}
        sibs = _repo_siblings(repo)
        addon = vision_addon(sibs)
        if not addon:
            return {"state": "text", "repo": repo}
        size = next((x.get("size") or (x.get("lfs") or {}).get("size") for x in sibs if x.get("rfilename") == addon), None)
        return {"state": "needs_file", "repo": repo, "file": addon, "save_as": vision_addon_name(repo, addon),
                "folder": os.path.dirname(os.path.abspath(path)),
                "size_gb": round(size / 1e9, 2) if isinstance(size, (int, float)) else None}
    except Exception as e:
        return {"state": "unknown", "error": f"{type(e).__name__}: {e}"}


def vision_addon_name(repo: str, filename: str) -> str:
    """Saved under the repo's name, so two models' projectors in the one models
    folder don't overwrite each other and each pairs with its own model."""
    return f"{repo.split('/')[-1]}-{filename.split('/')[-1]}"


_SOURCES_FILE = "sources.json"


def _sources_path() -> Path:
    return MODELS_DIR / _SOURCES_FILE


def record_model_source(path: str, repo: str) -> None:
    """Remember which Hugging Face repo a downloaded GGUF came from.

    The file is saved flat in the models folder, so the repo it came from was
    lost the moment the download finished, and the UI could only guess it back
    from the filename. Keep a small sidecar map instead. Best-effort: a failure
    here must never break a download.
    """
    if not path or not repo:
        return
    try:
        import json as _j
        p = _sources_path()
        data = {}
        if p.is_file():
            try:
                data = _j.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        data[os.path.basename(str(path)).lower()] = repo
        p.write_text(_j.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def model_source(filename: str) -> str:
    """Repo a downloaded model came from, or "" if unknown."""
    try:
        import json as _j
        p = _sources_path()
        if not p.is_file():
            return ""
        data = _j.loads(p.read_text(encoding="utf-8")) or {}
        return str(data.get(os.path.basename(str(filename)).lower()) or "")
    except Exception:
        return ""


def download_from_hf_repo(repo: str, filename: str,
                          on_progress: Optional[Callable[[int, int], None]] = None,
                          cancel: Optional[Callable[[], bool]] = None,
                          dest_dir: str = "", save_as: str = "") -> Dict[str, Any]:
    """Download an arbitrary GGUF picked from search_huggingface() results.
    A .part file holds it until it is whole, so an interrupted download
    never looks complete."""
    if not repo or not filename:
        return {"ok": False, "error": "repo and filename required"}
    if not filename.lower().endswith(".gguf"):
        return {"ok": False, "error": "only .gguf files supported"}
    safe_name = (save_as or filename).split("/")[-1]
    dest = (Path(dest_dir) if dest_dir else MODELS_DIR) / safe_name
    if dest.exists():
        record_model_source(str(dest), repo)   # backfill if it predates the map
        return {"ok": True, "path": str(dest), "already": True}
    url = _hf_resolve_url(repo, filename)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        done = _download_with_resume(url, dest, tmp, on_progress, cancel=cancel)
        record_model_source(str(dest), repo)
        return {"ok": True, "path": str(dest), "bytes": done}
    except DownloadCancelled:
        return {"ok": False, "cancelled": True,
                "error": "download paused (progress kept, it will resume)"}
    except Exception as e:
        # Keep the .part so the next attempt RESUMES instead of restarting.
        return {"ok": False, "error": f"download failed: {type(e).__name__}: {e}"}


_status_cache: Dict[str, Any] = {"ts": 0, "data": None, "key": None}
# Time-to-live for the status cache. Multiple GUI surfaces (Models tab,
# Server settings card, /api/state, /api/llmserver/status) call status()
# rapidly and each probe was hitting LM Studio's /api/v0/models — the
# response includes the FULL tokenizer chat_template (8K+ chars). LM
# Studio's main thread serializing that every 1-2s explained the chat
# lag the user noticed after adding the built-in server.
# Bumped to 8s so the refreshState (12s) and refreshModels (9s) GUI
# pollers stay under the cache TTL — they share one probe instead of
# each one round-tripping LM Studio.
_STATUS_TTL_SECONDS = 8.0


_PENDING_DL_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "pending_download.json")


def _read_pending() -> Dict[str, Dict[str, Any]]:
    """{filename: {"repo": ..., "paused": bool}} for every unfinished download.
    Older installs stored a single {"repo", "filename"} pair; that still reads."""
    try:
        with open(_PENDING_DL_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return {}
    if "downloads" in data:
        return {k: dict(v) for k, v in (data.get("downloads") or {}).items() if isinstance(v, dict)}
    if data.get("filename"):
        return {data["filename"]: {"repo": data.get("repo", ""), "paused": False}}
    return {}


def _write_pending(entries: Dict[str, Dict[str, Any]]) -> None:
    try:
        os.makedirs(os.path.dirname(_PENDING_DL_PATH), exist_ok=True)
        tmp = _PENDING_DL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"downloads": entries}, f, indent=1)
        os.replace(tmp, _PENDING_DL_PATH)
    except Exception:
        pass


def save_pending_download(repo: str, filename: str, paused: bool = False, vision_for: str = "") -> None:
    """Remember which repo a download comes from so it can resume after a restart;
    a model's vision file also remembers the model it belongs beside."""
    with _dl_lock:
        entries = _read_pending()
        entries[filename] = {"repo": repo, "paused": bool(paused), **({"vision_for": vision_for} if vision_for else {})}
        _write_pending(entries)


def clear_pending_download(filename: Optional[str] = None) -> None:
    """Forget one download (or all of them when no filename is given)."""
    with _dl_lock:
        entries = _read_pending() if filename else {}
        entries.pop(filename, None)
        _write_pending(entries)


# Model downloads run on a backend thread, not on the window's HTTP request, so
# closing the window to the tray doesn't stop them. The window watches progress
# and can reattach; only an explicit pause or discard stops a transfer.
_dl_lock = _threading.RLock()
_dl_jobs: Dict[str, Dict[str, Any]] = {}


def _run_download_job(job: Dict[str, Any]) -> None:
    def on_progress(done: int, total: int) -> None:
        job["done"], job["total"] = done, total

    try:
        result = download_from_hf_repo(job["repo"], job["filename"], on_progress=on_progress,
                                       cancel=job["stop"].is_set, dest_dir=job.get("dest_dir", ""),
                                       save_as=job.get("save_as", ""))
        if result.get("ok") and not job.get("save_as"):
            _fetch_vision_addon(job, on_progress)
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    job["result"] = result
    if result.get("ok"):
        job["status"] = "done"
        clear_pending_download(job["filename"])
        _status_cache.pop("disk_models_cache", None)
        _status_cache.pop("disk_scan_ts", None)
    elif result.get("cancelled"):
        job["status"] = "paused"
    else:
        job["status"] = "error"


def _fetch_vision_addon(job: Dict[str, Any], on_progress) -> None:
    """A model that can see needs its projector beside it; fetch it once the
    model is in. Missing it only costs vision, so a failure here is noted, not fatal."""
    try:
        url = f"https://huggingface.co/api/models/{urllib.parse.quote(job['repo'], safe='/')}"
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen/0.7.6"})
        with urllib.request.urlopen(req, timeout=8) as r:
            addon = vision_addon(json.loads(r.read().decode("utf-8")).get("siblings") or [])
        if not addon:
            return
        dest = MODELS_DIR / vision_addon_name(job["repo"], addon)
        if dest.exists():
            return
        job["phase"] = "vision"
        _download_with_resume(_hf_resolve_url(job["repo"], addon), dest, dest.with_suffix(dest.suffix + ".part"),
                              on_progress, cancel=job["stop"].is_set)
    except Exception as e:
        job["vision_error"] = f"{type(e).__name__}: {e}"
    finally:
        job.pop("phase", None)


def start_model_download(repo: str, filename: str, dest_dir: str = "", save_as: str = "",
                         vision_for: str = "") -> Dict[str, Any]:
    """Start a download, or return the one already running for this file."""
    name = (save_as or filename).split("/")[-1]
    with _dl_lock:
        job = _dl_jobs.get(name)
        if job and job["status"] == "downloading":
            return job
        if save_as and dest_dir:
            save_pending_download(repo, name, vision_for=vision_for)
        else:
            save_pending_download(repo, filename)
        job = {"repo": repo, "filename": filename, "status": "downloading",
               "done": 0, "total": 0, "result": None, "stop": _threading.Event(),
               "dest_dir": dest_dir, "save_as": save_as}
        _dl_jobs[name] = job
        _threading.Thread(target=_run_download_job, args=(job,), daemon=True,
                          name=f"download-{name}").start()
        return job


def model_download_state(filename: str) -> Optional[Dict[str, Any]]:
    job = _dl_jobs.get(filename.split("/")[-1])
    if not job:
        return None
    return {k: job[k] for k in ("repo", "filename", "status", "done", "total", "result")}


def pause_model_download(filename: str) -> bool:
    """Stop a running download and keep its partial file. A paused download is
    not resumed automatically at the next start."""
    name = filename.split("/")[-1]
    with _dl_lock:
        entries = _read_pending()
        if name in entries:
            entries[name]["paused"] = True
            _write_pending(entries)
        job = _dl_jobs.get(name)
        if job and job["status"] == "downloading":
            job["stop"].set()
            return True
    return False


def resume_interrupted_downloads() -> List[str]:
    """Continue every download that was cut off (app closed, crash, network drop)
    rather than paused, as long as its partial file is still there."""
    started = []
    for name, entry in _read_pending().items():
        if entry.get("paused") or not entry.get("repo"):
            continue
        if not (MODELS_DIR / (name + ".part")).is_file():
            clear_pending_download(name)
            continue
        start_model_download(entry["repo"], name)
        started.append(name)
    return started


try:
    import re as _re_mod
    _PROMPT_PROG_RE = _re_mod.compile(
        r"prompt processing,\s*n_tokens\s*=\s*(\d+),\s*progress\s*=\s*([\d.]+)")
except Exception:
    _PROMPT_PROG_RE = None


def prompt_progress():
    """Current cold-prompt processing progress from the built-in server log.
    llama.cpp emits 'progress = 0.88' lines while it chews a big prompt before
    the first token, so the UI can show 'Processing prompt 88%' instead of a
    frozen-looking spinner. Returns {'progress': 0..1, 'n_tokens': int} from the
    most recent line, or None if it's not our server / nothing processing. Best-
    effort, never raises; only the built-in server logs this."""
    try:
        if _PROMPT_PROG_RE is None or not (_proc and _proc.poll() is None):
            return None
        p = WORKSPACE / "logs" / "llamaserver.log"
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "replace")
        last = None
        for m in _PROMPT_PROG_RE.finditer(tail):
            last = m
        if not last:
            return None
        # If the prompt already finished (an 'eval time' line came after the last
        # progress line), the bar would be stale — hide it.
        if "eval time" in tail[last.end():]:
            return None
        return {"progress": float(last.group(2)), "n_tokens": int(last.group(1))}
    except Exception:
        return None


def clear_server_log() -> bool:
    """Empty llamaserver.log in place. The child holds the file open, so on
    Windows we can't rename/delete it — truncate through our own append-mode
    handle instead (next child write appends from 0). Falls back to a direct
    truncate when no child is running."""
    if _log_fh is not None:
        try:
            _log_fh.seek(0)
            _log_fh.truncate()
            _log_fh.flush()
            return True
        except Exception:
            pass
    try:
        p = WORKSPACE / "logs" / "llamaserver.log"
        if p.exists():
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
        return True
    except Exception:
        return False


def list_partial_downloads() -> List[Dict[str, Any]]:
    """Interrupted downloads (*.gguf.part) in MODELS_DIR, for the GUI resume UI.
    Attaches the saved repo (if known) so Resume re-requests it directly."""
    out: List[Dict[str, Any]] = []
    pend = _read_pending()
    try:
        for p in MODELS_DIR.glob("*.gguf.part"):
            try:
                fn = p.name[:-5]
                job = _dl_jobs.get(fn)
                out.append({"filename": fn,
                            "gb": round(p.stat().st_size / (1024 ** 3), 2),
                            "path": str(p),
                            "repo": (pend.get(fn) or {}).get("repo", ""),
                            "vision_for": (pend.get(fn) or {}).get("vision_for", ""),
                            "paused": bool((pend.get(fn) or {}).get("paused")),
                            "active": bool(job and job["status"] == "downloading")})
            except OSError:
                pass
    except Exception:
        pass
    return out


def discard_partial(filename: str) -> Dict[str, Any]:
    """Delete an interrupted model download. Pause and resume existed but there
    was no way to abandon one, so a mistaken multi-GB pull sat on disk with no
    way out of the UI. Confined to MODELS_DIR and to *.gguf.part."""
    name = os.path.basename((filename or "").strip())
    if not name:
        return {"ok": False, "error": "no filename"}
    if not name.endswith(".part"):
        name += ".part"
    started = _dl_jobs.get(name[:-5]) or {}
    base = Path(started["dest_dir"]) if started.get("dest_dir") else MODELS_DIR
    target = (base / name).resolve()
    try:
        # Never let a crafted name walk outside the folder its download was started for.
        target.relative_to(base.resolve())
    except ValueError:
        return {"ok": False, "error": "outside models dir"}
    if not target.name.endswith(".gguf.part") or not target.is_file():
        return {"ok": False, "error": "no such partial download"}
    job = _dl_jobs.get(target.name[:-5])
    if job and job["status"] == "downloading":
        job["stop"].set()
        for _ in range(50):            # let the writer close the file first
            if job["status"] != "downloading":
                break
            time.sleep(0.1)
    freed = 0
    try:
        freed = target.stat().st_size
        target.unlink()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    clear_pending_download(target.name[:-5])   # so it can't reappear
    return {"ok": True, "freed_gb": round(freed / (1024 ** 3), 2)}


def delete_model(path: str) -> Dict[str, Any]:
    """Delete a downloaded .gguf. Only files Hearth manages (MODELS_DIR) are
    removable — a model detected inside LM Studio's or Ollama's own store
    belongs to that app, and silently deleting it there would be hostile."""
    if not path:
        return {"ok": False, "error": "no path"}
    try:
        target = Path(path).resolve()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    try:
        target.relative_to(MODELS_DIR.resolve())
    except ValueError:
        return {"ok": False, "error":
                "This model lives outside Inglen's models folder (LM Studio / "
                "Ollama / HF cache). Remove it from that app instead."}
    if target.suffix.lower() != ".gguf" or not target.is_file():
        return {"ok": False, "error": "not a .gguf on disk"}
    if _proc is not None and _proc.poll() is None and \
            os.path.normcase(str(target)) == os.path.normcase(str(_proc_info.get("model_path") or "")):
        return {"ok": False, "error": "That model is loaded right now — eject it first."}
    try:
        freed = target.stat().st_size
        target.unlink()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "freed_gb": round(freed / (1024 ** 3), 2)}


def status(api_base: str = "") -> Dict[str, Any]:
    """Snapshot of the LLM-server situation, for the GUI Models panel.

    `external` = something at api_base that isn't our builtin.
    `builtin_running` = our subprocess is alive.
    `local_models` = downloaded GGUF files the user can boot.
    `picks` = curated download recommendations.
    `external_model_id` / `external_model_path` = what the external server
       (LM Studio / Ollama) has currently loaded, when discoverable. Lets
       the Models tab show "Eject" instead of "Use this" on the row that is
       already serving — the user's reported bug where LM Studio's loaded
       model didn't reflect in the Models tab.

    Result is cached for ~3s so a burst of GUI callers shares one probe.
    Forced fresh when builtin state changes (start / stop / swap).
    """
    api_base = api_base or _local_base()
    builtin = _proc is not None and _proc.poll() is None
    # Cache key includes builtin-running so a load/eject invalidates instantly.
    cache_key = (api_base, builtin)
    now = time.time()
    if (_status_cache["data"] is not None
            and _status_cache["key"] == cache_key
            and now - _status_cache["ts"] < _STATUS_TTL_SECONDS):
        return _status_cache["data"]
    # Detect cloud vs local endpoint so we can skip LM-Studio-specific probes
    # when the user is on Grok/Gemini/OpenAI. The /api/v0/models probe is
    # pointless against cloud (returns 404 after a 2s timeout each call). The
    # disk scan is also skipped on cloud → snappy startup when llm_provider
    # is xai/anthropic/google/openai/openrouter.
    try:
        from urllib.parse import urlparse
        _host = (urlparse(api_base).hostname or "").lower()
        _is_local = _host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
    except Exception:
        _is_local = True
    _key = os.environ.get("LOCAL_API_KEY") or ""
    _responding = external_server_running(api_base, timeout=0.5,
                                          api_key=_key or BUILTIN_API_KEY)
    # A built-in server the OTHER surface started is still ours. Without this
    # the CLI's server looks foreign to the GUI (and vice versa), so the UI
    # falls through to its LM-Studio probes and starts naming a program that
    # may not be installed. `_binfo` is whichever config describes it.
    _binfo = _proc_info if builtin else {}
    if _responding and not builtin:
        adopted = read_runfile(verify=False)
        if adopted and _is_local:
            builtin, _binfo = True, adopted
    ext = _responding and not builtin
    ext_id: Optional[str] = None
    ext_path: Optional[str] = None
    _hdr = {"Authorization": f"Bearer {_key}"} if _key else {}
    if ext and _is_local:
        # Try LM Studio's v0/models first (gives us a real path field).
        # Same response is needed by _list_models elsewhere — we share via
        # the status cache so neither caller hits LM Studio twice.
        try:
            v0 = api_base.replace("/v1", "/api/v0") + "/models"
            req = urllib.request.Request(v0, headers=_hdr)
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            for m in (data.get("data") or []):
                if m.get("state") == "loaded":
                    ext_id = m.get("id")
                    ext_path = m.get("path") or m.get("modelPath") or ""
                    break
        except Exception:
            pass
        # Fall back to the generic /v1/models — id only, no path. Skip if
        # v0 already gave us an id; LM Studio also exposes /v1/models so we'd
        # be hitting the same server twice for no extra info.
        if not ext_id:
            try:
                req = urllib.request.Request(api_base + "/models", headers=_hdr)
                with urllib.request.urlopen(req, timeout=2) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
                items = data.get("data") or []
                if items:
                    ext_id = items[0].get("id")
            except Exception:
                pass
    # Cloud-aware disk scan: do a full scan when on local endpoints OR
    # when the cache is cold. On subsequent cloud polls, reuse the cached
    # result so the Models tab keeps showing what's on disk without
    # paying multi-second filesystem scans on every status poll.
    # Cache the disk scan itself — it walks several directories and costs
    # multiple seconds. Reuse it for up to 60s even while local, instead of
    # re-walking the disk on every status rebuild; that constant rescan was the
    # startup lag and the Models-tab churn. A fresh scan still runs on the first
    # call, once the 60s lapses, or on an explicit force_local_rescan().
    _disk_fresh = (time.time() - _status_cache.get("disk_scan_ts", 0)) < 60
    if _status_cache.get("disk_models_cache") is None or (_is_local and not _disk_fresh):
        _disk = scan_disk_for_models()
        _local = list_local_models()
        _status_cache["disk_models_cache"] = _disk
        _status_cache["local_models_cache"] = _local
        _status_cache["disk_scan_ts"] = time.time()
    else:
        _disk = _status_cache.get("disk_models_cache") or []
        _local = _status_cache.get("local_models_cache") or []
    _is_scanning = bool(_status_cache.get("rescan_in_progress"))
    result = {
        "llama_cpp_installed": llama_cpp_available(),
        "external_running": ext,
        "external_model_id": ext_id,
        "external_model_path": ext_path,
        "builtin_running": builtin,
        # _binfo is _proc_info when we own the process, or the run-file when
        # another Hearth surface booted it, so these read the same either way.
        "builtin_pid": (_proc.pid if (_proc and _proc.poll() is None)
                        else (_binfo.get("pid") if builtin else None)),
        "builtin_url": _binfo.get("url") if builtin else None,
        "builtin_model": _binfo.get("model_path") if builtin else None,
        "builtin_gpu_layers": _binfo.get("gpu_layers_on") if builtin else None,
        "builtin_total_layers": _binfo.get("total_layers") if builtin else None,
        "builtin_engine": _binfo.get("engine") if builtin else None,
        "builtin_slots": _binfo.get("slots") if builtin else None,
        "builtin_owned": bool(_proc and _proc.poll() is None),
        "disk_models": _disk,
        "partial_downloads": list_partial_downloads(),
        "helper": helper_status(),
        "is_lite": _is_lite_edition(),
        "full_url": FULL_DOWNLOAD_URL,
        "local_models": _local,
        "remote_endpoint": not _is_local,
        "scanning": _is_scanning,
        "vram_gb": detect_gpu_vram_gb(),
        "ram_gb": detect_ram_gb(),
        "models_dir": str(MODELS_DIR),
        # Per-model saved load-config (keyed by normalized path) so the GUI
        # can pre-fill the load-config UI with what the user picked last time.
        "model_configs": load_model_configs(),
    }
    # Land the result in the cache and return it. The cache is invalidated
    # implicitly on builtin start/stop because the cache_key includes the
    # `builtin` flag — so swap → instant fresh status, not a stale 8s window.
    # IMPORTANT: stamp ts with the END-OF-FUNCTION time, not the start. When
    # no external server is up, `external_server_running` blocks for ~4s on
    # TCP timeout; using the start time made the first cached entry already
    # "8 seconds old" the moment it was written, so the very next call
    # treated it as stale and re-probed. Cost the user 8s per refresh tick.
    _status_cache["data"] = result
    _status_cache["ts"] = time.time()
    _status_cache["key"] = cache_key
    return result


def force_local_rescan() -> Dict[str, Any]:
    """Invalidate the disk-model cache and kick a fresh scan on a background
    thread. Used by the GUI when the user switches their brain from cloud to
    local: the cached disk_models from the last local poll may be stale, and
    silently re-scanning makes the user think the app froze. Now we return
    immediately, the scan runs in the background, and status() reports
    `scanning: true` until it finishes."""
    import threading

    def _run():
        try:
            _status_cache["disk_models_cache"] = scan_disk_for_models()
            _status_cache["local_models_cache"] = list_local_models()
        finally:
            _status_cache["rescan_in_progress"] = False
            _status_cache["data"] = None  # next status() does a fresh build

    if _status_cache.get("rescan_in_progress"):
        return {"ok": True, "scanning": True, "note": "already in progress"}
    _status_cache["rescan_in_progress"] = True
    t = threading.Thread(target=_run, daemon=True,
                          name="hearth-local-rescan")
    t.start()
    return {"ok": True, "scanning": True}


# ---------------------------------------------------------------------------
# Helper model: a second, smaller model on its own port, so helpers and
# sub-agents have a model of their own while the main one keeps the chat.
# Two servers rather than one server juggling two models keeps the main
# model's load path exactly as it is.
# ---------------------------------------------------------------------------
HELPER_PORT = _helper_port()
_helper: Dict[str, Any] = {"proc": None, "info": {}}


def helper_base() -> str:
    return f"http://{BUILTIN_HOST}:{HELPER_PORT}/v1"


def helper_status() -> Dict[str, Any]:
    p = _helper["proc"]
    running = p is not None and p.poll() is None
    info = dict(_helper["info"]) if running else {}
    return {"running": running, "url": helper_base() if running else "",
            "model_path": info.get("model_path", ""), "model": info.get("model", ""),
            "ctx": info.get("ctx", 0)}


def stop_helper() -> Dict[str, Any]:
    p = _helper["proc"]
    was = p is not None and p.poll() is None
    if was:
        try:
            p.terminate()
            try:
                p.wait(timeout=6)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=3)
        except Exception:
            pass
    _helper["proc"], _helper["info"] = None, {}
    return {"ok": True, "was_running": was}


def start_helper(model_path: str, ctx: int = 16384, n_gpu_layers: int = -1,
                 slots: int = 2, wait_s: float = 120.0) -> Dict[str, Any]:
    """Start the helper model on HELPER_PORT. Needs the llama.cpp engine.
    `ctx` is per helper; `slots` helpers can run on it at once."""
    if not model_path or not Path(model_path).is_file():
        return {"ok": False, "error": f"model file not found: {model_path}"}
    # The main model is this file already: a second server would load a second
    # copy of the same weights. Helpers use the main server's slots instead.
    main = (dict(_proc_info or {}) if _proc is not None and _proc.poll() is None else {}) or read_runfile()
    if main and same_file(main.get("model_path") or "", model_path):
        stop_helper()
        n = int(main.get("slots") or 1)
        return {"ok": True, "shared": True, "url": main.get("url") or "", "model": Path(model_path).stem,
                "note": ("That model is already loaded as the main brain, so helpers use it there "
                         + (f"({n - 1} at once beside the chat) " if n > 1 else
                            "(one at a time with the chat; raise Settings, server_parallel_slots to overlap them) ")
                         + "instead of loading a second copy.")}
    native = engine_for(model_path) or find_native_llama_server()
    if not native:
        return {"ok": False, "error": "a helper model needs the llama.cpp engine; install it in Models > Engine"}
    stop_helper()
    blackwell = (detect_gpu_compute_cap() or 0.0) >= 12.0
    cmd = [native["exe"], "--model", model_path, "--host", BUILTIN_HOST, "--port", str(HELPER_PORT),
           "--ctx-size", str(int(ctx) * max(1, slots)), "--parallel", str(max(1, slots)),
           "--n-gpu-layers", str(999 if n_gpu_layers == -1 else n_gpu_layers),
           "--api-key", BUILTIN_API_KEY, "--batch-size", "1024", "--ubatch-size", "512"]
    cmd += _shared_kv_args(native["exe"], max(1, slots), [])
    # Same KV choice as the main model: q8_0 with flash attention, except on
    # RTX 50-series where this engine's flash kernels misbehave on long context.
    cmd += (["--flash-attn", "off"] if blackwell else
            ["--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"])
    env = dict(os.environ)
    env = _with_libs(env, native.get("dll_dirs") or [])
    logs_dir = WORKSPACE / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "llama_helper.log"
    try:
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                                creationflags=_NO_WINDOW, env=env)
    except OSError as e:
        return {"ok": False, "error": f"could not start the helper model: {e}"}
    try:
        _assign_to_job(proc)
    except Exception:
        pass
    _helper["proc"] = proc
    _helper["info"] = {"model_path": model_path, "model": Path(model_path).stem, "ctx": ctx,
                       "slots": max(1, slots), "kv_unified": "--kv-unified" in cmd}
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if proc.poll() is not None:
            _helper["proc"], _helper["info"] = None, {}
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
            except OSError:
                pass
            hint = " (out of VRAM: try a smaller helper or fewer GPU layers)" if "out of memory" in tail.lower() else ""
            return {"ok": False, "error": f"the helper model stopped while loading{hint}", "log_tail": tail}
        if external_server_running(helper_base(), timeout=0.5, api_key=BUILTIN_API_KEY):
            return {"ok": True, "url": helper_base(), "model": _helper["info"]["model"]}
        time.sleep(0.5)
    stop_helper()
    return {"ok": False, "error": "the helper model did not start within two minutes"}


def _atexit():
    """Best-effort cleanup so we don't leave a llama.cpp server orphaned."""
    if _proc is not None and _proc.poll() is None:
        try:
            stop_builtin()
        except Exception:
            pass
    try:
        stop_helper()
    except Exception:
        pass


import atexit as _atexit_module
_atexit_module.register(_atexit)
