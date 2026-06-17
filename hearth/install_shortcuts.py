"""Create Windows shortcuts so Hearth is one click away.

Creates:
  1. Desktop shortcut → launches the tray app
  2. Startup-folder shortcut → auto-launches the tray on boot (optional)

Run once after install:
    .\\.venv\\Scripts\\python.exe -m hearth.install_shortcuts
    .\\.venv\\Scripts\\python.exe -m hearth.install_shortcuts --no-autostart
    .\\.venv\\Scripts\\python.exe -m hearth.install_shortcuts --uninstall
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


def _windows_paths():
    """Return (desktop, startup) folder paths via WinAPI."""
    if sys.platform != "win32":
        return None, None
    import ctypes
    from ctypes import wintypes
    CSIDL_DESKTOP = 0x0010
    CSIDL_STARTUP = 0x0007
    SHGFP_TYPE_CURRENT = 0
    def _get(csidl):
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, SHGFP_TYPE_CURRENT, buf)
        return buf.value
    return _get(CSIDL_DESKTOP), _get(CSIDL_STARTUP)


def _make_shortcut(target_path: str, args: str, lnk_path: str,
                   icon_path: Optional[str] = None,
                   description: str = "",
                   working_dir: Optional[str] = None,
                   pythonw: bool = False) -> bool:
    """Create a Windows .lnk via WScript.Shell COM (no extra deps)."""
    if sys.platform != "win32":
        print(f"[shortcuts] {sys.platform} not supported (yet)", file=sys.stderr)
        return False
    try:
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(lnk_path)
        sc.TargetPath = target_path
        sc.Arguments = args
        if working_dir:
            sc.WorkingDirectory = working_dir
        if icon_path and os.path.isfile(icon_path):
            sc.IconLocation = icon_path
        if description:
            sc.Description = description
        if pythonw:
            sc.WindowStyle = 7  # minimized — pythonw means no console anyway
        sc.Save()
        return True
    except ImportError:
        # No pywin32 — fall back to writing a .bat that calls our launcher
        # and a basic .lnk via PowerShell. Less pretty but works without deps.
        return _make_shortcut_via_powershell(target_path, args, lnk_path,
                                              icon_path, description, working_dir)


def _make_shortcut_via_powershell(target_path: str, args: str, lnk_path: str,
                                   icon_path: Optional[str],
                                   description: str,
                                   working_dir: Optional[str]) -> bool:
    import subprocess
    icon_line = f'$Shortcut.IconLocation = "{icon_path}"' if icon_path and os.path.isfile(icon_path) else ""
    wd_line = f'$Shortcut.WorkingDirectory = "{working_dir}"' if working_dir else ""
    ps = f'''$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{lnk_path}")
$Shortcut.TargetPath = "{target_path}"
$Shortcut.Arguments = '{args}'
{wd_line}
{icon_line}
$Shortcut.Description = "{description}"
$Shortcut.Save()
'''
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception as e:
        print(f"[shortcuts] PowerShell shortcut failed: {e}", file=sys.stderr)
        return False


def install(no_autostart: bool = False) -> int:
    desktop, startup = _windows_paths()
    if not desktop:
        print("[shortcuts] Windows only for now.", file=sys.stderr)
        return 1

    # Resolve the venv pythonw.exe + repo root
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    pythonw = os.path.join(repo_root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.isfile(pythonw):
        # Fall back to python.exe (will show console window)
        pythonw = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(pythonw):
        print(f"[shortcuts] FATAL: no python found in .venv at {pythonw}", file=sys.stderr)
        return 2

    icon_path = os.path.join(repo_root, "assets", "icon.ico")
    if not os.path.isfile(icon_path):
        icon_path = os.path.join(repo_root, "assets", "icon.png")
        if not os.path.isfile(icon_path):
            icon_path = None  # let Windows pick a default

    # Prefer the PACKAGED tray exe if it's been built — that's what most users
    # actually run (no Python needed). It shows up cleanly in Task Manager's
    # Startup tab. Fall back to the dev venv `pythonw -m hearth.tray`.
    packaged = os.path.join(repo_root, "dist", "Hearth", "Hearth.exe")
    if os.path.isfile(packaged):
        target, t_args, t_wd, t_pythonw = packaged, "", os.path.dirname(packaged), False
        icon_path = packaged  # exe carries its own icon
    else:
        target, t_args, t_wd, t_pythonw = pythonw, "-m hearth.tray", repo_root, True

    # Desktop shortcut → opens tray + immediately opens window
    desktop_lnk = os.path.join(desktop, "Hearth.lnk")
    ok = _make_shortcut(
        target_path=target,
        args=(t_args + " --open").strip() if t_args else "",
        lnk_path=desktop_lnk,
        icon_path=icon_path,
        description="Hearth — local AI for your machine",
        working_dir=t_wd,
        pythonw=t_pythonw,
    )
    print(f"[shortcuts] {'OK ' if ok else 'FAIL '} Desktop: {desktop_lnk}")

    # Startup shortcut → runs Hearth at login. Lands in Task Manager > Startup,
    # where you can toggle it on/off any time.
    if not no_autostart:
        startup_lnk = os.path.join(startup, "Hearth.lnk")
        ok = _make_shortcut(
            target_path=target,
            args=t_args,
            lnk_path=startup_lnk,
            icon_path=icon_path,
            description="Hearth — auto-starts at login",
            working_dir=t_wd,
            pythonw=t_pythonw,
        )
        print(f"[shortcuts] {'OK ' if ok else 'FAIL '} Startup: {startup_lnk}")
        print(f"[shortcuts] -> now visible in Task Manager > Startup (toggle it there)")
    else:
        print(f"[shortcuts] skip Startup (--no-autostart)")

    print("\n[shortcuts] Done. Hearth is on your desktop. The tray app will")
    print("[shortcuts] start automatically next time you log in.")
    return 0


def uninstall() -> int:
    desktop, startup = _windows_paths()
    if not desktop:
        return 1
    removed = 0
    for path in (
        os.path.join(desktop, "Hearth.lnk"),
        os.path.join(startup, "Hearth.lnk"),
        os.path.join(startup, "Hearth (tray).lnk"),  # legacy name
    ):
        if os.path.isfile(path):
            try:
                os.remove(path)
                print(f"[shortcuts] removed {path}")
                removed += 1
            except OSError as e:
                print(f"[shortcuts] could not remove {path}: {e}", file=sys.stderr)
    if not removed:
        print("[shortcuts] nothing to remove.")
    return 0


def _startup_lnk_path() -> Optional[str]:
    _, startup = _windows_paths()
    return os.path.join(startup, "Hearth.lnk") if startup else None


def is_autostart_enabled() -> bool:
    """True if the tray is set to launch at login (Startup-folder shortcut present)."""
    p = _startup_lnk_path()
    return bool(p and os.path.isfile(p))


def set_autostart(enabled: bool) -> bool:
    """Toggle launch-at-login for the tray. Returns the resulting on/off state.

    Mirrors the target resolution in install(): prefer the packaged Hearth.exe,
    fall back to the dev venv `pythonw -m hearth.tray`. Disabling just removes the
    Startup-folder shortcut (this is the same entry Task Manager > Startup shows).
    """
    p = _startup_lnk_path()
    if not p:
        return False
    if not enabled:
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        # legacy name, just in case
        legacy = os.path.join(os.path.dirname(p), "Hearth (tray).lnk")
        try:
            if os.path.isfile(legacy):
                os.remove(legacy)
        except OSError:
            pass
        return False

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    pythonw = os.path.join(repo_root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
    icon_path = os.path.join(repo_root, "assets", "icon.ico")
    if not os.path.isfile(icon_path):
        icon_path = None
    packaged = os.path.join(repo_root, "dist", "Hearth", "Hearth.exe")
    if os.path.isfile(packaged):
        target, t_args, t_wd, t_pythonw = packaged, "", os.path.dirname(packaged), False
        icon_path = packaged
    else:
        target, t_args, t_wd, t_pythonw = pythonw, "-m hearth.tray", repo_root, True
    _make_shortcut(
        target_path=target, args=t_args, lnk_path=p, icon_path=icon_path,
        description="Hearth -- auto-starts at login", working_dir=t_wd,
        pythonw=t_pythonw,
    )
    return is_autostart_enabled()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.install_shortcuts",
        description="Create Hearth Desktop + Startup shortcuts (Windows).",
    )
    parser.add_argument("--no-autostart", action="store_true",
                        help="Skip the Startup-folder shortcut.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the shortcuts created by this script.")
    args = parser.parse_args(argv)
    if args.uninstall:
        return uninstall()
    return install(no_autostart=args.no_autostart)


if __name__ == "__main__":
    sys.exit(main())
