"""Desktop accessibility — read the foreground window's UI tree like Playwright
reads the DOM, and click elements BY NAME (precise, no vision tokens).

Uses Windows UI Automation via `uiautomation`. This is the reliable way to drive
the desktop: instead of screenshot → guess pixels, the model gets a text list of
the real interactive controls (buttons, fields, menu items, list items) with
exact centers, and acts on them by index/name. Windows-only for now; Linux
AT-SPI / macOS AX are the cross-OS follow-ons (wire off the boot OS-detector).

Best-effort throughout — a flaky UIA call must never crash a turn.
"""
from __future__ import annotations

import sys

# Cache of the last snapshot's live control refs, parallel to the indices we
# hand the model, so desktop_click(idx) acts on the exact element it saw.
_last: list = []

_INTERACTIVE = {
    "ButtonControl", "EditControl", "HyperlinkControl", "MenuItemControl",
    "CheckBoxControl", "RadioButtonControl", "TabItemControl", "ComboBoxControl",
    "ListItemControl", "SplitButtonControl", "TreeItemControl", "SliderControl",
}


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import uiautomation  # noqa: F401
        return True
    except Exception:
        return False


def snapshot(max_elements: int = 50) -> dict:
    """Walk the FOREGROUND window's UIA tree; return interactive elements as
    {window, elements:[{idx,type,name,x,y}]}. Caches control refs for click()."""
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
            _last.append({"ctrl": ctrl, "x": cx, "y": cy, "name": name})
        if depth < 14:
            try:
                kids = ctrl.GetChildren()
            except Exception:
                kids = []
            for k in reversed(kids):
                stack.append((k, depth + 1))
    return {"window": win_name[:90], "elements": out}


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
    """Click a cached element by idx or name. Returns its (x,y) or None."""
    target = _find(idx, name)
    if not target:
        return None
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
        # Fallback: real-cursor click at the cached center.
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
