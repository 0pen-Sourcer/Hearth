# Changelog

Notable changes to Inglen (called Hearth through v0.7.5). Format loosely follows [Keep a Changelog](https://keepachangelog.com); semver is not strict pre-1.0.

## v0.7.6-preview

Hearth is now Inglen. Updating keeps your chats, memory, models, folders and settings where they were. A new install calls the assistant Inglen and keeps its files in `~/Inglen`; an older one keeps JARVIS and `~/Jarvis`, and either can be renamed in Settings, Behavior.

The long version, with how each part works, is in [docs/RELEASE_NOTES_v0.7.6.md](docs/RELEASE_NOTES_v0.7.6.md).

### Agent brains under one approval switch
- Claude Code, Codex and Cursor can be the brain, on the plan you already pay for and without an API key. They keep Inglen's tools, memory, voice and persona, and each chat stays one continuing session.
- Any agent that speaks the Agent Client Protocol can be the brain too. OpenCode has been run this way; Copilot CLI, Goose, Grok Build, Gemini CLI, and Pi, Amp and Antigravity through their adapters follow the same protocol.
- Every brain answers to one switch in the chat bar (Shift+Tab cycles it, `/mode` in the terminal). Ask asks before anything that acts on the PC, Auto-edit lets workspace edits through, and Auto runs routine work. The agents' own commands and file changes come back to Inglen to be allowed or asked about, with a diff for each change.
- In Auto, a reviewer reads your own words in the chat and the pending step, never the brain's words or a tool's output, and lets a step through when you asked for exactly that. Power, disk, account and security changes always ask.
- The model menu lists each agent's own models as your install reports them, Think sets how hard the model reasons, and Fast switches Codex's priority tier and Claude Code's fast mode.
- Anthropic's API is spoken natively, with effort levels and streamed thinking. Each turn on a provider shows what it cost; on a plan it shows as plan usage.
- An agent that goes quiet for a minute and a half is reported on every surface with Stop to end it, and a usage limit says when it resets.
- `delegate` hands one task to an installed coding agent and brings back its answer, with adapters for twenty-three of them. Inglen can also read the Claude Code and Codex sessions on the PC and send one a message.
- Ask the assistant what it can do and it checks this PC: engines, models, keys, phone, chat apps, helpers. What's missing it installs itself once you say yes, the image engine and a model that fits your card, a chat model, voice, an agent program, with the progress in Downloads. It can also take back a change it made to your files.

### Helpers and the Team room
- Helpers can run on the brain, on this PC's model, on a saved provider model or on an installed agent, and one sentence can start a mixed team that knows who is on it.
- The Team room shows each helper at a desk in a pixel house by the fire, up to eight to a room across four rooms. Click anyone for who asked, the task, each step and what it changed.
- You can tell a working helper something part-way, and helpers message each other and Inglen.
- An agent working in a git repository gets its own copy, and its changes wait for you to bring them in, discard them or undo them. `race_helpers` gives one task to several agents and compares the results of a check run in each.

### Computer use
- When Inglen drives the desktop, an edge in your accent breathes round the screen and a pill names the action. At the end the edge fades, a chime plays and the window you were in comes back.
- Its pointer is the Inglen mark, on the desktop and in the browser, turning toward where it moves and filling its dot on a click, so its actions read apart from your own mouse.
- A click raises the target window first, apps it opens stay open after the turn, and typing adds to a filled field unless told to replace it.

### The phone, with Inglen Plus
- Plus is a paid add-on with a phone app of its own, in the browser at app.inglen.app or as an Android app. Pair by scanning a QR code in Settings, Plus and checking that both screens show the same six numbers. Messages between the phone and the PC are sealed with the pairing's keys, so the relay in between can't read them.
- On the phone you get your chats with each reply's steps as they happen, Allow and Deny, the brain and approval mode, files and pictures from the PC, the Team room, recent changes with Undo, memory, and the sessions of Claude Code, Codex and other coding agents on the PC, which you can read, carry on or start. The window and the phone follow each other live.
- Notifications for a waiting question (with Allow and Deny on it), a finished reply and a coding agent finishing on the PC.
- Voice calls from the phone, with both sides' words on screen and Mute and End in the notification while the app is away. A spoken yes is enough for what Auto would run; anything riskier takes a tap.
- Inglen can call you, once you turn it on and pick the reasons, such as a reminder coming due or a question left waiting. Never in quiet hours or while you are at the PC. A coding agent can ask for a call when it's done.
- Without Plus, Telegram, Discord and WhatsApp still work as before, now with pairing codes for new senders, approvals asked in the chat, and voice notes, photos and files both ways. A custom connector is one Python file.

### Voice
- Voice mode in the window runs with echo cancelling, so you can talk over a reply on speakers. A spoken yes is enough for what Auto would run, with Allow and Deny on screen for the rest.
- Hindi is spoken and understood. Inglen picks a listening model that can hear Hindi and reads a Hindi reply sentence by sentence.
- The voice is picked per sentence, so a reply that changes language changes voice and comes back.
- Listening set to the graphics card now runs there, and a turn's first spoken words come sooner.
- The wake word loads its model when needed and lets it go when idle.

### Memory
- Each chat keeps a short card of where it stands (the goal, what you settled on, what's left), written once the chat goes quiet. A new chat starts with the newest open ones, so a follow-up days later picks up where you were.
- Facts record when they became true and when they stopped, and where they came from. Every chat is searchable by words and by time, so "what did we decide last week" works.
- The memory pass and chat titles run on every brain, on a model loaded on this PC first.
- The "in mind" list under a reply shows each fact and chat used, with forget and done.
- Share with agents gives Claude Code, Codex, Cursor and OpenCode your memory as a tool.

### Images and video
- Pictures and short videos are made on your own graphics card through stable-diffusion.cpp, installed for your card from the Models page. A saved Grok, OpenAI or Gemini key covers the cloud.
- Models, Discover lists image and video families beside the chat models. Download fetches a whole set (diffusion model, text encoder, VAE and any LoRA) and offers GGUF sizes, picking the largest that fits your card.
- Models already in a ComfyUI or WebUI folder are read where they are. My Models lists image and video models beside the chat ones.

### Security
- Saved keys are sealed with Windows' own encryption instead of kept as plain text, and never reach a model or the phone. Reading a place that holds keys, such as `.env` files or `~/.ssh`, asks in every mode.
- Once a chat has read a web page, an email or a downloaded file, a request to an address nobody gave asks first in every mode. Fetching from this PC or the local network needs the host named.
- Update patches and installers must carry Inglen's release signature, or they are refused. Engine and voice downloads are checked against published hashes.
- Every file Inglen's own tools write, edit, delete or move is copied first, up to 200 MB a change, and `/undo` puts it back for a week.
- Auto asks before destructive git commands, emptying the Recycle Bin, and hidden or encoded commands.
- The local server refuses requests from other sites, and dialogs show text as text.
- An install count is opt-in, off by default, and setup shows exactly what it would send.

### The window and the terminal
- New looks (Classic, Light, Dark, System) with six accents. A new install opens in Dark with Rose.
- A quiet new chat with the mark showing the model's state and up to three suggestions by the message box.
- Setup in eight steps that acts on your answers, finds the agents and models already on the PC, and ends in a short tour.
- Every Settings page is always shown, with expert rows in closed Advanced sections. Detail in chats, Simple or Full, sets how much of each step a chat shows.
- A Files tab that opens on your recent files and folders, pinned chats, a Logs page, `/status` with plan limits, and `@` to attach chats, files and folders.
- Reminders can be edited and paused, a due one opens in its own chat, and its notice has Snooze and Done.
- One set of chats for the window, the terminal and the phone.
- The terminal streams replies above an input that stays open for steering, uses arrow-key pickers for brain, model, mode and chats, and takes its colours from the window's look.
- AMD and Intel graphics cards get llama.cpp's Vulkan build.
- On Linux and macOS, engine downloads and app lists work from source, and Inglen can start at login on Linux.

### Removed
- ntfy. Reminders go to the paired phone or to Telegram or Discord.
- The Forge WebUI tools. Images run on the built-in engine, and a Forge install's models are still read in place.

## v0.7.1 to v0.7.5

Called Hearth at the time. See [v0.7.3](docs/RELEASE_NOTES_v0.7.3.md), [v0.7.4](docs/RELEASE_NOTES_v0.7.4.md) and [v0.7.5](docs/RELEASE_NOTES_v0.7.5.md).

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
