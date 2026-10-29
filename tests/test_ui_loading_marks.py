"""Things still on their way show the Nook waiting, and the Nook itself moves
without jumps.

A site's icon that hasn't come yet shows the Nook with its getting-ready
stroke, and one that can't be had shows a line icon for what it stands for: a
globe for a site, the MCP icon for a server, the agent icon for an agent. No
tile shows a site's first letter. A chat that is still loading shows the Nook
too. The empty chat's mark goes from loading to ready, and from running to its
first percentage, without moving more than a little between two frames.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_loading_marks.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-loadmarks-")
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = work
from hearth import chat_sync, web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True, ui_theme="dark"))
os.makedirs(chat_sync.CONVOS_DIR, exist_ok=True)
with open(os.path.join(chat_sync.CONVOS_DIR, "c_slow.json"), "w", encoding="utf-8") as f:
    json.dump({"id": "c_slow", "title": "A long chat", "updated": 2e12,
               "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}, f)

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da"
                    "63f8cfc0f01f0005000201a3a3d5f30000000049454e44ae426082")
# later.example and charm.sh are still being fetched; gone.example and goose-docs.ai have none.
LATER = {"later.example", "charm.sh"}
NONE = {"gone.example", "goose-docs.ai"}


def favicon(route):
    d = re.search(r"[?&]d=([^&]+)", route.request.url)
    d = d.group(1) if d else ""
    if d in LATER:
        return route.fulfill(status=404, headers={"X-Icon": "later", "Cache-Control": "no-store"}, body=b"")
    if d in NONE:
        return route.fulfill(status=404, headers={"X-Icon": "none", "Cache-Control": "no-store"}, body=b"")
    route.fulfill(status=200, content_type="image/png", body=PNG)


PROG = {"phase": "idle", "percent": 0}
LOADED = {"on": False}
SLOW_CHAT = {"hold": False}


def progress(route):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(PROG))


def state(route):
    r = route.fetch()
    try:
        j = r.json()
    except Exception:
        return route.fulfill(response=r)
    j["model"] = {"id": "demo.gguf", "loaded_context_length": 8192} if LOADED["on"] else None
    route.fulfill(response=r, body=json.dumps(j), content_type="application/json")


def models(route):
    r = route.fetch()
    try:
        j = r.json()
    except Exception:
        return route.fulfill(response=r)
    if isinstance(j, dict):
        for k in ("loaded", "data", "models"):
            for m in (j.get(k) if isinstance(j.get(k), list) else []):
                if isinstance(m, dict) and "state" in m:
                    m["state"] = "loaded" if LOADED["on"] else "not-loaded"
    route.fulfill(response=r, body=json.dumps(j), content_type="application/json")


def goto(pg):
    # The first request on a fresh context occasionally stalls on Windows loopback.
    for attempt in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=15000)
            return
        except Exception:
            if attempt == 3:
                raise


def open_page(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 760})
    ctx.add_init_script("try{localStorage.setItem('hearth_onboarded','1');localStorage.setItem('hearth_theme','dark')}catch(e){}")
    pg = ctx.new_page()
    pg.route("**/api/favicon*", favicon)
    pg.route("**/api/llmserver/progress*", progress)
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/state", state)
    pg.route("**/api/models", models)
    goto(pg)
    pg.wait_for_function("typeof harnessChip === 'function' && document.querySelector('.convo')", timeout=30000)
    return ctx, pg


TILES = """() => {
  const box = document.createElement('div'); box.id = 'tiles'; box.style.cssText = 'position:fixed;left:20px;top:20px;z-index:9999;display:flex;gap:10px;align-items:center;background:var(--bg-1);padding:10px';
  box.innerHTML = [harnessChip('crush'), harnessChip('goose'), harnessChip('aider'), mcpMark({ site: 'later.example' }, ''),
    mcpMark({ site: 'gone.example' }, ''), mcpMark({ site: 'ok.example' }, ''), brainMarkHTML('openrouter')].map(h => `<span class="t">${h}</span>`).join('');
  const msg = document.createElement('div'); msg.innerHTML = '<div class="body"></div>'; box.appendChild(msg);
  attachSources(msg, [{ kind: 'tool', name: 'web_fetch', args: { url: 'https://later.example/a' } },
    { kind: 'tool', name: 'web_fetch', args: { url: 'https://gone.example/b' } },
    { kind: 'tool', name: 'web_fetch', args: { url: 'https://ok.example/c' } }]);
  document.body.appendChild(box);
}"""

READ_TILES = """() => {
  const tiles = [...document.querySelectorAll('#tiles .hx-chip b, #tiles .mp-ico, #tiles .brain-mark, #tiles .src-chip, #tiles .src-list a')];
  const shown = (el) => el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
  const of = (sel) => { const el = document.querySelector(sel); return el && {
    letters: [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(''),
    wait: shown(el.querySelector('.nook-wait')), kind: shown(el.querySelector('.fav-kind')),
    img: shown(el.querySelector('img')) && (el.querySelector('img') || {}).complete && (el.querySelector('img') || {}).naturalWidth > 0,
    light: el.classList.contains('has-fav') }; };
  return {
    letterTiles: tiles.filter(t => [...t.querySelectorAll('*'), t].some(e => [...e.childNodes].some(n => n.nodeType === 3 && /^\\s*[A-Z?]\\s*$/i.test(n.textContent)))).map(t => t.outerHTML.slice(0, 80)),
    crush: of('#tiles .t:nth-child(1) .hx-chip b'), goose: of('#tiles .t:nth-child(2) .hx-chip b'),
    aider: of('#tiles .t:nth-child(3) .hx-chip b'), mcpLater: of('#tiles .t:nth-child(4) .mp-ico'),
    mcpGone: of('#tiles .t:nth-child(5) .mp-ico'), mcpOk: of('#tiles .t:nth-child(6) .mp-ico'),
    srcLater: of('#tiles .src-list a:nth-child(1)'), srcGone: of('#tiles .src-list a:nth-child(2)'),
    srcOk: of('#tiles .src-list a:nth-child(3)'),
    goneKind: (document.querySelector('#tiles .t:nth-child(5) .fav-kind path') || {}).outerHTML || '',
    srcGoneKind: (document.querySelector('#tiles .src-list a:nth-child(2) .fav-kind circle') || {}).outerHTML || '',
  };
}"""

SAMPLER = """() => { window._s = []; const f = () => {
  const d = document.querySelector('.empty .empty-mark .hm-dot'), t = document.querySelector('.empty .empty-mark .hm-trace');
  if (d) { const r = d.getBoundingClientRect(), ts = t ? getComputedStyle(t) : null;
    window._s.push([r.x, r.y, r.width, r.height, ts ? ts.strokeDasharray : '', ts ? ts.strokeDashoffset : '', ts ? ts.opacity : '0']); }
  if (window._s.length < 4000) requestAnimationFrame(f); }; requestAnimationFrame(f); }"""


def worst_steps(data):
    dot = 0.0
    trace = 0.0
    for a, b in zip(data, data[1:]):
        dot = max(dot, *(abs(b[i] - a[i]) for i in range(4)))
        try:
            da, db = float(re.findall(r"[\d.e+-]+", a[4])[0]), float(re.findall(r"[\d.e+-]+", b[4])[0])
            oa, ob = float(a[5].replace("px", "")), float(b[5].replace("px", ""))
        except (IndexError, ValueError):
            continue
        if float(a[6]) < 0.05 or float(b[6]) < 0.05:
            continue
        if -oa > 90 and -ob < 5:               # the running stroke starting its next lap
            continue
        trace = max(trace, abs(oa - ob), abs((-ob + db) - (-oa + da)))
    return dot, trace


with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge", args=["--disable-gpu"])
    except Exception as e:
        print(f"skipped: no browser ({type(e).__name__})")
        sys.exit(0)

    # A chat still on its way shows the Nook.
    ctx = browser.new_context(viewport={"width": 1100, "height": 760})
    ctx.add_init_script("try{localStorage.setItem('hearth_onboarded','1');localStorage.setItem('hearth.activeId','c_slow')}catch(e){}")
    pg = ctx.new_page()
    held: list = []
    listing: list = []
    pg.route("**/api/conversations/c_slow", lambda r: held.append(r))
    pg.route("**/api/conversations", lambda r: listing.append(r))
    goto(pg)
    pg.wait_for_timeout(800)
    first = pg.evaluate("""() => { const n = document.querySelector('#convo-list .nook-wait');
      return !!n && getComputedStyle(n.querySelector('.hm-trace')).animationName; }""")
    check(f"the chat list shows the Nook waiting before the chats arrive ({first}, {len(listing)} asked)",
          first == "nookTrace" and listing)
    for r in listing:
        r.continue_()
    pg.wait_for_selector('.convo', timeout=30000)
    pg.wait_for_timeout(600)
    opening = pg.evaluate("""() => { const n = document.querySelector('#chat-body > .nook-loading .nook-wait');
      return !!n && getComputedStyle(n.querySelector('.hm-trace')).animationName; }""")
    check(f"a chat being opened shows the Nook with its getting-ready stroke ({opening})", opening == "nookTrace")
    for r in held:
        r.continue_()
    pg.wait_for_function("document.querySelectorAll('#chat-body .msg').length > 0 || !document.querySelector('#chat-body > .nook-loading')",
                         timeout=15000)
    check("once the chat arrives the Nook gives way to it",
          pg.evaluate("() => !document.querySelector('#chat-body > .nook-loading')"))
    pg.unroute_all(behavior='ignoreErrors')
    ctx.close()

    # Icons still coming, icons that can't be had, and icons that came.
    ctx, pg = open_page(browser)
    pg.evaluate(TILES)
    pg.hover("#tiles .src-chip")
    pg.wait_for_timeout(500)
    early = pg.evaluate(READ_TILES)
    pg.wait_for_timeout(2500)
    t = pg.evaluate(READ_TILES)
    check(f"no tile shows a site's first letter {t['letterTiles'] + early['letterTiles']}",
          not t["letterTiles"] and not early["letterTiles"])
    check(f"an agent's icon still on its way shows the Nook waiting {t['crush']}",
          t["crush"] and t["crush"]["wait"] and not t["crush"]["kind"] and not t["crush"]["letters"])
    check(f"a server's icon still on its way shows the Nook waiting {t['mcpLater']}", t["mcpLater"] and t["mcpLater"]["wait"])
    check(f"a source's icon still on its way shows the Nook waiting {t['srcLater']}", t["srcLater"] and t["srcLater"]["wait"])
    check(f"an agent whose icon can't be had shows the agent icon {t['goose']}",
          t["goose"] and t["goose"]["kind"] and not t["goose"]["wait"] and not t["goose"]["letters"])
    check(f"an agent with no icon at all shows the agent icon {t['aider']}",
          t["aider"] and t["aider"]["kind"] and not t["aider"]["letters"])
    check(f"a server whose icon can't be had shows the MCP icon {t['mcpGone']}",
          t["mcpGone"] and t["mcpGone"]["kind"] and "M22 11.08" in t["goneKind"])
    check(f"a source whose icon can't be had shows a globe {t['srcGone']}",
          t["srcGone"] and t["srcGone"]["kind"] and 'r="9"' in t["srcGoneKind"])
    check(f"an icon that came shows on its light tile, the Nook gone {t['mcpOk']} {t['srcOk']}",
          t["mcpOk"] and t["mcpOk"]["img"] and t["mcpOk"]["light"] and not t["mcpOk"]["wait"]
          and t["srcOk"] and t["srcOk"]["img"] and not t["srcOk"]["wait"])
    LATER.discard("later.example")
    pg.wait_for_timeout(3500)
    t2 = pg.evaluate(READ_TILES)
    check(f"an icon the PC was still fetching shows once it is there {t2['mcpLater']}",
          t2["mcpLater"] and t2["mcpLater"]["img"] and not t2["mcpLater"]["wait"])
    pg.unroute_all(behavior='ignoreErrors')
    ctx.close()

    # The empty chat's mark from no model, through a load, to ready.
    ctx, pg = open_page(browser)
    pg.evaluate("() => newConversation()")
    pg.wait_for_selector(".empty.idle-nomodel", timeout=30000)
    pg.wait_for_timeout(800)
    pg.evaluate(SAMPLER)
    pg.evaluate("() => { STATE.brain.busy = true; _refreshEmptyIfShown(); startLoadProgress(); }")
    pg.wait_for_timeout(1300)
    PROG.update(phase="loading_weights", percent=8)
    for p in range(8, 100, 15):
        PROG.update(percent=p)
        pg.wait_for_timeout(520)
    PROG.update(phase="ready", percent=100)
    pg.wait_for_timeout(300)
    LOADED["on"] = True
    pg.evaluate("""() => { STATE.loadedModel = { id: 'demo.gguf', state: 'loaded' }; STATE.brain.loaded = true;
      STATE.brain.busy = false; _refreshEmptyIfShown(); }""")
    pg.wait_for_timeout(1800)
    data = pg.evaluate("window._s")
    cls = pg.evaluate("document.querySelector('.empty') && document.querySelector('.empty').className")
    dot, trace = worst_steps(data)
    check(f"the load ends on the ready mark ({cls}, {len(data)} frames)", cls == "empty" and len(data) > 200)
    check(f"from loading to ready the dot never moves more than 1 px between frames (worst {dot:.2f} px)", dot <= 1.0)
    check(f"the stroke never jumps along the bracket, the first percentage included (worst {trace:.1f} of 100)",
          trace <= 15)
    pg.unroute_all(behavior='ignoreErrors')
    ctx.close()
    browser.close()

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all loading mark checks passed")
