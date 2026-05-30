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

BUILTIN_PORT = int(os.environ.get("JARVIS_BUILTIN_PORT", "1234"))
BUILTIN_HOST = "127.0.0.1"

# Hide the cmd-flash for any subprocess we spawn on Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Curated picks - we test these and pin the exact HF paths. Tuned for the
# common case (one consumer GPU). The fields match what the GUI needs to render
# a clean "pick a model" panel.
TOP_PICKS: List[Dict[str, Any]] = [
    {
        "id": "qwen2.5-7b-instruct-q4_k_m",
        "name": "Qwen 2.5 7B Instruct (Q4_K_M)",
        "size_gb": 4.7,
        "vram_min_gb": 6,
        "context": 32768,
        "hf_repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "hf_file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "description": "Best balance of speed + tool use. The default recommendation.",
        "tags": ["recommended", "tools"],
    },
    {
        "id": "hermes-3-llama-3.2-3b-q4_k_m",
        "name": "Hermes 3 Llama 3.2 3B (Q4_K_M)",
        "size_gb": 2.0,
        "vram_min_gb": 3,
        "context": 8192,
        "hf_repo": "NousResearch/Hermes-3-Llama-3.2-3B-GGUF",
        "hf_file": "Hermes-3-Llama-3.2-3B.Q4_K_M.gguf",
        "description": "Tiny + fast. Good for low-VRAM machines and quick smoke tests.",
        "tags": ["small"],
    },
    {
        "id": "gemma-2-9b-it-q4_k_m",
        "name": "Gemma 2 9B Instruct (Q4_K_M)",
        "size_gb": 5.5,
        "vram_min_gb": 8,
        "context": 8192,
        "hf_repo": "bartowski/gemma-2-9b-it-GGUF",
        "hf_file": "gemma-2-9b-it-Q4_K_M.gguf",
        "description": "Stronger reasoning. Heavier on VRAM and slower than Qwen 7B.",
        "tags": ["smart"],
    },
]


def llama_cpp_available() -> bool:
    """Is llama-cpp-python importable in this Python?"""
    try:
        import importlib.util
        return importlib.util.find_spec("llama_cpp") is not None
    except Exception:
        return False


def external_server_running(api_base: str, timeout: float = 1.5) -> bool:
    """Is anything (LM Studio / Ollama / our own builtin) listening at <base>/models?"""
    url = api_base.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


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
            })
        except OSError:
            continue
    return out


def download_model(pick_id: str,
                   on_progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Download a curated pick from Hugging Face into ~/Jarvis/models/.
    `on_progress(done_bytes, total_bytes)` is called every ~256KB. Returns
    {ok, path} on success or {ok: False, error: ...}. Uses a .part file so
    interrupted downloads never look complete."""
    pick = next((p for p in TOP_PICKS if p["id"] == pick_id), None)
    if not pick:
        return {"ok": False, "error": f"unknown pick id: {pick_id!r}"}

    url = f"https://huggingface.co/{pick['hf_repo']}/resolve/main/{pick['hf_file']}"
    dest = MODELS_DIR / pick["hf_file"]
    if dest.exists():
        return {"ok": True, "path": str(dest), "already": True}

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hearth/0.5 (HF-download)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
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
        return {"ok": True, "path": str(dest), "bytes": done}
    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "error": f"download failed: {type(e).__name__}: {e}"}


_proc: Optional[subprocess.Popen] = None
_proc_info: Dict[str, Any] = {}


def start_builtin(model_path: str, port: Optional[int] = None,
                  ctx: int = 8192, n_gpu_layers: int = -1) -> Dict[str, Any]:
    """Spawn llama-cpp-python's OpenAI-compatible server.
    Returns {ok, url, pid} or {ok: False, error}. n_gpu_layers=-1 offloads
    everything to GPU; set to 0 for CPU-only."""
    global _proc, _proc_info

    if not llama_cpp_available():
        return {"ok": False, "error":
                "llama-cpp-python is not installed. Run: pip install llama-cpp-python"}

    if _proc is not None and _proc.poll() is None:
        return {"ok": True, "url": _proc_info.get("url"), "pid": _proc.pid, "already": True}

    if not Path(model_path).is_file():
        return {"ok": False, "error": f"model file not found: {model_path}"}

    port = port or BUILTIN_PORT
    cmd = [
        sys.executable, "-m", "llama_cpp.server",
        "--model", model_path,
        "--host", BUILTIN_HOST,
        "--port", str(port),
        "--n_ctx", str(ctx),
        "--n_gpu_layers", str(n_gpu_layers),
        "--api_key", "hearth-builtin",
    ]

    try:
        _proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )
    except Exception as e:
        return {"ok": False, "error": f"could not spawn server: {type(e).__name__}: {e}"}

    api_base = f"http://{BUILTIN_HOST}:{port}/v1"

    # Wait for the server to come up. llama.cpp loading a 7B Q4 takes 10-30s.
    deadline = time.time() + 90
    while time.time() < deadline:
        if _proc.poll() is not None:
            return {"ok": False, "error": f"server exited (code {_proc.returncode}) before responding"}
        if external_server_running(api_base, timeout=1.0):
            break
        time.sleep(1)
    else:
        return {"ok": False, "error": "server did not respond within 90s - check llama.cpp logs"}

    _proc_info = {"url": api_base, "model_path": model_path, "port": port, "ctx": ctx}
    return {"ok": True, "url": api_base, "pid": _proc.pid, "info": _proc_info}


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
    return {"ok": True, "was_running": True}


def status(api_base: str = "http://localhost:1234/v1") -> Dict[str, Any]:
    """Snapshot of the LLM-server situation, for the GUI Models panel.

    `external` = something at api_base that isn't our builtin.
    `builtin_running` = our subprocess is alive.
    `local_models` = downloaded GGUF files the user can boot.
    `picks` = curated download recommendations.
    """
    builtin = _proc is not None and _proc.poll() is None
    ext = external_server_running(api_base) and not builtin
    return {
        "llama_cpp_installed": llama_cpp_available(),
        "external_running": ext,
        "builtin_running": builtin,
        "builtin_url": _proc_info.get("url") if builtin else None,
        "builtin_model": _proc_info.get("model_path") if builtin else None,
        "local_models": list_local_models(),
        "picks": TOP_PICKS,
        "models_dir": str(MODELS_DIR),
    }


def _atexit():
    """Best-effort cleanup so we don't leave a llama.cpp server orphaned."""
    if _proc is not None and _proc.poll() is None:
        try:
            stop_builtin()
        except Exception:
            pass


import atexit as _atexit_module
_atexit_module.register(_atexit)
