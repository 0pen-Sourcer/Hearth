"""The steps panel and Inglen's overlays.

Overlays (panel, pill, glow, pointer) are made out of every capture and are
skipped where windows are read; Inglen's own window is not an overlay and stays
in both. A turn's steps, its questions and Stop reach the panel through
headless.run_once, whichever surface started the turn. Nothing here shows a
window on screen: overlay windows are made but never shown, and the one plain
window used for the main-window check sits far off every monitor.

    .\\.venv\\Scripts\\python.exe tests\\test_cu_panel.py
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
import time
import types
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _home  # noqa: E402,F401

from hearth import cu_panel, desktop_session as ds, headless, overlays  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


WIN = sys.platform == "win32"
u32 = ctypes.WinDLL("user32") if WIN else None
if WIN:
    u32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def affinity(hwnd) -> int:
    d = wintypes.DWORD(0)
    u32.GetWindowDisplayAffinity(wintypes.HWND(int(hwnd)), ctypes.byref(d))
    return d.value


# ------------------------------------------------ overlays are out of captures
if WIN:
    from hearth import activity_hud, agent_cursor

    p = cu_panel._Panel()
    made = p._setup()
    check("the panel's window is made", made)
    check("the panel is left out of captures", made and affinity(p.surface.hwnd) == overlays.WDA_EXCLUDEFROMCAPTURE)
    check("the panel is not on screen until a turn shows it", made and not u32.IsWindowVisible(p.surface.hwnd))

    try:
        pill = activity_hud._Pill()
        pill_hwnd = pill.s.hwnd
    except Exception as e:
        print("  pill:", e)
        pill_hwnd = 0
    check("the status pill is left out of captures", bool(pill_hwnd) and affinity(pill_hwnd) == overlays.WDA_EXCLUDEFROMCAPTURE)

    ptr = agent_cursor._Pointer()
    try:
        ptr._create()
    except Exception as e:
        print("  pointer:", e)
    check("the pointer is left out of captures", bool(ptr.hwnd) and affinity(ptr.hwnd) == overlays.WDA_EXCLUDEFROMCAPTURE)

    glow = ds._Glow._build()
    check("the edge glow is left out of captures", bool(glow) and affinity(glow) == overlays.WDA_EXCLUDEFROMCAPTURE)
    check("each is known as an overlay, by class too",
          all(overlays.is_overlay(h) for h in (p.surface.hwnd, pill_hwnd, ptr.hwnd, glow))
          and overlays.class_of(p.surface.hwnd) in overlays.CLASSES)

    with overlays.visible_to_model():
        inside = affinity(p.surface.hwnd)
    check("a look about the overlays shows them, and only for that look",
          inside == overlays.WDA_NONE and affinity(p.surface.hwnd) == overlays.WDA_EXCLUDEFROMCAPTURE)

    # Inglen's main window: a plain window of this process, shown far off every monitor.
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                    wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    main = u32.CreateWindowExW(0x00000080 | 0x08000000, "STATIC", "Inglen", 0x80000000,
                               -32000, -32000, 40, 40, None, None, k32.GetModuleHandleW(None), None)
    u32.ShowWindow(wintypes.HWND(main), 4)                           # SW_SHOWNOACTIVATE
    check("the stand-in main window is not an overlay", bool(main) and not overlays.is_overlay(main))

    from hearth import tools
    seen = {}
    import PIL.ImageGrab as _IG

    class _Img:
        size = (10, 10)

        def save(self, *_a, **_k):
            pass

        def resize(self, *_a, **_k):
            return self

    def _grab(*a, **k):
        seen["main"] = affinity(main)
        seen["panel"] = affinity(p.surface.hwnd)
        return _Img()
    real_grab = _IG.grab
    _IG.grab = _grab
    import hearth.capture_overlay as _co
    real_flash = _co.flash
    _co.flash = lambda *a, **k: None
    try:
        tools._screenshot({})
        check("a screenshot keeps Inglen's own window in", seen.get("main") == overlays.WDA_NONE)
        check("a screenshot leaves the panel out", seen.get("panel") == overlays.WDA_EXCLUDEFROMCAPTURE)
        seen.clear()
        tools._screenshot({"include_overlays": True})
        check("a screenshot about the overlays has them in", seen.get("panel") == overlays.WDA_NONE)
    finally:
        _IG.grab = real_grab
        _co.flash = real_flash

    # The accessibility tree: a window search for "Inglen" finds the window, not an overlay.
    from hearth import desktop_a11y

    class _Ctrl:
        def __init__(self, name, hwnd):
            self.Name, self.NativeWindowHandle = name, hwnd
            self.ControlTypeName = "WindowControl"

        def Exists(self, *_a):
            return True

        def GetChildren(self):
            return []

    over, win = _Ctrl("Inglen", p.surface.hwnd), _Ctrl("Inglen", main)
    fake = types.ModuleType("uiautomation")
    fake.SetGlobalSearchTimeout = lambda *_a: None
    fake.WindowControl = lambda **_k: over
    fake.GetRootControl = lambda: types.SimpleNamespace(GetChildren=lambda: [over, win])
    fake.GetForegroundControl = lambda: over
    real_ui = sys.modules.get("uiautomation")
    sys.modules["uiautomation"] = fake
    picked = []
    real_walk = desktop_a11y._walk_win
    desktop_a11y._walk_win = lambda root, n: picked.append(root) or []
    try:
        desktop_a11y._snapshot_win(10, "Inglen")
        check("a window read by name skips the overlay and reads Inglen's window", picked and picked[-1] is win)
        picked.clear()
        desktop_a11y._snapshot_win(10, "")
        check("an overlay is never read as the window in front", not picked)
    finally:
        desktop_a11y._walk_win = real_walk
        if real_ui is not None:
            sys.modules["uiautomation"] = real_ui
        else:
            sys.modules.pop("uiautomation", None)

    p.shape = (100, 100, 400, 500)
    p.make_way(200, 200)
    ex = u32.GetWindowLongW(wintypes.HWND(p.surface.hwnd), -20)
    check("an action that lands on the panel goes through it", bool(ex & 0x20))
    u32.DestroyWindow(wintypes.HWND(main))

# ------------------------------------------------ the panel follows a turn
shown = {"n": 0}
cu_panel._ensure = lambda: shown.__setitem__("n", shown["n"] + 1)     # nothing drawn
cu_panel.enabled = lambda: True
ds._WIN = True
ds._enabled = lambda: True
ds._foreground = lambda: None
ds._Glow.show = classmethod(lambda cls: None)
ds._Glow.hide = classmethod(lambda cls: None)
ds._chime = lambda: None
from hearth import activity_hud as _hud  # noqa: E402
_hud.for_tool = lambda *a, **k: None
_hud.done = lambda *a, **k: None

events: list = []
mid: dict = {}


async def fake_turn(prompt, **kw):
    emit = kw["emit"]
    emit("tool_call", name="read_file", args={"path": "notes.txt"},
         words={"doing": "Reading notes.txt", "done": "Read notes.txt"})
    emit("tool_result", name="read_file", content="hello")
    mid["before"] = cu_panel.state()
    mid["shown_before"] = shown["n"]
    emit("tool_call", name="desktop_click", args={"name": "Save"},
         words={"doing": "Clicking Save", "done": "Clicked Save"})
    ds.begin("desktop_click", {"name": "Save"})
    mid["running"] = cu_panel.state()
    mid["shown_running"] = shown["n"]
    emit("tool_result", name="desktop_click", content="clicked")
    emit("tool_call", name="desktop_type", args={"text": "hi"})
    emit("tool_result", name="desktop_type", content="Error: no such field")
    mid["after"] = cu_panel.state()
    mid["cancel_before"] = kw["should_cancel"]()
    cu_panel.press("stop")
    mid["cancel_after"] = kw["should_cancel"]()
    return 0


real_run = headless._run_once
headless._run_once = fake_turn
asyncio.run(headless.run_once("Save the notes into the form", emit=lambda k, **f: events.append((k, f)),
                              should_cancel=lambda: False))
b, r, a = mid["before"], mid["running"], mid["after"]
check("before any desktop action the panel stays away", not b["want"] and mid["shown_before"] == 0)
check("the first desktop action brings it in", r["want"] and r["phase"] == "work" and mid["shown_running"] == 1)
check("it shows what was asked", r["request"] == "Save the notes into the form")
check("it has the turn's steps in the app's own words",
      [s["doing"] for s in r["steps"]] == ["Reading notes.txt", "Clicking Save"])
check("a step runs, then is done", r["steps"][-1]["state"] == "run" and a["steps"][1]["state"] == "ok")
check("a step without words gets them from step_words", a["steps"][2]["doing"].startswith("Using") or
      a["steps"][2]["doing"] != "")
check("a step that failed says so", a["steps"][2]["state"] == "err")
check("Stop on the panel stops the turn", mid["cancel_before"] is False and mid["cancel_after"] is True)
check("the surface still gets every event", [k for k, _ in events].count("tool_call") == 3)
check("the turn's end says Done", cu_panel.state()["phase"] == "done")

# ------------------------------------------------ a question shows on the panel
fake_web = types.ModuleType("hearth.web")
fake_web._permission_queues = {}
sys.modules["hearth.web"] = fake_web
import queue  # noqa: E402

asked: dict = {}


def window_check(name, args):
    """The window's own check: a card, then a wait on its queue."""
    q = queue.Queue()
    fake_web._permission_queues["perm_1"] = q
    asked["emit"]("permission_request", id="perm_1", name=name, args=args)
    try:
        return q.get(timeout=5)
    finally:
        fake_web._permission_queues.pop("perm_1", None)


async def asking_turn(prompt, **kw):
    asked["emit"] = kw["emit"]
    ds.begin("computer_click", {"x": 1, "y": 2})
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__("d", kw["permission_check"]("delete_path", {"path": "a.pdf"})))
    t.start()
    for _ in range(100):
        if cu_panel.state()["ask"]:
            break
        await asyncio.sleep(0.02)
    out["ask"] = cu_panel.state()["ask"]
    cu_panel.press("allow")
    t.join(5)
    mid["ask"] = out
    return 0

events.clear()
headless._run_once = asking_turn
asyncio.run(headless.run_once("Delete the copy", emit=lambda k, **f: events.append((k, f)),
                              permission_check=window_check))
q = mid["ask"]
check(f"the question leads with who wants to do what, the path apart, never the tool's name ({q.get('ask')})",
      bool(q.get("ask")) and q["ask"]["head"].endswith("wants to delete a file") and q["ask"]["detail"] == "a.pdf"
      and "delete_path" not in q["ask"]["head"] + q["ask"]["detail"])
check("Allow on the panel answers the window's own question", q.get("d") == "allow")
check("the window is told the question was answered",
      any(k == "permission_settled" and f.get("id") == "perm_1" for k, f in events))
check("the panel clears the question once answered", cu_panel.state()["ask"] is None)


async def quick_turn(prompt, **kw):
    ds.begin("computer_click", {})
    mid["quick"] = kw["permission_check"]("open_app", {"name": "x"})
    mid["quick_ask"] = cu_panel.state()["ask"]
    return 0

headless._run_once = quick_turn
asyncio.run(headless.run_once("Open it", emit=lambda k, **f: None, permission_check=lambda n, a: "allow"))
check("a step the approval mode lets through never asks on the panel",
      mid["quick"] == "allow" and mid["quick_ask"] is None)


async def cli_turn(prompt, **kw):
    ds.begin("computer_click", {})
    task = asyncio.ensure_future(kw["permission_check"]("run_command", {"command": "del x"}))
    for _ in range(100):
        if cu_panel.state()["ask"]:
            break
        await asyncio.sleep(0.02)
    cu_panel.press("deny")
    mid["cli"] = await asyncio.wait_for(task, 5)
    return 0


async def terminal_check(name, args):
    await asyncio.sleep(30)                    # the terminal's picker, never answered here
    return "allow"

headless._run_once = cli_turn
asyncio.run(headless.run_once("Run it", emit=lambda k, **f: None, permission_check=terminal_check))
check("an async check (the terminal's) is answered from the panel too", mid["cli"] == "deny")
rows = cu_panel.state()["steps"]
check(f"a no leaves one quiet line among the steps ({rows[-1:]})",
      rows and rows[-1]["state"] == "declined" and rows[-1]["done"] == "Declined: run a command")

# ------------------------------------------------ one approval, said the same everywhere
from hearth import approval, channel_core  # noqa: E402
c = approval.card("delete_path", {"path": "C:/Bills/old.pdf"}, who="Inglen")
check(f"the card says who wants what in plain words ({c})",
      c["head"] == "Inglen wants to delete a file" and c["detail"] == "C:/Bills/old.pdf")
c2 = approval.card("run_command", {"command": "npm run build"}, who="Codex")
check("a command is the specifics, apart from the words",
      c2["head"] == "Codex wants to run a command" and c2["detail"] == "npm run build")
check("never a tool's raw name, even for a tool with no words of its own",
      "_" not in approval.card("frob_widget", {"x": 1})["head"])
many = approval.card("delete_file", {"paths": [f"C:/Bills/{i}.pdf" for i in range(6)]}, who="Inglen")
check(f"every path is in the box, one per line, the rest counted ({many['detail']!r})",
      many["head"] == "Inglen wants to delete 6 files"
      and many["detail"].splitlines() == ["C:/Bills/0.pdf", "C:/Bills/1.pdf", "C:/Bills/2.pdf", "C:/Bills/3.pdf",
                                          "and 2 more"])
check("the terminal lists them the same", [l.strip() for l in approval.terminal(many)[1:]] == many["detail"].splitlines())
from hearth.relay_bridge import ask_parts  # noqa: E402
check("the phone's box gets the same lines", ask_parts("Inglen", approval.question(many), many["detail"])["cmd"]
      == many["detail"])
lines = approval.terminal(c)
check(f"the terminal says the same: the who-and-what line, the path indented ({lines})",
      lines[0].strip() == c["head"] and lines[1] == "      C:/Bills/old.pdf")


class _App:
    name, takes_detail = "phone", True

    def __init__(self):
        self.asked = []

    def ask(self, chat, text, detail=None):
        self.asked.append((text, detail))


from hearth import relay_bridge  # noqa: E402
app = _App()
hub = channel_core.Hub.__new__(channel_core.Hub)
hub.a, hub._lock = app, threading.Lock()
run = {}
tools_mod = sys.modules["hearth.tools"]
real_allows = tools_mod.mode_allows
tools_mod.mode_allows = lambda n, a: False
import hearth.call_me as _cm  # noqa: E402
real_wait = getattr(_cm, "ask_wait", None)
_cm.ask_wait = lambda s: 0.01
try:
    channel_core.Hub._ask(hub, "me", run, "delete_path", {"path": "C:/Bills/old.pdf"})
finally:
    tools_mod.mode_allows = real_allows
    if real_wait:
        _cm.ask_wait = real_wait
text, detail = app.asked[0]
pp = relay_bridge.ask_parts("Inglen", text, (detail or {}).get("cmd", ""))
check(f"the phone card from a chat turn reads the same ({pp})",
      pp["head"] == "Inglen wants to delete a file" and pp["cmd"] == "C:/Bills/old.pdf")

headless._run_once = real_run
sys.modules.pop("hearth.web", None)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all panel checks passed")
