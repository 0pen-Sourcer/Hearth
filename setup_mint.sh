#!/usr/bin/env bash
# Hearth — one-shot Linux setup (Mint / Ubuntu / any apt-based distro).
#
# You're new to Linux — that's fine. Open a terminal IN this folder and run:
#     bash setup_mint.sh
# It creates a Python venv, installs Hearth's deps, and installs the system
# packages the experimental Linux desktop-control (AT-SPI a11y tree) needs.
#
# It does NOT install a local LLM. Easiest is to point Hearth at a cloud brain
# or at LM Studio running on your Windows PC over the LAN — see the end.
set -e

say() { printf '\n\033[1;35m== %s\033[0m\n' "$1"; }

# --- 1. Python + venv ------------------------------------------------------
say "Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it:  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi
python3 --version

# --- 2. System packages ----------------------------------------------------
# python3-gi + gir1.2-atspi-2.0 + at-spi2-core = the accessibility tree Hearth
# reads for desktop_snapshot/click/type on Linux. python3-tk/portaudio help
# voice + some GUI bits. We use the SYSTEM python3-gi (pip's PyGObject needs a
# compiler toolchain), so the venv is created with --system-site-packages so it
# can see it.
say "Installing system packages (needs sudo — it'll ask for your password)"
sudo apt update
sudo apt install -y \
  python3-venv python3-pip python3-dev \
  python3-gi gir1.2-atspi-2.0 at-spi2-core \
  portaudio19-dev libgirepository1.0-dev \
  || echo "(some apt packages failed — desktop a11y / voice may be limited, the rest still works)"

# --- 3. Virtualenv + Python deps ------------------------------------------
say "Creating virtualenv (.venv, with access to system python3-gi)"
python3 -m venv --system-site-packages .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel

say "Installing Hearth Python dependencies"
# On Linux the requirements markers pull pynput (mouse/keyboard) and skip the
# Windows-only uiautomation/comtypes automatically.
pip install -r requirements.txt

say "Fetching the browser engine for browser control (optional, ~150MB)"
python -m playwright install chromium || echo "(playwright browser skipped — 'browse' won't work until you run this)"

# --- 4. Turn on the accessibility bus -------------------------------------
# Cinnamon/GNOME expose the a11y tree only when accessibility is enabled.
say "Enabling the accessibility bus (for desktop_snapshot)"
gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null || true
gsettings set org.cinnamon.desktop.interface toolkit-accessibility true 2>/dev/null || true
echo "If desktop_snapshot finds nothing, log out and back in once so the a11y bus restarts."

# --- 5. How to run ---------------------------------------------------------
say "Done. To run Hearth:"
cat <<'EOF'

  source .venv/bin/activate

  # Point it at a brain. Two easy options:
  #  (a) A cloud brain (paste your key):
  #        export LOCAL_API_BASE="https://api.x.ai/v1"
  #        export LOCAL_API_KEY="<your key>"
  #        export LOCAL_MODEL="grok-4.3"
  #  (b) LM Studio on your Windows PC over the LAN (find its IP with ipconfig):
  #        export LOCAL_API_BASE="http://<windows-ip>:1234/v1"
  #        export LOCAL_MODEL="<loaded model id>"

  # Smoke test the agent loop:
  python -m hearth.headless --prompt "what's my screen resolution?" --format text

  # The big Linux test — desktop a11y. Open Mint's Text Editor or Files FIRST,
  # then (it's experimental, this is what we're verifying):
  python -m hearth.headless --prompt "run desktop_snapshot and list what's in my focused window" --format text

  # Full CLI:
  python hearth_cli.py
EOF
