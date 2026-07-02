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


def snapshot(max_elements: int = 50) -> dict:
    """Walk the FOREGROUND window's a11y tree; return interactive elements as
    {window, elements:[{idx,type,name,x,y}]}. Caches element refs for click()."""
    if _WIN:
        return _snapshot_win(max_elements)
    if _LINUX:
        return _snapshot_linux(max_elements)
    if _MAC:
        return _snapshot_mac(max_elements)
    return {"error": "desktop accessibility is not supported on this OS",
            "elements": []}


# ---------------------------------------------------------------- Windows (UIA)
def _snapshot_win(max_elements: int) -> dict:
    global _last
    _last = []
    try:
        import uiautomation as auto
    except Exception as e:
        return {"error": f"uiautomation unavailable: {e}", "elements": []}
    try:
        auto.SetGlobalSearchTimeout(1.5)
    except Exception:
        pass
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

    out: list = []
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
        if tname in _INTERACTIVE and on_screen and (name or tname == "EditControl"):
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
    return {"window": win_name[:90], "elements": out}


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
    """Nearest cached interactive element to screen point (sx, sy), or None if
    none is within `tol` px. This is the FUSE step of vision-point + a11y: the
    vision model gives an approximate pixel; we snap it to the real named control
    so the click lands on the actual button, not a guessed coordinate. Call
    snapshot() first so `_last` is populated."""
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
    """Click an element to focus it, then type into it."""
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
