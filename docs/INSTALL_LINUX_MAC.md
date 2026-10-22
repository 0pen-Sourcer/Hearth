# Running Hearth on macOS / Linux

Hearth ships a one-click installer for Windows. On macOS and Linux you run it
from source. The terminal app and web UI are fully usable, and most tools work the same
way they do on Windows. A few Windows-specific conveniences (desktop shortcuts,
Start-Menu app launching, the registry app list) either fall back to a POSIX
equivalent or are skipped; nothing crashes when they're unavailable.

> Status: the CLI, web UI, tools, skills, the model engine and the image engine
> are verified running on Linux (Mint 22 / Ubuntu 24.04), and the whole test suite
> passes there. macOS runs the same code and the tests on every change, but hasn't
> been tried on a real Mac yet. If something breaks, please open an issue with the
> error and your OS/distro.

## Requirements

- Python 3.11 or newer
- `git`
- A local model server (LM Studio, Ollama, llama.cpp, …) **or** a cloud API key,
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
| Bring-a-window-to-front (`focus_window`) on Linux | `sudo apt install wmctrl` (or `xdotool`). X11 only, not Wayland. |
| Screenshots on Linux | `sudo apt install scrot` (or `gnome-screenshot` / `spectacle`). |
| Native desktop window instead of the browser, on Linux | `pip install pyqtwebengine` (uncomment it in `requirements.txt`). Otherwise the UI opens in your default browser, which works fine. |
| Telegram, Discord or WhatsApp | Set it up in the window under **Settings, Connectors**, which installs what that app needs. See [PHONE.md](PHONE.md). |
| Browser automation | `python -m playwright install chromium` |

## The built-in model server

Open **Models, Engine** in the window and install the engine. Hearth downloads
the official llama.cpp build for your system and card from its releases: CUDA for
an NVIDIA card, Vulkan for AMD and Intel, Metal on Apple silicon, and the CPU
build otherwise. Nothing needs compiling.

If you'd rather not run the built-in server, just point Hearth at LM Studio or
Ollama in **Settings, Chat brain**. That path is identical on every OS.

## Images and video

**Models, Images & video** installs stable-diffusion.cpp the same way and lists
the image models it finds, in `~/.hearth/image-models` or in a ComfyUI or WebUI
models folder you add. Without a local model, images come from a cloud provider
when you have a key for one.

## Notes

- The packaged `.exe` and one-click installer are Windows-only for now. Native
  packaged builds for macOS (`.app`) and Linux (AppImage) are on the roadmap.
- Voice (TTS/STT) depends on system audio libraries; on Linux you may need
  `portaudio` (`sudo apt install portaudio19-dev`) for microphone capture.
