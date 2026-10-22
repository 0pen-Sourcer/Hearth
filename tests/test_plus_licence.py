"""A Plus licence: activated once per PC, checked about once a day, good
offline for a while after its last check, and off when the subscription ends.

Runs against a stand-in for Dodo's public licence endpoints.

    .\\.venv\\Scripts\\python.exe tests\\test_plus_licence.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-plus-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


DODO = {"keys": {"GOOD-KEY": {"active": True, "limit": 2, "instances": []}, "OLD-KEY": {"active": False}},
        "calls": []}


class Dodo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        DODO["calls"].append((self.path, body))
        k = DODO["keys"].get(body.get("license_key"))
        code, out = 200, {}
        if self.path == "/licenses/activate":
            if not k:
                code = 404
            elif not k["active"]:
                code = 403
            elif len(k["instances"]) >= k["limit"]:
                code = 422
            else:
                k["instances"].append(f"lki_{len(k['instances'])}")
                out = {"id": k["instances"][-1], "name": body.get("name")}
        elif self.path == "/licenses/validate":
            out = {"valid": bool(k and k["active"] and body.get("license_key_instance_id") in k.get("instances", []))}
        elif self.path == "/licenses/deactivate":
            if k and body.get("license_key_instance_id") in k.get("instances", []):
                k["instances"].remove(body["license_key_instance_id"])
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Dodo)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["HEARTH_DODO_BASE"] = f"http://127.0.0.1:{srv.server_address[1]}"
from hearth import plus  # noqa: E402

check("without a key there is no Plus", plus.status()["plus"] is False)
check("an unknown key says to check it", "wasn't found" in plus.activate("NOPE")["error"])
check("an ended subscription's key says so", "isn't active" in plus.activate("OLD-KEY")["error"])
check("a good key activates this PC", plus.activate("  GOOD-KEY \n")["ok"] and plus.status()["plus"])
check("its activation is remembered", plus._read().get("instance") == "lki_0")
check("only the end of the key is shown", plus.status()["key_hint"] == "…-KEY")
DODO["keys"]["GOOD-KEY"]["limit"] = 1
check("past the activation limit it says to free one", "deactivate it on one" in plus.activate("GOOD-KEY")["error"])
DODO["keys"]["GOOD-KEY"]["limit"] = 2

n = len(DODO["calls"])
plus.status()
check("it isn't checked again within the day", len(DODO["calls"]) == n)

d = plus._read(); d["checked"] = time.time() - 2 * 24 * 3600; plus._write(d)
os.environ["HEARTH_DODO_BASE"] = "http://127.0.0.1:9"           # offline
check("offline, a key checked two days ago still counts", plus.status()["plus"] is True)
d = plus._read(); d["checked"] = time.time() - 9 * 24 * 3600; plus._write(d)
check("but not a week and more later", plus.status()["plus"] is False)

os.environ["HEARTH_DODO_BASE"] = f"http://127.0.0.1:{srv.server_address[1]}"
DODO["keys"]["GOOD-KEY"]["active"] = False                     # the subscription ended
check("when the subscription ends, the next check turns Plus off",
      plus.check(force=True).get("valid") is False and plus.status()["lapsed"])
DODO["keys"]["GOOD-KEY"]["active"] = True
plus.check(force=True)
check("and back on when it resumes", plus.status()["plus"] is True)

plus.deactivate()
check("deactivating frees the activation and forgets the key",
      DODO["keys"]["GOOD-KEY"]["instances"] == [])
check("and Plus is off here", plus.status()["plus"] is False and not plus.status()["has_key"])

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all plus licence checks passed")
