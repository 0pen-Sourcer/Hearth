# PyInstaller spec — builds Hearth as a single-folder Windows app.
#
# Build:
#     .\.venv\Scripts\python.exe -m PyInstaller Hearth.spec --clean
#
# Output:
#     dist\Hearth\Hearth.exe         ← double-click to launch tray + open window
#     dist\Hearth\Hearth-cli.exe     ← classic CLI as a console exe
#
# Notes:
#  - We bundle the entire `hearth/` package + ui.html as a data file
#  - voices/ and conversations/ are NOT bundled — they live in %USERPROFILE%\Jarvis
#  - Tray icon comes from assets/icon.png (falls back to procedurally drawn H)
#  - PyWebView's Edge WebView2 runtime is assumed installed (Windows 10/11 ship it)

import os
import glob
from PyInstaller.utils.hooks import collect_submodules, collect_data_files


def _unfreeze_hearth(analysis):
    """Keep Hearth's OWN modules out of the frozen PYZ archive.

    The bundle is ~1 GB of payload (CUDA, llama.cpp, onnxruntime) that never
    changes between releases, against ~4 MB of Hearth code that changes every
    release. Frozen, a one-line fix costs the user a full reinstall. Shipped as
    loose .py beside the exe, a release can hand out a small patch instead.

    Safe because Analysis still *sees* these modules (so every third-party
    dependency they import is discovered and bundled normally) — we only drop
    them from the archive afterwards. At runtime PyInstaller's FrozenImporter
    simply doesn't find `hearth`, so Python falls through to sys.path, which
    includes the bundle directory where the loose files live.
    """
    analysis.pure = [e for e in analysis.pure
                     if not (e[0] == "hearth" or e[0].startswith("hearth."))]
    return analysis

# HEARTH_LITE=1 builds a slim bundle (~0.8 GB) WITHOUT the built-in CUDA
# llama.cpp server + nvidia CUDA wheels (~1.7 GB). Lite users run LM Studio /
# Ollama / a cloud key (the recommended path anyway). Default build keeps the
# self-contained server. Usage:  set HEARTH_LITE=1 && build.bat
LITE = bool(os.environ.get("HEARTH_LITE"))

block_cipher = None

REPO_ROOT = os.path.abspath(SPECPATH)
ICON_PATH = os.path.join(REPO_ROOT, "assets", "icon.ico")
if not os.path.isfile(ICON_PATH):
    ICON_PATH = os.path.join(REPO_ROOT, "assets", "icon.png")
    if not os.path.isfile(ICON_PATH):
        ICON_PATH = None

# Version resource so the exe reports "Hearth" (not "Hearth.exe"/"Python") in
# Task Manager Startup, file properties, and toast attribution.
VERSION_PATH = os.path.join(REPO_ROOT, "assets", "version_info.txt")
if not os.path.isfile(VERSION_PATH):
    VERSION_PATH = None

# Datas: bundle ui.html, persona, anything in hearth/ that isn't a .py
DATAS = [
    (os.path.join(REPO_ROOT, "hearth", "ui.html"), "hearth"),
    # Hearth's modules ride as loose .py (see _unfreeze_hearth) so an update can
    # replace ~4 MB of code instead of forcing a ~1 GB reinstall. Only the
    # top-level package has importable modules; hearth/skills/*/scripts are data
    # and are collected separately below.
    *[(p, "hearth") for p in glob.glob(os.path.join(REPO_ROOT, "hearth", "*.py"))],
    (os.path.join(REPO_ROOT, "README.md"), "."),
    # CLI script needs to be importable by _launch_cli.py at runtime
    (os.path.join(REPO_ROOT, "hearth_cli.py"), "."),
    # Bat launcher that opens the CLI exe inside the bundled WT (renamed
    # to Hearth-cli.bat in the final bundle)
    (os.path.join(REPO_ROOT, "dist_cli_launcher.bat"), "."),
    # v0.7 skills + subagent personas — SKILL.md, scripts/*.py, assets. These
    # are data (not imported modules), so PyInstaller's module analysis misses
    # them entirely. Without this the packaged app ships ZERO skills — the
    # model finds nothing, recreates a crippled version, and all the bundled
    # make-pdf/pptx/xlsx work never reaches the user.
    (os.path.join(REPO_ROOT, "hearth", "skills"), os.path.join("hearth", "skills")),
    (os.path.join(REPO_ROOT, "hearth", "subagents"), os.path.join("hearth", "subagents")),
]
if os.path.isdir(os.path.join(REPO_ROOT, "assets")):
    DATAS.append((os.path.join(REPO_ROOT, "assets"), "assets"))
# Bundle the portable Windows Terminal (~30MB) — Hearth-cli.bat prefers
# this over the system wt.exe so the CLI gets a nice tabbed terminal even
# on machines without Windows Terminal installed.
_wt_dir = os.path.join(REPO_ROOT, "Windows Terminal")
if os.path.isdir(_wt_dir):
    DATAS.append((_wt_dir, "Windows Terminal"))

# Bundle data files PyInstaller misses by default for packages that ship
# runtime assets (config JSON, ONNX VAD models, vocab tables). Without this
# the frozen voice modules crash on first call:
#   - kokoro_onnx  → config.json (TTS vocab)
#   - faster_whisper → assets/*.onnx (Silero VAD model)
for _pkg in ("kokoro_onnx", "espeakng_loader", "language_tags",
             "faster_whisper", "onnxruntime", "matplotlib"):
    try:
        DATAS += collect_data_files(_pkg)
    except Exception:
        pass

# webrtcvad (pulled in by RealtimeSTT's VAD) ships package metadata that's read
# at runtime; the frozen app needs it copied or import fails. Doing it HERE in
# the repo's spec means no one has to hand-edit the venv's PyInstaller hook
# (that edit is non-reproducible — gone on every reinstall).
try:
    from PyInstaller.utils.hooks import copy_metadata as _copy_metadata
    try:
        DATAS += _copy_metadata("webrtcvad")
    except Exception:
        DATAS += _copy_metadata("webrtcvad-wheels")  # the wheels-packaged fork
except Exception:
    pass

# Optional deps pulled in via runtime detection — be explicit so PyInstaller
# doesn't drop them during static analysis
HIDDEN = [
    "openai", "openai._streaming", "openai.types.chat",
    "truststore",  # imported in a try/except in hearth/__init__ — pin it so
                   # the bundled exe keeps OS-cert-store TLS (cloud behind proxy)
    "pystray._win32", "PIL", "PIL.Image", "PIL.ImageDraw",
    "kokoro_onnx",
    "faster_whisper",
    "pypdf", "pypdfium2",  # pypdfium2 = PDF→VLM-OCR fallback for scanned pages
    "fitz", "pymupdf",     # pdf-tools skill (split/merge/search) — model-written scripts import fitz
    "pyfiglet",            # make-ascii banners — imported only by skill scripts, so force-include
    "docx", "openpyxl", "pptx",
    "reportlab", "reportlab.pdfgen", "reportlab.platypus", "reportlab.lib", "reportlab.lib.pagesizes", "reportlab.lib.styles", "reportlab.lib.units", "reportlab.lib.colors",
    # matplotlib — the skills' build scripts import it at runtime to render
    # themed charts; static analysis can't see those, so force-include it
    # (and use the Agg backend, no GUI toolkit).
    "matplotlib", "matplotlib.backends.backend_agg",
    "psutil", "sounddevice", "numpy",
    "webview", "webview.platforms.edgechromium",
    "plyer", "plyer.platforms.win.notification",
    # v0.6 round-7 modules — must be explicitly listed or PyInstaller's
    # tree-shaker may drop them since nothing imports them at top level.
    "hearth.reminders", "hearth.session_search", "hearth.wake",
    "hearth.desktop_attach",
    "hearth.singleton",           # tray single-instance lock
    "hearth.realtime_voice",      # silero VAD + streaming whisper
    "hearth.tool_call_parser",    # multi-family tool-call parser
    "hearth.skills_loader",       # v0.7 skills layer
    "hearth.skills_install",      # v0.7 skill install-from-GitHub flywheel
    "hearth.email_tools",         # v0.7 IMAP/SMTP email (stdlib, opt-in)
    "hearth.subagents",           # v0.7 sub-agent fork system
    "hearth.mcp_client",          # v0.7 outbound MCP client
    "hearth.migrate",             # Hermes/OpenClaw import path
    "hearth.computer",            # computer-use: ctypes mouse/keyboard/drag
    "hearth.desktop_a11y",        # desktop a11y snapshot/click/type
    "hearth.team",                # watch-a-team multi-agent panes
    "hearth.voice_overlay",       # desktop voice HUD (win32 dot grid)
    # Desktop control needs the UI Automation COM bridge bundled, or
    # desktop_snapshot/click/type fail in the packaged exe with ImportError.
    # (comtypes.gen is generated at runtime — collect_all("comtypes") below
    # pulls what's needed; don't list it here or PyInstaller warns.)
    "uiautomation", "comtypes", "comtypes.client",
    # RealtimeSTT + silero VAD for "ChatGPT voice mode" feel
    "RealtimeSTT", "silero_vad",
    # llama_cpp.server's runtime extras — without these the builtin
    # server exits code 1 with ModuleNotFoundError. Belt + suspenders
    # with collect_all("llama_cpp") below.
    "fastapi", "uvicorn", "sse_starlette",
    "pydantic_settings", "starlette_context",
    # CUDA runtime wheels (we ship them via pip so users without CUDA
    # Toolkit installed can still boot the CUDA build of llama_cpp).
    "nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cuda_nvrtc",
]

# Browser control (optional dep). Bundle the Playwright python package + its node
# driver so the PACKAGED GUI actually has the browse tools (otherwise Playwright
# isn't found in the frozen app, the browse tools gate OUT, and the model falls
# back to open_in_browser — the "it won't browse in the .exe" bug). We use
# channel="chrome" (the user's real Chrome), so we deliberately do NOT bundle the
# ~170MB Chromium browser binary — only the lightweight driver is needed to drive
# the installed Chrome. NOTE: a Playwright-in-PyInstaller bundle must be verified
# by actually running the rebuilt exe's browse — it can't be confirmed statically.
PW_BINARIES = []
try:
    from PyInstaller.utils.hooks import collect_all as _collect_all
    _pw_datas, _pw_bins, _pw_hidden = _collect_all("playwright")
    DATAS += _pw_datas
    PW_BINARIES += _pw_bins
    HIDDEN += _pw_hidden + ["playwright", "playwright.sync_api", "hearth.browse"]
except Exception:
    pass

# pythonnet + clr_loader power pywebview's Windows (EdgeChromium/winforms)
# backend. collect_all is REQUIRED — without Python.Runtime.dll + the clr_loader
# .NET shim + their data, the frozen GUI dies with "Failed to resolve
# Python.Runtime.Loader.Initialize" (the crash a fresh machine hits). The window
# also falls back to the browser at runtime if this still can't init, but bundle
# it so the NATIVE window works when the machine has .NET.
for _pkg in ("pythonnet", "clr_loader"):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass
HIDDEN += ["clr", "pythonnet", "clr_loader", "clr_loader.netfx", "clr_loader.ffi"]

# Built-in LLM server (optional dep). Bundle llama-cpp-python so the PACKAGED
# Hearth.exe can run its own OpenAI-compatible server with no LM Studio / no
# Ollama / no extra installs - the "self-contained" story. We do NOT bundle a
# GGUF; the Models tab downloads one from Hugging Face on first run, picked
# to fit the user's VRAM. NOTE: this can balloon the bundle by 50-400 MB
# depending on whether the user installed a CPU or CUDA wheel. Verified by
# running the rebuilt exe -> Models tab -> "Use this" path.
if not LITE:
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _llc_datas, _llc_bins, _llc_hidden = _collect_all("llama_cpp")
        DATAS += _llc_datas
        PW_BINARIES += _llc_bins
        HIDDEN += _llc_hidden + ["llama_cpp", "llama_cpp.server", "hearth.llmserver"]
        # Belt-and-suspenders: collect_all can miss the lib/ DLLs (llama.dll,
        # ggml*.dll) on some llama-cpp-python wheels. Force them in explicitly,
        # preserving the llama_cpp/lib/ layout the loader expects.
        from PyInstaller.utils.hooks import collect_dynamic_libs as _cdl
        PW_BINARIES += _cdl("llama_cpp")
    except Exception:
        pass

# CUDA runtime DLLs — bundle the nvidia-cuda-runtime-cu12 / nvidia-cublas-cu12 /
# nvidia-cuda-nvrtc-cu12 pip wheels so llama.dll can find cudart64_12.dll +
# cublas64_12.dll + nvrtc64_120_0.dll at runtime even on machines without
# CUDA Toolkit installed. Without this, the builtin LLM 401's silently in
# the frozen exe (the "could not find module" loader error that bit the
# user before round 4). hearth/__init__.py adds the wheel bin dirs to the
# DLL path on import.
for _pkg in (() if LITE else ("nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cuda_nvrtc")):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# llama_cpp.server runtime extras — fastapi / uvicorn / sse-starlette /
# pydantic-settings / starlette-context. The prebuilt llama-cpp-python wheel
# DOES NOT bundle these and llama_cpp.server crashes with
# ModuleNotFoundError without them. Collecting eagerly so PyInstaller's
# tree-shaker can't drop them.
for _pkg in (() if LITE else ("fastapi", "uvicorn", "sse_starlette",
             "pydantic_settings", "starlette_context")):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# Real-time voice (RealtimeSTT + silero VAD). Without collecting their
# data files, the silero ONNX model is missing at runtime and the recorder
# silently falls back to webrtc VAD (laggier endpoint).
for _pkg in ("RealtimeSTT", "silero_vad"):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# Desktop control (Windows). uiautomation drives UI Automation through comtypes,
# which GENERATES its interface modules into comtypes.gen at runtime — a bare
# hiddenimport misses those, so collect_all both. Skipped off Windows (try/except),
# where the cross-OS backends use pynput / AT-SPI instead of these.
for _pkg in ("uiautomation", "comtypes"):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# Phone bridges (optional). Each is imported lazily inside its bridge module,
# so static analysis never sees them. The GUI spawns them via the
# `--hearth-run-python -m hearth.<x>_bridge` sentinel — without collecting the
# package the frozen bridge dies on import. Discord = discord.py; WhatsApp =
# neonize (+ segno for the QR). Telegram is pure-stdlib (urllib), nothing to add.
# Missing packages are skipped (the try/except), so a venv without one is fine.
for _pkg in ("discord", "neonize", "segno"):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# Skill deps that ship binaries / data files and are imported ONLY by
# model-written skill scripts (so static analysis never sees them):
#   - pymupdf (fitz): compiled mupdf binaries — pdf-tools split/merge/search.
#   - pyfiglet: .flf font data files — make-ascii banners.
#   - matplotlib: mpl-data (fonts + matplotlibrc) — make-pdf/make-pptx charts.
#     A bare hiddenimport bundles the package but NOT mpl-data, so import
#     fails at runtime ("Could not find the matplotlib data files").
# Bare hiddenimports aren't enough; collect_all pulls the binaries + fonts.
for _pkg in ("pymupdf", "pyfiglet", "matplotlib"):
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        _d, _b, _h = _collect_all(_pkg)
        DATAS += _d
        PW_BINARIES += _b
        HIDDEN += _h
    except Exception:
        pass

# Stamp the edition (full/lite) into the bundle so the running app can SHOW the
# user which build they have (Lite ships without the built-in llama.cpp server).
# hearth/edition.py reads this marker from sys._MEIPASS at runtime.
_edition_marker = os.path.join(REPO_ROOT, "_hearth_edition.txt")
with open(_edition_marker, "w", encoding="utf-8") as _ef:
    _ef.write("lite" if LITE else "full")
DATAS += [(_edition_marker, ".")]

# ============================================================
# Analysis (shared imports across both entry points)
# ============================================================

a_tray = Analysis(
    [os.path.join(REPO_ROOT, "_launch_tray.py")],
    pathex=[REPO_ROOT],
    binaries=PW_BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN + collect_submodules("hearth"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "tcl", "tk", "scipy", "torch"],
    cipher=block_cipher,
    noarchive=False,
)
pyz_tray = PYZ(_unfreeze_hearth(a_tray).pure, a_tray.zipped_data, cipher=block_cipher)
exe_tray = EXE(
    pyz_tray,
    a_tray.scripts,
    [],
    exclude_binaries=True,
    name="Hearth",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # GUI — no console window
    disable_windowed_traceback=False,
    icon=ICON_PATH,
    version=VERSION_PATH,
    onefile=False,
)

a_cli = Analysis(
    [os.path.join(REPO_ROOT, "_launch_cli.py")],
    pathex=[REPO_ROOT],
    binaries=PW_BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN + collect_submodules("hearth"),
    excludes=["tkinter", "tcl", "tk", "scipy", "torch"],
    cipher=block_cipher,
    noarchive=False,
)
pyz_cli = PYZ(_unfreeze_hearth(a_cli).pure, a_cli.zipped_data, cipher=block_cipher)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="Hearth-cli",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # CLI — keep the console
    icon=ICON_PATH,
    version=VERSION_PATH,
    onefile=False,
)

# Third entry point: the desktop window subprocess the tray spawns.
a_window = Analysis(
    [os.path.join(REPO_ROOT, "_launch_window.py")],
    pathex=[REPO_ROOT],
    binaries=PW_BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN + collect_submodules("hearth"),
    excludes=["tkinter", "tcl", "tk", "scipy", "torch"],
    cipher=block_cipher,
    noarchive=False,
)
pyz_window = PYZ(_unfreeze_hearth(a_window).pure, a_window.zipped_data, cipher=block_cipher)
exe_window = EXE(
    pyz_window,
    a_window.scripts,
    [],
    exclude_binaries=True,
    # Underscore-prefix signals "internal helper, don't double-click me".
    # This exe is only meant to be spawned by Hearth.exe when the user
    # clicks "Open Hearth" in the tray menu.
    name="_HearthWindow",
    debug=False,
    strip=False,
    upx=False,
    console=False,        # GUI subprocess — no console
    icon=ICON_PATH,
    version=VERSION_PATH,
    onefile=False,
)

# LITE: torch here is the CPU build and llama_cpp is dropped, so the ~900 MB of
# nvidia CUDA wheels that a hook (ctranslate2 / leftover site-packages) drags in
# are dead weight — nothing in a LITE bundle loads them. Strip every nvidia/*
# entry from each Analysis's binaries + datas. The FULL build keeps them: its
# bundled CUDA llama.cpp server needs cudart/cublas/nvrtc at runtime.
if LITE:
    def _strip_nvidia(toc):
        kept = []
        for entry in toc:
            parts = entry[0].lower().replace("\\", "/").split("/")
            if "nvidia" in parts:
                continue
            kept.append(entry)
        return kept
    for _a in (a_tray, a_cli, a_window):
        _a.binaries = _strip_nvidia(_a.binaries)
        _a.datas = _strip_nvidia(_a.datas)

# Collect all three into one folder so the user gets one drop-in dist/
coll = COLLECT(
    exe_tray, exe_cli, exe_window,
    a_tray.binaries, a_tray.zipfiles, a_tray.datas,
    a_cli.binaries, a_cli.zipfiles, a_cli.datas,
    a_window.binaries, a_window.zipfiles, a_window.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Hearth",
)
