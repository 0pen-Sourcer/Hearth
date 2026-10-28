"""The think control in the real page shows the current brain and model's own
levels, and isn't there when the brain offers nothing to set.

The page runs in Edge; brains and models are put in place in the page.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_think_levels.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-think-ui-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
from hearth import web                                               # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# Puts a brain in place and reads the control back.
SHOW = """([brain, info, facts]) => {
  STATE.brain = Object.assign({}, STATE.brain || {}, brain);
  STATE.agentInfo = info;
  STATE.settings = Object.assign({}, STATE.settings || {}, { llm_model: brain.model || '' });
  if (facts) CLOUD_FACTS.set(`prov::${brain.providerKey}::${brain.model}`, facts);
  renderThinkMenu(); applyThinkButton();
  const btn = document.getElementById('btn-think');
  return { hidden: btn.hidden || btn.disabled, shown: !btn.hidden,
           items: [...document.querySelectorAll('#think-menu .think-menu-item:not([data-fast])')].map(e => e.firstChild.nextSibling.firstChild.textContent.trim()),
           fast: !!document.querySelector('#think-menu .think-fast') };
}"""

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/model/facts*", lambda r: r.fulfill(json={}))
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)

    agent = lambda models, model="", fast=False: [{"providerKey": "cursor-agent", "kind": "agent"},
                                                   {"provider": "cursor-agent", "model": model, "models": models,
                                                    "fast_on": fast}, None]
    got = pg.evaluate(SHOW, agent([{"id": "gpt-5.5", "efforts": ["low", "medium", "high", "xhigh"]}], "gpt-5.5"))
    check(f"an agent model's own levels, after its own default {got}",
          not got["hidden"] and got["items"] == ["Default", "Low", "Medium", "High", "Extra high"])
    got = pg.evaluate(SHOW, agent([{"id": "composer", "efforts": []}], "composer"))
    check(f"an agent model with no levels has the control greyed in its place, not a lone Default {got}",
          got["hidden"] and got["shown"])
    got = pg.evaluate(SHOW, agent([{"id": "composer", "efforts": [], "fast": "Faster"}], "composer"))
    check(f"one with only a Fast tier keeps the control for it {got}", not got["hidden"] and got["fast"] and got["items"] == [])

    cloud = lambda model, facts: [{"providerKey": "openai", "kind": "cloud", "model": model}, None, facts]
    got = pg.evaluate(SHOW, cloud("gpt-new", {"reasoning": True, "efforts": ["low", "high"], "can_off": False}))
    check(f"a cloud model's levels, and no Off where it can't stop {got}",
          not got["hidden"] and got["items"] == ["On", "Low effort", "High effort"])
    got = pg.evaluate(SHOW, cloud("gpt-plain", {"reasoning": False}))
    check(f"a cloud model that doesn't reason has the control greyed in its place {got}", got["hidden"] and got["shown"])
    got = pg.evaluate(SHOW, cloud("gem-pro", {"reasoning": True}))
    check(f"one that reasons with no levels named is on or off {got}", got["items"] == ["Off", "On"])
    check(f"no page errors {errors}", not errors)
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all think control checks passed")
