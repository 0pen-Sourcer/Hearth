<#
.SYNOPSIS
    One-shot installer for Hearth. Creates a venv, installs deps, builds the
    ~/Jarvis workspace, optionally downloads voice models, and prints next steps.

.DESCRIPTION
    Idempotent - re-running is safe. Skip optional steps with switches.

.PARAMETER NoVoice
    Skip Kokoro TTS voice model download (~150MB). You can grab it later.

.PARAMETER NoSTT
    Skip the faster-whisper STT install. Voice INPUT (/listen) won't work.

.PARAMETER NoMCP
    Skip installing the mcp SDK (only needed if you use LM Studio's chat UI bridge).

.PARAMETER NoFileReaders
    Skip pypdf / python-docx / openpyxl / python-pptx. Without them, read_file
    on PDF/DOCX/XLSX/PPTX returns an "install X" hint instead of extracting text.

.PARAMETER NoDesktop
    Skip pywebview. Without it, 'python -m hearth.desktop' falls back to your
    default browser instead of opening a native window.

.PARAMETER Browser
    ALSO install Playwright + Chromium (~150MB) so the browse/browse_click tools
    work (the "AI web browser"). Off by default - it's a heavy, optional extra.

.PARAMETER VoiceDevice
    'cpu' (recommended), 'gpu' (CUDA - installs onnxruntime-gpu, but steals VRAM
    from your LLM), or 'ask' (default - prompts you during install).

.PARAMETER NoRealtimeVoice
    Skip the real-time voice loop (RealtimeSTT + silero-vad). Without it,
    voice-mode falls back to press-to-talk recording. Realtime adds ~30MB.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Browser -BuiltinLLM cuda -VoiceDevice gpu
    .\install.ps1 -NoVoice -NoSTT
#>

[CmdletBinding()]
param(
    [switch]$NoVoice,
    [switch]$NoSTT,
    [switch]$NoMCP,
    [switch]$NoFileReaders,
    [switch]$NoDesktop,
    [switch]$Browser,
    [string]$BuiltinLLM = '',   # 'cuda' / 'cpu' / '' (off). EXPERIMENTAL: installs llama-cpp-python so Hearth runs its own LLM server. Tool-call reliability still lags a dedicated runner like LM Studio - BYO is recommended for daily use.
    [ValidateSet('cpu','gpu','ask')][string]$VoiceDevice = 'ask',
    [switch]$NoRealtimeVoice
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # silences Invoke-WebRequest's slow PS5 progress bar

# ----- Colors --------------------------------------------------------------
function Write-Step    ($msg) { Write-Host ""; Write-Host "==> " -NoNewline -ForegroundColor Cyan;  Write-Host $msg -ForegroundColor White }
function Write-OK      ($msg) { Write-Host "   ok  " -NoNewline -ForegroundColor Green; Write-Host $msg }
function Write-Skip    ($msg) { Write-Host "   skip " -NoNewline -ForegroundColor DarkGray; Write-Host $msg -ForegroundColor DarkGray }
function Write-WarnX   ($msg) { Write-Host "   warn " -NoNewline -ForegroundColor Yellow; Write-Host $msg -ForegroundColor Yellow }
function Write-FailMsg ($msg) { Write-Host "   FAIL " -NoNewline -ForegroundColor Red; Write-Host $msg -ForegroundColor Red }

# ----- Banner --------------------------------------------------------------
Write-Host ""
Write-Host "  Hearth installer" -ForegroundColor Cyan
Write-Host "  Local AI for your machine. It talks. It listens. It actually does things." -ForegroundColor DarkGray
Write-Host ""

# ----- 0. Pre-flight: Python ----------------------------------------------
Write-Step "Checking Python 3.11+"
$py = $null
# Try the Windows Python launcher (auto-picks installed version), then plain python/python3.
# We invoke each with --version and regex-match the output. No cmd /c, no nested quoting.
$candidates = @('py', 'python', 'python3')
foreach ($exe in $candidates) {
    try {
        $verOut = (& $exe --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $verOut -match 'Python\s+(\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $py = $exe
                Write-OK "found '$exe' -> Python $major.$minor"
                break
            }
        }
    } catch {}
}
if (-not $py) {
    Write-FailMsg "Python 3.11+ not found. Install from https://python.org/downloads (check 'Add to PATH')."
    exit 1
}

# ----- 1. Repo layout sanity ----------------------------------------------
Write-Step "Checking repo layout"
$root = $PSScriptRoot
foreach ($must in @('hearth', 'hearth_cli.py', 'hearth.bat')) {
    $p = Join-Path $root $must
    if (-not (Test-Path $p)) {
        Write-FailMsg "missing $must in $root - are you running install.ps1 from the repo root?"
        exit 1
    }
}
Write-OK "repo looks complete"

# ----- 2. Create / reuse venv ----------------------------------------------
Write-Step "Creating virtualenv at .venv"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Skip "already exists - reusing"
} else {
    & $py -m venv "$root\.venv"
    if ($LASTEXITCODE -ne 0) { Write-FailMsg "venv creation failed"; exit 1 }
    Write-OK "created"
}

# ----- 3. Install dependencies ---------------------------------------------
Write-Step "Upgrading pip"
& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-WarnX "pip self-upgrade failed; continuing" }

Write-Step "Installing required deps (openai, prompt_toolkit, psutil, pillow, truststore, plyer)"
& $venvPython -m pip install --quiet openai prompt_toolkit psutil pillow truststore plyer
if ($LASTEXITCODE -ne 0) { Write-FailMsg "core deps failed to install"; exit 1 }
Write-OK "installed"

if (-not $NoVoice) {
    Write-Step "Installing voice-OUT deps (kokoro-onnx, sounddevice, numpy)"
    & $venvPython -m pip install --quiet kokoro-onnx sounddevice numpy
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "voice-OUT deps failed - TTS won't work, but the rest will. Re-run with -NoVoice to silence this warning."
    } else {
        Write-OK "installed"
    }

    # --- Voice device: CPU (default) or GPU. CPU is recommended because TTS/STT
    #     on GPU steals VRAM from your LLM (an 8GB card wants every MB for the
    #     model). GPU only makes sense if you have headroom. ---
    $dev = $VoiceDevice
    if ($dev -eq 'ask') {
        Write-Host "   Run voice (TTS + STT) on CPU or GPU?" -ForegroundColor DarkGray
        Write-Host "   CPU is recommended - GPU voice competes with your LLM for VRAM. [C/g]: " -NoNewline -ForegroundColor DarkGray
        try { $ans = Read-Host } catch { $ans = "" }
        $dev = if ($ans -match '^[gG]') { 'gpu' } else { 'cpu' }
    }
    if ($dev -eq 'gpu') {
        Write-Step "Configuring voice for GPU (CUDA) + installing onnxruntime-gpu"
        & $venvPython -m pip install --quiet onnxruntime-gpu
        [Environment]::SetEnvironmentVariable("JARVIS_TTS_DEVICE", "cuda", "User")
        [Environment]::SetEnvironmentVariable("JARVIS_STT_DEVICE", "cuda", "User")
        Write-OK "voice set to GPU (JARVIS_TTS_DEVICE / JARVIS_STT_DEVICE = cuda, user env). Reopen your terminal for it to take effect."
    } else {
        [Environment]::SetEnvironmentVariable("JARVIS_TTS_DEVICE", "cpu", "User")
        [Environment]::SetEnvironmentVariable("JARVIS_STT_DEVICE", "cpu", "User")
        Write-OK "voice set to CPU (recommended - no VRAM contention with your LLM)."
    }
} else {
    Write-Skip "voice-OUT (-NoVoice passed)"
}

if (-not $NoSTT) {
    Write-Step "Installing voice-IN deps (faster-whisper)"
    & $venvPython -m pip install --quiet faster-whisper
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "faster-whisper failed - /listen won't work. Re-run with -NoSTT to silence."
    } else {
        Write-OK "installed (model downloads on first /listen on, ~150MB)"
    }
} else {
    Write-Skip "voice-IN (-NoSTT passed)"
}

if (-not $NoMCP) {
    Write-Step "Installing MCP SDK (for LM Studio chat bridge)"
    & $venvPython -m pip install --quiet mcp
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "mcp failed - LM Studio chat bridge won't work. Re-run with -NoMCP to silence."
    } else {
        Write-OK "installed"
    }
} else {
    Write-Skip "MCP SDK (-NoMCP passed)"
}

if (-not $NoFileReaders) {
    Write-Step "Installing file-reader deps (pypdf, pypdfium2, python-docx, openpyxl, python-pptx, pymupdf)"
    & $venvPython -m pip install --quiet pypdf pypdfium2 python-docx openpyxl python-pptx pymupdf
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "file-reader deps failed - PDF/DOCX/XLSX/PPTX won't be readable until installed. Re-run with -NoFileReaders to silence."
    } else {
        Write-OK "installed (read_file handles PDF/DOCX/XLSX/PPTX/EPUB/IPYNB/CSV/JSON/HTML/RTF; pdf-tools skill ready)"
    }
    Write-Step "Installing skill deps (reportlab, matplotlib, pyfiglet)"
    & $venvPython -m pip install --quiet reportlab matplotlib pyfiglet
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "skill deps failed - make-pdf/pptx/ascii will need pip install on first use."
    } else {
        Write-OK "installed (make-pdf / make-pptx / make-xlsx / make-ascii skills ready)"
    }
} else {
    Write-Skip "file-reader deps (-NoFileReaders passed)"
}

if (-not $NoDesktop) {
    Write-Step "Installing desktop / tray deps (pywebview, pystray, pillow, pywin32)"
    & $venvPython -m pip install --quiet pywebview pystray pillow pywin32
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "desktop deps failed - native window + tray won't work. Re-run with -NoDesktop to silence."
    } else {
        Write-OK "installed (native window + system tray + Desktop shortcut support)"
    }
} else {
    Write-Skip "desktop app dep (-NoDesktop passed)"
}

if ($Browser) {
    Write-Step "Installing browser-control deps (playwright + Chromium, ~150MB)"
    & $venvPython -m pip install --quiet playwright
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "playwright failed - the browse tools won't work."
    } else {
        & $venvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            Write-WarnX "Chromium download failed - run 'python -m playwright install chromium' later."
        } else {
            Write-OK "installed (browse/browse_click/browse_type now work - a real Chromium you can watch)"
        }
    }
} else {
    Write-Skip "browser control (pass -Browser to enable the 'AI web browser' tools)"
}

if ($BuiltinLLM) {
    $variant = $BuiltinLLM.ToLower().Trim()
    if ($variant -eq 'cuda' -or $variant -eq 'gpu') {
        Write-Step "Installing llama-cpp-python (CUDA 12.4 prebuilt wheel - ~460MB) so Hearth has its own LLM server"
        & $venvPython -m pip install --quiet llama-cpp-python `
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
        if ($LASTEXITCODE -eq 0) {
            # CRITICAL: the prebuilt CUDA wheel links against cudart64_12.dll +
            # cublas64_12.dll + nvrtc64_120_0.dll at runtime. If the user does
            # NOT have CUDA Toolkit installed system-wide, llama.dll will fail
            # to load with "could not find module" before this step we ship
            # the runtime via pip wheels (~750MB total) so any RTX user works
            # out of the box. hearth/__init__.py adds these to the DLL path.
            Write-Step "Installing CUDA 12 runtime DLLs (cudart + cublas + nvrtc, ~750MB) - so llama.cpp loads on machines without CUDA Toolkit"
            & $venvPython -m pip install --quiet `
                nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cuda-nvrtc-cu12
            if ($LASTEXITCODE -ne 0) {
                Write-WarnX "CUDA runtime wheels failed to install - the built-in LLM may not load on this machine."
                Write-WarnX "Retry: pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cuda-nvrtc-cu12"
            }
            # llama-cpp-python's prebuilt wheels do NOT include the [server]
            # extras (fastapi/uvicorn/sse-starlette/starlette-context). Without
            # them, `python -m llama_cpp.server` exits code 1 with ModuleNotFoundError.
            Write-Step "Installing llama_cpp.server extras (fastapi, uvicorn, sse-starlette, starlette-context, pydantic-settings)"
            & $venvPython -m pip install --quiet `
                fastapi "uvicorn[standard]" sse-starlette pydantic-settings starlette-context
            if ($LASTEXITCODE -ne 0) {
                Write-WarnX "server extras failed - the built-in LLM HTTP server won't boot."
                Write-WarnX "Retry: pip install fastapi 'uvicorn[standard]' sse-starlette pydantic-settings starlette-context"
            }
        }
    } elseif ($variant -eq 'cpu') {
        Write-Step "Installing llama-cpp-python (CPU prebuilt wheel - ~50MB) so Hearth has its own LLM server"
        & $venvPython -m pip install --quiet llama-cpp-python `
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
        if ($LASTEXITCODE -eq 0) {
            Write-Step "Installing llama_cpp.server extras (fastapi, uvicorn, sse-starlette, starlette-context, pydantic-settings)"
            & $venvPython -m pip install --quiet `
                fastapi "uvicorn[standard]" sse-starlette pydantic-settings starlette-context
            if ($LASTEXITCODE -ne 0) {
                Write-WarnX "server extras failed - the built-in LLM HTTP server won't boot."
            }
        }
    } else {
        Write-WarnX "unknown -BuiltinLLM value '$BuiltinLLM' (use 'cuda' or 'cpu'). Skipping."
        $variant = ''
    }
    if ($variant -and $LASTEXITCODE -ne 0) {
        Write-WarnX "llama-cpp-python install failed. You can retry with:"
        Write-WarnX "  pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/$( if ($variant -eq 'cuda' -or $variant -eq 'gpu') { 'cu124' } else { 'cpu' } )"
    } elseif ($variant) {
        Write-OK "installed (Hearth's Models tab can now download a GGUF and run a built-in server - no LM Studio needed)"
    }
} else {
    Write-Skip "built-in LLM server (pass -BuiltinLLM cuda or -BuiltinLLM cpu to install llama-cpp-python and skip LM Studio entirely)"
}

if (-not $NoRealtimeVoice) {
    Write-Step "Installing real-time voice deps (RealtimeSTT + silero-vad)"
    & $venvPython -m pip install --quiet RealtimeSTT silero-vad
    if ($LASTEXITCODE -ne 0) {
        Write-WarnX "RealtimeSTT/silero-vad failed - voice mode will fall back to press-to-talk. Re-run with -NoRealtimeVoice to silence."
    } else {
        Write-OK "installed (voice-mode now does live captions + silero VAD - silero model auto-downloads on first use, ~2MB)"
    }
} else {
    Write-Skip "real-time voice (-NoRealtimeVoice passed)"
}

# ----- 4. Workspace --------------------------------------------------------
Write-Step "Setting up ~/Jarvis workspace"
$workspace = if ($env:JARVIS_WORKSPACE) { $env:JARVIS_WORKSPACE } else { Join-Path $HOME 'Jarvis' }
foreach ($sub in @('', 'memory', 'logs', 'voices', 'screenshots')) {
    $p = if ($sub) { Join-Path $workspace $sub } else { $workspace }
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}
$rulesPath = Join-Path $workspace 'rules.md'
if (-not (Test-Path $rulesPath)) {
    $rulesText = @'
# Your house rules for Jarvis

This file is re-read every turn. Add anything you want him to always do or
never do. Examples:

- Don't ever lecture me about NSFW topics.
- Open URLs in Brave with profile "Default" by default.
- My nickname is <whatever> - call me that, not "user".

Edit freely; no restart needed.
'@
    [System.IO.File]::WriteAllText($rulesPath, $rulesText, (New-Object System.Text.UTF8Encoding $false))
    Write-OK "workspace created at $workspace (with starter rules.md)"
} else {
    Write-Skip "workspace already exists at $workspace"
}

# ----- 5. Optional: download Kokoro voice models --------------------------
if (-not $NoVoice) {
    Write-Step "Voice model files for Kokoro TTS"
    $voicesDir = Join-Path $workspace 'voices'
    $onnxPath  = Join-Path $voicesDir 'kokoro-v1.0.onnx'
    $binPath   = Join-Path $voicesDir 'voices-v1.0.bin'

    if ((Test-Path $onnxPath) -and (Test-Path $binPath)) {
        Write-Skip "voice models already present"
    } else {
        # Use the regular v1.0 .onnx (not fp16) - better cross-compat with the
        # current kokoro_onnx Python package + matching voices-v1.0.bin.
        # If you specifically want the smaller fp16, swap the URL to the
        # `.fp16.onnx` variant from the same release.
        $onnxUrl = 'https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/kokoro-v1.0.onnx'
        $binUrl  = 'https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/voices-v1.0.bin'

        Write-Host "   Voice download is ~150MB. Press Enter to download, or Ctrl-C to skip." -ForegroundColor DarkGray
        try { [void](Read-Host) } catch { Write-Skip "non-interactive; skipping voice download"; $skip = $true }

        if (-not $skip) {
            try {
                if (-not (Test-Path $onnxPath)) {
                    Write-Host "   downloading kokoro-v1.0.onnx ..." -ForegroundColor DarkGray
                    Invoke-WebRequest -Uri $onnxUrl -OutFile $onnxPath -UseBasicParsing
                }
                if (-not (Test-Path $binPath)) {
                    Write-Host "   downloading voices-v1.0.bin ..." -ForegroundColor DarkGray
                    Invoke-WebRequest -Uri $binUrl -OutFile $binPath -UseBasicParsing
                }
                Write-OK "voice models in $voicesDir"
            } catch {
                Write-WarnX "voice download failed: $($_.Exception.Message)"
                Write-Host "         Drop the files manually from:" -ForegroundColor DarkGray
                Write-Host "         https://github.com/thewh1teagle/kokoro-onnx/releases" -ForegroundColor DarkGray
                Write-Host "         into: $voicesDir" -ForegroundColor DarkGray
            }
        }
    }
}

# ----- 6. LM Studio detection ---------------------------------------------
Write-Step "Looking for LM Studio"
$lmsRunning = $false
try {
    $r = Invoke-WebRequest -Uri 'http://localhost:1234/v1/models' -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $lmsRunning = $true }
} catch {}

if ($lmsRunning) {
    Write-OK "LM Studio is running on :1234 (perfect)"
} elseif ($BuiltinLLM) {
    # User opted into the built-in llama-cpp server with -BuiltinLLM. They
    # don't need LM Studio at all, so warning about it not running is just
    # noise that makes the install look broken when it isn't.
    Write-OK "Built-in LLM server installed. Pick a GGUF from the Models tab to boot it."
} else {
    $likelyPaths = @(
        "$env:LOCALAPPDATA\Programs\LM Studio\LM Studio.exe",
        "$env:LOCALAPPDATA\LM Studio\LM Studio.exe",
        "$env:ProgramFiles\LM Studio\LM Studio.exe"
    )
    $installed = $likelyPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($installed) {
        Write-WarnX "LM Studio installed but not serving on :1234. Open it, load a chat model, click 'Start Server'."
    } else {
        Write-WarnX "LM Studio not detected. Install from https://lmstudio.ai (or re-run with -BuiltinLLM cuda to use Hearth's built-in server, or point LOCAL_API_BASE at any OpenAI-compatible endpoint)."
    }
}

# ----- 7. All done ---------------------------------------------------------
Write-Host ""
Write-Host "  Hearth is ready." -ForegroundColor Green
Write-Host ""
Write-Host "  Next:" -ForegroundColor White
Write-Host "    1. Make sure LM Studio (or your OpenAI-compatible server) is running" -ForegroundColor DarkGray
Write-Host "    2. Run:  " -NoNewline -ForegroundColor DarkGray
Write-Host ".\hearth.bat" -ForegroundColor Cyan
Write-Host "    3. Try:  /voice on  (TTS) or  /listen on  (mic input)" -ForegroundColor DarkGray
Write-Host "    4. Edit your house rules anytime:  $rulesPath" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  If something didn't work, the warnings above told you what." -ForegroundColor DarkGray
Write-Host ""
