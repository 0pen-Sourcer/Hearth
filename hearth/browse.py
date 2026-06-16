"""Interactive browser control — Hearth's "AI web browser".

Drives a real controlled Chromium via Playwright rather than moving the
physical mouse (which is fragile and steals focus) — navigate, read the
rendered page, click links/
buttons, fill fields. The session PERSISTS across tool calls, so the agent can
do multi-step browsing (search → click result → read → click again).

Optional dependency (like the voice models): if Playwright or its Chromium
aren't installed, the tools return a clear one-line install hint instead of
crashing. To enable:
    pip install playwright
    python -m playwright install chromium

Threading note: Playwright objects are thread-affine — they must be used in
the thread that created them. execute_tool runs in arbitrary worker threads,
so we run ALL Playwright work in ONE dedicated browser thread and dispatch
commands to it over a queue. That's what makes a persistent session safe.

Headed by default (you watch it browse); set HEARTH_BROWSE_HEADLESS=1 for a
hidden browser (CI / servers / no display).
"""

from __future__ import annotations

import atexit
import os
import queue
import threading
import time
from typing import Any, Callable, Optional, Tuple

_INSTALL_HINT = (
    "Browser control needs Playwright + Chromium (one-time):\n"
    "  pip install playwright\n"
    "  python -m playwright install chromium\n"
    "Then retry."
)

_worker: "Optional[_BrowserWorker]" = None
_lock = threading.Lock()

# A violet cursor dot that marks where the AGENT is acting. It is driven ONLY by
# the agent's clicks (see _CURSOR_GLIDE_JS) — it deliberately does NOT follow the
# user's physical mouse. (It used to: a mousemove listener made the dot chase the
# real cursor, so when the user moved their mouse the agent's indicator jumped to
# them and looked like they were hijacking it. Removed.) pointer-events:none so it
# never blocks the user's own clicks. Starts hidden; appears on the first action.
_CURSOR_JS = r"""
(() => {
  const add = () => {
    if (document.getElementById('__hearth_cursor')) return;
    const c = document.createElement('div');
    c.id = '__hearth_cursor';
    // Visible from the moment the page loads so users see the agent is in
    // control. Starts centered at top of viewport, glides on the first click.
    c.style.cssText = 'position:fixed;left:50%;top:80px;width:18px;height:18px;'
      + 'border:2px solid #7c5cff;border-radius:50%;background:rgba(124,92,255,.25);'
      + 'z-index:2147483647;pointer-events:none;margin:-9px 0 0 -9px;'
      + 'box-shadow:0 0 12px #7c5cff;opacity:.85;'
      + 'transition:left .45s ease, top .45s ease, opacity .2s';
    (document.body || document.documentElement).appendChild(c);
  };
  if (document.readyState !== 'loading') add();
  else document.addEventListener('DOMContentLoaded', add);
})();
"""

# Glide the overlay cursor to (x, y) via a CSS transition — a VISIBLE travel
# the user can watch, independent of Playwright's slow_mo (a real stepped
# mouse-move would be multiplied by slow_mo and crawl). Pair with a short sleep
# before the click. This is the ONLY thing that moves the dot — so it always
# represents the agent, never the user.
_CURSOR_GLIDE_JS = r"""
([x, y]) => {
  const c = document.getElementById('__hearth_cursor');
  if (!c) return;
  c.style.opacity = '1';
  c.style.transition = 'left .45s ease, top .45s ease, opacity .2s';
  c.style.left = x + 'px';
  c.style.top = y + 'px';
}
"""


class _BrowserWorker(threading.Thread):
    """Owns the Playwright instance + page on a single dedicated thread."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._cmds: "queue.Queue" = queue.Queue()
        self._ready = threading.Event()
        self.err: Optional[str] = None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self.err = _INSTALL_HINT
            self._ready.set()
            return
        try:
            headless = os.getenv("HEARTH_BROWSE_HEADLESS", "0") == "1"
            # slow_mo paces every action so a HUMAN can watch it drive (instant
            # actions are invisible). Default 600ms; 0 to disable / speed up.
            slowmo = int(os.getenv("HEARTH_BROWSE_SLOWMO", "600"))
            # --disable-blink-features=AutomationControlled hides the
            # navigator.webdriver flag so sites (Amazon, etc.) are less likely
            # to bot-block us. Optional window placement (set
            # HEARTH_BROWSE_WINDOW="x,y,w,h") so it can sit beside the terminal.
            # Maximize on open so the agent has the same screen real estate the
            # user does, AND pages actually load full-width instead of squished
            # into a tiny default 1280x720 viewport stuck in the top-left.
            args = [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ]
            win = os.getenv("HEARTH_BROWSE_WINDOW", "").strip()
            if win and len(win.split(",")) == 4:
                x, y, w, h = win.split(",")
                args += [f"--window-position={x},{y}", f"--window-size={w},{h}"]
            self._pw = sync_playwright().start()
            # DEDICATED, PERSISTENT Hearth Chrome profile. It lives in its own
            # user-data-dir (separate from the user's everyday Chrome) so it
            # NEVER hits Chrome's "profile already open in another window" lock.
            # The user logs into YouTube / Google / etc. ONCE here and it sticks
            # — which also stops the logged-out interruptions (YouTube "are you
            # still watching?", survey + ad prompts) that the agent can't see and
            # the user has to manually barge-in to clear. channel="chrome" keeps
            # the proprietary codecs (H.264/AAC) YouTube needs. Override the dir
            # with HEARTH_BROWSE_PROFILE_DIR.
            prof_dir = os.getenv("HEARTH_BROWSE_PROFILE_DIR") or os.path.join(
                os.environ.get("JARVIS_WORKSPACE") or os.path.expanduser("~/Jarvis"),
                ".browser_profile")
            self._browser = None
            self._context = None
            ctx_common = dict(headless=headless, slow_mo=slowmo, args=args, no_viewport=True)
            try:
                self._context = self._pw.chromium.launch_persistent_context(
                    prof_dir, channel="chrome", **ctx_common)
            except Exception:
                try:
                    self._context = self._pw.chromium.launch_persistent_context(
                        prof_dir, **ctx_common)
                except Exception:
                    # Last resort: ephemeral (throwaway) profile so browse still works.
                    common = dict(headless=headless, slow_mo=slowmo, args=args)
                    try:
                        self._browser = self._pw.chromium.launch(channel="chrome", **common)
                    except Exception:
                        self._browser = self._pw.chromium.launch(**common)
            if self._context is not None:
                self._page = (self._context.pages[0] if self._context.pages
                              else self._context.new_page())
            else:
                self._page = self._browser.new_page(no_viewport=True)
            self._page.set_default_timeout(20000)
            # Pull the controlled window to the foreground so the user actually
            # SEES it drive instead of hunting for it on the taskbar. Playwright
            # opens Chrome un-activated; bring_to_front() handles the tab, and a
            # Win32 SetForegroundWindow fallback handles the OS-level activation.
            if not headless:
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass
                if os.name == "nt":
                    try:
                        import ctypes
                        time.sleep(0.4)  # let the window appear + title settle
                        _t = self._page.title() or ""
                        hwnd = ctypes.windll.user32.FindWindowW(None, f"{_t} - Google Chrome")
                        if hwnd:
                            ctypes.windll.user32.ShowWindow(hwnd, 9)      # SW_RESTORE
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass
            # Inject a visible cursor that follows the mouse — Playwright moves
            # the real mouse during clicks but draws no cursor, so without this
            # you can't SEE it drive. Runs on every navigation. Harmless headless.
            try:
                self._page.add_init_script(_CURSOR_JS)
            except Exception:
                pass
        except Exception as e:
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                self.err = _INSTALL_HINT
            else:
                self.err = f"browser launch failed: {type(e).__name__}: {e}"
            self._ready.set()
            return
        self._ready.set()
        while True:
            fn, args, rq = self._cmds.get()
            if fn is None:  # stop signal
                break
            try:
                rq.put(("ok", fn(self._page, args)))
            except Exception as e:
                rq.put(("err", f"{type(e).__name__}: {e}"))
        try:
            # Persistent profile uses a context (no separate browser handle).
            if self._context is not None:
                self._context.close()
            elif self._browser is not None:
                self._browser.close()
            self._pw.stop()
        except Exception:
            pass

    def call(self, fn: Callable, args: dict, timeout: float = 60) -> Tuple[str, Any]:
        self._ready.wait(timeout=45)
        if self.err:
            return ("err", self.err)
        rq: "queue.Queue" = queue.Queue()
        self._cmds.put((fn, args, rq))
        try:
            return rq.get(timeout=timeout)
        except queue.Empty:
            return ("err", "browser action timed out")

    def stop(self) -> None:
        self._cmds.put((None, None, None))


def _get_worker() -> _BrowserWorker:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = _BrowserWorker()
            _worker.start()
        return _worker


# ---- command functions (run INSIDE the browser thread, get the live page) ----

def _page_summary(page, max_text: int = 2800, max_links: int = 25) -> str:
    title = (page.title() or "").strip()
    url = page.url
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    body = " ".join(body.split())
    if len(body) > max_text:
        body = body[:max_text] + " …[truncated]"
    # Collect clickable elements (links + buttons) with visible text.
    items = []
    try:
        loc = page.locator("a[href], button, [role=button], input[type=submit]")
        n = min(loc.count(), 60)
        seen = set()
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                t = (el.inner_text() or el.get_attribute("value") or el.get_attribute("aria-label") or "").strip()
            except Exception:
                continue
            t = " ".join(t.split())
            if not t or t.lower() in seen or len(t) > 80:
                continue
            seen.add(t.lower())
            items.append(t)
            if len(items) >= max_links:
                break
    except Exception:
        pass
    out = [f"PAGE: {title}", f"URL: {url}", "", body]
    if items:
        out.append("\nCLICKABLE (use browse_click with the exact text):")
        out.extend(f"  - {t}" for t in items)
    return "\n".join(out)


def _cmd_navigate(page, args: dict) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        # No URL + nothing open yet is the common small-model mistake (calling
        # browse() expecting it to "open the browser"). Guide it instead of
        # returning a useless about:blank summary that triggers a retry spiral.
        cur = (page.url or "").strip()
        if not cur or cur.startswith("about:"):
            return ("browse needs a URL. Call it as browse(url='...') — e.g. "
                    "browse(url='https://www.youtube.com') to open a site, or a search "
                    "like browse(url='https://www.google.com/search?q=upcoming+games'). "
                    "Then use browse_click / browse_type on the items it lists.")
        # A page is ALREADY open. Calling browse with no url to "re-read" it just
        # returns the same content you already have — and small models spam this,
        # which spirals (and trips the no-progress loop guard). So don't re-dump
        # the page; tell the model it already has it and to act or answer.
        return (f"You already have the page at {cur} loaded (its content + clickable "
                f"items were returned by your earlier browse call). Don't call browse "
                f"again with no url. Either: browse_click an item from that list, "
                f"browse(url='...') somewhere new, or ANSWER the user now with what you "
                f"have.")
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    resp = page.goto(url, wait_until="domcontentloaded")
    # A 4xx/5xx means the URL is bad (usually a GUESSED review/article path that
    # doesn't exist). Report it as an Error so the model STOPS guessing URLs and
    # the loop guard counts it — otherwise it 404-wanders a dozen times. Steer it
    # back to clicking real search results.
    if resp is not None and resp.status >= 400:
        return (f"Error: {url} returned HTTP {resp.status}. That URL isn't valid — "
                f"don't guess article URLs. Go back to a search "
                f"(browse(url='https://www.google.com/search?q=...')) and browse_click "
                f"a real result from the list instead.")
    return _page_summary(page)


def _cmd_click(page, args: dict) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return "Error: browse_click needs 'text' (the visible text of the link/button)."
    # Try exact, then contains.
    for getter in (
        lambda: page.get_by_role("link", name=text, exact=True),
        lambda: page.get_by_role("button", name=text, exact=True),
        lambda: page.get_by_text(text, exact=True),
        lambda: page.get_by_text(text),
    ):
        try:
            loc = getter().first
            if loc.count() > 0:
                # First SMOOTH-SCROLL the target into view so the user watches the
                # page travel there instead of Playwright teleporting to an
                # off-screen element on click. Then glide the cursor to it, then
                # click. (scroll → settle → cursor glide → land → click.)
                try:
                    loc.evaluate(
                        "el => el.scrollIntoView({behavior:'smooth', block:'center', inline:'center'})"
                    )
                    time.sleep(0.7)  # let the smooth scroll actually play out
                except Exception:
                    pass
                # Animate the visible cursor to the element (now in-viewport), then
                # click. The CSS glide keeps it watchable without slow_mo scaling.
                try:
                    bb = loc.bounding_box()
                    if bb:
                        cx = bb["x"] + bb["width"] / 2
                        cy = bb["y"] + bb["height"] / 2
                        page.evaluate(_CURSOR_GLIDE_JS, [cx, cy])
                        time.sleep(0.55)  # let the cursor visibly travel + land
                except Exception:
                    pass
                loc.click()
                page.wait_for_load_state("domcontentloaded")
                return _page_summary(page)
        except Exception:
            continue
    return f"Couldn't find a clickable element with text '{text}'. Use browse() to re-list clickable items."


def _cmd_scroll(page, args: dict) -> str:
    """Scroll the page so the agent can read more. direction can be 'down'
    (default), 'up', 'top', 'bottom'. Pixels = how far (default = one viewport).
    Returns the freshly summarized page after the scroll."""
    direction = (args.get("direction") or "down").lower()
    px = int(args.get("pixels") or 0)
    try:
        if direction == "top":
            page.evaluate("window.scrollTo({top:0, left:0, behavior:'smooth'})")
        elif direction == "bottom":
            page.evaluate("window.scrollTo({top: document.body.scrollHeight, left:0, behavior:'smooth'})")
        else:
            sign = -1 if direction == "up" else 1
            amount = px if px > 0 else "window.innerHeight"
            page.evaluate(f"window.scrollBy({{top: {sign} * ({amount}), behavior:'smooth'}})")
        time.sleep(0.6)  # let the smooth-scroll play out
    except Exception as e:
        return f"Couldn't scroll: {type(e).__name__}: {e}"
    return _page_summary(page)


def _cmd_key(page, args: dict) -> str:
    """Press a keyboard key (or combo) on the page — the missing piece for
    media controls and app shortcuts that aren't clickable buttons.
    YouTube: 'f' fullscreen, 'k' or ' ' play/pause, 'm' mute, 't' theater,
    'j'/'l' seek 10s, ArrowUp/Down volume. Also 'Escape', 'Control+L', etc.
    Optional 'focus' (a selector/text) is clicked first so the keypress
    lands on the right element — e.g. focus the video before 'f'."""
    key = (args.get("key") or "").strip()
    if not key:
        return "browse_key needs a 'key' — e.g. 'f' for fullscreen, 'k' to play/pause."
    focus = (args.get("focus") or "").strip()
    try:
        if focus:
            try:
                page.get_by_text(focus, exact=False).first.click(timeout=2500)
            except Exception:
                # Fall back to clicking the main <video> so shortcuts register.
                try:
                    page.locator("video").first.click(timeout=2500)
                except Exception:
                    pass
        elif key.lower() == "f":
            # Fullscreen only fires when the player has focus — click the
            # video first so a bare browse_key('f') just works on YouTube.
            try:
                page.locator("video").first.click(timeout=2500)
            except Exception:
                pass
        page.keyboard.press(key)
        time.sleep(0.4)
        return f"Pressed '{key}'."
    except Exception as e:
        return f"Couldn't press '{key}': {type(e).__name__}: {e}"


def _cmd_type(page, args: dict) -> str:
    text = args.get("text") or ""
    label = (args.get("field") or "").strip()
    submit = bool(args.get("submit"))
    try:
        if label:
            page.get_by_label(label).first.fill(text)
        else:
            page.locator("input[type=text], input[type=search], textarea, input:not([type])").first.fill(text)
        if submit:
            page.keyboard.press("Enter")
            page.wait_for_load_state("domcontentloaded")
        return _page_summary(page)
    except Exception as e:
        return f"Couldn't type into the page: {type(e).__name__}: {e}"


# ---- public tool entry points (called from tools.py, any thread) ----

def browse(args: dict) -> str:
    code, res = _get_worker().call(_cmd_navigate, args)
    return res


def browse_click(args: dict) -> str:
    code, res = _get_worker().call(_cmd_click, args)
    return res


def browse_scroll(args: dict) -> str:
    code, res = _get_worker().call(_cmd_scroll, args)
    return res


def browse_type(args: dict) -> str:
    code, res = _get_worker().call(_cmd_type, args)
    return res


def browse_key(args: dict) -> str:
    code, res = _get_worker().call(_cmd_key, args)
    return res


def browse_close(args: dict) -> str:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            _worker.stop()
            _worker = None
            return "Browser session closed."
    return "No browser session was open."


def _shutdown() -> None:
    """Close the browser cleanly at interpreter exit. Without this, the process
    exits while Playwright's node subprocess is still attached and its pipe is
    torn mid-write -> the EPIPE crash on quit. Joining lets it close gracefully."""
    global _worker
    w = _worker
    if w is not None and w.is_alive():
        try:
            w.stop()
            w.join(timeout=5)
        except Exception:
            pass
        _worker = None


atexit.register(_shutdown)
