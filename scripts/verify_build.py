"""Check a built Hearth before it ships.

Each release that went out broken was broken in a way nobody looked for: Full
without voice, Full carrying an old engine, Lite without voice, a spec change
that dropped a package, and an executable Windows Defender quarantined. This
runs the BUILT app's own bundled Python and inspects the bundle from the
inside, and the release script refuses to package a build that fails.

    python scripts/verify_build.py dist\\Hearth --edition full
    python scripts/verify_build.py dist\\Hearth --edition lite
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Imports the packaged app needs at runtime, by feature. A missing one is how
# voice went missing from a release.
MODULES = {
    "core": ["hearth", "hearth.web", "hearth.headless", "hearth.tools", "hearth.memory"],
    "voice, speaking": ["hearth.voice", "kokoro_onnx", "onnxruntime", "sounddevice"],
    "voice, listening": ["hearth.listen", "faster_whisper", "ctranslate2"],
    "voice mode": ["hearth.realtime_voice", "RealtimeSTT", "silero_vad", "pyaudio",
                   "torch", "scipy.signal", "soundfile"],
    "wake word": ["hearth.wake"],
    "documents": ["pypdfium2", "matplotlib", "reportlab", "pptx", "openpyxl"],
    "Plus phone link": ["hearth.relay_crypto", "hearth.relay_bridge", "hearth.plus",
                        "cryptography.hazmat.primitives.ciphers.aead", "websockets", "segno"],
    "reminder buttons": ["windows_toasts", "winrt.windows.ui.notifications"],
}

PROBE = r'''
import importlib, json, os, sys
out = {"modules": {}, "files": {}}
for name in MODULES:
    try:
        importlib.import_module(name)
        out["modules"][name] = "ok"
    except BaseException as e:
        out["modules"][name] = f"{type(e).__name__}: {e}"[:200]
try:
    import kokoro_onnx
    out["files"]["kokoro config.json"] = os.path.isfile(
        os.path.join(os.path.dirname(kokoro_onnx.__file__), "config.json"))
except BaseException:
    out["files"]["kokoro config.json"] = False
try:
    import faster_whisper
    a = os.path.join(os.path.dirname(faster_whisper.__file__), "assets")
    out["files"]["whisper VAD model"] = any(f.endswith(".onnx") for f in os.listdir(a))
except BaseException:
    out["files"]["whisper VAD model"] = False
try:
    import hearth
    from hearth import realtime_voice
    out["realtime_available"] = bool(realtime_voice.is_available())
    out["tools"] = len(hearth.TOOL_DEFINITIONS)
    out["prompt_chars"] = len(hearth.system_prompt())
    from hearth import updater
    out["version"] = updater.HEARTH_VERSION
    base = os.path.dirname(hearth.__file__)
    out["skills"] = len([d for d in os.listdir(os.path.join(base, "skills"))
                         if os.path.isfile(os.path.join(base, "skills", d, "SKILL.md"))])
    out["personas"] = len([f for f in os.listdir(os.path.join(base, "subagents"))
                           if f.endswith(".md")])
except BaseException as e:
    out["hearth_error"] = f"{type(e).__name__}: {e}"[:300]
print("VERIFY_JSON " + json.dumps(out))
'''


def find_exe(dist: str) -> str:
    for n in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe"):
        p = os.path.join(dist, n)
        if os.path.isfile(p):
            return p
    return ""


def engine_build(dist: str):
    """(path, build number) of the bundled llama.cpp server, or (None, None)."""
    root = os.path.join(dist, "_internal", "llamacpp")
    if not os.path.isdir(root):
        return None, None
    for d in sorted(os.listdir(root)):
        exe = os.path.join(root, d, "llama-server.exe")
        if os.path.isfile(exe):
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, timeout=60)
                txt = (r.stdout + r.stderr).decode("utf-8", "replace")
                m = re.search(r"build (\d+)", txt)
                return exe, int(m.group(1)) if m else None
            except Exception:
                return exe, None
    return None, None


def source_facts() -> dict:
    """What the source tree says the build should contain."""
    sys.path.insert(0, ROOT)
    import hearth
    from hearth import updater
    iss = open(os.path.join(ROOT, "Hearth.iss"), encoding="utf-8").read()
    m = re.search(r'#define MyAppVersion "([^"]+)"', iss)
    return {"tools": len(hearth.TOOL_DEFINITIONS), "version": updater.HEARTH_VERSION,
            "installer_version": m.group(1) if m else ""}


def defender_scan(paths) -> list:
    """Scan each file with Windows Defender; return the ones it flags."""
    mp = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                      "Windows Defender", "MpCmdRun.exe")
    if not os.path.isfile(mp):
        return []
    flagged = []
    for p in paths:
        r = subprocess.run([mp, "-Scan", "-ScanType", "3", "-File", p, "-DisableRemediation"],
                           capture_output=True, timeout=600)
        if r.returncode == 2:                    # MpCmdRun: 2 means a threat was found
            txt = (r.stdout + r.stderr).decode("utf-8", "replace")
            m = re.search(r"Threat\s*:\s*(\S+)", txt)
            flagged.append(f"{os.path.basename(p)} ({m.group(1) if m else 'flagged'})")
    return flagged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dist", help="the built folder, e.g. dist\\Hearth")
    ap.add_argument("--edition", required=True, choices=("full", "lite"))
    ap.add_argument("--min-engine", type=int, default=10000,
                    help="oldest llama.cpp build Full may ship")
    ap.add_argument("--no-av", action="store_true", help="skip the Defender scan")
    a = ap.parse_args()

    fails, notes = [], []

    def need(ok, what):
        (notes if ok else fails).append(("ok    " if ok else "FAIL  ") + what)

    dist = os.path.abspath(a.dist)
    exe = find_exe(dist)
    need(bool(exe), f"an executable is in {dist}")
    for n in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe"):
        need(os.path.isfile(os.path.join(dist, n)), f"{n} is present")
    marker = os.path.join(dist, "_internal", "_hearth_edition.txt")
    ed = open(marker, encoding="utf-8").read().strip() if os.path.isfile(marker) else ""
    need(ed == a.edition, f"the bundle says it is {a.edition!r} (it says {ed!r})")

    facts = {}
    if exe:
        code = f"MODULES = {json.dumps(sum(MODULES.values(), []))}\n" + PROBE
        try:
            r = subprocess.run([exe, "--hearth-run-python", "-c", code], capture_output=True,
                               timeout=600, cwd=dist)
            txt = (r.stdout + r.stderr).decode("utf-8", "replace")
            line = next((l for l in txt.splitlines() if l.startswith("VERIFY_JSON ")), "")
            facts = json.loads(line[len("VERIFY_JSON "):]) if line else {}
            need(bool(facts), "the bundled Python runs" + ("" if facts else f": {txt[-300:]}"))
        except Exception as e:
            need(False, f"the bundled Python runs: {type(e).__name__}: {e}")

    mods = facts.get("modules", {})
    for feature, names in MODULES.items():
        bad = [f"{n} ({mods.get(n, 'not checked')})" for n in names if mods.get(n) != "ok"]
        need(not bad, f"{feature}: " + ("all present" if not bad else "missing " + "; ".join(bad)))
    for f, ok in facts.get("files", {}).items():
        need(bool(ok), f"{f} is bundled")
    need(facts.get("realtime_available") is True, "voice mode reports itself available")
    if "hearth_error" in facts:
        need(False, f"hearth loads: {facts['hearth_error']}")

    src = source_facts()
    need(facts.get("tools") == src["tools"],
         f"the bundle has every tool ({facts.get('tools')} of {src['tools']})")
    need(src["version"] == src["installer_version"],
         f"the app and the installer agree on the version ({src['version']} and "
         f"{src['installer_version']})")
    need(bool(src["version"]) and facts.get("version") == src["version"],
         f"the bundle is version {src['version']} (it says {facts.get('version')!r})")
    need((facts.get("skills") or 0) > 0, f"skills are bundled ({facts.get('skills')})")
    need((facts.get("personas") or 0) > 0, f"sub-agent personas are bundled ({facts.get('personas')})")

    wt_saves = os.path.join(dist, "_internal", "Windows Terminal", "saves")
    need(not os.path.exists(wt_saves), "no runtime save files ride along with the bundled terminal")

    eng, build = engine_build(dist)
    if a.edition == "full":
        need(eng is not None, "Full bundles the llama.cpp engine")
        need(build is not None and build >= a.min_engine,
             f"the engine is build {build}, at least {a.min_engine}")
    else:
        need(eng is None, "Lite carries no llama.cpp engine")

    if not a.no_av and os.name == "nt":
        exes = [os.path.join(dist, n) for n in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe")
                if os.path.isfile(os.path.join(dist, n))]
        flagged = defender_scan(exes)
        need(not flagged, "Windows Defender does not flag the executables"
             + (": " + ", ".join(flagged) if flagged else ""))

    for n in notes + fails:
        print(n)
    print()
    if fails:
        print(f"{len(fails)} check(s) failed: do not ship this build")
        return 1
    print(f"{a.edition} build verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
