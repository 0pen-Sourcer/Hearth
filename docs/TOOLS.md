# Tools

Inglen gives the model **112 tools** to operate your machine. Most work is local, but tools that are explicitly about the network (for example web search/fetch, email, phone/connectors, or configured cloud generation services) make the requests needed for that feature. Actions that change the machine or have external side effects follow Inglen's permission rules; see `SECURITY.md` for the threat model and known sharp edges.

To keep the prompt small, **37** core tools load with their full description and **75** niche ones (marked †) are listed by name only until the model pulls one in with `load_tools`. Set `HEARTH_ALL_TOOLS=1` to load everything up front.

_This file is generated from the live tool definitions (`scripts/gen_tools_doc.py`), so don't edit it by hand._


## Files & docs

| Tool | What it does |
|---|---|
| `create_directory` † | Create a directory inside the workspace |
| `delete_path` | Delete a file or directory inside the workspace |
| `edit_file` | Edit a file by replacing exact text: each edit swaps old_text for new_text |
| `extract_archive_file` † | Pull one file out of an archive into the workspace, without unpacking the whole thing |
| `find_file` | Find files or folders by name (a substring or a glob) across the workspace, Desktop, Documents, Downloads, Pictures, Videos, Music, ~/Code, ~/Projects, the wor… |
| `glob_files` † | Find files by glob pattern |
| `grep_search` | Regex search across files |
| `list_archive` † | List contents of a .zip/.jar/.whl/.apk/.tar/.tar.gz/.tar.bz2/.tar.xz archive without extracting it |
| `list_directory` | List a directory |
| `locate_path` † | Find a folder or an app by name without searching the whole disk: the top of every drive, the usual places (Documents, Downloads, Desktop, Program Files, AppDa… |
| `move_path` | Move or rename inside the workspace |
| `read_file` | Read a file |
| `read_pdf_large` † | Summarize a very large PDF (hundreds of pages) that won't fit in one context: it is read in page windows, each summarized by a small model call, then combined… |
| `summarize_file` † | Summarise a long document instead of reading all of it |
| `undo` † | Take back a change a tool made to files: a write, an edit, a move or a delete |
| `write_file` | Create a new file in the workspace |

## Web & browser

| Tool | What it does |
|---|---|
| `browse` | Drive a real, visible Chromium window: navigate, get the page text and a numbered list of links and buttons, then act with browse_click and browse_type |
| `browse_click` † | Click a link or button on the current browser page by its visible text (use the exact text from the clickable list that browse returned) |
| `browse_close` † | Close the browser session, only once the person is done with it |
| `browse_key` † | Press a keyboard key/shortcut on the current browser page, for media controls and shortcuts that aren't clickable buttons |
| `browse_scroll` † | Smooth-scroll the current browser page so you can see more content |
| `browse_type` † | Type into a field on the current browser page (a search box, login field, etc.) |
| `list_browsers` † | List installed browsers detected on this machine |
| `open_in_browser` | Open a link in a browser the person names, optionally in a given profile, where they see it and it stays open |
| `open_url` | Open a URL in the user's default browser for them to use |
| `web_fetch` | Fetch a page's readable text for you to read (the article, without menus; a PDF's text too); the user sees nothing |
| `web_search` | Search the web and get the top results: titles, snippets and links, merged from several search services at once |

## Desktop control

| Tool | What it does |
|---|---|
| `arrange_windows` † | Lay windows out on screen in one call: position and size several windows at named regions |
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
| `focus_window` † | Bring an already-open window to the front / focus it, by a substring of its title (e.g |
| `list_windows` † | List every open window: its title, whether it is in front, minimized or open, and the app it belongs to |
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
| `learn_environment` † | This machine as it is now: GPU/VRAM, RAM, OS and the models the local server has |
| `list_installed_apps` † | Installed applications (Windows registry uninstall keys) |
| `list_models` † | List the models the current brain offers (Inglen's built-in engine, another local server, a cloud provider), from its API |
| `list_processes` † | List running processes (top by memory) |
| `load_model` † | Load a model file from this PC on Inglen's built-in engine |
| `network_info` † | Local IP address, hostname, network adapters, wifi |
| `open_app` | Open an app by name, a file with its default program, a folder in Explorer, or a URL |
| `run_command` | Run a shell command |
| `screenshot` | Capture the whole screen and return the saved path; follow with view_image to see it |
| `set_up` † | Install what's missing on this PC yourself, so nobody has to fetch a server from the web |
| `system_info` | Machine snapshot in one call: OS, CPU, GPUs with VRAM, temperature and load, RAM, disks, hostname, user, uptime, battery |
| `view_image` | Load an image file so you can see it (png, jpg, gif, webp, bmp) |
| `what_can_i_do` | What you can do on this PC and what is set up right now, read live: brain and keys, chat and image engines and models, voice, phone, chat apps, email, helpers,… |
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
| `agent_sessions` † | The coding-agent sessions on this PC (Claude Code, Codex and others), the ones the person runs in those apps: list them, read where one got to, or send one a m… |
| `delegate` | Hand one task to another AI agent CLI installed on this PC (Claude Code, Codex, Antigravity, Gemini CLI and others) and move on |
| `get_subagent_result` † | Poll a background subagent for its result |
| `helper_changes` † | See or act on what a finished helper changed on disk |
| `launch_team` † | Spawn a team of sub-agents that work in parallel and open a live terminal pane for each so the user can watch them build side by side (Windows Terminal panes /… |
| `list_subagent_personas` † | List the personas available for spawn_subagent |
| `message_helper` † | For a helper: ask or tell another helper that is working at the same time, by name, or the assistant that sent you (to: 'main') |
| `race_helpers` † | Give one coding task to two or more agent programs at once (say codex and claude-code), each on its own copy of a git repository, to compare their work |
| `spawn_subagent` | Start a helper that works beside you |
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
| `call_phone` † | Ring the user's phone (the Inglen app, paired through Plus): when they answer, they hear `message` and can talk to you in the call |
| `cancel_reminder` † | Cancel a scheduled reminder by id |
| `connectors` † | The chat apps people reach this PC from: Telegram, Discord, WhatsApp |
| `list_reminders` | List all upcoming (un-fired) reminders |
| `notify` † | Show a desktop notification now |
| `pause_reminder` † | Pause a reminder so it stops firing without deleting it, or resume it (paused=false) |
| `send_to_phone` † | Send a message or a file (a screenshot, a photo, a PDF) from this PC to the user's phone, where it arrives with a notification |
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
| `create_plugin` † | Write a new tool for yourself when none of the existing tools can do something the person needs, then use it, now and later |
| `create_skill` † | Save a workflow as a skill, in the workspace's skills folder |
| `delete_plugin` † | Delete an installed plugin by name (removes its file + unregisters the tool) |
| `install_skill` † | Install a skill someone shares, from GitHub or a folder on this PC, into the workspace's skills folder |
| `list_plugins` † | List the self-authored/installed plugins in ~/Jarvis/plugins/ (name, status, description) |
| `list_skills` † | List available skills, bundled and installed |
| `load_skill` | Load a skill's full instructions and its bundled files before following it |

## Image generation

| Tool | What it does |
|---|---|
| `check_video_task` † | Check a video once, without waiting: pending, done, failed or expired |
| `generate_image` † | Make an image (picture, photo, artwork, illustration) from a text prompt, or change one (pass its path as image) |
| `generate_video` † | Start making a video: on this PC when a video model is set up under Models, else with xAI Grok (a saved Grok key) |
| `list_generations` † | List the 10 most recent image/video generation tasks (live + finished) |

## Session

| Tool | What it does |
|---|---|
| `ask_user` † | Ask the person to choose between options before going on: two valid approaches, which of several matches to act on, an ambiguous file or folder |
| `end_session` † | Call this when the user is clearly wrapping up the conversation (says bye, goodbye, see you, thanks that's all, etc.) |
| `worklog` † | Your scratch pad for a long task: note findings as you go and read them back instead of re-reading the sources |
