# Tools

Hearth gives the model **56 tools** to operate your machine. Everything runs locally; the only outbound calls are web searches the model itself makes. Risky tools (shell, file writes, app launch, browser control) prompt for `[y/n/a/N]` permission in the CLI before they run.

_This list is generated from the live tool definitions._

## System & apps

| Tool | What it does |
| --- | --- |
| `clipboard_read` | Read current clipboard text. |
| `clipboard_write` | Write text to the clipboard. |
| `disk_usage` | Find the biggest folders and files under a path. |
| `get_time` | Current local datetime, weekday, timezone offset. |
| `learn_environment` | Re-scan this machine (GPU/VRAM, RAM, installed models, top-level drive map) and refresh it into long-term memory. |
| `list_installed_apps` | Installed applications (Windows registry uninstall keys). |
| `list_models` | List the LLM models available on the connected server (LM Studio / Ollama / cloud) by querying its API. |
| `list_processes` | List running processes (top by memory). |
| `network_info` | Local IP, hostname, network adapters. |
| `open_app` | Launch / open with default association. |
| `run_command` | Execute a shell command. |
| `screenshot` | Capture the screen, save to workspace/screenshots/, return the path. |
| `system_info` | OS, CPU, RAM, disk, hostname, user, uptime. |
| `view_image` | Load an image file from disk so you can SEE it. |
| `whoami` | Introspect your own runtime config. |

## Web & browser

| Tool | What it does |
| --- | --- |
| `browse` | Drive a REAL web browser (controlled Chromium) — the user SEES the window. |
| `browse_click` | Click a link or button on the CURRENT browser page by its visible text (use the exact text from the CLICKABLE list that browse returned). |
| `browse_close` | Close the browser session. |
| `browse_type` | Type into a field on the current browser page (a search box, login field, etc.). |
| `list_browsers` | List installed browsers detected on this machine. |
| `open_in_browser` | Open a URL in a SPECIFIC browser (and optional profile) — the user SEES it in their own browser, and it stays open. |
| `open_url` | Open a URL in the user's DEFAULT browser (their login, fullscreen). |
| `validate_url` | Probe a URL with a HEAD/GET request — confirms it's reachable and returns status code, content-type, redirect target, and response time. |
| `web_fetch` | Fetch a URL and return its readable text (HTML stripped) to YOU. |
| `web_search` | Free DuckDuckGo HTML search. |

## Voice

| Tool | What it does |
| --- | --- |
| `list_voices` | List available built-in Kokoro voice ids. |
| `set_voice` | Change the active TTS voice. |

## Other

| Tool | What it does |
| --- | --- |
| `text_encoder_tool` | Encode/decode text using various methods (Base64, Hex, ROT13), count characters, or format strings. |
| `website_status_tool` | Check if a website is reachable and returns its HTTP status code. |

## Files & docs

| Tool | What it does |
| --- | --- |
| `create_directory` | Create a directory inside the workspace. |
| `delete_path` | Delete a file or directory inside the workspace. |
| `edit_file` | Targeted string-replace edits — never rewrites the whole file. |
| `extract_archive_file` | Pull ONE file out of an archive into the workspace, without unpacking the whole thing. |
| `find_file` | Find files (or folders) by name across common locations. |
| `glob_files` | Find files by glob pattern. |
| `grep_search` | Regex search across files. |
| `list_archive` | List contents of a .zip/.jar/.whl/.apk/.tar/.tar.gz/.tar.bz2/.tar.xz archive WITHOUT extracting it. |
| `list_directory` | List a directory. |
| `locate_path` | Smart locator: find a folder or app by name without globbing the whole disk. |
| `move_path` | Move or rename inside the workspace. |
| `read_file` | Smart file reader. |
| `summarize_file` | Read a file (any format read_file supports. |
| `write_file` | Create a NEW file inside the workspace. |

## Memory

| Tool | What it does |
| --- | --- |
| `memory_forget` | Delete a memory by title. |
| `memory_list` | Show the full memory index (all titles and one-line hooks). |
| `memory_recall` | Search saved memories. |
| `memory_save` | Save a long-term memory. |
| `search_chats` | Search across ALL past chat conversations (full-text via SQLite FTS5). |

## Reminders & alerts

| Tool | What it does |
| --- | --- |
| `cancel_reminder` | Cancel a scheduled reminder by id. |
| `list_reminders` | List all upcoming (un-fired) reminders. |
| `notify` | Pop a desktop notification (Windows toast) RIGHT NOW. |
| `set_reminder` | Schedule a desktop notification at a future time. |

## Session

| Tool | What it does |
| --- | --- |
| `end_session` | Call this when the user is clearly wrapping up the conversation (says bye, goodbye, see you, thanks that's all, etc.). |

## Self-extending (plugins)

| Tool | What it does |
| --- | --- |
| `create_plugin` | Write a NEW local tool (plugin) for yourself when no existing tool fits a capability the user needs — then use it immediately and forever after. |
| `delete_plugin` | Delete an installed plugin by name (removes its file + unregisters the tool). |
| `list_plugins` | List the self-authored/installed plugins in ~/Jarvis/plugins/ (name, status, description). |
