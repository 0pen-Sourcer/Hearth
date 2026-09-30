"""The shells a command can run in, and how to run it in each so its output and
exit status come back true.

Windows PowerShell 5.1 writes redirected output in the console code page, so a
file named in Hindi or Chinese came back as question marks; it exits 0 when a
command inside it failed with an ordinary error, so a failure read as success;
and it has no `&&`, `grep` or `export`, which is the syntax models write most.
PowerShell now runs with UTF-8 in and out and an exit code that reflects
errors, and bash is offered wherever it exists (Git for Windows, as Claude Code
uses, or WSL), named in the tool description so the model picks the syntax
rather than Hearth guessing it from the command.
"""
from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@lru_cache(maxsize=1)
def bash_path() -> Optional[str]:
    """Git for Windows' bash, else WSL's; on other systems the one on PATH."""
    if sys.platform != "win32":
        return shutil.which("bash") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
    cands: List[str] = []
    git = shutil.which("git")
    if git:
        root = os.path.dirname(os.path.dirname(os.path.realpath(git)))  # ...\Git
        cands += [os.path.join(root, "bin", "bash.exe"),
                  os.path.join(root, "usr", "bin", "bash.exe")]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        cands.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    wsl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "bash.exe")
    if os.path.isfile(wsl) and os.path.isfile(
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wsl.exe")):
        return wsl
    return None


def available() -> List[str]:
    """Shell names usable here, the default first."""
    if sys.platform != "win32":
        return ["bash"] if bash_path() else ["sh"]
    return ["powershell", "cmd"] + (["bash"] if bash_path() else [])


def describe() -> str:
    """One line for the tool description: which shells exist on this machine."""
    if sys.platform != "win32":
        return "Runs in bash." if bash_path() else "Runs in sh."
    kind = ""
    b = bash_path()
    if b:
        kind = "WSL bash" if b.lower().endswith(r"system32\bash.exe") else "Git Bash"
    extra = f", or shell='bash' ({kind}) for bash syntax (&&, grep, sed, export)" if b else ""
    return ("PowerShell 5.1 by default: join commands with ';', not '&&'. "
            f"shell='cmd' for cmd.exe{extra}.")


# Run the command, then turn what PowerShell swallowed into an exit code: a
# native program's own code, else 1 when an error was written.
_PS_PRELUDE = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
    "$OutputEncoding=[System.Text.Encoding]::UTF8\n"
    "$ErrorActionPreference='Continue'\n"
    "$global:LASTEXITCODE=0\n"
    "$__hearthErrors=$Error.Count\n"
)
_PS_EPILOGUE = (
    "\n$__hearthOk=$?\n"
    "if ($global:LASTEXITCODE -ne 0) { exit $global:LASTEXITCODE }\n"
    "if (-not $__hearthOk -or $Error.Count -gt $__hearthErrors) { exit 1 }\n"
)


def powershell_script(command: str) -> str:
    return _PS_PRELUDE + command + _PS_EPILOGUE


def child_env() -> Dict[str, str]:
    """The environment for a command: Python children write UTF-8 into the
    pipe rather than the legacy code page they crash on for non-ASCII."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def normalize_shell(shell: Optional[str]) -> Tuple[str, str]:
    """(shell to use, '' or why the one asked for is not available)."""
    s = (shell or "").strip().lower()
    if s in ("", "default", "auto"):
        return available()[0], ""
    if s in ("pwsh", "ps", "powershell.exe"):
        s = "powershell"
    if s in ("sh", "git-bash", "gitbash", "wsl"):
        s = "bash"
    if s not in ("powershell", "cmd", "bash"):
        return available()[0], f"unknown shell '{shell}'; shells here: {', '.join(available())}"
    if s == "bash" and not bash_path():
        return available()[0], ("bash is not installed here (install Git for Windows for "
                                "Git Bash); use PowerShell syntax instead")
    if sys.platform != "win32" and s in ("powershell", "cmd"):
        return available()[0], f"{s} is Windows-only; this machine runs {available()[0]}"
    return s, ""


def argv(command: str, shell: str):
    """(args, use_shell) for subprocess. `shell` must come from normalize_shell."""
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                powershell_script(command)], False
    if shell == "cmd":
        # chcp 65001 switches cmd's output to UTF-8 for this one run.
        return f"chcp 65001>nul & {command}", True
    if shell == "bash":
        return [bash_path() or "bash", "-c", command], False
    return ["/bin/sh", "-c", command], False
