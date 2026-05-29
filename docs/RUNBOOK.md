# Hearth Runbook — how to run it, what's safe, what to publish

The one-page "I forgot what does what" reference. (Private notes live in CLAUDE.md / IDEAS.md.)

---

## 1. Ways to run it

| Command | What it is | When |
|---|---|---|
| `.\hearth.bat` | **CLI on your local model** (LM Studio). The daily driver. | Normal use. Start LM Studio + load a model first. |
| `.\gemini.ps1` | CLI on **Gemini** (cloud). | Want a smarter model. Key baked in, gitignored. |
| `.\grok.ps1` | CLI on **xAI/Grok** (cloud). | Best for hard multi-step + browser driving. |
| `python -m hearth.headless --prompt "..." --format text` | **Bridge** — one prompt, no UI, prints events. | Scripts, testing, automation. |
| `python -m hearth.mcp_server` | **MCP** — tools as cards inside LM Studio's chat. | Drive from LM Studio's own UI. |
| `python -m hearth.tui` | **TUI** (Textual). Preview. | Optional; the CLI is more polished. |
| `python -m hearth.web` / `python -m hearth.tray` | **Web UI / desktop** (v0.6 preview). | Optional GUI. |

All of these are **safe to run** — they're Hearth itself. The risk isn't the launcher; it's what tools the agent calls (see §3).

---

## 2. Optional features (install once to enable)

| Feature | Enable it | Notes |
|---|---|---|
| **Voice out (TTS)** | `pip install kokoro-onnx sounddevice numpy` | Auto-downloads the voice model on first `/voice on`. |
| **Voice in (STT)** | `pip install faster-whisper` | Auto-downloads on first `/listen`. |
| **Browser control** | `pip install playwright` then `python -m playwright install chromium` | Enables `browse`/`browse_click`/etc. Opens a real Chromium you watch. |
| **Image generation** | set `HEARTH_ENABLE_FORGE=1` | Needs a local Forge/SD WebUI. Off by default. |

Missing an optional dep = the related tool returns a clear install hint, never a crash.

---

## 3. Safety — what's gated

- **Writes/deletes/moves are confined to `~/Jarvis/`** (+ any `/allow <path>`). A write outside is refused.
- **Reads are open** (the agent needs to know your machine). Set `JARVIS_LOCKDOWN=1` to confine reads too.
- **Risky tools prompt `[y]es / [n]o / [a]lways / [N]ever`** before running: `write_file`, `edit_file`, `delete_path`, `move_path`, `create_directory`, `run_command`, `open_app`, `open_url`, `open_in_browser`, `create_plugin`, `delete_plugin`, `browse_click`, `browse_type`, `disk_usage`, `forge_*`. At the prompt you can also **type an instruction** ("use Brave instead") and it's handed to the model.
- **`run_command` refuses** destructive commands (`rm -rf`, `format`, `Remove-Item`, registry nukes, process kills) AND blocking ones (`timeout`/`sleep`/`pause`/`Read-Host`) unless you've set auto-approve.
- **`JARVIS_AUTO_APPROVE=1`** skips all the prompts. Only set it once you trust a session (the bridge uses it by default for automation).
- **Plugins** the agent writes run local Python (same trust level as `run_command`) — that's why `create_plugin` is gated.

---

## 4. What to PUBLISH vs keep PRIVATE (read before `git push`)

**Publish (the actual project):** `hearth/`, `README.md`, `LICENSE`, `CHANGELOG.md`, `install.ps1`, `hearth.bat`, `*.spec`/`build.bat`, `docs/` (except private ones), `.github/`, `CONTRIBUTING.md`, `SECURITY.md`.

**NEVER publish (already gitignored — verify before pushing):**
- **Secrets:** `gemini.ps1`, `grok.ps1`, `*-secret.ps1`, `.env` — they hold raw API keys.
- **Private notes:** `CLAUDE.md`, `IDEAS.md`, `run*.txt`, `dexter.txt`, `QUICKSTART.md`, `claude_battleplan_*.md`.
- **Other people's repos:** `openclaw-main/`, `claude-code-main/`, `hermes-agent-main/`, `openhuman-main/`, `superpowers-main/`, `skill-creator/`, `awesome-openclaw-skills-main/`, `oh-my-openagent-dev/`, `graphify-7/`, `Windows Terminal/` — scavenged for ideas, **not ours to redistribute**.
- **Your data / build junk:** `~/Jarvis/` (memory, logs, voices, screenshots), `.venv/`, `build/`, `dist/`, `bench/reports/`.

⚠ **Rotate the API keys** that have appeared in chat (the Gemini key and the xAI key) at their consoles before/around launch — they're recoverable from logs.

Sanity check before pushing: `git status` should show ONLY Hearth's own files. If you see any `*-main/` folder or a `.ps1` with a key, stop.

---

## 5. Pre-launch checklist

- [ ] Record the **demo GIF** → `docs/demo.gif` (the README's centerpiece). Use local Hermes, the "find + play a movie" prompt.
- [ ] `git init` → `git add .` → **check `git status` against §4** → commit.
- [ ] `gh repo create hearth --public --source=. --push` (you drive this).
- [ ] Tag `v0.5.x`, cut a GitHub Release.
- [ ] README renders correctly on github.com.
- [ ] Post **Tue/Wed 9–11 AM US Eastern** on r/LocalLLaMA (weekends die). Drafts in `docs/LAUNCH_POSTS.md`.

---

## 6. Env vars (quick reference)

| Variable | Default | Purpose |
|---|---|---|
| `LOCAL_API_BASE` | `http://localhost:1234/v1` | OpenAI-compatible endpoint (local or cloud) |
| `LOCAL_API_KEY` | (dummy for local) | API key for cloud endpoints |
| `LOCAL_MODEL` | auto-detect | Pin the model id |
| `JARVIS_CONTEXT` | auto-detect | Context window (tokens) |
| `JARVIS_AUTO_APPROVE` | `0` | `1` = skip risky-tool prompts |
| `JARVIS_LOCKDOWN` | `0` | `1` = confine reads to workspace |
| `JARVIS_VOICE` / `JARVIS_VOICE_SPEED` | `am_michael` / `1.5` | TTS voice + rate |
| `HEARTH_ENABLE_FORGE` | `0` | `1` = load the image-gen tools |
| `HEARTH_BROWSE_HEADLESS` | `0` | `1` = hidden browser (default: visible, you watch) |
| `HEARTH_BROWSE_SLOWMO` | `600` | ms between browser actions (lower = faster) |
| `HEARTH_MAX_TURNS` | `25` | hard ceiling on agent tool-loop turns |

Anywhere `JARVIS_*` works, `HEARTH_*` does too.
