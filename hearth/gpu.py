"""Graphics cards and how busy they are, for any vendor.

Windows reports this the same way for NVIDIA, AMD and Intel, which is what
Task Manager reads: DXGI lists the adapters with their dedicated memory, and
the "GPU Engine" and "GPU Adapter Memory" performance counters give load and
memory in use. Temperature has no vendor-neutral source, so it is filled in
only where nvidia-smi exists. Elsewhere (Linux, macOS) this falls back to
nvidia-smi alone.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x5143: "Qualcomm", 0x1414: "Microsoft"}


# ---------------------------------------------------------------- adapters
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint),
                ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    import uuid
    u = uuid.UUID(s)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2])
    g.d[:] = list(u.bytes[8:])
    return g


def _vcall(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    """A COM method by its slot in the object's table."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


_adapters_cache: List[List[Dict[str, Any]]] = []


def adapters() -> List[Dict[str, Any]]:
    """Physical graphics adapters: name, vendor, dedicated VRAM, and the LUID
    the performance counters use. Software renderers are left out. Cached."""
    if _adapters_cache:
        return _adapters_cache[0]
    out: List[Dict[str, Any]] = []
    if sys.platform == "win32":
        try:
            dxgi = ctypes.windll.dxgi
            factory = ctypes.c_void_p()
            iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")          # IDXGIFactory1
            if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) == 0 and factory:
                i = 0
                while True:
                    adapter = ctypes.c_void_p()
                    hr = _vcall(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                        factory, i, ctypes.byref(adapter))                 # EnumAdapters1
                    if hr != 0 or not adapter:
                        break
                    d = _DESC1()
                    if _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(_DESC1))(adapter, ctypes.byref(d)) == 0:
                        if not d.Flags & 2:                                # DXGI_ADAPTER_FLAG_SOFTWARE
                            out.append({
                                "name": d.Description.strip(),
                                "vendor": VENDORS.get(d.VendorId, f"0x{d.VendorId:04X}"),
                                "mem_total_mb": int(d.DedicatedVideoMemory // (1024 * 1024)),
                                "shared_mb": int(d.SharedSystemMemory // (1024 * 1024)),
                                "luid": f"luid_0x{d.AdapterLuid.HighPart & 0xFFFFFFFF:08X}_0x{d.AdapterLuid.LowPart:08X}",
                            })
                    _vcall(adapter, 2, ctypes.c_ulong)(adapter)            # Release
                    i += 1
                _vcall(factory, 2, ctypes.c_ulong)(factory)
        except Exception:
            out = []
    if not out and sys.platform.startswith("linux"):
        out = _linux_cards()
    if not out and sys.platform == "darwin":
        out = _apple_silicon()
    if not out or (sys.platform.startswith("linux") and any(a["vendor"] == "NVIDIA" for a in out)):
        named = _nvidia_smi()
        if named:          # its rows carry the card's name and memory
            out = [a for a in out if a["vendor"] != "NVIDIA"]
        for g in named:
            out.append({"name": g["name"], "vendor": "NVIDIA", "mem_total_mb": g["mem_total_mb"],
                        "shared_mb": 0, "luid": ""})
    # A card with its own memory first: that is the one models run on.
    out.sort(key=lambda a: a["mem_total_mb"], reverse=True)
    _adapters_cache.append(out)
    return out


_PCI_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}


def _linux_cards(drm: str = "/sys/class/drm") -> List[Dict[str, Any]]:
    """Cards the Linux kernel lists. AMD's driver reports its own memory; a card
    without that number counts as having none of its own (an integrated one),
    and NVIDIA's comes from nvidia-smi."""
    out: List[Dict[str, Any]] = []
    try:
        names = sorted(n for n in os.listdir(drm) if n.startswith("card") and n[4:].isdigit())
    except OSError:
        return out
    for n in names:
        dev = os.path.join(drm, n, "device")
        try:
            with open(os.path.join(dev, "vendor")) as f:
                vendor = _PCI_VENDORS.get(f.read().strip().lower())
        except OSError:
            continue
        if not vendor:
            continue
        mem = 0
        try:
            with open(os.path.join(dev, "mem_info_vram_total")) as f:
                mem = int(f.read().strip()) // (1024 * 1024)
        except (OSError, ValueError):
            pass
        out.append({"name": f"{vendor} graphics", "vendor": vendor, "mem_total_mb": mem, "shared_mb": 0, "luid": ""})
    return out


def _apple_silicon() -> List[Dict[str, Any]]:
    """A Mac with Apple's own chip shares its memory with the GPU; macOS lets
    the GPU use about three quarters of it."""
    import platform
    if platform.machine().lower() != "arm64":
        return []
    try:
        r = subprocess.run(["sysctl", "-n", "hw.memsize", "machdep.cpu.brand_string"],
                           capture_output=True, text=True, timeout=5)
        lines = (r.stdout or "").strip().splitlines()
        total = int(lines[0])
        name = lines[1].strip() if len(lines) > 1 else "Apple silicon"
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return []
    return [{"name": name, "vendor": "Apple", "mem_total_mb": int(total * 0.75) // (1024 * 1024),
             "shared_mb": 0, "luid": ""}]


def primary() -> Optional[Dict[str, Any]]:
    a = adapters()
    return a[0] if a else None


# ---------------------------------------------------------------- live load
class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]


class _PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PDH_FMT_COUNTERVALUE)]


_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2


class _Counters:
    """One open PDH query sampled on demand. Rate counters need two samples,
    so the first read primes it and later reads are cheap."""

    def __init__(self) -> None:
        self.ok = False
        self.lock = threading.Lock()
        self.last = 0.0
        if sys.platform != "win32":
            return
        try:
            self.pdh = ctypes.windll.pdh
            # PDH status codes are unsigned; read signed, "more data" never matches.
            for fn in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                       "PdhGetFormattedCounterArrayW"):
                getattr(self.pdh, fn).restype = ctypes.c_uint32
            self.q = ctypes.c_void_p()
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
                return
            self.eng = ctypes.c_void_p()
            self.mem = ctypes.c_void_p()
            add = self.pdh.PdhAddEnglishCounterW
            if add(self.q, "\\GPU Engine(*)\\Utilization Percentage", 0, ctypes.byref(self.eng)) != 0:
                return
            if add(self.q, "\\GPU Adapter Memory(*)\\Dedicated Usage", 0, ctypes.byref(self.mem)) != 0:
                return
            self.pdh.PdhCollectQueryData(self.q)
            self.last = time.time()
            self.ok = True
        except Exception:
            self.ok = False

    def _array(self, counter) -> Dict[str, float]:
        size = ctypes.c_uint32(0)
        count = ctypes.c_uint32(0)
        get = self.pdh.PdhGetFormattedCounterArrayW
        rc = get(counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if rc not in (_PDH_MORE_DATA, 0) or not size.value:
            return {}
        buf = (ctypes.c_byte * size.value)()
        if get(counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM_W))
        out: Dict[str, float] = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus in (0, 1):                 # valid or new data
                out[it.szName or ""] = out.get(it.szName or "", 0.0) + it.FmtValue.doubleValue
        return out

    def read(self) -> Dict[str, Dict[str, float]]:
        """{luid: {"util": %, "used_mb": MB}}"""
        if not self.ok:
            return {}
        with self.lock:
            if time.time() - self.last < 0.25:
                time.sleep(0.25)
            if self.pdh.PdhCollectQueryData(self.q) != 0:
                return {}
            self.last = time.time()
            eng, mem = self._array(self.eng), self._array(self.mem)
        per: Dict[str, Dict[str, float]] = {}
        # Engine load: add up every process's share per engine type, then the
        # busiest engine type is the card's load, as Task Manager shows it.
        by_type: Dict[str, Dict[str, float]] = {}
        for name, v in eng.items():
            if "_luid_" not in name or "_engtype_" not in name:
                continue
            luid = "luid_" + name.split("_luid_", 1)[1].split("_phys_", 1)[0]
            et = name.rsplit("_engtype_", 1)[1]
            by_type.setdefault(luid, {})
            by_type[luid][et] = by_type[luid].get(et, 0.0) + v
        for luid, types in by_type.items():
            per.setdefault(luid, {})["util"] = min(100.0, max(types.values()) if types else 0.0)
        for name, v in mem.items():
            luid = name.split("_phys_", 1)[0]
            per.setdefault(luid, {})["used_mb"] = per.get(luid, {}).get("used_mb", 0.0) + v / (1024 * 1024)
        return per


_counters: Optional[_Counters] = None


def _live() -> Dict[str, Dict[str, float]]:
    global _counters
    if _counters is None:
        _counters = _Counters()
    return _counters.read()


# ---------------------------------------------------------------- nvidia-smi, where present
_smi_cache: Dict[str, Any] = {"ts": 0.0, "rows": [], "missing": False}


def _nvidia_smi() -> List[Dict[str, Any]]:
    if _smi_cache["missing"]:
        return []
    if time.time() - _smi_cache["ts"] < 2:
        return _smi_cache["rows"]
    rows: List[Dict[str, Any]] = []
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW)
        for line in (r.stdout or "").strip().splitlines():
            b = [x.strip() for x in line.split(",")]
            if len(b) >= 5:
                num = lambda x: int(float(x)) if x.replace(".", "", 1).isdigit() else None
                rows.append({"name": b[0], "util_pct": num(b[1]), "mem_used_mb": num(b[2]),
                             "mem_total_mb": num(b[3]) or 0, "temp_c": num(b[4])})
    except (OSError, subprocess.SubprocessError):
        _smi_cache["missing"] = True
    _smi_cache.update(ts=time.time(), rows=rows)
    return rows


# ---------------------------------------------------------------- the one call
def snapshot() -> List[Dict[str, Any]]:
    """Each adapter with load, memory used and total, and temperature when known."""
    live = _live()
    smi = {g["name"].lower(): g for g in _nvidia_smi()} if any(a["vendor"] == "NVIDIA" for a in adapters()) else {}
    out = []
    for a in adapters():
        l = live.get(a["luid"], {})
        s = smi.get(a["name"].lower()) or (next(iter(smi.values())) if a["vendor"] == "NVIDIA" and len(smi) == 1 else {})
        util = l.get("util") if "util" in l else s.get("util_pct")
        used = l.get("used_mb") if "used_mb" in l else s.get("mem_used_mb")
        out.append({"name": a["name"], "vendor": a["vendor"],
                    "util_pct": int(round(util)) if util is not None else None,
                    "mem_used_mb": int(round(used)) if used is not None else None,
                    # DXGI leaves out what the driver reserves; nvidia-smi doesn't.
                    "mem_total_mb": max(a["mem_total_mb"] or 0, s.get("mem_total_mb") or 0),
                    "temp_c": s.get("temp_c")})
    return out


_SIZES_GB = (2, 3, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96)


def _nominal(gb: float) -> float:
    """The size the card is sold as. Windows reports dedicated memory minus
    what the driver reserves, so an 8 GB card reads 7.7, under every check
    that asks for 8."""
    for size in _SIZES_GB:
        if size * 0.93 <= gb <= size:
            return float(size)
    return round(gb, 1)


def vram_total_gb() -> Optional[float]:
    snap = snapshot()
    mb = snap[0]["mem_total_mb"] if snap else 0
    return _nominal(mb / 1024.0) if mb else None


def vram_free_gb() -> Optional[float]:
    snap = snapshot()
    if not snap or not snap[0]["mem_total_mb"] or snap[0]["mem_used_mb"] is None:
        return None
    return round(max(0, snap[0]["mem_total_mb"] - snap[0]["mem_used_mb"]) / 1024.0, 1)


if __name__ == "__main__":
    import json
    print(json.dumps(adapters(), indent=2))
    snapshot()
    time.sleep(1)
    print(json.dumps(snapshot(), indent=2))
