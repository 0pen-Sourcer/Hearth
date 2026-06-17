# Tools

Hearth gives the model **93 tools** to operate your machine. Everything runs locally; the only outbound calls are web searches the model itself makes. Risky tools (shell, file writes, app launch, browser control) prompt for `[y/n/a/N]` permission in the CLI before they run.

To keep the prompt small, **53** core tools load by default and **40** niche ones (marked †) stay behind a `load_tools` meta-tool the model calls on demand. Set `HEARTH_ALL_TOOLS=1` to load everything up front.

_This file is generated from the live tool definitions (`scripts/gen_tools_doc.py`) — don't edit by hand._


## Files & docs

| Tool | What it does |
|---|---|
| `create_directory` | Create a directory inside the workspace |
| `delete_path` | Delete a file or directory inside the workspace |
| `edit_file` | Targeted string-replace edits — never rewrites the whole file |
| `extract_archive_file` † | Pull ONE file out of an archive into the workspace, without unpacking the whole thing |
| `find_file` | Find files (or folders) by name across common locations — workspace, Desktop, Documents, Downloads, Pictures, Videos, Music, ~/Code, ~/Projects, the current wo… |
| `glob_files` | Find files by glob pattern |
| `grep_search` | Regex search across files |
| `list_archive` † | List contents of a .zip/.jar/.whl/.apk/.tar/.tar.gz/.tar.bz2/.tar.xz archive WITHOUT extracting it |
| `list_directory` | List a directory |
| `locate_path` | Smart locator: find a folder or app by name without globbing the whole disk |
| `move_path` | Move or rename inside the workspace |
| `read_file` | Smart file reader |
| `read_pdf_large` † | Map-reduce summarize a VERY large PDF (hundreds of pages) that won't fit any single context |
| `write_file` | Create a NEW file inside the workspace |

## Web & browser

| Tool | What it does |
|---|---|
| `browse` | Drive a REAL web browser (controlled Chromium) — the user SEES the window |
| `browse_click` | Click a link or button on the CURRENT browser page by its visible text (use the exact text from the CLICKABLE list that browse returned) |
| `browse_close` † | Close the browser session |
| `browse_key` † | Press a keyboard key/shortcut on the current browser page — for media controls and shortcuts that aren't clickable buttons |
| `browse_scroll` | Smooth-scroll the current browser page so you can see more content |
| `browse_type` | Type into a field on the current browser page (a search box, login field, etc.) |
| `list_browsers` | List installed browsers detected on this machine |
| `open_in_browser` | Open a URL in a SPECIFIC browser (and optional profile) — the user SEES it in their own browser, and it stays open |
| `open_url` | Open a URL in the user's DEFAULT browser (their login, fullscreen) |
| `web_fetch` | Fetch a URL and return its readable text (HTML stripped) to YOU |
| `web_search` | Free DuckDuckGo HTML search |

## System & apps

| Tool | What it does |
|---|---|
| `clipboard_read` | Read the clipboard |
| `clipboard_write` | Write text to the clipboard |
| `disk_usage` † | Find the biggest folders and files under a path |
| `focus_window` | Bring an ALREADY-OPEN window to the front / focus it, by a substring of its title (e.g |
| `get_time` | Current local datetime, weekday, timezone offset |
| `learn_environment` † | Re-scan this machine (GPU/VRAM, RAM, installed models, top-level drive map) and refresh it into long-term memory |
| `list_installed_apps` † | Installed applications (Windows registry uninstall keys) |
| `list_models` † | List the LLM models available on the connected server (LM Studio / Ollama / cloud) by querying its API |
| `list_processes` | List running processes (top by memory) |
| `network_info` † | Local IP, hostname, network adapters |
| `open_app` | Launch / open with default association |
| `run_command` | Execute a shell command |
| `screenshot` | Capture the screen, save to workspace/screenshots/, return the path |
| `system_info` | OS, CPU, RAM, disk, hostname, user, uptime — a snapshot of the machine |
| `view_image` | Load an image file from disk so you can SEE it |
| `whoami` | Introspect your own runtime config |

## Memory

| Tool | What it does |
|---|---|
| `append_soul` † | Add ONE line to your soul.md without rewriting the whole file |
| `edit_soul` † | Write your own identity to ~/Jarvis/soul.md |
| `memory_forget` | Delete a memory by title |
| `memory_list` | Show the full memory index (all titles and one-line hooks) |
| `memory_recall` | Search saved memories |
| `memory_save` | Save a long-term memory |
| `read_soul` † | Read back your current soul.md content |
| `search_chats` | Search across ALL past chat conversations (full-text via SQLite FTS5) |

## Reminders & alerts

| Tool | What it does |
|---|---|
| `cancel_reminder` | Cancel a scheduled reminder by id |
| `list_reminders` | List all upcoming (un-fired) reminders |
| `notify` | Pop a desktop notification (Windows toast) RIGHT NOW |
| `set_reminder` | Schedule a future desktop notification, optionally with a TOOL CALL that runs at the same moment (an 'action reminder') |
| `snooze_reminder` † | Push a reminder's due time forward by N minutes (default 10) |

## Email

| Tool | What it does |
|---|---|
| `read_inbox` † | Read recent email from the user's configured mailbox (IMAP, read-only) |
| `send_email` † | Send a plain-text email from the user's configured address (SMTP) |

## Voice

| Tool | What it does |
|---|---|
| `list_voices` † | List available built-in Kokoro voice ids |
| `set_voice` † | Change the active TTS voice |

## Self-extending (plugins)

| Tool | What it does |
|---|---|
| `create_plugin` † | Write a NEW local tool (plugin) for yourself when no existing tool fits a capability the user needs — then use it immediately and forever after |
| `delete_plugin` † | Delete an installed plugin by name (removes its file + unregisters the tool) |
| `install_skill` | Install a shareable skill from a GitHub repo or a local folder into ~/Jarvis/skills/ |
| `list_plugins` † | List the self-authored/installed plugins in ~/Jarvis/plugins/ (name, status, description) |

## Image generation

| Tool | What it does |
|---|---|
| `check_video_task` † | Poll a video generation task ONCE |
| `generate_image` † | Generate an image from a text prompt |
| `generate_video` † | Start an ASYNC video generation |
| `list_generations` † | List the 10 most recent image/video generation tasks (live + finished) |

## Session

| Tool | What it does |
|---|---|
| `end_session` † | Call this when the user is clearly wrapping up the conversation (says bye, goodbye, see you, thanks that's all, etc.) |

## Sub-agents

| Tool | What it does |
|---|---|
| `get_subagent_result` | Poll a background subagent for its result |
| `list_subagent_personas` | List the personas available for spawn_subagent |
| `spawn_subagent` | Fork a focused, scoped sub-agent |

## Other

| Tool | What it does |
|---|---|
| `ask_user` | Ask the user a multi-choice question when you genuinely need a decision before continuing — picking between two valid approaches, clarifying an ambiguous file/… |
| `color_hex2rgb` † | Convert a hex color code to RGB values |
| `create_skill` † | Author a NEW user skill saved to ~/Jarvis/skills/<name>/ |
| `draft_soul` † | Propose a starter soul.md when soul.md is empty or sparse |
| `entity_graph_extractor` † | Extracts entities and relationships from text and stores them in the local knowledge graph |
| `forge_generate` | Generate an image with the user's local Stable Diffusion install (Forge WebUI) |
| `forge_shutdown` † | Kill the Forge process to free VRAM back to the LLM |
| `forge_status` † | Check if Forge WebUI is running and reachable |
| `job_kill` † | Terminate a running background job by job_id |
| `job_list` † | List recent background jobs (newest first) |
| `job_status` † | Return current status + last ~40 lines of output for a background job |
| `job_wait` † | Block up to `timeout_s` seconds for the job to finish, then return final status + output |
| `list_skills` | List available skills (bundled + user-installed) |
| `load_skill` | Load the full SKILL.md body + asset manifest for one skill |
| `math_matrix_cruncher` | Class 12 Math practice: generate randomized problems on Matrices, Determinants, Calculus, Vectors |
| `start_job` | Run a shell command in the BACKGROUND and return a job_id immediately so you can keep working while it runs |
| `study_reminder` † | Checks if it's time to study and creates/reminds you |
| `text_encoder_tool` † | Encode/decode text using various methods (Base64, Hex, ROT13), count characters, or format strings |
| `tic_tac_toe` † | Persistent Tic-Tac-Toe game engine |
| `weather` | Get current weather for any city using wttr.in (no API key) |
| `website_status_tool` † | Check if a website is reachable and returns its HTTP status code |

## Background jobs

| Tool | What it does |
|---|---|
| `get_job_result` | Get the result of a background job by id |
| `list_jobs` | List background jobs (started by tools like disk_usage on a drive root, or explicit start_job calls) |
