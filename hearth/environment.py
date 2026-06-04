"""Auto-discover the user's machine and seed it into local memory.

The whole point: a capable agent (like Claude) walks into a session already
knowing the hardware, the installed models, and where files live — so it never
flails with slow recursive disk scans. A small local model has none of that
context. So at first run (and on demand via the `learn_environment` tool / the
CLI `/learn` command) we detect the environment ONCE, fast, and write it to
~/Jarvis/memory/ as normal memory facts. After that the model just *knows*.

Everything here is generic and machine-agnostic — it reads the real machine, it
never hardcodes anyone's paths. The detected facts land only in the user's local
memory dir (gitignored), never in the shipped repo.

Fast by construction: hardware is one nvidia-smi / CIM call, models is one HTTP
GET, the drive map is a NON-recursive top-level listing of each fixed drive.
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
    return (f"With ~{vram_gb:g}GB VRAM, {cap} in LM Studio. Don't suggest models "
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


def _format_drive_map(drives: Dict[str, List[str]]) -> str:
    lines = ["Drive layout on this PC (go straight here; do NOT scan whole drives "
             "recursively — it's slow and usually finds nothing):"]
    for root, names in drives.items():
        shown = ", ".join(names[:14])
        more = f" (+{len(names) - 14} more)" if len(names) > 14 else ""
        lines.append(f"  - {root}  {shown}{more}")
    lines.append("To find something, look at the top-level folder names above and go "
                 "to the likely one; only then list/scan that one folder.")
    return "\n".join(lines)


def learn_environment(endpoint: Optional[str] = None, write: bool = True) -> str:
    """Detect hardware + models + drive map and (optionally) seed local memory.
    Returns a short human-readable summary of what was learned."""
    from . import memory

    gpu = detect_gpu()
    models = detect_models(endpoint)
    drives = detect_drives()

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
    hw_body = (
        f"This machine: {gpu_str}; "
        f"{cores or '?'} logical CPU cores; "
        f"{ram_gb or '?'}GB RAM; {osname}.\n"
        + _vram_guidance(gpu.get("vram_gb"))
        + "\nSee [[Local LLM setup]] and [[Where things live]]."
    )
    if write:
        memory.save("Hardware and VRAM budget", "user",
                    f"{gpu_str}, {ram_gb}GB RAM, {cores} cores", hw_body)
    summary_bits.append(f"hardware ({gpu_str}, {ram_gb}GB RAM)")

    # --- Models ---------------------------------------------------------
    base = (endpoint or os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")).rstrip("/")
    if models:
        model_lines = "\n".join(f"  - {m}" for m in models)
        mbody = (
            f"The local LLM server is at {base}.\n"
            f"To see installed models call the list_models tool (queries the API "
            f"instantly). NEVER scan the disk for model files. Models seen at setup:\n"
            f"{model_lines}\n"
            f"See [[Hardware and VRAM budget]]."
        )
        if write:
            memory.save("Local LLM setup", "reference",
                        f"LLM server at {base}; use list_models, never disk-scan", mbody)
        summary_bits.append(f"{len(models)} model(s)")
    else:
        summary_bits.append("no model server reachable (start LM Studio, then /learn)")

    # --- Drive map ------------------------------------------------------
    if drives:
        if write:
            memory.save("Where things live", "reference",
                        "Drive map: go straight to the right folder instead of scanning",
                        _format_drive_map(drives))
        summary_bits.append(f"{len(drives)} drive(s)")

    return "Learned " + ", ".join(summary_bits) + "."
