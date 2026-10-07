"""A stand-in bridge process for tests/test_bridge_spawn.py."""
import os
import sys
import time

mode = os.environ.get("HEARTH_STANDIN_MODE", "")
if mode == "die":
    print("ModuleNotFoundError: No module named 'discord'")
    sys.exit(1)
print("[standin] bridge up \u2014 long-polling")
for i in range(4096):
    print(f"[standin] message {i:05d} " + "x" * 48)
with open(os.environ["HEARTH_STANDIN_MARKER"], "w") as f:
    f.write("reached")
time.sleep(30)
