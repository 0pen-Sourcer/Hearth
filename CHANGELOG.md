# Changelog

Notable changes to Hearth. Format loosely follows [Keep a Changelog](https://keepachangelog.com); semver is not strict pre-1.0.

## v0.7.6-preview

Full notes in [docs/RELEASE_NOTES_v0.7.6.md](docs/RELEASE_NOTES_v0.7.6.md).

### Added
- Team view: live state of every helper from its own transcript, with a room view drawn in layers. People react when clicked, JARVIS moves with the work and can be talked to from the room, helpers get first names, and each one's full log opens from the list. `/team` in the terminal.
- Claude Code as the brain (`hearth/claude_brain.py`): turns run `claude -p` with Hearth's MCP server for tools, the persona appended, one session per chat, and permission questions answered by Hearth through an `approve` tool on its MCP server.
- Codex as the brain (`hearth/codex_brain.py`): turns run `codex exec --json` (found on PATH or inside the Codex desktop app) with Hearth's MCP server attached, the persona as developer instructions, one thread per chat, Codex's sandbox matched to the approval switch, and risky Hearth tools asking Hearth from inside the server. The model menu groups local models and agents, and picking an agent there makes it the brain.
- `delegate`: hand one task to another agent CLI and relay its answer. Twenty-three agent CLIs including Claude Code, Codex, Antigravity, Gemini CLI, Cline and OpenHands, run headless on the user's install; npm shims are started without cmd.exe.
- Approval modes Ask, Auto-edit and Auto, shared by the window (chat bar menu, Shift+Tab), the terminal (/mode), helpers and external agents.
- Appearance: Classic (default), Light, Dark and System, six accents, a hearth mark; tray and window icons follow the choice. Simple and Advanced apply app-wide.
- First-run setup writes its answers to profile.md, asks headphones or speakers, approval mode and look, and animates on springs; `/me` in the terminal.
- `hearth/gpu.py`: load and memory for any vendor's card from DXGI and the GPU performance counters. AMD and Intel cards get llama.cpp's Vulkan build.
- Tool runs show the agents, skills and sites they reached; a running tool counts up on its card.
- Pinned chats.

### Fixed
- Engine install and repair took llama.cpp's "latest" release, which now points at a tag with no Windows files; the newest numbered build is used.
- Repair checks real files, restores skills, art and the engine, and frees the patch backup. The update patch now carries `hearth/**` and `assets/**`.
- The window's private mode forgot the chosen look on every launch; the server writes the saved look into the page.
- The send wrapper dropped its options, so a notification flush ran as an ordinary turn.

### Security
- The local API refuses requests from other sites; a page in any browser tab could read stored keys and call tools.
- Command approval is a per-process token, so model output cannot approve its own call. The destructive-command guard covers PowerShell's aliases and .NET forms, and background jobs share it.
- MCP tools go through the permission prompt and no longer inherit credentials from the environment. `smart_click` and `browse_key` are gated like the other input tools.
- Write confinement compares real paths, so a link inside the workspace cannot carry a write outside it. `grep_search` passes its pattern after `-e` and `--`. `web_fetch` takes http(s) only, so `file://` no longer bypasses read lockdown. CLI `/lockdown` uses the persisted toggle.
- WhatsApp's own-chat mode answers only the linked number's own chat. More key shapes are redacted. The TUI is removed.

### Voice
- Speak and listen in the eight languages the voice bank ships (54 voices); multilingual Whisper sizes and a spoken-language setting.
- Voice sessions end with the recorder shut down, releasing the mic and the Whisper process; each session owns its recorder and callbacks, so reopening voice no longer stalls or stops.
- The mic is saved by name and looked up in the table of the library that opens it; no fallback to a virtual or loopback device.
- The wake word keeps a pre-roll and every block of an utterance, keeps the start of long speech, and releases the mic while voice mode runs.
- Failures in synthesis, playback and transcription are reported. A settings save no longer unloads the TTS engine.

### Agent
- Persona cut from about 10K to 2K tokens and always-on tool descriptions roughly halved. Native tool calls on the standalone llama.cpp engine.
- Deferred tools are all named to the model and selectable by name; every tool has a category.
- Mistral and Command-R turns keep every call; a partly unparsed reply is reported rather than dropped.
- Reminders take an ISO time or a duration, converted by the model, and read back the stored time in words.

### Commands
- `run_command` and `start_job` run in PowerShell, cmd or bash (Git Bash or WSL), with UTF-8 output and exit codes that reflect errors.

### Memory
- Slugs keep non-Latin titles; the index is derived from the fact files and heals itself. Extraction can update a saved fact, archiving the previous version. Supersede and curate act only on exact matches.

### Bridges and teams
- Bridges log to `<workspace>/logs/<channel>_bridge.log` instead of an undrained pipe. WhatsApp sends files; all bridges send quoted paths with spaces.
- Team panes render tool results and failures; `launch_team` reports members that did not start.

### Terminal
- Fixed the first-tool-call crash, the Ctrl+Z spin, `--help`, `/brain lmstudio` and `/brain builtin`, model sync on cloud brains, vision detection on llama.cpp, context-overflow retries and `/history` with images.

### Models, updates and storage
- Measured load progress; downloads that pause, resume, recover from a bad range, keep going with the window closed and pick up after a restart; updates that recover from an interrupted patch; atomic chat, settings and reminder writes; reminders that fire once across processes.

## v0.7.1 to v0.7.5

See [v0.7.3](docs/RELEASE_NOTES_v0.7.3.md), [v0.7.4](docs/RELEASE_NOTES_v0.7.4.md) and [v0.7.5](docs/RELEASE_NOTES_v0.7.5.md).

## v0.7-preview

First public release. Windows-first; macOS/Linux support is partial.

### Interfaces
- CLI with a voice loop, command history, slash-command autocomplete, in-session model control, and a context-usage footer.
- Desktop app and web UI sharing the CLI backend: multi-chat sidebar, a Models tab for downloading and loading GGUFs with per-model load settings, voice mode, file drop, inline permission prompts, and a settings panel.
- Headless bridge (`python -m hearth.headless`) that runs a single prompt non-interactively and emits JSONL events to stdout.
- MCP server (`python -m hearth.mcp_server`) exposing every built-in tool to MCP-aware chat hosts.

### Models
- Runs against any OpenAI-compatible server. Auto-detects LM Studio, Ollama, and llama.cpp at boot.
- Optional bundled llama.cpp server installed via `install.ps1 -BuiltinLLM cuda|cpu`, so no separate LLM app is required. Preview-quality; tool-call reliability lags a dedicated runner.
- Optional cloud endpoints (Gemini, OpenAI, Grok, OpenRouter) selectable in Settings, switchable mid-session without restart.
- Tool-call parser that recognizes Gemma, Hermes, Qwen 2.5/3, Llama 3.x, Mistral, Phi, Granite, and Cohere Command-R formats plus a generic `<function=NAME>` form.

### Tools and capabilities
- File read/write/edit/list/move/delete. `read_file` extracts text from PDF, DOCX, XLSX, PPTX, EPUB, IPYNB, CSV, JSON, HTML, RTF, and `.gz`/`.bz2`/`.xz`. Archive inspect/extract without unpacking. `read_pdf_large` for map-reduce summarization of large PDFs.
- Shell command execution (PowerShell / cmd) with captured, sanitized output.
- App, file, folder, and URL launching with one tool.
- Real browser driving (Chrome / Brave / Edge): open a page, list clickable elements, click, type, scroll, with a persistent session across calls.
- Desktop control (Windows): `desktop_snapshot` reads the focused window's interactive controls via UI Automation (precise, no vision tokens); `desktop_click` / `desktop_type` act on them by element. Plus direct mouse/keyboard control (`computer_move` / `click` / `type` / `key` / `scroll`) that moves the real cursor. State-changing actions go through the permission prompt.
- Screenshot capture and image viewing through a vision-capable model.
- Web search and fetch via DuckDuckGo.
- Persistent per-fact memory with recall tracking, automatic cold-fact archiving and warm-back, and overlap detection on save.
- Reminders (one-shot and recurring) set in natural language, optionally firing a tool when due, with catch-up on next launch for reminders missed while closed (surfaced in the CLI chat, since Windows often suppresses the toast), an optional ntfy phone push, and their own sidebar tab in the desktop app.
- Skills: prose+script workflow bundles the model loads on demand. Built-in skills generate documents (PDF, PPTX, XLSX, diagrams, ASCII art, PDF split/merge). **Shareable**: install a skill from a GitHub repo or local path with `/skill install <source>` (or the `install_skill` tool when a link is pasted in chat). Install discloses the declared tools + shipped scripts and asks before writing; scripts run only through the normal command-permission prompt. Scaffold your own with `/skill new`; the assistant can also author one when it notices a repeated workflow.
- Sub-agents (researcher, coder, archivist, librarian, summarizer, PDF coordinator) with per-agent tool allowlists and a tightened prompt, runnable synchronously or in the background with results delivered on the next turn. Fork depth is bounded.
- Watch-a-team mode (opt-in): spawn several sub-agents at once and open a live terminal pane per agent (Windows Terminal split-panes, or tmux on Linux/macOS) to watch them work side by side.
- Background jobs for long-running operations, returning a job id immediately.
- MCP client: consume other MCP servers via an `mcp.json` in the workspace, surfacing their tools alongside the built-ins.
- Voice: streaming Kokoro text-to-speech and faster-whisper speech-to-text with continuous-listen barge-in and optional wake word. CUDA auto-detect; CPU by default. Preview-quality.
- Self-extending tools: the assistant can author a validated plugin to fill a capability gap; hand-written plugins auto-load from the workspace.
- A `soul.md` self-identity file loaded into every prompt, and end-to-end agent rename (avatar, persona, and workspace folder).
- Optional cloud image/video generation and local Stable Diffusion (Forge) image generation.
- Memory migration from prior agent layouts (API keys are never copied), including pasting a memory dump from ChatGPT/Claude.
- Phone reach: a Telegram bridge (bot token only, no OAuth) to message Hearth from your phone. It runs the full agent on your PC and replies, files included, gated by a chat-id allowlist.
- Opt-in auto-update: check GitHub for a newer release and install (`/update` in the CLI, a button in Settings).
- Optionally launch at login (desktop tray), so reminders fire before you open Hearth.

### Behavior
- Default tool set is trimmed to a core group; niche tools are revealed on demand via a `load_tools` meta-tool to keep the prompt small. `HEARTH_ALL_TOOLS=1` loads everything up front.
- A loop guard detects and breaks repeated, no-progress, or ping-pong tool-call patterns.
- Writes confined to the workspace by default; risky tools prompt for permission per session, and permission decisions persist and are shared between the CLI and the app.
- Procedural local time injected into the system prompt each turn.

## 0.5.0

- CLI as the primary interface, with voice in/out, permission prompts, and context auto-detection.
- Desktop app (native window) and web UI sharing one backend; system tray; optional wake word.
- File reading across PDF/DOCX/XLSX/PPTX/EPUB/IPYNB/CSV/JSON/HTML/RTF and archive inspect/extract.
- First-run onboarding wizard writing answers to memory and house rules.
- Smart file finder scanning non-system drives with kind hints.
- Headless bridge mode emitting JSONL events.

## 0.4.0

- First version cut for public release. Package renamed to `hearth`; the assistant persona remains JARVIS.
- Smart file finder, scoped search guards, voice input via faster-whisper, ambient interrupt, wake-word filter.
- LM Studio context auto-detection.
- Windows installer (`install.ps1`), MIT license, contributing/security docs, and CI smoke tests.
