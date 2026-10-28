# Tools

Inglen gives the model **111 tools** to operate your machine. Everything runs locally, and the only outbound calls are web searches and fetches the model itself makes. Tools that act on your machine (shell, file writes, app launch, clicking and typing) ask permission before they run.

To keep the prompt small, **36** core tools load with their full description and **75** niche ones (marked †) are listed by name only until the model pulls one in with `load_tools`. Set `HEARTH_ALL_TOOLS=1` to load everything up front.

_This file is generated from the live tool definitions (`scripts/gen_tools_doc.py`), so don't edit it by hand._


## Files & docs

| Tool | What it does |
|---|---|
| `create_directory` † | Create a directory inside the workspace |
| `delete_path` | Delete a file or directory inside the workspace |
| `edit_file` | Edit a file by replacing exact text: each edit swaps old_text for new_text |
| `extract_archive_file` † | Pull ONE file out of an archive into the workspace, without unpacking the whole thing |
| `find_file` | Find files or folders by name (a substring or a glob) across the workspace, Desktop, Documents, Downloads, Pictures, Videos, Music, ~/Code, ~/Projects, the wor… |
| `glob_files` † | Find files by glob pattern |
| `grep_search` | Regex search across files |
| `list_archive` † | List contents of a .zip/.jar/.whl/.apk/.tar/.tar.gz/.tar.bz2/.tar.xz archive WITHOUT extracting it |
| `list_directory` | List a directory |
| `locate_path` † | Smart locator: find a folder or app by name without globbing the whole disk |
| `move_path` | Move or rename inside the workspace |
| `read_file` | Read a file |
| `read_pdf_large` † | Map-reduce summarize a VERY large PDF (hundreds of pages) that won't fit any single context |
| `summarize_file` † | Summarise a long document instead of reading all of it |
| `write_file` | Create a new file in the workspace |

## Web & browser

| Tool | What it does |
|---|---|
| `browse` | Drive a real, visible Chromium window: navigate, get the page text and a numbered list of links and buttons, then act with browse_click and browse_type |
| `browse_click` † | Click a link or button on the CURRENT browser page by its visible text (use the exact text from the CLICKABLE list that browse returned) |
| `browse_close` † | Close the browser session |
| `browse_key` † | Press a keyboard key/shortcut on the current browser page, for media controls and shortcuts that aren't clickable buttons |
| `browse_scroll` † | Smooth-scroll the current browser page so you can see more content |
| `browse_type` † | Type into a field on the current browser page (a search box, login field, etc.) |
| `list_browsers` † | List installed browsers detected on this machine |
| `open_in_browser` | Open a URL in a SPECIFIC browser (and optional profile), the user SEES it in their own browser, and it stays open |
| `open_url` | Open a URL in the user's default browser for them to use |
| `web_fetch` | Fetch a page's readable text for you to read; the user sees nothing |
| `web_search` | Free DuckDuckGo HTML search |

## Desktop control

| Tool | What it does |
|---|---|
| `arrange_windows` † | Lay windows out on screen in ONE call: position and size several windows at named regions |
| `capture_active_window` † | Screenshot only the focused window, or a named one with window_title; tighter than screenshot for vision |
| `computer_click` † | Click the mouse |
| `computer_drag` † | Drag-and-drop: press at (x1,y1), move to (x2,y2) while held, release |
| `computer_key` | Press a key or chord: a named key ('enter','tab','esc','f5','up') or a combo joined with '+' ('ctrl+s','alt+tab','ctrl+shift+esc','win+d') |
| `computer_move` † | Glide the mouse to absolute screen pixel (x,y) |
| `computer_screen` † | Report screen size + current cursor position |
| `computer_scroll` † | Scroll the mouse wheel |
| `computer_type` | Type text at the current keyboard focus (click the field first) |
| `desktop_click` | Click a UI element from the last desktop_snapshot, by idx (preferred) or a name substring |
| `desktop_snapshot` | List a window's controls (buttons, fields, menu and list items, checkboxes) with numbers and positions, read from the accessibility tree, including inside Chro… |
| `desktop_type` | Write text into a UI element from the last desktop_snapshot (by idx or name) |
| `execute_actions` | Run several desktop steps in one call, in order, stopping at the first failure |
| `focus_window` † | Bring an ALREADY-OPEN window to the front / focus it, by a substring of its title (e.g |
| `list_windows` † | List EVERY open window (title, state, foreground/minimized/open, and owning app) |
| `manage_window` † | Minimize, maximize, restore, or close a window by a substring of its title |
| `smart_click` | Click something you located in a captured image: pass the pixel (x, y) at the target's centre, in that image's own coordinates |
| `wait_for_element` | Block until a named control appears in a window, then report its idx |

## System & apps

| Tool | What it does |
|---|---|
| `clipboard_read` † | Read the clipboard |
| `clipboard_write` † | Write text to the clipboard |
| `disk_usage` † | Find the biggest folders and files under a path |
| `get_time` † | Current local datetime, weekday, timezone offset |
| `learn_environment` † | Re-scan this machine (GPU/VRAM, RAM, installed models, top-level drive map) and refresh it into long-term memory |
| `list_installed_apps` † | Installed applications (Windows registry uninstall keys) |
| `list_models` † | List the LLM models available on the connected server (LM Studio / Ollama / cloud) by querying its API |
| `list_processes` † | List running processes (top by memory) |
| `load_model` † | Load a model file from this PC on Inglen's built-in engine |
| `network_info` † | Local IP address, hostname, network adapters, wifi |
| `open_app` | Open an app by name, a file with its default program, a folder in Explorer, or a URL |
| `run_command` | Run a shell command |
| `screenshot` | Capture the whole screen and return the saved path; follow with view_image to see it |
| `system_info` | Machine snapshot in one call: OS, CPU, GPUs with VRAM, temperature and load, RAM, disks, hostname, user, uptime, battery |
| `view_image` | Load an image file so you can see it (png, jpg, gif, webp, bmp) |
| `whoami` † | Introspect your own runtime config |

## Background jobs

| Tool | What it does |
|---|---|
| `get_job_result` † | Get the result of a background job by id |
| `job_kill` † | Terminate a running background job by job_id |
| `job_status` † | Return current status + last ~40 lines of output for a background job |
| `job_wait` † | Block up to `timeout_s` seconds for the job to finish, then return final status + output |
| `list_jobs` † | List background jobs (started by tools like disk_usage on a drive root, or explicit start_job calls) |
| `start_job` | Run a shell command in the background and get a job_id at once; check it with job_status or job_wait |

## Sub-agents

| Tool | What it does |
|---|---|
| `delegate` | Hand one task to another AI agent CLI installed on this PC (Claude Code, Codex, Antigravity, Gemini CLI and others) and move on |
| `get_subagent_result` † | Poll a background subagent for its result |
| `helper_changes` † | See or act on what a finished helper changed on disk |
| `launch_team` † | Spawn a TEAM of sub-agents that work in parallel and open a live terminal pane for each so the user can WATCH them build side by side (Windows Terminal panes /… |
| `list_subagent_personas` † | List the personas available for spawn_subagent |
| `message_helper` † | For a helper: ask or tell another helper that is working at the same time, by name, or the assistant that sent you (to: 'JARVIS') |
| `race_helpers` † | Give one coding task to two or more agent programs at once (say codex and claude-code), each on its own copy of a git repository, to compare their work |
| `spawn_subagent` | Start a scoped sub-agent (see list_subagent_personas for personas) |
| `tell_subagent` † | Give a helper that is still working a message: a correction, an extra detail, a change of direction |

## Memory

| Tool | What it does |
|---|---|
| `memory_forget` | Delete a memory by title |
| `memory_list` † | Show the full memory index (all titles and one-line hooks) |
| `memory_recall` | Search saved memories |
| `memory_save` | Save a lasting fact about the user or their world (preferences, hardware, projects, people, setups) |
| `read_chat` † | Read one earlier chat in order, by its title or id: what it was about, and the latest turns in full |
| `search_chats` | Search every past conversation for anything said or decided in an earlier chat, best match first, with what is still going on in each |

## Reminders & alerts

| Tool | What it does |
|---|---|
| `cancel_reminder` † | Cancel a scheduled reminder by id |
| `connectors` † | The chat apps people reach this PC from: Telegram, Discord, WhatsApp |
| `list_reminders` | List all upcoming (un-fired) reminders |
| `notify` † | Pop a desktop notification (Windows toast) RIGHT NOW |
| `pause_reminder` † | Pause a reminder so it stops firing without deleting it, or resume it (paused=false) |
| `send_to_phone` † | Send a message FROM this PC TO the user's phone, on a chat app they connected (Telegram, or Discord as a DM) |
| `set_reminder` | Schedule a desktop notification, optionally running a tool at the same moment (action_tool with action_args) |
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
| `create_plugin` † | Write a NEW local tool (plugin) for yourself when no existing tool fits a capability the user needs, then use it immediately and forever after |
| `create_skill` † | Author a NEW user skill saved to ~/Jarvis/skills/<name>/ |
| `delete_plugin` † | Delete an installed plugin by name (removes its file + unregisters the tool) |
| `install_skill` † | Install a shareable skill from a GitHub repo or a local folder into ~/Jarvis/skills/ |
| `list_plugins` † | List the self-authored/installed plugins in ~/Jarvis/plugins/ (name, status, description) |
| `list_skills` † | List available skills, bundled and installed |
| `load_skill` | Load a skill's full instructions and its bundled files before following it |

## Image generation

| Tool | What it does |
|---|---|
| `check_video_task` † | Check a video once, without waiting: pending, done, failed or expired |
| `forge_generate` † | Make an image on this PC with the local Stable Diffusion install (Forge WebUI) |
| `forge_shutdown` † | Kill the Forge process to free VRAM back to the LLM |
| `forge_status` † | Check if Forge WebUI is running and reachable |
| `generate_image` † | Make an image (picture, photo, artwork, illustration) from a text prompt, or change one (pass its path as image) |
| `generate_video` † | Start making a video: on this PC when a video model is set up under Models, else with xAI Grok (a saved Grok key) |
| `list_generations` † | List the 10 most recent image/video generation tasks (live + finished) |

## Session

| Tool | What it does |
|---|---|
| `ask_user` † | Ask the user a multi-choice question when you genuinely need a decision before continuing, picking between two valid approaches, clarifying an ambiguous file/f… |
| `end_session` † | Call this when the user is clearly wrapping up the conversation (says bye, goodbye, see you, thanks that's all, etc.) |
| `worklog` † | Your scratch pad for a long task: note findings as you go and read them back instead of re-reading the sources |

## Other

| Tool | What it does |
|---|---|
| `system_vibe` † | Quick PC health snapshot: CPU %, RAM, top processes, GPU VRAM, and a vibe-check verdict |
