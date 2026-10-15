"""What a turn cost, shown the way the brain bills it.

With an API key on a provider Hearth showed no cost at all, though the model
card's catalog knows each model's prices. With Claude Code on a Claude plan it
showed the figure Claude Code reports as if it were billed; on a plan nothing is.

    .\\.venv\\Scripts\\python.exe tests\\test_turn_cost.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import model_facts as M, term_render  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


M.catalog_entry = lambda pid, mid: ({"cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}}
                                    if mid == "priced" else {"cost": {"input": 1, "output": 5}} if mid == "plain" else None)
c = M.turn_cost("anthropic", "priced", {"in": 10000, "out": 1000, "cache_read": 8000})
check(f"a turn is priced from the catalog, cached input at its own rate ({c})", c is not None and abs(c - 0.0234) < 1e-9)
check("a model without a cache rate charges cached input at the input rate",
      abs(M.turn_cost("x", "plain", {"in": 1000, "out": 0, "cache_read": 1000}) - 0.001) < 1e-9)
check("a model the catalog doesn't price has no cost", M.turn_cost("x", "unknown", {"in": 5}) is None)

lines = []
t = term_render.Turn(out=lines.append)
try:
    t._on_done(stats={"brain": "Claude Code", "cost_usd": 0.0024, "plan": True, "tokens_in": 900, "tokens_out": 20})
    said = " ".join(lines)
    check(f"on a plan the cost is said as plan usage, not a bill ({said!r})", "on your plan" in said and "900" in said)
except Exception as e:
    check(f"the terminal's line for a turn on a plan ({type(e).__name__}: {e})", False)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all turn cost checks passed")
