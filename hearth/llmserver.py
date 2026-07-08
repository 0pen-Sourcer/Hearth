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
    3. download_model() pulls the GGUF from Hugging Face into ~/Jarvis/models/.
    4. start_builtin(path) spawns the server on 1234. Hearth talks to it normally.
    5. stop_builtin() shuts it down at exit (and on /api/llmserver/stop).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", Path.home() / "Jarvis"))
MODELS_DIR = WORKSPACE / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

FULL_DOWNLOAD_URL = "https://github.com/0pen-sourcer/hearth/releases"


def _is_lite_edition() -> bool:
    """True on a packaged Lite build (no bundled server). Source = Full."""
    try:
        from . import edition
        return edition.is_lite()
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

BUILTIN_PORT = int(os.environ.get("JARVIS_BUILTIN_PORT", "1234"))
BUILTIN_HOST = "127.0.0.1"

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

# Curated picks - we test these and pin the exact HF paths. Tuned for the
# common case (one consumer GPU). The fields match what the GUI needs to render
# a clean "pick a model" panel.
TOP_PICKS: List[Dict[str, Any]] = [
    {
        "id": "qwen2.5-3b-instruct-q4_k_m",
        "name": "Qwen 2.5 3B Instruct (Q4_K_M)",
        "size_gb": 1.9,
        "vram_min_gb": 3,
        "context": 32768,
        "hf_repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "hf_file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "description": "Tiny daily-driver. Fits 4 GB GPUs and CPU-only rigs without breaking a sweat.",
        "tags": ["tiny", "tools", "low-vram"],
    },
    {
        "id": "qwen2.5-7b-instruct-q4_k_m",
        "name": "Qwen 2.5 7B Instruct (Q4_K_M)",
        "size_gb": 4.7,
        "vram_min_gb": 6,
        "context": 32768,
        "hf_repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "hf_file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "description": "Best balance of speed + tool use. Solid daily-driver default on 6-8 GB cards.",
        "tags": ["recommended", "tools"],
    },
    {
        "id": "harmonic-hermes-9b-q4_k_m",
        "name": "Harmonic Hermes 9B (Q4_K_M)",
        "size_gb": 5.3,
        "vram_min_gb": 8,
        "context": 32768,
        "hf_repo": "DJLougen/Harmonic-Hermes-9B-GGUF",
        "hf_file": "Qwen3.5-9B-Harmonic.Q4_K_M.gguf",
        "description": "Qwen3.5 base + Hermes finetune. Strong reasoning + tool-use. Fits 10 GB without spilling.",
        "tags": ["recommended", "reasoning", "tools"],
    },
    {
        "id": "gemma-4-e4b-it-q4_k_m",
        "name": "Gemma 4 E4B Instruct (Q4_K_M)",
        "size_gb": 5.3,
        "vram_min_gb": 8,
        "context": 8192,
        "hf_repo": "lmstudio-community/gemma-4-E4B-it-GGUF",
        "hf_file": "gemma-4-E4B-it-Q4_K_M.gguf",
        "description": "Google Gemma 4 — multimodal vision-capable, sharp reasoning. Comfortable on 8 GB.",
        "tags": ["smart", "vision"],
    },
    {
        "id": "qwen2.5-14b-instruct-q4_k_m",
        "name": "Qwen 2.5 14B Instruct (Q4_K_M)",
        "size_gb": 8.5,
        "vram_min_gb": 12,
        "context": 32768,
        "hf_repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "hf_file": "qwen2.5-14b-instruct-q4_k_m.gguf",
        "description": "Step up in reasoning quality. Lands cleanly on 12-16 GB cards.",
        "tags": ["smart", "tools", "mid-vram"],
    },
    {
        "id": "qwen2.5-32b-instruct-q4_k_m",
        "name": "Qwen 2.5 32B Instruct (Q4_K_M)",
        "size_gb": 18.5,
        "vram_min_gb": 24,
        "context": 32768,
        "hf_repo": "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "hf_file": "qwen2.5-32b-instruct-q4_k_m.gguf",
        "description": "Top-tier local reasoning. For 24 GB+ cards (RTX 3090 / 4090 / 5090 / pro).",
        "tags": ["heavy", "reasoning", "high-vram"],
    },
]


def detect_gpu_vram_gb() -> Optional[float]:
    """Best-effort: return total VRAM in GB on the primary NVIDIA GPU, or None
    if nvidia-smi isn't available / there's no GPU. Used to pick the right
    model size for this rig (4-6 GB -> small, 6-8 GB -> default, 8+ -> can run
    the smart-but-heavy options)."""
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


def detect_gpu_vram_free_gb() -> Optional[float]:
    """Return FREE VRAM in GB right now. Different from detect_gpu_vram_gb()
    (which returns total). Used by start_builtin pre-flight to catch the
    'LM Studio is already using 5 GB of your 8 GB' scenario before we boot
    another 5+ GB model on top and freeze the whole PC."""
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


def recommend_pick_for_this_pc() -> Dict[str, Any]:
    """Return the pick that's the best fit for this machine's VRAM/RAM.
    Returns the pick dict with an extra 'reason' string explaining the choice.

    Logic:
    - 8 GB+ VRAM: Harmonic Hermes 9B (Qwen3.5 base + Hermes finetune — best
      tool-use + reasoning combo on an 8GB rig).
    - 6-8 GB VRAM: Qwen 2.5 7B still fits at Q4_K_M (4.7 GB) and is faster.
    - <6 GB VRAM / CPU-only: Qwen 2.5 7B too — it'll spill to CPU but still
      gives the best tool-use of the curated picks. (Hermes-3-3B was a
      smaller option but was removed from the picks list — Qwen 2.5 is the
      better universal default.)
    """
    vram = detect_gpu_vram_gb()
    ram = detect_ram_gb()

    if vram is not None and vram >= 24:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-32b-instruct-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM detected — Qwen 2.5 32B Q4 fits with room for context."
    elif vram is not None and vram >= 12:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-14b-instruct-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM detected — Qwen 2.5 14B is the sweet spot at this tier."
    elif vram is not None and vram >= 8:
        pick = next(p for p in TOP_PICKS if p["id"] == "harmonic-hermes-9b-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM detected — Harmonic Hermes 9B is the sweet spot (reasoning + tool use)."
    elif vram is not None and vram >= 6:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-7b-instruct-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM detected — Qwen 7B Q4 fits and gives the best tool-use at this tier."
    elif vram is not None and vram >= 3.5:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-3b-instruct-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM detected — Qwen 2.5 3B fits cleanly without spillover."
    elif vram is not None:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-3b-instruct-q4_k_m")
        reason = f"NVIDIA GPU with {vram:g} GB VRAM — Qwen 2.5 3B will partial-offload to CPU but still runs."
    elif ram is not None and ram >= 8:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-7b-instruct-q4_k_m")
        reason = f"No NVIDIA GPU; {ram:g} GB RAM — Qwen 2.5 7B can run on CPU (slow but works)."
    else:
        pick = next(p for p in TOP_PICKS if p["id"] == "qwen2.5-3b-instruct-q4_k_m")
        reason = "Couldn't probe GPU/RAM — defaulting to Qwen 2.5 3B (fits anywhere)."

    out = dict(pick)
    out["recommended_for_this_pc"] = True
    out["recommendation_reason"] = reason
    return out


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


def detect_running_server(default_api_base: str = "http://localhost:1234/v1") -> Optional[str]:
    """Probe the configured endpoint + well-known local LLM ports. Return the
    first one that answers, or None if nothing is running.

    Lets Hearth transparently use whatever the user already has up - LM Studio,
    Ollama, llama.cpp server - without forcing them to set LOCAL_API_BASE.
    """
    if external_server_running(default_api_base):
        return default_api_base
    for _label, url in KNOWN_LOCAL_SERVERS:
        if url == default_api_base:
            continue  # already tried as default
        if external_server_running(url):
            return url
    return None


def list_local_models() -> List[Dict[str, Any]]:
    """List GGUF files in ~/Jarvis/models. Surfaced in the GUI Models panel."""
    out = []
    if not MODELS_DIR.is_dir():
        return out
    for p in sorted(MODELS_DIR.glob("*.gguf")):
        try:
            out.append({
                "filename": p.name,
                "path": str(p),
                "size_gb": round(p.stat().st_size / (1024 ** 3), 2),
                "source": "Hearth",
            })
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
    if "/jarvis/" in s.replace("\\", "/"):   return "Hearth"
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
                out.append({
                    "filename": p.name,
                    "path": sp,
                    "size_gb": size_gb,
                    "source": _source_label(p),
                })
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
    return out


def _download_with_resume(url, dest, tmp,
                          on_progress: Optional[Callable[[int, int], None]] = None,
                          ua: str = "Hearth/0.7 (HF-download)") -> int:
    """Stream `url` into `tmp`, RESUMING from an existing .part via an HTTP Range
    request so a closed/interrupted download continues instead of restarting
    from zero (matters a lot on slow/metered links). Atomic rename to `dest` on
    completion. The caller LEAVES the .part on failure so the next attempt
    resumes. Returns total bytes written."""
    resume_from = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": ua}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        status = getattr(r, "status", 200) or 200
        clen = int(r.headers.get("Content-Length") or 0)
        if resume_from and status == 206:          # server honored the range
            total, mode, done = resume_from + clen, "ab", resume_from
        else:                                       # 200 / no range -> clean restart
            total, mode, done = clen, "wb", 0
        with open(tmp, mode) as f:
            while True:
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
    tmp.rename(dest)
    return done


def download_model(pick_id: str,
                   on_progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Download a curated pick from Hugging Face into ~/Jarvis/models/.
    `on_progress(done_bytes, total_bytes)` is called every ~256KB. Returns
    {ok, path} on success or {ok: False, error: ...}. Uses a .part file so
    interrupted downloads never look complete.

    Disk-first: before downloading, scan every known location (LM Studio's
    cache, Ollama, HF cache, ~/Jarvis/models) for a file matching the pick's
    hf_file. If found, return that path with `already: True` and source so
    the caller can tell the user "using your LM Studio copy" instead of
    silently burning 5+ GB of disk and bandwidth on a duplicate."""
    pick = next((p for p in TOP_PICKS if p["id"] == pick_id), None)
    if not pick:
        return {"ok": False, "error": f"unknown pick id: {pick_id!r}"}

    # Disk-first short-circuit: does this exact filename already live on disk
    # somewhere Hearth knows how to find it? LM Studio users routinely have
    # the same GGUF cached under .lmstudio/models/<author>/<repo>/ and there's
    # no reason to redownload it into ~/Jarvis/models/.
    target_name = pick["hf_file"].lower()
    try:
        for existing in scan_disk_for_models():
            fn = (existing.get("filename") or "").lower()
            if fn == target_name:
                return {
                    "ok": True,
                    "path": existing["path"],
                    "already": True,
                    "source": existing.get("source", "disk"),
                }
    except Exception:
        # scan failure should NEVER block the user from downloading — fall
        # through to the normal HF path.
        pass

    url = f"https://huggingface.co/{pick['hf_repo']}/resolve/main/{pick['hf_file']}"
    dest = MODELS_DIR / pick["hf_file"]
    if dest.exists():
        return {"ok": True, "path": str(dest), "already": True, "source": "Hearth"}

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        done = _download_with_resume(url, dest, tmp, on_progress)
        return {"ok": True, "path": str(dest), "bytes": done}
    except Exception as e:
        # Keep the .part so the next attempt RESUMES instead of restarting.
        return {"ok": False, "error": f"download failed: {type(e).__name__}: {e}"}


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


def _phase_for_line(line: str) -> Optional[Tuple[str, int]]:
    """Map a llama_cpp.server stdout line to (phase, percent). Returns None
    when the line isn't a known phase marker.

    Percent values are calibrated to a typical 8-25s load on this build:
    spawn → ~5%, weights → 10-50% (file read is the long bit), backend init →
    50-70%, KV reserve → 70-85%, HTTP startup → 85-99%, ready → 100%.
    Within a phase we stay flat so the bar never goes backwards.
    """
    l = line
    # Roughly in the order llama.cpp prints these:
    if "llama_model_load:" in l or "llama_model_loader: loaded meta data" in l:
        return ("loading_weights", 15)
    if "llama_model_loader: - kv" in l or "Model metadata:" in l:
        return ("loading_weights", 25)
    if "load_tensors:" in l:
        return ("loading_weights", 45)
    if "llm_load_print_meta:" in l or "print_info:" in l:
        return ("initializing_backend", 55)
    if "CUDA : " in l or "ggml_cuda_init:" in l:
        return ("initializing_backend", 65)
    if "llama_kv_cache" in l or "kv self size" in l:
        return ("reserving_kv", 75)
    if "sched_reserve:" in l or "graph_reserve:" in l:
        return ("reserving_kv", 82)
    if "Started server process" in l or "Waiting for application startup" in l:
        return ("starting_http", 92)
    if "Application startup complete" in l:
        return ("starting_http", 97)
    if "Uvicorn running on" in l:
        return ("ready", 100)
    return None


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
        candidates += sorted(hearth_rt.glob("*/llama-server.exe"),
                             key=lambda p: p.parent.name, reverse=True)
    # 2) Reuse LM Studio's CUDA-12 backend if the user has it (zero download)
    lms = Path(os.path.expanduser("~/.lmstudio/extensions/backends"))
    if lms.is_dir():
        candidates += sorted(lms.glob("*nvidia-cuda12*/llama-server.exe"),
                             key=lambda p: p.parent.name, reverse=True)
    for exe in candidates:
        try:
            # Skip a missing exe OR an AV-quarantined stub. AVs flag the unsigned
            # official llama-server.exe as IDP.generic and truncate it (seen: 9 KB).
            # llama-server.exe is a thin launcher — the real one is ~20 KB+ (heavy
            # code lives in the DLLs) — so a 15 KB floor drops the stub while
            # keeping real builds, and we fall back to a signed copy (LM Studio's)
            # that AV trusts.
            if not exe.is_file() or exe.stat().st_size < 15_000:
                continue
        except OSError:
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


def llama_runtime_info() -> Dict[str, Any]:
    """What runtime is Hearth using for the builtin server, and where from.
    Powers the CLI/GUI 'update' UI."""
    n = find_native_llama_server()
    exe = (n or {}).get("exe") or ""
    managed = Path(os.path.expanduser("~/.hearth/llamacpp"))
    have_managed = managed.is_dir() and any(managed.glob("*/llama-server.exe"))
    if not n:
        source = "wheel"      # falling back to the bundled llama-cpp-python
    elif ".hearth" in exe:
        source = "hearth"     # Hearth's own downloaded runtime (self-sufficient)
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
    }


def download_llama_runtime(cuda: str = "12.4", tag: Optional[str] = None,
                           on_progress: Optional[Callable[[int, int], None]] = None
                           ) -> Dict[str, Any]:
    """Download the official ggml-org llama.cpp Windows CUDA build (llama-server
    .exe + ggml DLLs) and its CUDA runtime into ~/.hearth/llamacpp/<tag>/. This
    is what lets Hearth's builtin run on any GPU without bundling a giant wheel
    and without depending on LM Studio — the 'updatable runtime'. Also serves as
    the Lite->Full upgrade (pull the runtime, not the whole app).
    Returns {ok, dir, tag} or {ok: False, error}."""
    import urllib.request
    import zipfile
    import tempfile
    try:
        api = (f"https://api.github.com/repos/{_LLAMA_CPP_REPO}/releases/"
               + (f"tags/{tag}" if tag else "latest"))
        req = urllib.request.Request(api, headers={"User-Agent": "Hearth",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
    except Exception as e:
        return {"ok": False, "error": f"couldn't reach GitHub releases: {e}"}
    tagname = rel.get("tag_name") or tag or "latest"
    assets = rel.get("assets") or []

    # NB: both "llama-<tag>-bin-win-cuda-X-x64.zip" (the binaries) and
    # "cudart-llama-bin-win-cuda-X-x64.zip" (the CUDA runtime) contain the
    # substring "bin-win-cuda-X-x64.zip" — so the binaries matcher must exclude
    # "cudart" or it grabs the runtime zip twice and misses llama-server.exe.
    _tail = f"bin-win-cuda-{cuda}-x64.zip"
    bins = next((a for a in assets
                 if _tail in a.get("name", "") and "cudart" not in a.get("name", "")), None)
    runtime = next((a for a in assets
                    if f"cudart-llama-{_tail}" in a.get("name", "")), None)
    if not bins or not runtime:
        return {"ok": False, "error": f"no cuda-{cuda} Windows assets in release {tagname}"}

    dest = Path(os.path.expanduser(f"~/.hearth/llamacpp/{tagname}"))
    dest.mkdir(parents=True, exist_ok=True)
    total = int(bins.get("size", 0)) + int(runtime.get("size", 0))
    done = [0]

    def _fetch(asset) -> None:
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            u = urllib.request.Request(asset["browser_download_url"],
                                       headers={"User-Agent": "Hearth"})
            with urllib.request.urlopen(u, timeout=60) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done[0] += len(chunk)
                    if on_progress:
                        try:
                            on_progress(done[0], total)
                        except Exception:
                            pass
            with zipfile.ZipFile(tmp) as z:
                z.extractall(dest)   # exe + ggml DLLs + cudart all land together
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        _fetch(bins)
        _fetch(runtime)
    except Exception as e:
        return {"ok": False, "error": f"download/extract failed: {type(e).__name__}: {e}"}
    reset_native_cache()
    exe = next(iter(dest.rglob("llama-server.exe")), None)
    if not exe:
        return {"ok": False, "error": "llama-server.exe not found after extract"}
    return {"ok": True, "dir": str(dest), "tag": tagname}


def start_builtin(model_path: str, port: Optional[int] = None,
                  ctx: int = 24576, n_gpu_layers: int = -1,
                  n_threads: Optional[int] = None,
                  cache_type_k: Optional[str] = None,
                  cache_type_v: Optional[str] = None,
                  flash_attn: bool = True,
                  force: bool = False) -> Dict[str, Any]:  # noqa: ARG001 — kept for caller compat; load no longer refuses on VRAM
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

    if not llama_cpp_available():
        return {"ok": False, "error":
                "llama-cpp-python is not installed. Run: pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"}

    # Preflight: the prebuilt wheel doesn't include server deps.
    missing = server_extras_missing()
    if missing:
        return {"ok": False, "error":
                f"llama_cpp.server can't start — '{missing}' is missing. "
                "Run: pip install fastapi 'uvicorn[standard]' sse-starlette pydantic-settings starlette-context"}

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
            if os.path.normcase(os.path.abspath(existing_path)) == normalized_path:
                return {"ok": True, "url": _proc_info.get("url"), "pid": _proc.pid, "already": True}
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
    _native = find_native_llama_server()
    _use_native = _native is not None
    if n_gpu_layers != 0:
        # The native llama-server has working Blackwell kernels → keep the
        # q8_0 + flash VRAM saver (fits a 9B on 8 GB, like LM Studio). Only the
        # old llama-cpp-python wheel needs the f16 + flash-off workaround on
        # Blackwell, where its flash kernel returns empty on long context.
        _needs_bw_workaround = _blackwell and not _use_native
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
            _reserve = 1.0 if _use_native else 2.3
            if n_gpu_layers == -1 and free_vram is not None:
                est = estimate_safe_gpu_layers(sz, free_vram, ctx,
                                               layer_count=layers, cache_type=cache_type_k,
                                               reserve_gb=_reserve)
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
                        f"Tight VRAM (~{free_vram:.1f} GB free): offloading {est}{_tot} "
                        f"layers to GPU, the rest to CPU — loads fine, a bit slower. "
                        f"Close other GPU apps or lower context for full-GPU speed."
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
                    vram_warning = (
                        f"Heads up: {n_gpu_layers} GPU layers likely won't fit "
                        f"~{free_vram:.1f} GB free VRAM — the driver will spill to "
                        f"shared RAM, which is slow and can return empty output on "
                        f"some GPUs. Auto (or ~{max(fits,0)} layers) is the safe pick."
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
        with _start_lock:
            _starting_paths.discard(normalized_path)
        return {
            "ok": False,
            "error": (
                f"Port {port} is already taken by another LLM server (most likely "
                f"LM Studio or Ollama). Two ways out:\n"
                f"  • Use what's already there → CLI: /brain lmstudio  ·  GUI: Settings → Chat brain → Local LM Studio\n"
                f"  • Stop the other server, then retry  →  Hearth's built-in will boot on port {port}."
            ),
            "conflict_port": port,
        }

    # Auto-pick CPU thread counts when caller didn't specify. LM Studio uses
    # all logical cores by default for batch + worker counts; previously we
    # only passed --n_threads when explicitly set, leaving llama.cpp to fall
    # back to a conservative min(4, hw_concurrency). On the user's 16-thread
    # Ryzen, that's a 4x prefill slowdown vs LM Studio. Audit finding.
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
            "--ctx-size", str(ctx),
            "--n-gpu-layers", str(999 if n_gpu_layers == -1 else n_gpu_layers),
            "--api-key", "hearth-builtin",
            "--batch-size", "2048",
            "--ubatch-size", "512",
            "--threads", str(effective_threads),
            "--flash-attn", ("on" if _flash else "off"),
        ]
        if cache_type_k:
            cmd += ["--cache-type-k", str(cache_type_k)]
        if cache_type_v:
            cmd += ["--cache-type-v", str(cache_type_v)]
    else:
        # PyInstaller: sys.executable is Hearth.exe (tray), so route via the
        # --hearth-run-llama-server sentinel; from source it's python → -m.
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
            "--api_key", "hearth-builtin",
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
        _env = dict(os.environ)
        if _use_native and _native.get("dll_dirs"):
            # ggml-cuda.dll + the CUDA runtime (cudart/cublas) live next to the
            # native exe / in its vendor dir — put them on PATH so it loads.
            _env["PATH"] = os.pathsep.join(_native["dll_dirs"]) + os.pathsep + _env.get("PATH", "")
        spawned = subprocess.Popen(
            cmd, stdout=log_fh, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW, env=_env,
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
    last_marker = "spawned"
    log_pos = 0  # byte offset we've already scanned for phase markers
    while time.time() < deadline:
        # If a concurrent call replaced the global _proc with a different
        # subprocess, our work is moot — abandon ship cleanly.
        if _proc is None or _proc is not spawned:
            try: spawned.terminate()
            except Exception: pass
            with _start_lock: _starting_paths.discard(normalized_path)
            _set_progress(phase="error", error="superseded by a newer model load",
                          finished_at=time.time())
            return {"ok": False, "error": "superseded by a newer model load",
                    "log_path": str(log_path)}
        if spawned.poll() is not None:
            tail = _log_tail()
            hint = ""
            if "ModuleNotFoundError" in tail:
                hint = " — missing server extras; run: pip install fastapi 'uvicorn[standard]' sse-starlette pydantic-settings starlette-context"
            elif "out of memory" in tail.lower() or "CUDA error" in tail:
                hint = " — out of VRAM; try a smaller model or lower n_ctx"
            elif "could not load model" in tail.lower():
                hint = " — model file unreadable or wrong format"
            elif "could not find module" in tail.lower():
                hint = " — CUDA runtime DLLs missing; re-run install.ps1 -BuiltinLLM cuda"
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
                        last_marker = ln.strip()[:120]
                        _set_progress(phase=ph, percent=pct, marker=last_marker)
        except OSError:
            pass
        # Probe with the API key — builtin 401s unauthenticated requests, which
        # would make external_server_running return False even though the server
        # is fully up. external_server_running handles 401 as "alive" too.
        # Lowered timeout to 0.5s: when the server isn't ready yet, the TCP
        # connect either succeeds quickly (port bound, uvicorn not accepting
        # yet — slow socket close) or fails fast. 0.5s is long enough for the
        # response when the server IS ready and short enough to keep cadence.
        if external_server_running(api_base, timeout=0.5, api_key="hearth-builtin"):
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

    _proc_info = {"url": api_base, "model_path": model_path, "port": port, "ctx": ctx}
    # Remember the user's tuned config so the next "Use this" on the same
    # .gguf restores GPU offload / ctx / KV cache without them setting it again.
    save_model_config(model_path, {
        "ctx": ctx,
        "n_gpu_layers": n_gpu_layers,
        "n_threads": n_threads,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "flash_attn": flash_attn,
    })
    # Release the path-level lock — this load is fully complete.
    with _start_lock: _starting_paths.discard(normalized_path)
    result = {"ok": True, "url": api_base, "pid": _proc.pid, "info": _proc_info}
    if vram_warning:
        result["warning"] = vram_warning
    return result


def stop_builtin() -> Dict[str, Any]:
    """Terminate the running builtin server, if any."""
    global _proc, _proc_info
    if _proc is None or _proc.poll() is not None:
        _proc = None
        _proc_info = {}
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
    """Search HF for GGUF models matching the query - lets users browse beyond
    the 3 curated picks without leaving Hearth. Filters to GGUF repos so the
    list is restricted to things llama.cpp can actually load. Public API,
    no auth needed."""
    if not query or not query.strip():
        return []
    url = ("https://huggingface.co/api/models?"
           f"search={urllib.parse.quote(query.strip() + ' gguf')}"
           f"&filter=gguf&sort=downloads&direction=-1&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hearth/0.7"})
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
        })
    return out


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
# 2.29 GB is what we leave on the table by default. Tweakable.
_VRAM_DRIVER_RESERVE_GB = 2.29


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


def estimate_safe_gpu_layers(model_size_gb: Optional[float],
                             free_vram_gb: Optional[float],
                             ctx: int = 24576,
                             layer_count: Optional[int] = None,
                             cache_type: Optional[str] = None,
                             reserve_gb: float = 2.3) -> int:
    """How many transformer layers to offload so the GPU allocation stays UNDER
    free VRAM. Returns -1 (offload all — it fits comfortably), 0 (CPU-only —
    no VRAM budget), or a positive count (partial offload; the rest run on CPU).

    `reserve_gb` is headroom kept free for the CUDA context + n_batch compute
    buffer. The old llama-cpp-python wheel needed a big reserve (2.3) because a
    spill there returned empty/garbage — but the native llama-server spills
    gracefully (slow, not broken), like LM Studio, so it can run a smaller
    reserve and keep far more layers on the GPU (much faster on a tight 8 GB)."""
    if not model_size_gb or not free_vram_gb or model_size_gb <= 0:
        return 0
    kv_gb = estimate_kv_cache_gb(model_size_gb, ctx, cache_type)
    L = layer_count or 40  # real count when known, proxy otherwise
    budget = free_vram_gb - reserve_gb
    footprint = model_size_gb + kv_gb
    if budget <= 0:
        return 0
    if footprint <= budget:
        return -1  # everything fits — offload all
    return max(1, int((budget / footprint) * L))


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


def vram_fit_class(model_size_gb: Optional[float],
                   free_vram_gb: Optional[float] = None,
                   ctx: int = 8192) -> Dict[str, str]:
    """Classify how a model fits the current GPU. Returns
    {'tier': 'good'|'partial'|'overflow'|'unknown', 'label': '...'}.

    Math:
      required = model_size + estimate_kv_cache_gb(model_size, ctx)
      usable   = max(0, total_vram - 2.29 GB driver reserve)

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
        free_vram_gb = detect_gpu_vram_gb()
    if free_vram_gb is None:
        return {"tier": "unknown", "label": "no GPU detected"}
    required = model_size_gb + estimate_kv_cache_gb(model_size_gb, ctx)
    usable = max(0.0, free_vram_gb - _VRAM_DRIVER_RESERVE_GB)
    if required <= usable:
        return {"tier": "good",     "label": f"fits comfortably ({required:.1f} of {usable:.1f} GB)"}
    if required <= free_vram_gb:
        return {"tier": "partial",  "label": f"tight — partial GPU offload ({required:.1f} > {usable:.1f} usable)"}
    return {"tier": "overflow",     "label": f"likely too big — math says it exceeds {free_vram_gb:.1f} GB total VRAM (estimate)"}


_QUANT_PATTERNS = [
    ("F16",  ("f16", "fp16", "float16")),
    ("Q8",   ("q8_0", "q8_k")),
    ("Q6",   ("q6_k",)),
    ("Q5",   ("q5_k_m", "q5_k_s", "q5_1", "q5_0")),
    ("Q4",   ("q4_k_m", "q4_k_s", "q4_0", "q4_1")),
    ("Q3",   ("q3_k_l", "q3_k_m", "q3_k_s")),
    ("Q2",   ("q2_k",)),
]


def quant_tier(filename: str) -> str:
    """Return the bucket label (Q2..Q8/F16) for a GGUF filename. The picker
    uses this to badge each file and to mark the modal Q4_K_M as recommended.
    Returns empty string when no quant tag is detectable."""
    fn = (filename or "").lower()
    for tier, tags in _QUANT_PATTERNS:
        if any(t in fn for t in tags):
            return tier
    return ""


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
        req = urllib.request.Request(url, headers={"User-Agent": "Hearth/0.7"})
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"could not reach HuggingFace: {type(e).__name__}: {e}"}]
    free_vram = detect_gpu_vram_gb()
    out = []
    has_recommended = False
    for sib in info.get("siblings", []) or []:
        fn = sib.get("rfilename") or ""
        if not fn.lower().endswith(".gguf"):
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


def download_from_hf_repo(repo: str, filename: str,
                          on_progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Download an arbitrary GGUF picked from search_huggingface() results.
    Same .part-file handling as the curated picks so interrupted downloads
    never look complete."""
    if not repo or not filename:
        return {"ok": False, "error": "repo and filename required"}
    if not filename.lower().endswith(".gguf"):
        return {"ok": False, "error": "only .gguf files supported"}
    safe_name = filename.split("/")[-1]
    dest = MODELS_DIR / safe_name
    if dest.exists():
        return {"ok": True, "path": str(dest), "already": True}
    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        done = _download_with_resume(url, dest, tmp, on_progress)
        return {"ok": True, "path": str(dest), "bytes": done}
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


def save_pending_download(repo: str, filename: str) -> None:
    """Remember what's downloading so Resume can re-request it after a restart."""
    try:
        os.makedirs(os.path.dirname(_PENDING_DL_PATH), exist_ok=True)
        with open(_PENDING_DL_PATH, "w", encoding="utf-8") as f:
            json.dump({"repo": repo, "filename": filename}, f)
    except Exception:
        pass


def clear_pending_download() -> None:
    try:
        os.remove(_PENDING_DL_PATH)
    except Exception:
        pass


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


def _load_pending_download() -> dict:
    try:
        with open(_PENDING_DL_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def list_partial_downloads() -> List[Dict[str, Any]]:
    """Interrupted downloads (*.gguf.part) in MODELS_DIR, for the GUI resume UI.
    Attaches the saved repo (if known) so Resume re-requests it directly."""
    out: List[Dict[str, Any]] = []
    pend = _load_pending_download()
    try:
        for p in MODELS_DIR.glob("*.gguf.part"):
            try:
                fn = p.name[:-5]
                out.append({"filename": fn,
                            "gb": round(p.stat().st_size / (1024 ** 3), 2),
                            "path": str(p),
                            "repo": pend.get("repo", "") if pend.get("filename") == fn else ""})
            except OSError:
                pass
    except Exception:
        pass
    return out


def status(api_base: str = "http://localhost:1234/v1") -> Dict[str, Any]:
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
    ext = external_server_running(api_base, timeout=0.5,
                                  api_key=_key or "hearth-builtin") and not builtin
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
    rec = recommend_pick_for_this_pc()
    # Cloud-aware disk scan: do a full scan when on local endpoints OR
    # when the cache is cold. On subsequent cloud polls, reuse the cached
    # result so the Models tab keeps showing what's on disk without
    # paying multi-second filesystem scans on every status poll.
    if _is_local or _status_cache.get("disk_models_cache") is None:
        _disk = scan_disk_for_models()
        _local = list_local_models()
        _status_cache["disk_models_cache"] = _disk
        _status_cache["local_models_cache"] = _local
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
        "builtin_pid": (_proc.pid if (_proc and builtin) else None),
        "builtin_url": _proc_info.get("url") if builtin else None,
        "builtin_model": _proc_info.get("model_path") if builtin else None,
        "disk_models": _disk,
        "partial_downloads": list_partial_downloads(),
        "is_lite": _is_lite_edition(),
        "full_url": FULL_DOWNLOAD_URL,
        "local_models": _local,
        "remote_endpoint": not _is_local,
        "scanning": _is_scanning,
        "picks": TOP_PICKS,
        "recommended_pick_id": rec.get("id"),
        "recommendation_reason": rec.get("recommendation_reason"),
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


def _atexit():
    """Best-effort cleanup so we don't leave a llama.cpp server orphaned."""
    if _proc is not None and _proc.poll() is None:
        try:
            stop_builtin()
        except Exception:
            pass


import atexit as _atexit_module
_atexit_module.register(_atexit)
