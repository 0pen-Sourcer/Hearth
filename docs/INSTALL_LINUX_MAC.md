# Running Hearth on macOS / Linux

Hearth ships a one-click installer for Windows. On macOS and Linux you run it
from source — the CLI and web UI are fully usable, and most tools work the same
way they do on Windows. A few Windows-specific conveniences (desktop shortcuts,
Start-Menu app launching, the registry app list) either fall back to a POSIX
equivalent or are skipped; nothing crashes when they're unavailable.

> Status: macOS/Linux is **experimental** — it runs, but it hasn't had the same
> mileage as Windows. If something breaks, please open an issue with the error
> and your OS/distro.

## Requirements

- Python 3.11 or newer
- `git`
- A local model server (LM Studio, Ollama, llama.cpp, …) **or** a cloud API key —
  same as on Windows.

## Install

```bash
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
# CLI
./hearth.sh

# Web UI (opens in your browser at http://127.0.0.1:8765)
python -m hearth.web
```

On first run the CLI walks you through a short setup (agent name, workspace
location, which model to talk to).

## Optional extras

These are only needed for specific features:

| Feature | Install |
| --- | --- |
| Bring-a-window-to-front (`focus_window`) on Linux | `sudo apt install wmctrl` (or `xdotool`). X11 only — not Wayland. |
| Screenshots on Linux | `sudo apt install scrot` (or `gnome-screenshot` / `spectacle`). |
| Native desktop window instead of the browser, on Linux | `pip install pyqtwebengine` (uncomment it in `requirements.txt`). Otherwise the UI opens in your default browser, which works fine. |
| Discord bridge | `pip install discord.py`, then configure it in the web UI under **Settings → Reach from phone**. |
| Browser automation | `python -m playwright install chromium` |

## The built-in model server

The bundled llama.cpp server installs via pip:

```bash
# CPU (works everywhere)
pip install llama-cpp-python

# NVIDIA / CUDA on Linux — see the project's install matrix for the right wheel
# Apple Silicon (Metal) builds with Metal support automatically
```

If you'd rather not run the built-in server, just point Hearth at LM Studio or
Ollama in **Settings → Chat brain** — that path is identical on every OS.

## Notes

- The packaged `.exe` and one-click installer are Windows-only for now. Native
  packaged builds for macOS (`.app`) and Linux (AppImage) are on the roadmap.
- Voice (TTS/STT) depends on system audio libraries; on Linux you may need
  `portaudio` (`sudo apt install portaudio19-dev`) for microphone capture.
