# Changelog

Notable changes to Inglen (called Hearth through v0.7.5). Format loosely follows [Keep a Changelog](https://keepachangelog.com); semver is not strict pre-1.0.

## v0.7.6-preview

Full notes in [docs/RELEASE_NOTES_v0.7.6.md](docs/RELEASE_NOTES_v0.7.6.md).

### Added
- Agent brains. Claude Code (`hearth/claude_brain.py`, `claude -p` with stdin kept open for steering), Codex (`hearth/codex_brain.py`, through `codex app-server`: streamed replies, each command and file change sent to Inglen's approval with its diff, `turn/steer`, Fast tier, threads archived after each turn, `codex exec` as the fallback) and Cursor (`hearth/cursor_brain.py`) think for JARVIS on the user's own plan with Inglen's MCP server for tools. Any installed agent that speaks the Agent Client Protocol can be the brain too (`hearth/acp_brain.py`): OpenCode, Copilot CLI, Goose, Grok Build and Gemini CLI, and Pi, Amp and Antigravity through their adapter programs. OpenCode is verified; the others follow their documented protocol mode.
- Agents list their own models (Claude Code's `initialize` answer, Codex's model cache, Cursor's `--list-models`, a protocol agent's session options). The Think button offers a model's effort levels; Fast is a switch for Codex's priority tier and Claude Code's fast mode, which reports back when a plan doesn't have it.
- Settings shows every brain as a card grouped by where it runs; the model menu adds a cloud provider from there. A model on this PC can stay loaded under a cloud brain to run helpers for free.
- Team view: every helper from its own transcript, as a list or a pixel room with up to eight desks a room (cabin, winter cabin, library, ocean), a wooden sign and a pixel wipe between rooms, a home room in Appearance. Clicking anyone shows who asked, the task, each step, what it changed and its plan usage.
- Helpers can run on JARVIS's brain, this PC's model, a saved provider and model (`runs_on`, `model`), or an agent program. `launch_team` starts a mixed team in one call and tells each member who else is on it. Helpers can be told something part-way (`tell_subagent`, Codex helpers through its app server), stopped, and they message each other or JARVIS with `message_helper`; the room shows each message as one walking over to say it.
- Helpers' changes are kept apart: an agent program working in a git repository gets its own worktree, and its changes wait to be brought in, discarded or undone (`helper_changes`, panel buttons, `/team changes|bring|discard|undo`). Inglen's own helpers claim the files they write, so two can't write one at once, and their changes can be undone.
- `race_helpers`: one task to several agent programs on their own copies, a check command run in each, and one comparison back.
- A general helper role: the everyday tools, and `load_tools` for the rest into its own list. A team member with no role named is one.
- `delegate`: hand one task to another agent CLI and relay its answer. Twenty-three adapters, run headless on the user's install; Claude Code, Codex, Cursor and OpenCode have been run from Inglen. Installs and updates show in the Downloads list with Cancel.
- Approval modes Ask, Auto-edit and Auto, shared by the window (chat bar menu, Shift+Tab), the terminal (`/mode`), helpers and agent programs.
- One set of chats for the window and the terminal (`hearth/chat_sync.py`): a terminal chat is listed in the window and can be carried on there, and `/chats` and `/chat` open any chat in the terminal.
- Logs page: each tool call with its result in words, by day, with errors and search; the model server's recent requests summed up.
- `/status` in the window and the terminal: what is thinking, how full the chat is, and plan limits for every brain that reports them.
- Appearance: Classic, Light, Dark and System, six accents; tray and window icons follow the choice. Every look but Classic carries the Nook mark (a rounded corner bracket with a dot in the accent); Classic's mark is an i with a flame for its dot. A new install opens in Dark with Rose, and one set up before keeps Classic. The app icon is the Nook on a dark tile with a Rose dot (`hearth/look_icon.py`).
- The empty chat's mark shows the model's state: an empty ring with no model, a stroke along the bracket while one loads that follows the load percentage once it is known, and a filled dot that settles once when it is ready. Reduced motion holds each state still.
- Settings shows every page in both modes. Expert rows (review every command, reasoning effort, keep local warm, bringing memory over, tool permissions, the built-in server's concurrent agents and port) sit in a closed Advanced section at the foot of their page. The mode is "Detail in chats: Simple / Full" in Appearance and sets only how much of each step a chat shows.
- First-run setup is eight steps (welcome, a model on this PC, a cloud model, voice and the language you speak, what to download, about you, how it should work, bringing memory over) and ends in a six-stop tour, each stop with something to try. It finds the agents and model files already on the PC, writes its answers to profile.md, asks headphones or speakers, approval mode and look, and plays a sample task beside the approval choices; `/me` asks the same in the terminal.
- Settings, MCP servers: two plain rows (servers Inglen uses, using Inglen from other apps) with the live server list between them. Each server shows its service's icon with a status dot, and a failed one says why in one sentence ("Couldn't start: node isn't installed"), with the raw error a click away.
- Terminal colours are shades of the window's look: the accent, statuses, tool names, warnings, frames and the pickers' chosen row. Errors and diff lines keep red and green. The banner reads INGLEN in the saved look's colours.
- Tray menu follows the look (dark or light) and lists pinned and recent chats that open in the window, New chat, Voice mode and Listen for the agent's name, with the rest under More.
- `hearth/gpu.py`: load and memory for any vendor's card from DXGI and the GPU performance counters. AMD and Intel cards get llama.cpp's Vulkan build.
- Pinned chats; tool runs show the agents, skills and sites they reached; a running tool counts up on its card.
- Anthropic as a provider talks to its own Messages API (`hearth/anthropic_chat.py`): the model's effort levels, streamed thinking, and fields a model refuses learned per model. Effort levels and whether thinking can be turned off come from each provider's model list.
- The terminal runs turns on the shared engine (`headless.run_once`, drawn by `hearth/term_render.py`): replies stream above an input that stays open for steering, Esc stops, thinking folds to one line, tables render, and `/brain`, `/model`, `/mode`, `/think`, `/chats` and approvals are arrow-key pickers (`hearth/term_pick.py`).
- Messages carry the time they were said, with a line where the day changes; the chat list shows when each chat was last used.
- A due reminder is said in its own chat (clock mark, unread dot); the Reminders page starts a typed task in a new chat and suggests reminders from memory.
- `auto_memory` setting (Remember things on its own) for the background fact saver, which also saves nothing from a chat that asked it not to.
- A GGUF without a tokenizer (an image or video model) or cut short is marked in Models and left out of the model menu.
- The Team room's fire is animated.
- Images and video on this PC (`hearth/sdserver.py`): stable-diffusion.cpp's sd-server fetched for the system and card and run like the chat engine. Models in `~/.hearth/image-models` or a ComfyUI/WebUI folder read in place, matched to the engine's own recipes; Models > Images & video installs, loads and lists what each family still needs. `generate_image` / `generate_video` use a ready local model first (`where=local|cloud`), else a saved Grok, OpenAI or Gemini key from any brain.
- Computer use you can watch (`hearth/desktop_session.py`): a breathing accent edge round the screen (click-through, excluded from capture) and a pill naming the action; the turn's end fades it, chimes, restores the window that was in front and stops taskbar flashing. Agent brains' tool servers report through `/api/brain/desktop`. A click raises the target window first; apps JARVIS opens outlive the turn; `desktop_type` appends to a filled field unless told to replace.
- Connectors on one core (`hearth/channel_core.py`): Telegram, Discord and WhatsApp with pairing codes for new senders (approved in Settings > Connectors, `/pair`, or the `connectors` tool), approvals asked in the chat, voice notes, photos and files both ways, each conversation an Inglen chat. Custom connectors are one Python file in `<workspace>/connectors` ([docs/CONNECTORS.md](docs/CONNECTORS.md)).
- Memory shared with coding agents (`hearth/memory_mcp.py`): the Memory tab's Share with agents adds Inglen's memory to Claude Code, Codex, Cursor and OpenCode as an MCP server.
- Memory in layers (`hearth/memory_cards.py`, `hearth/memory_pass.py`, `hearth/session_search.py`): each chat keeps one card of where it stands (goal, open/done/dropped, values like a budget or a shortlist, what is left), replaced by the memory pass once the chat has gone quiet for a few minutes or before it is compacted, on whichever model is at hand: one loaded on this PC first, else the brain's own one-shot, for every agent program. A new chat's first message carries the newest open cards. Facts carry valid_from, valid_to, source chat, last use and importance; a superseded fact keeps valid_to in the archive. Every exchange of every chat is indexed in SQLite FTS5 with its time and names, ranked by words, shared names, recency, importance and open cards; time words become a date range, and `search_chats` takes after/before. The reply's "in mind" list names each card, fact and search with its source chat, with forget and done. `read_chat` reads one earlier chat in order, with where it stood.
- @ in the message box picks chats, files and folders; long pastes go as a chip; web sources show as one chip; `ask_user` questions stay in the chat as a card.
- Files tab: sandboxed preview, an Open menu of the apps the system lists for a file type with their icons (`hearth/openers.py`).
- Linux and macOS: the llama.cpp and stable-diffusion.cpp downloads read release file names for any system (`hearth/release_files.py`); Linux apps from `.desktop` entries (`hearth/desktop_entries.py`); card detection from the kernel's device files and Apple silicon. Whole suite passes on Ubuntu.
- Setup plays a sample task beside the approval choices; a What to get step lists the model, engine and voice the choices need.
- Discover opens on the month's most downloaded chat models; load estimates give the share that runs on the CPU; Add vision asks first and downloads in Downloads.
- `crash.log` (faulthandler) records every thread's stack when native code takes a process down; the window restarts a backend that died.
- A turn on a provider brain shows what it cost: every call's tokens added up (cached input apart) and priced from the model catalog, or the provider's own figure when it gives one (`model_facts.turn_cost`). Every agent brain ends a turn the same way: its own figure, else its tokens at catalog prices (`claude_brain.priced`), marked as plan usage when Codex is signed in with ChatGPT, for Cursor, and for Claude Code on a Claude plan.
- Run in background on a slow command's card (`/bg` in the terminal): the wait ends at once and the command carries on as a job that reports when it ends (`jobs.background_now`).
- Skills page redone: a switch per skill (`skills_off` setting; a switched-off skill is left out of the prompt and `load_skill` refuses it), a readable detail view, and the add box takes any Agent Skills folder on GitHub.
- Reminders can be changed in place (`update_reminder`, /api/reminders/update: words, time, repeat, last day `until_iso`) and paused and resumed (`pause_reminder`, the row's pause button); a repeating one resumes at its next slot. The row's old ten-minute snooze is gone from the list, since on a reminder not yet due it made it fire sooner.
- The app is called Inglen in everything a person reads. Names on disk stay (`hearth` package, `HEARTH_*`, `%LOCALAPPDATA%\Hearth`, `Hearth.exe`), so an update keeps settings, memory and chats. The assistant is still JARVIS by default (`agent_name`, renamed in Settings, Behavior) and takes "Inglen" or "Hearth" to mean itself.
- An agent brain that goes quiet: after 90 s with no event and no step running, every surface says it may still be thinking and Stop ends it (`HEARTH_AGENT_QUIET_S`); nothing ends on a timer. An error the agent wrote to its own log (an ACP spec's `log`) ends the turn with its words, and /status reports it with when it was logged.
- Inglen Plus (Settings, Plus): a licence key turns it on for this PC, and a phone pairs by scanning a QR code and checking that both screens show the same six numbers. The phone app reaches this PC from anywhere through a relay that only carries messages sealed with the pairing's keys (`hearth/relay_bridge.py`, `hearth/relay_crypto.py`). On the phone: the chats, a reply as it is written with its steps and what each ran, approvals, the brain and approval mode, files and pictures from the PC as cards fetched when opened, the Team room, recent file changes with Undo, and the Claude Code, Codex and other coding agents' sessions on the PC, read and carried on. The window and the phone see each other's changes as they happen. Notifications for a question waiting (with Allow and Deny), a finished reply, and a coding agent finishing on the PC.
- Voice calls from the phone (`hearth/call.py`, `hearth/call_audio.js`): one shape in the accent shows connecting, listening, thinking, speaking and muted; both sides' words appear as they are said, with the steps. The call's time starts once the PC is listening, with a soft ready tone and two falling notes at the end (both can be turned off in the call's settings). Mute and End, or a waiting question's Allow and Deny, sit in a notification while the app is out of sight, and the call keeps going in the background. A spoken yes counts only for a step Auto would run; anything riskier asks for a tap, and says so out loud. Voice and language can be changed from the call and are kept on the PC. A phone that goes quiet ends the call on the PC.
- The agent can call the phone (`hearth/call_me.py`), off until "Let JARVIS call me" is turned on in Settings, Plus, and then only for the reasons switched on: a reminder coming due, background work that finished or needs you, or a question unanswered for a set number of minutes. Never in quiet hours (22:00 to 08:00 by default), never while the PC is in use, one ring at a time, and not again for five minutes after a declined or missed call. An unanswered ring becomes a missed call; an answered one starts by saying why it called.
- Voice mode in the window runs on the call's path: the page opens the mic with echo cancelling and plays the voice through a WebRTC loop, so talking over a reply works on speakers. A spoken yes counts only for what Auto would run, with Allow and Deny on the voice screen for the rest. The mic, camera and clipboard are granted to Inglen's own page and nothing else, without the web view's own prompt; Windows' microphone privacy setting is the gate, and when it blocks the mic voice mode says so with a button to that setting. On macOS, and where the page can't open a mic, voice mode listens through the PC's own mic. Speakers mode is now for the terminal's `/listen`.
- Chats started from the phone or a chat app are named by the brain after their first real turn, as window and terminal chats are; a chat renamed by hand keeps its name.
- `send_to_phone` in a turn from the phone or a chat app answers in that same conversation and can send a file.
- Auto's reviewer (`hearth/auto_review.py`, Settings, Behavior): in Auto, a step that would stop to ask goes ahead when your own words in the chat asked for exactly it. It reads only your messages and the pending step, never the brain's words or a tool's output. A model is used as the reviewer only after it passes a labelled set of steps, checked the first time Auto meets it or from Settings. Shutting down, disks, accounts and the registry, security and start-up settings, hidden commands and saved passwords always ask.
- Undo for file changes: tools that write, edit, delete or move copy what was there first, and `/undo` in the window and the terminal (Recent changes on the phone) lists recent changes and puts one back. Auto no longer asks before deleting or moving something Inglen's own tools made in the past week.
- `agent_sessions`: the brain lists the Claude Code, Codex and other coding agents' sessions on this PC, reads where one got to, and sends one a message. Sending asks in Ask mode.
- Helpers on the model that is already loaded share its server, each on a slot of its own, instead of loading it twice. Concurrent agents defaults to Automatic, picked from graphics memory.
- A provider that answered with a reset time is not asked again before then ("Groq is out of usage until 4:30 PM"), and a turn that failed reaches the brain on the next one as a note before your message, on every surface.
- An opened memory says whether it was noticed on its own or saved on purpose, and links to the chat it came from. What setup learns about the PC is kept for the app, not as memories.
### Fixed
- Other builds of llama.cpp (Models, Engine, Add an engine): by a GitHub page, fetched for the card like the official build, or by a folder. When the official build can't read a model (invalid ggml type, unknown architecture) Inglen tries the added builds and remembers which one read it. They never replace the official build for other models.
- Engine install and repair took llama.cpp's "latest" release, which now points at a tag with no Windows files; the newest numbered build is used.
- Repair checks real files, restores skills, art and the engine, and frees the patch backup. The update patch now carries `hearth/**` and `assets/**`.
- The window's private mode forgot the chosen look on every launch; the server writes the saved look into the page.
- The send wrapper dropped its options, so a notification flush ran as an ordinary turn.
- Stop closes the open request at once, also while a local model is still reading the prompt.
- A turn's tool calls and results stay in the history for the next turn, in the window and the terminal.
- Memories matched by keyword are no longer attached to every message; the index stays in the system prompt and the model recalls on demand.
- A built-in server started by another window or the terminal is reused for the same model instead of failing on a taken port; a second terminal starts its own chat.
- The context count after a one-round turn is the server's measured one, not an estimate.
- Chat bubbles take the theme's accent instead of a fixed blue.
- An opened memory shows the fact, when it was saved and how often it was recalled, not the raw file with its header; graph labels no longer overlap.
- The terminal's `/help` lists `/context` and `/tokens` again.
- PortAudio start and stop are serialized (`listen._PA_LOCK`): the wake word retrying a missing mic while the window listed devices crashed the app.
- Fresh installs from source pulled mcp 2, which removed FastMCP; major versions are capped in `constraints.txt`.
- Provider limits: Retry-After and Gemini's retryDelay are honoured, a spent quota or a 402 is said once in the provider's words, an agent out of plan usage gets no turns until it resets.
- Pictures go to the model at most 1280 px on the long side (a 2560x1440 screenshot on a 9B: 195 s to 6 s).
- An agent brain's session restarts from the chat as it reads when an earlier message was edited or a reply regenerated.
- The saved voice and speed are read when speaking, not at import.
- A turn that hit the step limit or ended on an error no longer leaves the turn flag set (memory passes and reminders waited forever).
- Repeating reminders hold while nobody is at the PC and say how many came by.
- The terminal's pickers fall back to a numbered list without prompt_toolkit.

### Removed
- ntfy. Reminders go to the connected Telegram or Discord (`phone_reminders`).

### Security
- The local API refuses requests from other sites; a page in any browser tab could read stored keys and call tools.
- Command approval is a per-process token, so model output cannot approve its own call. The destructive-command guard covers PowerShell's aliases and .NET forms, and background jobs share it.
- MCP tools go through the permission prompt and no longer inherit credentials from the environment. `smart_click` and `browse_key` are gated like the other input tools.
- Write confinement compares real paths, so a link inside the workspace cannot carry a write outside it. `grep_search` passes its pattern after `-e` and `--`. `web_fetch` takes http(s) only, so `file://` no longer bypasses read lockdown. CLI `/lockdown` uses the persisted toggle.
- WhatsApp's own-chat mode answers only the linked number's own chat. More key shapes are redacted. The TUI is removed.
- An update patch is applied only when it is signed by a release key (`hearth/release_keys.py`) and every file in it matches the signed list; anything else is refused before a file is written.
- Auto asks before deletes and hidden commands that slipped past its patterns: `rd /s /q` after `cmd /c`, `find -delete`, inline interpreter code that deletes or overwrites, encoded PowerShell, decoded text piped into a shell, `shred`, `dd of=`, and redirects into the home folder's own files.

### Voice
- Speak and listen in the eight languages the voice bank ships (54 voices); multilingual Whisper sizes and a spoken-language setting.
- Voice sessions end with the recorder shut down, releasing the mic and the Whisper process; each session owns its recorder and callbacks, so reopening voice no longer stalls or stops.
- The mic is saved by name and looked up in the table of the library that opens it; no fallback to a virtual or loopback device.
- The wake word keeps a pre-roll and every block of an utterance, keeps the start of long speech, and releases the mic while voice mode runs.
- Failures in synthesis, playback and transcription are reported. A settings save no longer unloads the TTS engine.
- With no listening language set, speech is heard as the chosen voice's language or English, whichever it is; only a language named in Settings is forced.

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
- Team panes, opened for a team started in the terminal, show each step as what it was pointed at; `launch_team` reports members that did not start. A phone bridge ends with the Inglen that started it.

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
