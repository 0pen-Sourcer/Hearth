"""Desktop accessibility — read the foreground window's UI tree like Playwright
reads the DOM, and click elements BY NAME (precise, no vision tokens).

Instead of screenshot -> guess pixels, the model gets a text list of the real
interactive controls (buttons, fields, menu items, list items) with exact
centers, and acts on them by index/name. Three backends, picked by OS at import:

  * Windows  -> UI Automation via `uiautomation`            (tested, primary)
  * Linux    -> AT-SPI via PyGObject `gi.repository.Atspi`   (EXPERIMENTAL)
  * macOS    -> Accessibility (AX) via pyobjc ApplicationServices (EXPERIMENTAL)

The Linux/macOS backends are unverified on real hardware — they read the tree
for names + screen coordinates, then the CLICK itself goes through the
cross-platform `computer` module (real cursor), so even a partial tree read is
useful and degrades cleanly to the pixel path. macOS needs Accessibility
permission granted to the host app; Linux needs accessibility enabled + the
AT-SPI gir installed (`gir1.2-atspi-2.0`, `python3-gi`).

Best-effort throughout — a flaky a11y call must never crash a turn.
"""
from __future__ import annotations

import sys

_WIN = sys.platform == "win32"
_LINUX = sys.platform.startswith("linux")
_MAC = sys.platform == "darwin"

# Cache of the last snapshot's live element refs, parallel to the indices we
# hand the model, so desktop_click(idx) acts on the exact element it saw.
_last: list = []

# Windows UIA interactive control types.
_INTERACTIVE = {
    "ButtonControl", "EditControl", "HyperlinkControl", "MenuItemControl",
    "CheckBoxControl", "RadioButtonControl", "TabItemControl", "ComboBoxControl",
    "ListItemControl", "SplitButtonControl", "TreeItemControl", "SliderControl",
}

# Non-interactive containers that still carry the on-screen CONTENT (list rows,
# labels, file/chat names). Reported so a snapshot reflects what is visible.
_TEXTLIKE = {"TextControl", "DataItemControl", "ListControl", "TreeControl",
             "DocumentControl", "HeaderItemControl", "StatusBarControl"}

# AT-SPI (Linux) interactive role names (Atspi.get_role_name()).
_ATSPI_ROLES = {
    "push button", "toggle button", "text", "entry", "password text",
    "menu item", "check box", "radio button", "check menu item",
    "radio menu item", "page tab", "combo box", "list item", "slider",
    "link", "spin button",
}

# macOS AX interactive roles (kAXRoleAttribute).
_AX_ROLES = {
    "AXButton", "AXTextField", "AXTextArea", "AXMenuItem", "AXCheckBox",
    "AXRadioButton", "AXTabButton", "AXComboBox", "AXLink", "AXPopUpButton",
    "AXSlider", "AXCell", "AXMenuButton",
}


def available() -> bool:
    """True only if this OS's accessibility backend can actually be imported."""
    if _WIN:
        try:
            import uiautomation  # noqa: F401
            return True
        except Exception:
            return False
    if _LINUX:
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401
            return True
        except Exception:
            return False
    if _MAC:
        try:
            import ApplicationServices  # noqa: F401
            return True
        except Exception:
            return False
    return False


def unsupported_reason() -> str:
    """Human-readable why-not, used in tool error messages."""
    if _WIN:
        return "needs the `uiautomation` package (pip install uiautomation)"
    if _LINUX:
        return ("needs AT-SPI: install `gir1.2-atspi-2.0` + `python3-gi` and "
                "enable accessibility (EXPERIMENTAL on Linux)")
    if _MAC:
        return ("needs pyobjc + Accessibility permission granted to this app in "
                "System Settings > Privacy (EXPERIMENTAL on macOS)")
    return "not supported on this OS"


def snapshot(max_elements: int = 50, window: str = "") -> dict:
    """Walk a window's a11y tree; return interactive elements as
    {window, elements:[{idx,type,name,x,y}]}. Caches element refs for click().
    `window` (Windows only) targets a top-level window by name substring so a
    BACKGROUND window can be inspected + written to; empty = foreground window."""
    if _WIN:
        return _snapshot_win(max_elements, window)
    if _LINUX:
        return _snapshot_linux(max_elements)
    if _MAC:
        return _snapshot_mac(max_elements)
    return {"error": "desktop accessibility is not supported on this OS",
            "elements": []}


# ---------------------------------------------------------------- Windows (UIA)
# Chromium (Chrome/Edge/Brave) and every Electron app (Slack, Discord, VS Code,
# Spotify, ...) build their a11y tree LAZILY — a fresh window exposes only the
# native frame, so the walk finds nothing inside the page. They turn the tree on
# when an assistive client asks the render widget for it. We poke that once per
# process; after that the tree stays live for the session.
_native_a11y_enabled = False


def _is_chromium_window(root) -> bool:
    """True if this top-level is Chromium/Electron/WebView2 (its page content is
    only in the tree when the renderer has accessibility on)."""
    if not _WIN:
        return False
    try:
        import ctypes
        hwnd = int(getattr(root, "NativeWindowHandle", 0) or 0)
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return "Chrome_WidgetWin" in buf.value
    except Exception:
        return False


def _enable_chromium_a11y(root) -> bool:
    """If the foreground window is Chromium/Electron, signal its render widget to
    expose accessibility. Returns True if a poke was sent (caller then waits for
    the tree to build). Best-effort; any failure is swallowed."""
    if not _WIN:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(getattr(root, "NativeWindowHandle", 0) or 0)
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        # Chromium top-levels AND Electron apps are all "Chrome_WidgetWin_1".
        if "Chrome_WidgetWin" not in buf.value:
            return False
        WM_GETOBJECT = 0x003D
        OBJID_CLIENT = 0xFFFFFFFC  # -4, unsigned
        poked = {"n": 0}
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                   ctypes.c_void_p)

        def _cb(child, _lparam):
            cb = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cb, 256)
            if "Chrome_RenderWidgetHostHWND" in cb.value:
                user32.SendMessageW(child, WM_GETOBJECT, 0, OBJID_CLIENT)
                poked["n"] += 1
            return True

        user32.EnumChildWindows(hwnd, proto(_cb), 0)
        return poked["n"] > 0
    except Exception:
        return False


def _walk_win(root, max_elements: int) -> list:
    """DFS the UIA subtree, collecting on-screen interactive controls."""
    global _last
    out: list = []
    _last = []
    stack = [(root, 0)]
    visited = 0
    while stack and len(out) < max_elements and visited < 2500:
        ctrl, depth = stack.pop()
        visited += 1
        try:
            tname = ctrl.ControlTypeName
        except Exception:
            tname = ""
        try:
            name = (ctrl.Name or "").strip()
        except Exception:
            name = ""
        l = t = r = b = 0
        try:
            rect = ctrl.BoundingRectangle
            l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        on_screen = (r > l and b > t)
        # Named TEXT is kept too, not just interactive controls. A webview app
        # (Hearth's own GUI, Electron apps) renders its lists, labels and file
        # names as text nodes, so an interactive-only filter reported "3 elements"
        # for a window full of visible content. Text rows are still clickable by
        # coordinate, so they are worth reporting.
        _keep = (tname in _INTERACTIVE or (tname in _TEXTLIKE and 1 < len(name) <= 80))
        if _keep and on_screen and (name or tname == "EditControl"):
            cx, cy = (l + r) // 2, (t + b) // 2
            out.append({"idx": len(out), "type": tname.replace("Control", ""),
                        "name": name[:70], "x": cx, "y": cy})
            _last.append({"ctrl": ctrl, "x": cx, "y": cy, "name": name,
                          "backend": "uia"})
        if depth < 14:
            try:
                kids = ctrl.GetChildren()
            except Exception:
                kids = []
            for k in reversed(kids):
                stack.append((k, depth + 1))
    return out


def _snapshot_win(max_elements: int, window_name: str = "") -> dict:
    global _native_a11y_enabled
    try:
        import uiautomation as auto
    except Exception as e:
        return {"error": f"uiautomation unavailable: {e}", "elements": []}
    try:
        auto.SetGlobalSearchTimeout(1.5)
    except Exception:
        pass
    root = None
    if window_name:
        # Target a specific top-level window by name substring, so a BACKGROUND
        # window (VS Code, a chat app) can be read + written to without being
        # pulled to the foreground.
        try:
            w = auto.WindowControl(searchDepth=1, SubName=window_name)
            if w.Exists(1.0):
                root = w
        except Exception:
            root = None
        if root is None:
            try:
                wl = window_name.lower()
                for w in auto.GetRootControl().GetChildren():
                    if wl in (w.Name or "").lower():
                        root = w
                        break
            except Exception:
                pass
        if root is None:
            return {"window": "", "elements": [],
                    "error": f"no open window matching {window_name!r}"}
    if root is None:
        try:
            root = auto.GetForegroundControl()
        except Exception:
            root = None
    if root is None:
        return {"window": "", "elements": []}
    try:
        win_name = (root.Name or "").strip()
    except Exception:
        win_name = ""

    # Chromium/Electron: enable the a11y tree, then give it a beat to build so
    # the first walk already sees page content instead of a bare frame.
    poked = _enable_chromium_a11y(root)
    if poked and not _native_a11y_enabled:
        _native_a11y_enabled = True
        try:
            import time as _t
            _t.sleep(0.45)
        except Exception:
            pass

    out = _walk_win(root, max_elements)
    # If a Chromium window still came back with essentially nothing (the tree was
    # mid-build), poke + wait once more, then re-walk. Only when it looks empty,
    # so a normal app never pays the retry cost.
    if poked and len(out) < 3:
        try:
            import time as _t
            _t.sleep(0.6)
        except Exception:
            pass
        out = _walk_win(root, max_elements)
    res = {"window": win_name[:90], "elements": out}
    # Chromium can expose its OWN chrome (tabs, bookmarks, toolbar) while the page
    # inside stays invisible, because the renderer only turns accessibility on for
    # an assistive client. A caller then sees a healthy-looking element list with
    # none of the page in it and clicks the wrong thing. Flag that case explicitly
    # so the caller can fall back to vision instead of trusting a partial tree.
    if out and not _has_page_content(out) and (poked or _is_chromium_window(root)):
        res["chrome_only"] = True
    return res


# Control types that only ever come from a real web page, never from the browser's
# own frame. Seeing none of these in a Chromium window means the page is not in
# the tree.
# "Document" and "Group" are the webview's own container nodes, present even when
# the page inside is not exposed, so they are NOT evidence of page content.
_PAGE_TYPES = {"Edit", "Text", "ComboBox", "CheckBox", "RadioButton",
               "List", "ListItem", "Table", "Image"}
# Browser-frame controls, ignored when deciding whether the PAGE is exposed.
_CHROME_HINTS = ("new tab", "bookmark", "back", "forward", "reload", "address and search",
                 "minimize", "maximize", "close", "extensions", "profile", "downloads",
                 "search tabs", "tab strip", "customize and control", "toolbar")


def _has_page_content(elements: list) -> bool:
    """True if the walked tree includes controls from the PAGE, not just the
    browser's own frame."""
    for e in elements:
        nm = (e.get("name") or "").lower()
        if any(h in nm for h in _CHROME_HINTS):
            continue
        if e.get("type") in _PAGE_TYPES:
            return True
    return False


# ------------------------------------------------------------- Linux (AT-SPI)
def _snapshot_linux(max_elements: int) -> dict:
    global _last
    _last = []
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception as e:
        return {"error": f"AT-SPI unavailable: {e} ({unsupported_reason()})",
                "elements": []}
    try:
        desktop = Atspi.get_desktop(0)
    except Exception as e:
        return {"error": f"AT-SPI desktop error: {e}", "elements": []}

    # Find the active (focused) top-level window across all running apps.
    active_win = None
    win_name = ""
    try:
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win is None:
                    continue
                try:
                    st = win.get_state_set()
                    if st.contains(Atspi.StateType.ACTIVE):
                        active_win = win
                        win_name = (win.get_name() or "").strip()
                        break
                except Exception:
                    continue
            if active_win is not None:
                break
    except Exception:
        pass
    if active_win is None:
        return {"window": "", "elements": []}

    def _extents(acc):
        # GI exposes the Component interface methods directly on the Accessible.
        try:
            ext = acc.get_extents(Atspi.CoordType.SCREEN)
            return int(ext.x), int(ext.y), int(ext.width), int(ext.height)
        except Exception:
            try:
                comp = Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN)
                return int(comp.x), int(comp.y), int(comp.width), int(comp.height)
            except Exception:
                return 0, 0, 0, 0

    out: list = []
    stack = [(active_win, 0)]
    visited = 0
    while stack and len(out) < max_elements and visited < 2500:
        acc, depth = stack.pop()
        visited += 1
        try:
            role = (acc.get_role_name() or "").strip().lower()
        except Exception:
            role = ""
        try:
            name = (acc.get_name() or "").strip()
        except Exception:
            name = ""
        x, y, w, h = _extents(acc)
        on_screen = (w > 0 and h > 0)
        if role in _ATSPI_ROLES and on_screen and (name or role in ("text", "entry")):
            cx, cy = x + w // 2, y + h // 2
            out.append({"idx": len(out),
                        "type": role.title().replace(" ", ""),
                        "name": name[:70], "x": cx, "y": cy})
            _last.append({"ctrl": acc, "x": cx, "y": cy, "name": name,
                          "backend": "atspi"})
        if depth < 14:
            try:
                n = acc.get_child_count()
                kids = [acc.get_child_at_index(k) for k in range(n)]
            except Exception:
                kids = []
            for k in reversed(kids):
                if k is not None:
                    stack.append((k, depth + 1))
    return {"window": win_name[:90], "elements": out}


# --------------------------------------------------------------- macOS (AX)
def _snapshot_mac(max_elements: int) -> dict:
    global _last
    _last = []
    try:
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
            AXValueGetValue, kAXChildrenAttribute, kAXRoleAttribute,
            kAXTitleAttribute, kAXValueAttribute, kAXDescriptionAttribute,
            kAXPositionAttribute, kAXSizeAttribute, kAXValueTypeCGPoint,
            kAXValueTypeCGSize,
        )
    except Exception as e:
        return {"error": f"AX unavailable: {e} ({unsupported_reason()})",
                "elements": []}
    try:
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        pid = front.processIdentifier()
        win_name = (front.localizedName() or "").strip()
        app_el = AXUIElementCreateApplication(pid)
    except Exception as e:
        return {"error": f"AX frontmost-app error: {e}", "elements": []}

    def _attr(el, attr):
        try:
            err, val = AXUIElementCopyAttributeValue(el, attr, None)
            return val if err == 0 else None
        except Exception:
            return None

    def _point(el):
        pos = _attr(el, kAXPositionAttribute)
        size = _attr(el, kAXSizeAttribute)
        x = y = w = h = 0
        try:
            if pos is not None:
                ok, p = AXValueGetValue(pos, kAXValueTypeCGPoint, None)
                if ok:
                    x, y = int(p.x), int(p.y)
            if size is not None:
                ok, s = AXValueGetValue(size, kAXValueTypeCGSize, None)
                if ok:
                    w, h = int(s.width), int(s.height)
        except Exception:
            pass
        return x, y, w, h

    out: list = []
    stack = [(app_el, 0)]
    visited = 0
    while stack and len(out) < max_elements and visited < 2500:
        el, depth = stack.pop()
        visited += 1
        role = _attr(el, kAXRoleAttribute) or ""
        name = (_attr(el, kAXTitleAttribute) or _attr(el, kAXDescriptionAttribute)
                or _attr(el, kAXValueAttribute) or "")
        try:
            name = str(name).strip()
        except Exception:
            name = ""
        x, y, w, h = _point(el)
        on_screen = (w > 0 and h > 0)
        if role in _AX_ROLES and on_screen and name:
            cx, cy = x + w // 2, y + h // 2
            out.append({"idx": len(out), "type": role.replace("AX", ""),
                        "name": name[:70], "x": cx, "y": cy})
            _last.append({"ctrl": el, "x": cx, "y": cy, "name": name,
                          "backend": "ax"})
        if depth < 14:
            kids = _attr(el, kAXChildrenAttribute) or []
            try:
                for k in reversed(list(kids)):
                    stack.append((k, depth + 1))
            except Exception:
                pass
    return {"window": win_name[:90], "elements": out}


# ------------------------------------------------------------------- actions
def element_near(sx: int, sy: int, tol: int = 90):
    """Nearest cached element to screen point (sx, sy) within `tol` px, or None.
    Call snapshot() first to populate `_last`."""
    best = None
    best_d = float(tol) + 1.0
    for e in _last:
        try:
            d = ((e["x"] - sx) ** 2 + (e["y"] - sy) ** 2) ** 0.5
        except Exception:
            continue
        if d < best_d:
            best, best_d = e, d
    if best is None:
        return None
    return {"idx": _last.index(best), "name": best.get("name", ""),
            "x": best["x"], "y": best["y"], "dist": round(best_d, 1)}


def _find(idx=None, name=None):
    if idx is not None:
        try:
            i = int(idx)
        except (TypeError, ValueError):
            i = -1
        if 0 <= i < len(_last):
            return _last[i]
    if name:
        nm = str(name).lower().strip()
        for e in _last:                       # exact-ish first, then substring
            if (e["name"] or "").lower() == nm:
                return e
        for e in _last:
            if nm in (e["name"] or "").lower():
                return e
    return None


def click(idx=None, name=None, double=False, button="left"):
    """Click a cached element by idx or name. Returns its (x,y) or None.

    Windows uses the native UIA invoke (works even if the control is scrolled
    partly off-screen); other backends click the cached screen center via the
    cross-platform `computer` module (real cursor)."""
    target = _find(idx, name)
    if not target:
        return None
    if target.get("backend") == "uia":
        ctrl = target["ctrl"]
        try:
            if double:
                ctrl.DoubleClick(simulateMove=True)
            elif button == "right":
                ctrl.RightClick(simulateMove=True)
            else:
                ctrl.Click(simulateMove=True)
            return (target["x"], target["y"])
        except Exception:
            pass  # fall through to coordinate click
    try:
        from . import computer
        computer.click(target["x"], target["y"], button=button, double=double)
        return (target["x"], target["y"])
    except Exception:
        return None


def focus_and_type(idx=None, name=None, text="") -> bool:
    """Put `text` into an element. Prefers UIA ValuePattern.SetValue, which writes
    DIRECTLY into the control with no focus change and no need for the window to be
    foreground (paste-equivalent) — so a backgrounded editor field or message box
    gets the text with zero focus race. Falls back to click-to-focus + real
    keystrokes for controls with no value pattern (contenteditable, canvas)."""
    target = _find(idx=idx, name=name)
    if target is None:
        return False
    # 1) Direct value set — no focus, works even if the window is in the background.
    if target.get("backend") == "uia":
        try:
            vp = target["ctrl"].GetValuePattern()
            if vp is not None and not getattr(vp, "IsReadOnly", False):
                vp.SetValue(text)
                return True
        except Exception:
            pass   # no value pattern (contenteditable/canvas) — fall back below
    # 2) Fallback: click to focus, then type real keystrokes.
    if click(idx=idx, name=name) is None:
        return False
    try:
        import time
        from . import computer
        time.sleep(0.12)
        computer.type_text(text)
        return True
    except Exception:
        return False
