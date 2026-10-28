"""Auto-discover the user's machine, so the assistant knows it from the start.

What it finds is kept in ~/.hearth/this_pc.json, not in the person's memory:
memory is what the person told it and what they worked on together, read and
edited on the Memory page, and three entries about the GPU, the model server
and every drive's folders read as clutter there. The system prompt carries one
short line from the file (this_pc_line). There is no drive map any more: one
search finds a folder, and a list of everyone's folders went stale and cost
tokens every turn. Older versions saved these as memories;
drop_old_memories takes them out unless the person changed them.


Fast by construction: the GPU is one nvidia-smi / CIM call and the models one HTTP GET.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from typing import Dict, List, Optional

# Hide the console flash on Windows subprocess calls (same flag the rest of the
# codebase uses for nvidia-smi/tasklist).
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def detect_gpu() -> Dict[str, object]:
    """Return {name, vram_gb} for the primary GPU, or {} if none/undetectable."""
    try:
        from . import gpu as _gpu
        p = _gpu.primary()
        if p:
            return {"name": p["name"], "vram_gb": _gpu.vram_total_gb()}
    except Exception:
        pass
    # NVIDIA: one fast query.
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=6, creationflags=_NO_WINDOW,
        )
        line = (out.stdout or "").strip().splitlines()[0].strip()
        if line:
            name, mem = [p.strip() for p in line.split(",", 1)]
            mib = float("".join(c for c in mem if c.isdigit() or c == "."))
            return {"name": name, "vram_gb": round(mib / 1024, 1)}
    except Exception:
        pass
    # Any GPU (incl. AMD/Intel) via Windows CIM — name only, no VRAM.
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -First 1 -ExpandProperty Name)"],
                capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW,
            )
            name = (out.stdout or "").strip().splitlines()[0].strip() if out.stdout else ""
            if name:
                return {"name": name, "vram_gb": None}
        except Exception:
            pass
    return {}


def _vram_guidance(vram_gb: Optional[float]) -> str:
    if not vram_gb:
        return ("Match local model size to your VRAM; if a model won't load, "
                "use a smaller quant or the cloud option.")
    if vram_gb <= 8:
        cap = "only 7-9B models fit comfortably"
    elif vram_gb <= 12:
        cap = "up to ~13B models fit"
    elif vram_gb <= 16:
        cap = "up to ~20B models fit"
    else:
        cap = "larger models (30B+) are viable"
    return (f"With ~{vram_gb:g}GB VRAM, {cap}. Don't suggest models "
            f"too big for that; for heavier jobs the cloud option is there.")


def detect_models(endpoint: Optional[str] = None) -> List[str]:
    """Query the OpenAI-compatible server for its model ids. [] if unreachable."""
    base = (endpoint or os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")).rstrip("/")
    _key = os.environ.get("LOCAL_API_KEY") or ""
    _hdr = {"Authorization": f"Bearer {_key}"} if _key else {}
    try:
        _req = urllib.request.Request(base + "/models", headers=_hdr)
        with urllib.request.urlopen(_req, timeout=5) as r:
            data = json.loads(r.read().decode())
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def detect_drives(max_dirs: int = 18) -> Dict[str, List[str]]:
    """NON-recursive top-level directory names per fixed drive. Instant — this is
    exactly how you scan a drive in milliseconds instead of a 2-minute recurse."""
    roots: List[str] = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            mp = part.mountpoint
            # Skip removable/optical where possible; keep fixed drives.
            if os.name == "nt" and "cdrom" in (part.opts or "").lower():
                continue
            roots.append(mp)
    except Exception:
        if os.name == "nt":
            roots = [f"{d}:\\" for d in "CDEFGH" if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/"]

    out: Dict[str, List[str]] = {}
    for root in roots:
        names: List[str] = []
        try:
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if e.is_dir() and not e.name.startswith((".", "$")):
                            names.append(e.name)
                    except OSError:
                        continue
                    if len(names) >= max_dirs:
                        break
        except (PermissionError, OSError):
            continue
        if names:
            out[root] = names
    return out


THIS_PC_PATH = os.path.join(os.path.expanduser("~/.hearth"), "this_pc.json")
# The memories older versions wrote, by title, with the description each was given:
# one the person rewrote has another description and is left alone.
_OLD = {"Hardware and VRAM budget": r"^.*GB RAM, (\d+|None) cores$",
        "Local LLM setup": r"^LLM server at .*; use list_models, never disk-scan$",
        "Where things live": r"^Drive map: go straight to the right folder instead of scanning$"}


def read_this_pc() -> Dict[str, object]:
    try:
        with open(THIS_PC_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def this_pc_line() -> str:
    """One line for the system prompt: the machine, and where to look for the rest. '' before the first scan."""
    d = read_this_pc()
    if not d:
        return ""
    gpu = str(d.get("gpu") or "")
    if gpu and d.get("vram_gb"):
        gpu += f" ({d['vram_gb']:g} GB VRAM)"
    bits = [b for b in (gpu, f"{d['ram_gb']:g} GB RAM" if d.get("ram_gb") else "", str(d.get("os") or "")) if b]
    return "This PC: " + "; ".join(bits) + "." if bits else ""


def drop_old_memories() -> List[str]:
    """Take out the machine memories older versions saved, unless the person changed them."""
    import re as _re
    from . import memory
    gone = []
    for title, auto in _OLD.items():
        path = memory._path_for(title)
        if not os.path.isfile(path):
            continue
        try:
            fm, _body = memory._read_body(path)
        except Exception:
            continue
        if _re.match(auto, str(fm.get("description") or "").strip()):
            memory.forget(title)
            gone.append(title)
    return gone


def learn_environment(endpoint: Optional[str] = None, write: bool = True, detail: bool = False) -> str:
    """Detect hardware, models and the drive map, and (when `write`) keep them in
    THIS_PC_PATH. Returns a short summary, or with `detail` everything found (the
    learn_environment tool, for a model that needs the drive map)."""
    gpu = detect_gpu()
    models = detect_models(endpoint)
    drives = detect_drives()           # kept only to notice a drive coming or going (refresh)

    summary_bits: List[str] = []

    # --- Hardware -------------------------------------------------------
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        cores = psutil.cpu_count(logical=True)
    except Exception:
        ram_gb, cores = None, None
    import platform
    osname = f"{platform.system()} {platform.release()}"

    gpu_str = gpu.get("name") or "unknown GPU"
    if gpu.get("vram_gb"):
        gpu_str += f" ({gpu['vram_gb']:g}GB VRAM)"
    summary_bits.append(f"hardware ({gpu_str}, {ram_gb}GB RAM)")
    base = (endpoint or os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")).rstrip("/")
    summary_bits.append(f"{len(models)} model(s)" if models else "no model server reachable")
    if write:
        try:
            import time as _time
            os.makedirs(os.path.dirname(THIS_PC_PATH), exist_ok=True)
            tmp = THIS_PC_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"gpu": gpu.get("name") or "", "vram_gb": gpu.get("vram_gb"), "ram_gb": ram_gb,
                           "cores": cores, "os": osname, "drives": sorted(drives), "models": models,
                           "server": base, "at": int(_time.time())}, f)
            os.replace(tmp, THIS_PC_PATH)
        except OSError:
            pass
        try:
            drop_old_memories()
        except Exception:
            pass

    # Stamp the machine fingerprint so the per-boot refresh stays a no-op until
    # something actually changes (first-run onboarding and /learn both land here).
    if write:
        try:
            fp = _fingerprint_path()
            cur = _current_fingerprint()
            if cur is not None:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(cur, f)
        except Exception:
            pass

    said = "Learned " + ", ".join(summary_bits) + "."
    if not detail:
        return said
    out = [said, f"This machine: {gpu_str}; {cores or '?'} logical CPU cores; {ram_gb or '?'}GB RAM; {osname}.",
           _vram_guidance(gpu.get("vram_gb"))]
    if models:
        out.append(f"Models at {base} (list_models has the live list): " + ", ".join(models))
    return "\n".join(out)


def _fingerprint_path() -> str:
    return os.path.join(os.path.expanduser("~/.hearth"), "env_fingerprint.json")


def _current_fingerprint() -> Optional[Dict[str, object]]:
    """A cheap, boot-detectable signature of the machine: GPU, RAM, cores, and
    the set of mounted fixed drives. Models are intentionally excluded — they
    need the LLM server, which is often not up yet at boot."""
    try:
        gpu = detect_gpu()
        drives = detect_drives()
        import psutil
        import platform
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        cores = psutil.cpu_count(logical=True)
        osname = f"{platform.system()} {platform.release()}"
    except Exception:
        return None
    return {
        "gpu": gpu.get("name"),
        "vram": gpu.get("vram_gb"),
        "ram": ram_gb,
        "cores": cores,
        "os": osname,
        "drives": sorted(drives.keys()),
    }


def refresh_environment_if_changed(endpoint: Optional[str] = None) -> Optional[str]:
    """Cheap per-boot check: re-detect the machine and refresh the stored memory
    facts ONLY if it actually changed since last launch (a drive plugged in or
    removed, a swapped GPU, a RAM change). No-op when nothing changed, so it's
    safe to fire on every startup in a background thread. Returns the refresh
    summary if it ran, else None.

    This is what keeps Hearth's machine context FRESH after the one-time
    onboarding scan — otherwise it learns the drives once and never notices the
    external drive you plugged in yesterday."""
    current = _current_fingerprint()
    if current is None:
        return None
    fp = _fingerprint_path()
    prev = None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = None
    if prev == current:
        return None  # machine unchanged — nothing to relearn
    # Changed (or first time we've fingerprinted): relearn into memory. A server
    # that's down at boot just means the model section is skipped, not wiped.
    summary = None
    try:
        summary = learn_environment(endpoint=endpoint, write=True)
    except Exception:
        summary = None
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(current, f)
    except Exception:
        pass
    return summary
