"""Graphics card readings without NVIDIA's tool.

Load and memory came from nvidia-smi, so on an AMD or Intel card the status
bar read 0 MB used and model sizing knew nothing. Windows reports both for
every vendor; this hides nvidia-smi and checks Hearth still reads them, and
that an 8 GB card is sized as 8 GB although Windows reports a little less.

    .\\.venv\\Scripts\\python.exe tests\\test_gpu.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import gpu                                              # noqa: E402

check("a card sold as 8 GB is sized as 8 GB", gpu._nominal(7.71) == 8.0)
check("a 12 GB card reading 11.7 is 12", gpu._nominal(11.7) == 12.0)
check("an odd size is left as it is", gpu._nominal(9.0) == 9.0)

if sys.platform != "win32":
    print("skip  the vendor-neutral readings are Windows'")
elif not gpu.adapters():
    print("skip  no graphics card on this machine")
else:
    real_run = subprocess.run

    def no_smi(args, *a, **k):
        if args and str(args[0]).lower().startswith("nvidia-smi"):
            raise FileNotFoundError("nvidia-smi")
        return real_run(args, *a, **k)

    subprocess.run = no_smi
    gpu._smi_cache.update(ts=0.0, rows=[], missing=False)
    gpu._adapters_cache.clear()
    from hearth import web                                          # noqa: E402
    web._gpu_cache.update(ts=0.0, data=None)
    import time
    web._gpu_snapshot()
    time.sleep(0.6)
    web._gpu_cache.update(ts=0.0, data=None)
    snap = web._gpu_snapshot()
    g = (snap.get("gpus") or [{}])[0]
    check(f"without nvidia-smi the card is still found ({g.get('name')})", bool(g.get("name")))
    check(f"its memory in use is read ({g.get('mem_used_mb')} MB)", (g.get("mem_used_mb") or 0) > 0)
    check(f"its load is read ({g.get('util_pct')}%)", g.get("util_pct") is not None)
    check("its temperature is left out rather than shown as 0", g.get("temp_c") is None)
    check("its total memory is known", (g.get("mem_total_mb") or 0) > 0)
    subprocess.run = real_run

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all gpu checks passed")
